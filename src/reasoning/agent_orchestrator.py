"""
Reasoning Agent Orchestrator
Coordinates LLM reasoning with analysis tools
"""
import asyncio
import logging
import json
import math
from typing import Dict, List, Any, Optional
from datetime import datetime

from .llm_factory import LLMFactory
from .context_memory import ContextMemory
from .tools.historical_analyzer import HistoricalPatternAnalyzer
from .tools.cross_chain_correlator import CrossChainCorrelator
from .tools.market_context_fetcher import MarketContextFetcher
from .tools.wallet_profiler import WalletProfiler
from src.config.llm_config import LLMConfig
from src.analysis.transaction_analyzer import TransactionAnalyzer
from src.signals.signal_generator import SignalGenerator, SignalType, SignalStrength

logger = logging.getLogger(__name__)


class InvalidReasoningOutput(ValueError):
    pass


class ReasoningAgentOrchestrator:
    """
    Orchestrates AI reasoning loop for transaction analysis

    Coordinates:
    - LLM provider
    - Analysis tools
    - Context memory
    - Fallback strategies
    """

    # System prompt for the reasoning agent
    SYSTEM_PROMPT = """You are an expert blockchain analyst specializing in whale tracking and on-chain behavior.

Your role:
1. Analyze blockchain transactions to identify significant patterns
2. Explain what each whale move means, with a clear reasoning chain
3. Correlate multi-chain events and historical patterns
4. Assess the likely market impact of what you observe

This is a monitoring and analysis tool, not a trading system. Describe what is
happening on-chain and what it may signal. Do NOT give trade instructions, entry
or exit prices, position sizing, stop-loss or take-profit levels.

CONSTRAINTS:
- ALWAYS provide clear reasoning for every signal
- MUST correlate with historical patterns when available
- NEVER ignore transactions > 100 ETH
- MUST explain unusual patterns in detail
- Focus on observable, on-chain evidence
- Frame impact as analysis (bullish/bearish/neutral) — never as a recommendation to buy or sell

OUTPUT FORMAT:
Respond with a JSON object containing:
{
  "signals": [
    {
      "type": "accumulation|distribution|exchange_deposit|exchange_withdrawal|unusual_activity",
      "transaction_hash": "full hash of the referenced input transaction",
      "chain": "ethereum|arbitrum",
      "strength": "low|medium|high|very_high",
      "reasoning_chain": ["step 1", "step 2", "step 3"],
      "confidence": 0.0-1.0,
      "predicted_impact": "bullish|bearish|neutral",
      "recommended_actions": ["what to watch next", "..."]
    }
  ],
  "correlations": ["correlation 1", "correlation 2"],
  "market_context_relevance": "how current market conditions affect this analysis"
}
"""

    def __init__(self, config: Optional[LLMConfig] = None):
        """
        Initialize reasoning agent

        Args:
            config: LLM configuration
        """
        self.config = config or LLMConfig()

        # Initialize LLM provider (with fallback)
        try:
            self.llm = LLMFactory.create_provider(self.config, enable_fallback=True)
            logger.info(f"Reasoning agent initialized with {self.llm.get_provider_name()}")
        except Exception as e:
            logger.error('Failed to initialize LLM provider (%s)', type(e).__name__)
            self.llm = None

        # Initialize components
        self.memory = ContextMemory(max_size=100, retention_hours=24)
        self.fallback_analyzer = TransactionAnalyzer()

        # Initialize tools
        self.historical_analyzer = HistoricalPatternAnalyzer(self.memory)
        self.cross_chain_correlator = CrossChainCorrelator(self.memory)
        self.market_context = MarketContextFetcher()
        self.wallet_profiler = WalletProfiler(self.memory)

        # Circuit breaker
        self.consecutive_failures = 0
        self.max_failures = 3

    async def analyze_transactions(
        self,
        transactions: List[Dict[str, Any]],
        use_reasoning: bool = True
    ) -> List[Dict[str, Any]]:
        """
        Analyze transactions with optional AI reasoning

        Args:
            transactions: List of transactions to analyze
            use_reasoning: If False, use rule-based fallback

        Returns:
            List of enhanced signals
        """
        if not use_reasoning or not self.llm or not self.config.enable_reasoning:
            logger.info("Using rule-based fallback analysis")
            reason = 'reasoning_disabled' if not use_reasoning or not self.config.enable_reasoning else 'no_provider'
            return await self._fallback_analysis(transactions, reason=reason)

        # Circuit breaker check
        if self.consecutive_failures >= self.max_failures:
            logger.warning(f"Circuit breaker active ({self.consecutive_failures} failures)")
            return await self._fallback_analysis(transactions, reason='circuit_breaker')

        try:
            # Run reasoning loop with timeout
            async with asyncio.timeout(self.config.reasoning_timeout):
                results = await self._reasoning_loop(transactions)
                self.consecutive_failures = 0  # Reset on success
                return results

        except asyncio.TimeoutError:
            logger.warning(f"Reasoning timeout after {self.config.reasoning_timeout}s")
            self.consecutive_failures += 1
            return await self._fallback_analysis(transactions, reason='timeout')

        except Exception as e:
            logger.error('Reasoning error (%s)', type(e).__name__)
            self.consecutive_failures += 1
            return await self._fallback_analysis(transactions, reason='invalid_output' if isinstance(e, InvalidReasoningOutput) else 'provider_error')

    async def _reasoning_loop(
        self,
        transactions: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Execute AI reasoning loop, refining low-confidence signals up to
        max_reasoning_iterations times before returning.
        """
        max_iters = self.config.max_reasoning_iterations
        logger.info(
            f"Starting reasoning loop for {len(transactions)} transactions "
            f"(max {max_iters} iterations)"
        )

        context = await self._gather_context(transactions)
        signals: List[Dict[str, Any]] = []

        for iteration in range(max_iters):
            low_conf = [s for s in signals if s.get("confidence", 1.0) < 0.7]

            if iteration == 0:
                prompt = self._build_analysis_prompt(transactions, context)
            elif not low_conf:
                logger.info(f"All signals high-confidence, stopping at iteration {iteration}")
                break
            else:
                prompt = self._build_refinement_prompt(low_conf, transactions, context)

            logger.debug(f"Reasoning iteration {iteration + 1}/{max_iters}")

            try:
                response = await self.llm.generate_json(
                    prompt=prompt,
                    system_prompt=self.SYSTEM_PROMPT,
                    schema=self._get_response_schema(),
                    temperature=0.7,
                    max_tokens=2000
                )
                analysis = response.get("parsed_json", {})
            except Exception as e:
                logger.error('LLM generation failed at iteration %d (%s)', iteration + 1, type(e).__name__)
                raise

            new_signals = self._convert_to_signals(analysis, transactions, context, model=response.get('model'))
            signals = self._merge_signals(signals, new_signals)

        for signal in signals:
            self.memory.add_signal(signal)
        for tx in transactions:
            self.memory.add_transaction(tx)

        logger.info(f"Generated {len(signals)} enhanced signals")
        return signals

    def _build_refinement_prompt(
        self,
        low_conf_signals: List[Dict[str, Any]],
        transactions: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> str:
        """Build a focused refinement prompt for low-confidence signals."""
        base = self._build_analysis_prompt(transactions, context)
        low_conf_summary = json.dumps(
            [{"type": s.get("signal_type"), "transaction_hash": s.get('transaction_hash'), 'chain': s.get('chain'),
              "confidence": s.get("confidence"),
              "reasoning": s.get("reasoning_chain", [])} for s in low_conf_signals],
            indent=2
        )
        return (
            f"{base}\n\n"
            f"REFINEMENT REQUEST — the following signals had low confidence (<0.7).\n"
            f"Re-analyze and improve their confidence or replace with better signals:\n"
            f"{low_conf_summary}"
        )

    def _merge_signals(
        self,
        existing: List[Dict[str, Any]],
        new_signals: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """Merge new signals into existing list, replacing by type+tx_hash if confidence improved."""
        if not existing:
            return new_signals

        merged = {(s.get("signal_type"), s.get('chain'), s.get("transaction_hash", s.get("tx_hash"))): s for s in existing}
        for sig in new_signals:
            key = (sig.get("signal_type"), sig.get('chain'), sig.get("transaction_hash", sig.get("tx_hash")))
            existing_conf = merged.get(key, {}).get("confidence", 0.0)
            if sig.get("confidence", 0.0) >= existing_conf:
                merged[key] = sig

        return list(merged.values())

    async def _gather_context(
        self,
        transactions: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Gather contextual information using tools

        Args:
            transactions: Transactions to analyze

        Returns:
            Context dict
        """
        context = {}

        try:
            # Get market context
            context["market"] = await self.market_context.get_context()

            # Find cross-chain correlations
            context["correlations"] = await self.cross_chain_correlator.find_correlations(
                transactions,
                time_window_minutes=60
            )

            # Analyze involved wallets
            wallets = set()
            for tx in transactions:
                if tx.get("from"):
                    wallets.add(tx["from"])
                if tx.get("to"):
                    wallets.add(tx["to"])

            wallet_profiles = {}
            for wallet in list(wallets)[:5]:  # Limit to 5 wallets
                try:
                    profile = await self.wallet_profiler.profile_wallet(wallet)
                    if profile.get("has_profile"):
                        wallet_profiles[wallet] = profile
                except Exception as e:
                    logger.warning(f"Failed to profile wallet {wallet}: {str(e)}")

            context["wallet_profiles"] = wallet_profiles

            # Get historical patterns
            historical = {}
            for wallet in list(wallets)[:5]:
                try:
                    history = await self.historical_analyzer.analyze_wallet(wallet)
                    if history.get("has_history"):
                        historical[wallet] = history
                except Exception as e:
                    logger.warning(f"Failed to analyze wallet history {wallet}: {str(e)}")

            context["historical"] = historical

        except Exception as e:
            logger.error(f"Error gathering context: {str(e)}")

        return context

    def _build_analysis_prompt(
        self,
        transactions: List[Dict[str, Any]],
        context: Dict[str, Any]
    ) -> str:
        """
        Build prompt for LLM analysis

        Args:
            transactions: Transactions to analyze
            context: Contextual information

        Returns:
            Analysis prompt string
        """
        # Format transactions
        tx_summary = []
        for tx in transactions[:10]:  # Limit to 10 transactions
            tx_summary.append({
                "hash": tx.get("hash", "unknown"),
                "from": tx.get("from", "unknown")[:16] + "...",
                "to": tx.get("to", "unknown")[:16] + "...",
                "value_eth": tx.get("value_eth", 0),
                "chain": tx.get("chain", "unknown"),
                "type": tx.get("transaction_type", "unknown")
            })

        prompt = f"""Analyze these blockchain transactions and generate trading signals.

TRANSACTIONS ({len(transactions)} total, showing first {len(tx_summary)}):
{json.dumps(tx_summary, indent=2)}

MARKET CONTEXT:
{json.dumps(context.get("market", {}), indent=2)}

CORRELATIONS DETECTED:
{json.dumps(context.get("correlations", []), indent=2)}

WALLET PROFILES:
{json.dumps({k: v.get("summary", "") for k, v in context.get("wallet_profiles", {}).items()}, indent=2)}

HISTORICAL PATTERNS:
{json.dumps({k: v.get("summary", "") for k, v in context.get("historical", {}).items()}, indent=2)}

Analyze these transactions and provide:
1. On-chain signals with detailed reasoning, each referencing its exact full transaction_hash and chain from the input
2. Correlation analysis
3. Market context relevance
4. Predicted impact and what to watch next
"""

        return prompt

    def _get_response_schema(self) -> Dict[str, Any]:
        """Get JSON schema for LLM response"""
        return {
            "type": "object",
            "properties": {
                "signals": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "required": ["transaction_hash", "chain", "type", "strength", "reasoning_chain", "confidence"],
                        "properties": {
                            "transaction_hash": {"type": "string"},
                            "chain": {"type": "string", "enum": ["ethereum", "arbitrum"]},
                            "type": {"type": "string"},
                            "strength": {"type": "string"},
                            "reasoning_chain": {"type": "array", "items": {"type": "string"}},
                            "confidence": {"type": "number"},
                            "predicted_impact": {"type": "string"},
                            "recommended_actions": {"type": "array", "items": {"type": "string"}}
                        }
                    }
                },
                "correlations": {"type": "array", "items": {"type": "string"}},
                "market_context_relevance": {"type": "string"}
            }
        }

    def _convert_to_signals(
        self,
        analysis: Dict[str, Any],
        transactions: List[Dict[str, Any]],
        context: Dict[str, Any],
        model: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Convert LLM analysis to enhanced signal format

        Args:
            analysis: LLM analysis output
            transactions: Original transactions
            context: Context data

        Returns:
            List of enhanced signals
        """
        signals = []

        if not isinstance(analysis, dict) or not isinstance(analysis.get('signals'), list):
            raise InvalidReasoningOutput('Reasoning result must contain a signals list')
        references = {(tx.get('chain'), tx.get('hash')): tx for tx in transactions[:10]}
        if len(references) != len(transactions[:10]):
            raise InvalidReasoningOutput('Ambiguous input transaction references')
        correlations = analysis.get('correlations', [])
        if not isinstance(correlations, list) or not all(isinstance(item, str) for item in correlations):
            raise InvalidReasoningOutput('Invalid reasoning correlations')
        for signal_data in analysis['signals']:
            if not isinstance(signal_data, dict):
                raise InvalidReasoningOutput('Invalid reasoning signal')
            if not isinstance(signal_data.get('chain'), str) or not isinstance(signal_data.get('transaction_hash'), str):
                raise InvalidReasoningOutput('Reasoning transaction reference must be explicit strings')
            reference = (signal_data['chain'], signal_data['transaction_hash'])
            if reference not in references:
                raise InvalidReasoningOutput('Reasoning transaction reference is missing or unknown')
            tx = references[reference]
            confidence = signal_data.get('confidence')
            if (not isinstance(confidence, (int, float)) or isinstance(confidence, bool)
                    or not math.isfinite(confidence) or not 0 <= confidence <= 1):
                raise InvalidReasoningOutput('Invalid reasoning confidence')
            if signal_data.get('type') not in {kind.value for kind in SignalType} or signal_data.get('strength') not in {kind.value for kind in SignalStrength}:
                raise InvalidReasoningOutput('Invalid reasoning type or strength')
            for field in ('reasoning_chain', 'recommended_actions'):
                values = signal_data.get(field, [])
                if not isinstance(values, list) or not all(isinstance(item, str) for item in values):
                    raise InvalidReasoningOutput('Invalid reasoning text fields')
            if signal_data.get('predicted_impact', 'neutral') not in ('bullish', 'bearish', 'neutral'):
                raise InvalidReasoningOutput('Invalid reasoning impact')
            wallet_info = tx.get('to_wallet_info') if tx.get('direction') == 'incoming' else tx.get('from_wallet_info')
            wallet_info = wallet_info or {}

            signal = {
                "signal_type": signal_data.get("type", "unusual_activity"),
                "strength": signal_data.get("strength", "medium"),
                "transaction_hash": tx.get("hash", "unknown"),
                "wallet_address": wallet_info.get('address', tx.get('from', 'unknown')),
                "wallet_name": wallet_info.get('name'),
                "wallet_category": wallet_info.get('category', 'unknown'),
                "tags": wallet_info.get('tags', []),
                "chain": tx.get("chain", "ethereum"),
                "value_eth": tx.get("value_eth", 0),
                "reasoning_chain": signal_data.get("reasoning_chain", []),
                "confidence": signal_data.get("confidence", 0.5),
                "predicted_impact": signal_data.get("predicted_impact", "neutral"),
                "recommended_actions": signal_data.get("recommended_actions", []),
                "correlations": analysis.get("correlations", []),
                "market_context": context.get("market", {}),
                "timestamp": datetime.now().isoformat(),
                "source": "reasoning_agent",
                "provenance": self._provenance('ai', model=model)
            }

            signals.append(signal)

        return signals

    async def _fallback_analysis(
        self,
        transactions: List[Dict[str, Any]],
        reason: str = 'unspecified'
    ) -> List[Dict[str, Any]]:
        """
        Fallback to rule-based analysis

        Args:
            transactions: Transactions to analyze

        Returns:
            Basic signals without reasoning
        """
        logger.info("Using rule-based fallback")
        signals = []

        for tx in transactions:
            # Use existing TransactionAnalyzer
            analyzed = tx if 'is_significant' in tx else self.fallback_analyzer.analyze_transaction(tx)
            for basic in SignalGenerator().generate_signals(analyzed):
                signal = basic.to_dict()
                signal.update(source='fallback_analyzer', reasoning_chain=['Rule-based fallback: ' + reason],
                              predicted_impact='neutral', recommended_actions=[], correlations=[], market_context={},
                              provenance=self._provenance('rules_fallback', reason=reason))
                signals.append(signal)

        return signals

    def _provenance(self, origin, *, model=None, reason=None):
        actual = getattr(self.llm, 'runtime_provider', None)
        actual = actual if isinstance(actual, str) and actual in ('ollama', 'lmstudio', 'claude', 'openai') else None
        configured_model = getattr(self.llm, 'model_name', None)
        if not isinstance(configured_model, str):
            configured_model = getattr(self.llm, 'model', None)
        configured_model = configured_model if isinstance(configured_model, str) else None
        reported_model = model if isinstance(model, str) and model else None
        requested = getattr(self.config, 'provider', None)
        return {'schema_version': 1, 'origin': origin, 'requested_provider': requested,
                'provider': actual if origin == 'ai' else None,
                'model': (reported_model or configured_model) if origin == 'ai' else None,
                'model_source': ('response' if reported_model else 'configuration' if configured_model else None) if origin == 'ai' else None,
                'provider_fallback': actual != requested if actual is not None else None,
                'analysis_fallback': origin == 'rules_fallback', 'fallback_reason': reason,
                'attempted_provider': actual if origin != 'ai' else None,
                'attempted_model': configured_model if origin != 'ai' else None,
                'attribution': 'transaction_hash_and_chain' if origin == 'ai' else 'rule_input'}

    async def close(self):
        """Cleanup resources"""
        if hasattr(self.llm, 'close'):
            await self.llm.close()
        await self.market_context.close()
