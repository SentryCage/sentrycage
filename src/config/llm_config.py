"""
LLM configuration management
"""
import os
from typing import List
import logging

logger = logging.getLogger(__name__)


class LLMConfig:
    """Configuration for LLM providers"""

    def __init__(self, env=None):
        """Load configuration from environment variables"""

        from src.config.settings import normalized_ai_env, boolean
        values = normalized_ai_env(os.environ if env is None else env)

        # Primary provider
        self.provider = values.get("LLM_PROVIDER", "ollama").lower()

        # Enable/disable reasoning agent
        self.enable_reasoning = boolean(values, "ENABLE_REASONING", False)

        # Reasoning parameters
        self.max_reasoning_iterations = int(values.get("MAX_REASONING_ITERATIONS", "5"))
        self.reasoning_timeout = int(values.get("REASONING_TIMEOUT", "30"))

        # LMStudio configuration
        self.lmstudio_url = values.get("LMSTUDIO_URL", "http://localhost:1234/v1")
        self.lmstudio_model = values.get("LMSTUDIO_MODEL", "local-model")

        # Ollama configuration
        self.ollama_url = values.get("OLLAMA_URL", "http://localhost:11434")
        self.ollama_model = values.get("OLLAMA_MODEL", "llama3.1:8b")

        # Claude configuration
        self.claude_api_key = values.get("CLAUDE_API_KEY")
        self.claude_model = values.get("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")

        # OpenAI configuration
        self.openai_api_key = values.get("OPENAI_API_KEY")
        self.openai_model = values.get("OPENAI_MODEL", "gpt-4o")

        # Fallback chain
        self.fallback_providers = self._build_fallback_chain()

        logger.info(f"LLM Config loaded: primary={self.provider}, reasoning={self.enable_reasoning}")

    def _build_fallback_chain(self) -> List[str]:
        """
        Build fallback provider chain based on what's configured

        Returns:
            List of provider names in order of preference
        """
        chain = [self.provider]

        # Add other providers as fallbacks if configured
        candidates = ["lmstudio", "ollama", "claude", "openai"]

        for candidate in candidates:
            if candidate == self.provider:
                continue

            # Check if provider is configured
            if candidate == "claude" and not self.claude_api_key:
                continue
            if candidate == "openai" and not self.openai_api_key:
                continue

            chain.append(candidate)

        logger.info(f"Fallback chain: {' -> '.join(chain)}")
        return chain

    def get_provider_config(self, provider_name: str) -> dict:
        """
        Get configuration dict for a specific provider

        Args:
            provider_name: Provider name

        Returns:
            Configuration dict
        """
        if provider_name == "lmstudio":
            return {
                "base_url": self.lmstudio_url,
                "model_name": self.lmstudio_model
            }
        elif provider_name == "ollama":
            return {
                "base_url": self.ollama_url,
                "model_name": self.ollama_model
            }
        elif provider_name == "claude":
            return {
                "api_key": self.claude_api_key,
                "model": self.claude_model
            }
        elif provider_name == "openai":
            return {
                "api_key": self.openai_api_key,
                "model": self.openai_model
            }
        else:
            raise ValueError(f"Unknown provider: {provider_name}")
