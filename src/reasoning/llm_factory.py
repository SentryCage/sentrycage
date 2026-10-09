"""
Factory for creating LLM providers with automatic fallback
"""
import logging
from typing import Optional

from .llm_interface import LLMInterface
from .providers.lmstudio_provider import LMStudioProvider
from .providers.ollama_provider import OllamaProvider
from .providers.claude_provider import ClaudeProvider
from .providers.openai_provider import OpenAIProvider
from src.config.llm_config import LLMConfig

logger = logging.getLogger(__name__)


class LLMFactory:
    """
    Factory for creating LLM providers with automatic fallback

    Tries providers in order until one is available
    """

    @staticmethod
    def create_provider(
        config: Optional[LLMConfig] = None,
        enable_fallback: bool = True
    ) -> LLMInterface:
        """
        Create LLM provider based on configuration

        Args:
            config: LLM configuration (creates default if None)
            enable_fallback: If True, try fallback providers if primary fails

        Returns:
            Initialized LLM provider

        Raises:
            Exception: If no provider is available
        """
        if config is None:
            config = LLMConfig()

        providers_to_try = config.fallback_providers if enable_fallback else [config.provider]

        logger.info(f"Attempting to initialize LLM providers: {providers_to_try}")

        for provider_name in providers_to_try:
            try:
                logger.info(f"Trying provider: {provider_name}")
                provider = LLMFactory._create_single_provider(provider_name, config)

                # Test availability
                if provider.is_available():
                    provider.runtime_provider = provider_name
                    logger.info(f"✅ Successfully initialized: {provider.get_provider_name()}")
                    return provider
                else:
                    logger.warning(f"⚠️ Provider {provider_name} not available, trying next...")

            except Exception as e:
                logger.warning('Failed to initialize %s (%s)', provider_name, type(e).__name__)
                continue

        # No provider available
        error_msg = (
            "No LLM provider available! "
            "Ensure at least one provider is running:\n"
            "  - LMStudio: http://localhost:1234\n"
            "  - Ollama: http://localhost:11434\n"
            "  - Claude: Set CLAUDE_API_KEY\n"
            "  - OpenAI: Set OPENAI_API_KEY"
        )
        logger.error(error_msg)
        raise Exception(error_msg)

    @staticmethod
    def _create_single_provider(provider_name: str, config: LLMConfig) -> LLMInterface:
        """
        Create a single provider instance

        Args:
            provider_name: Provider name
            config: Configuration object

        Returns:
            LLM provider instance
        """
        provider_config = config.get_provider_config(provider_name)

        if provider_name == "lmstudio":
            return LMStudioProvider(**provider_config)

        elif provider_name == "ollama":
            return OllamaProvider(**provider_config)

        elif provider_name == "claude":
            if not provider_config.get("api_key"):
                raise Exception("Claude API key not configured")
            return ClaudeProvider(**provider_config)

        elif provider_name == "openai":
            if not provider_config.get("api_key"):
                raise Exception("OpenAI API key not configured")
            return OpenAIProvider(**provider_config)

        else:
            raise ValueError(f"Unknown provider: {provider_name}")

    @staticmethod
    async def test_all_providers(config: Optional[LLMConfig] = None):
        """
        Test all configured providers and return status

        Args:
            config: LLM configuration

        Returns:
            Dict of provider statuses
        """
        if config is None:
            config = LLMConfig()

        results = {}

        for provider_name in ["lmstudio", "ollama", "claude", "openai"]:
            try:
                provider = LLMFactory._create_single_provider(provider_name, config)
                is_available = provider.is_available()

                results[provider_name] = {
                    "available": is_available,
                    "provider_info": provider.get_provider_name() if is_available else None,
                    "context_window": provider.get_context_window() if is_available else None
                }
            except Exception as e:
                results[provider_name] = {
                    "available": False,
                    "error": str(e)
                }

        return results
