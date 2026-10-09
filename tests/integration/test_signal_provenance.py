"""Provider selection, explicit AI attribution, fallback labels and durable fidelity."""
from copy import deepcopy
from unittest.mock import patch
import asyncio
from types import SimpleNamespace
import pytest
from src.orchestration.pipeline import SQLiteStorage, TransactionPipeline
from src.reasoning.agent_orchestrator import InvalidReasoningOutput
from tests.integration.test_recovery import recipient
from tests.integration.test_runtime_pipeline import raw_transaction
from tests.unit.test_reasoning_orchestrator import _make_orchestrator


def response_for(raw, **changes):
    signal = dict(type='accumulation', strength='high', transaction_hash=raw['hash'], chain='ethereum',
                  reasoning_chain=['Observed accumulation', 'Check subsequent transfers'], confidence=.9,
                  predicted_impact='bullish', recommended_actions=['Observe later activity'])
    signal.update(changes)
    return dict(model='reported-model', parsed_json=dict(signals=[signal], correlations=['Related movement']))


class LocalProvider:
    runtime_provider = 'ollama'
    model_name = 'configured-model'

    def __init__(self, response, failure=None):
        self.response = response
        self.failure = failure

    async def generate_json(self, **kwargs):
        if self.failure == 'wait':
            await asyncio.Event().wait()
        if self.failure is not None:
            raise RuntimeError('https://private.invalid/secret-token')
        return deepcopy(self.response)

    def get_provider_name(self):
        return 'ollama (configured-model)'

    def is_available(self):
        return True


def orchestrator(raw, *, changes=None, failure=None):
    response = response_for(raw, **(changes or {}))
    agent = _make_orchestrator(response)
    agent.config.provider = 'openai'
    agent.llm = LocalProvider(response, failure)
    return agent


async def test_ai_provider_model_attribution_and_all_fields_survive_sqlite_and_outbox(tmp_path):
    raw = raw_transaction()
    agent = orchestrator(raw)
    store = SQLiteStorage(str(tmp_path/'ai.db'))
    pipeline = TransactionPipeline(store, recipient(), reasoning_agent=agent)
    with patch('socket.socket.connect', side_effect=AssertionError('network forbidden')):
        result = await pipeline.process([raw], 'ethereum', deliver=False)
    signal = result.signals[0]
    provenance = signal.provenance
    assert provenance['origin'] == 'ai'
    assert provenance['requested_provider'] == 'openai' and provenance['provider'] == 'ollama'
    assert provenance['model'] == 'reported-model' and provenance['model_source'] == 'response'
    assert provenance['provider_fallback'] is True and not provenance['analysis_fallback']
    assert provenance['attribution'] == 'transaction_hash_and_chain'
    assert signal.wallet_address == raw['to']
    row = SQLiteStorage(store.db_path).read()[0]
    assert row['source'] == 'ai_reasoning'
    assert row['reasoning_chain'] == signal.reasoning_chain
    for name, value in signal.to_dict().items():
        assert row['metadata'][name] == value, name
    import json
    notification = store.pending_notifications('injected')[0]
    assert json.loads(notification['payload'])['provenance'] == provenance
    assert notification['message'] == signal.get_message()
    assert 'AI Reasoning' in notification['message']


@pytest.mark.parametrize('reason', ['no_provider', 'provider_error', 'timeout', 'circuit_breaker', 'reasoning_disabled', 'invalid_output'])
async def test_fallback_has_rule_origin_cause_and_preserves_actual_signal(tmp_path, reason, caplog):
    raw = raw_transaction()
    agent = orchestrator(raw, failure='wait' if reason == 'timeout' else 'fail' if reason == 'provider_error' else None,
                         changes={'transaction_hash': 'unknown'} if reason == 'invalid_output' else None)
    if reason == 'no_provider':
        agent.llm = None
    if reason == 'circuit_breaker':
        agent.consecutive_failures = agent.max_failures
    if reason == 'reasoning_disabled':
        agent.config.enable_reasoning = False
    agent.config.reasoning_timeout = .01 if reason == 'timeout' else 30
    store = SQLiteStorage(str(tmp_path/'fallback.db'))
    result = await TransactionPipeline(store, recipient(), reasoning_agent=agent).process([raw], 'ethereum')
    signal = result.signals[0]
    assert signal.signal_type.value == 'accumulation' and signal.value_eth == 500
    assert signal.provenance['origin'] == 'rules_fallback'
    assert signal.provenance['fallback_reason'] == reason
    assert signal.provenance['provider'] is None and signal.provenance['model'] is None
    assert signal.provenance['analysis_fallback']
    row = store.read()[0]
    assert row['source'] == 'whale_tracker'
    assert row['metadata']['provenance'] == signal.provenance
    assert 'Rule-based fallback' in signal.get_message() and 'AI Reasoning' not in signal.get_message()
    assert 'secret-token' not in caplog.text


def test_out_of_order_ai_signals_are_matched_by_hash_and_chain():
    first = dict(raw_transaction(), chain='ethereum', value_eth=500)
    second = dict(first, hash='0x' + 'ef' * 32, value_eth=700)
    agent = orchestrator(first)
    analysis = response_for(second)['parsed_json']
    analysis['signals'].append(response_for(first)['parsed_json']['signals'][0])
    signals = agent._convert_to_signals(analysis, [first, second], {})
    assert [(signal['transaction_hash'], signal['value_eth']) for signal in signals] == [(second['hash'], 700), (first['hash'], 500)]
    prompt = agent._build_analysis_prompt([first, second], {})
    assert first['hash'] in prompt and second['hash'] in prompt


@pytest.mark.parametrize('change', [dict(transaction_hash='unknown'), dict(chain='arbitrum'),
                                  dict(transaction_hash=None), dict(chain=[]), dict(confidence=float('nan')),
                                  dict(confidence=2), dict(confidence=True), dict(type='invalid'),
                                  dict(strength='invalid'), dict(reasoning_chain='wrong'),
                                  dict(predicted_impact='invalid')])
def test_invalid_ai_output_is_rejected_instead_of_best_effort_attribution(change):
    raw = raw_transaction()
    agent = orchestrator(raw)
    with pytest.raises(InvalidReasoningOutput):
        agent._convert_to_signals(response_for(raw, **change)['parsed_json'], [dict(raw, chain='ethereum')], {})


def test_missing_reference_or_signals_list_is_rejected():
    raw = dict(raw_transaction(), chain='ethereum')
    agent = orchestrator(raw)
    analysis = response_for(raw)['parsed_json']
    del analysis['signals'][0]['transaction_hash']
    with pytest.raises(InvalidReasoningOutput):
        agent._convert_to_signals(analysis, [raw], {})
    with pytest.raises(InvalidReasoningOutput):
        agent._convert_to_signals({}, [raw], {})
    with pytest.raises(InvalidReasoningOutput):
        agent._convert_to_signals(response_for(raw)['parsed_json'], [raw, raw], {})


def test_merge_retains_distinct_transactions_and_chains():
    raw = raw_transaction()
    agent = orchestrator(raw)
    signals = [dict(signal_type='accumulation', chain='ethereum', transaction_hash='a', confidence=.8),
               dict(signal_type='accumulation', chain='ethereum', transaction_hash='b', confidence=.8),
               dict(signal_type='accumulation', chain='arbitrum', transaction_hash='a', confidence=.8)]
    assert len(agent._merge_signals(signals, [dict(signals[0], confidence=.9)])) == 3


def test_factory_records_actual_provider_and_config_model_when_response_omits_it():
    from src.reasoning.llm_factory import LLMFactory
    raw = raw_transaction()
    provider = LocalProvider(response_for(raw))
    unavailable = SimpleNamespace(is_available=lambda: False)
    config = SimpleNamespace(provider='openai', fallback_providers=['openai', 'ollama'])
    with patch.object(LLMFactory, '_create_single_provider', side_effect=[unavailable, provider]):
        assert LLMFactory.create_provider(config) is provider
    agent = orchestrator(raw)
    agent.llm = provider
    provenance = agent._provenance('ai')
    assert provenance['provider'] == 'ollama' and provenance['provider_fallback']
    assert provenance['model'] == 'configured-model' and provenance['model_source'] == 'configuration'


async def test_ai_off_records_rules_without_constructing_provider(tmp_path):
    store = SQLiteStorage(str(tmp_path/'rules.db'))
    with patch('src.reasoning.llm_factory.LLMFactory.create_provider', side_effect=AssertionError('provider forbidden')):
        result = await TransactionPipeline(store, recipient()).process([raw_transaction()], 'ethereum')
    assert result.signals[0].provenance['origin'] == 'rules'
    assert store.read()[0]['metadata']['provenance']['provider'] is None


async def test_pipeline_rejects_unrelated_injected_reasoning_without_marking_complete(tmp_path):
    from unittest.mock import AsyncMock
    store = SQLiteStorage(str(tmp_path/'reference.db'))
    agent = AsyncMock()
    agent.analyze_transactions.return_value = [dict(transaction_hash='unrelated', chain='ethereum')]
    with pytest.raises(ValueError, match='different transaction'):
        await TransactionPipeline(store, recipient(), reasoning_agent=agent).process([raw_transaction()], 'ethereum')
    assert not store.is_processed(raw_transaction()['hash'], 'ethereum')
    assert not store.read()


async def test_empty_reasoning_preserves_other_ai_fields(tmp_path):
    raw = raw_transaction()
    store = SQLiteStorage(str(tmp_path/'empty-reasoning.db'))
    agent = orchestrator(raw, changes={'reasoning_chain': []})
    result = await TransactionPipeline(store, recipient(), reasoning_agent=agent).process([raw], 'ethereum')
    metadata = store.read()[0]['metadata']
    assert metadata['reasoning_chain'] == []
    assert metadata['predicted_impact'] == 'bullish'
    assert metadata['recommended_actions'] == ['Observe later activity']
    assert metadata['correlations'] == ['Related movement']
    assert metadata['provenance'] == result.signals[0].provenance


async def test_wrong_reference_from_generator_cannot_be_committed(tmp_path):
    from unittest.mock import Mock
    from src.signals.signal_generator import SignalGenerator
    store = SQLiteStorage(str(tmp_path/'wrong-generator.db'))
    pipeline = TransactionPipeline(store, recipient())
    analyzed = pipeline.analyzer.analyze_transaction(dict(raw_transaction(), chain='ethereum'))
    wrong = SignalGenerator().generate_signals(analyzed)[0]
    wrong.transaction_hash = 'other'
    pipeline.generator = Mock()
    pipeline.generator.generate_signals.return_value = [wrong]
    with pytest.raises(ValueError, match='staged transaction'):
        await pipeline.process([raw_transaction()], 'ethereum')
    assert not store.is_processed(raw_transaction()['hash'], 'ethereum')
    assert not store.read()
