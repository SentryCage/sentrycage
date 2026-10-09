"""Unit tests for ReasoningAgentOrchestrator — mocks the LLM provider."""
import asyncio
import pytest
from unittest.mock import AsyncMock


SAMPLE_TX = {
    "hash": "0x" + "a" * 64,
    "from": "0x" + "b" * 40,
    "to": "0x" + "c" * 40,
    "value_eth": 500.0,
    "chain": "ethereum",
    "transaction_type": "transfer",
}

HIGH_CONF_RESPONSE = {
    "parsed_json": {
        "signals": [
            {
                "type": "accumulation",
                "transaction_hash": SAMPLE_TX['hash'],
                "chain": "ethereum",
                "strength": "high",
                "reasoning_chain": ["step1", "step2"],
                "confidence": 0.9,
                "predicted_impact": "bullish",
                "recommended_actions": ["watch for follow-up accumulation"],
            }
        ],
        "correlations": [],
        "market_context_relevance": "neutral",
    }
}

LOW_CONF_RESPONSE = {
    "parsed_json": {
        "signals": [
            {
                "type": "unusual_activity",
                "transaction_hash": SAMPLE_TX['hash'],
                "chain": "ethereum",
                "strength": "low",
                "reasoning_chain": ["step1"],
                "confidence": 0.4,
                "predicted_impact": "neutral",
                "recommended_actions": [],
            }
        ],
        "correlations": [],
        "market_context_relevance": "unclear",
    }
}


def _make_orchestrator(llm_response, max_iters=3):
    """Build an orchestrator with a fully mocked LLM and tools."""
    from src.config.llm_config import LLMConfig
    from src.reasoning.agent_orchestrator import ReasoningAgentOrchestrator
    from src.reasoning.context_memory import ContextMemory
    from src.reasoning.tools.historical_analyzer import HistoricalPatternAnalyzer
    from src.reasoning.tools.cross_chain_correlator import CrossChainCorrelator
    from src.reasoning.tools.market_context_fetcher import MarketContextFetcher
    from src.reasoning.tools.wallet_profiler import WalletProfiler
    from src.analysis.transaction_analyzer import TransactionAnalyzer

    config = LLMConfig()
    config.max_reasoning_iterations = max_iters
    config.enable_reasoning = True

    orch = ReasoningAgentOrchestrator.__new__(ReasoningAgentOrchestrator)
    orch.config = config
    orch.consecutive_failures = 0
    orch.max_failures = 3

    mock_llm = AsyncMock()
    mock_llm.get_provider_name.return_value = "mock"
    mock_llm.generate_json = AsyncMock(return_value=llm_response)
    orch.llm = mock_llm

    orch.memory = ContextMemory(max_size=100, retention_hours=24)
    orch.fallback_analyzer = TransactionAnalyzer()
    orch.historical_analyzer = HistoricalPatternAnalyzer(orch.memory)
    orch.cross_chain_correlator = CrossChainCorrelator(orch.memory)
    orch.market_context = MarketContextFetcher()
    orch.wallet_profiler = WalletProfiler(orch.memory)

    orch.market_context.get_context = AsyncMock(return_value={})
    orch.cross_chain_correlator.find_correlations = AsyncMock(return_value=[])
    orch.wallet_profiler.profile_wallet = AsyncMock(return_value={"has_profile": False})
    orch.historical_analyzer.analyze_patterns = AsyncMock(return_value={"summary": ""})

    return orch


def run(coro):
    return asyncio.run(coro)


def test_single_pass_high_confidence_stops_early():
    """High-confidence signals stop the loop after one LLM call."""
    orch = _make_orchestrator(HIGH_CONF_RESPONSE, max_iters=5)
    signals = run(orch._reasoning_loop([SAMPLE_TX]))

    assert len(signals) == 1
    assert signals[0]["signal_type"] == "accumulation"
    assert orch.llm.generate_json.call_count == 1


def test_low_confidence_triggers_refinement():
    """Low-confidence signals trigger up to max_iters LLM calls."""
    orch = _make_orchestrator(LOW_CONF_RESPONSE, max_iters=3)
    signals = run(orch._reasoning_loop([SAMPLE_TX]))

    assert orch.llm.generate_json.call_count == 3
    assert len(signals) == 1


def test_max_iterations_respected():
    """Loop must never exceed max_reasoning_iterations."""
    orch = _make_orchestrator(LOW_CONF_RESPONSE, max_iters=2)
    run(orch._reasoning_loop([SAMPLE_TX]))
    assert orch.llm.generate_json.call_count <= 2


def test_merge_signals_keeps_higher_confidence():
    """_merge_signals keeps the higher-confidence version on duplicate key."""
    from src.reasoning.agent_orchestrator import ReasoningAgentOrchestrator
    orch = ReasoningAgentOrchestrator.__new__(ReasoningAgentOrchestrator)

    existing = [{"signal_type": "accumulation", "tx_hash": "0xabc", "confidence": 0.5}]
    new = [{"signal_type": "accumulation", "tx_hash": "0xabc", "confidence": 0.9}]
    merged = orch._merge_signals(existing, new)

    assert len(merged) == 1
    assert merged[0]["confidence"] == 0.9


def test_merge_signals_does_not_downgrade():
    """_merge_signals keeps existing if new signal has lower confidence."""
    from src.reasoning.agent_orchestrator import ReasoningAgentOrchestrator
    orch = ReasoningAgentOrchestrator.__new__(ReasoningAgentOrchestrator)

    existing = [{"signal_type": "accumulation", "tx_hash": "0xabc", "confidence": 0.85}]
    new = [{"signal_type": "accumulation", "tx_hash": "0xabc", "confidence": 0.3}]
    merged = orch._merge_signals(existing, new)

    assert merged[0]["confidence"] == 0.85


def test_fallback_used_when_llm_none():
    """If LLM is None, analyze_transactions routes to _fallback_analysis, not generate_json."""
    orch = _make_orchestrator(HIGH_CONF_RESPONSE)
    orch.llm = None
    orch._fallback_analysis = AsyncMock(return_value=[{"signal_type": "fallback"}])
    signals = run(orch.analyze_transactions([SAMPLE_TX], use_reasoning=True))
    orch._fallback_analysis.assert_called_once()
    assert signals[0]["signal_type"] == "fallback"


def test_circuit_breaker_triggers_fallback():
    """After max_failures, circuit breaker routes to fallback without calling LLM."""
    orch = _make_orchestrator(HIGH_CONF_RESPONSE)
    orch.consecutive_failures = orch.max_failures
    orch._fallback_analysis = AsyncMock(return_value=[])
    run(orch.analyze_transactions([SAMPLE_TX], use_reasoning=True))
    orch.llm.generate_json.assert_not_called()
    orch._fallback_analysis.assert_called_once()


def test_reasoning_does_not_require_python_311_timeout_context_manager(monkeypatch):
    monkeypatch.delattr(asyncio, 'timeout', raising=False)
    orch = _make_orchestrator(HIGH_CONF_RESPONSE)
    signals = run(orch.analyze_transactions([SAMPLE_TX]))
    assert signals[0]['source'] == 'reasoning_agent'
    assert signals[0]['provenance']['origin'] == 'ai'
