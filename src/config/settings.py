"""Configuration without import-time I/O; validate before creating live components."""
import math
import os
import re
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping
from urllib.parse import urlsplit

from src.config.wallet_registry import Chain


class ConfigurationError(ValueError):
    """An actionable configuration error that never includes secret values."""


def boolean(env, name, default=False):
    value = env.get(name, str(default)).strip().lower()
    if value not in ('true', 'false'):
        raise ConfigurationError(f'{name}: use true or false')
    return value == 'true'


def positive(env, name, default, kind=float):
    try:
        value = kind(env.get(name, str(default)))
        if not math.isfinite(value) or value <= 0:
            raise ValueError
        return value
    except (ValueError, TypeError):
        raise ConfigurationError(f'{name}: expected a positive {kind.__name__}') from None


def required(value, name):
    if not value or any(marker in value.lower() for marker in
                        ('your_', 'placeholder', 'change-me', 'example.com', '<', '>')):
        raise ConfigurationError(f'{name}: set a real value; empty values and placeholders are invalid')
    return value


def endpoint(value, name):
    required(value, name)
    try:
        parsed = urlsplit(value)
        valid = parsed.scheme in ('http', 'https') and bool(parsed.hostname) and bool(parsed.port or 1)
    except ValueError:
        valid = False
    if not valid:
        raise ConfigurationError(f'{name}: expected an http(s) endpoint')
    return value


def chat_id(value, name='TELEGRAM_CHAT_ID'):
    if not re.fullmatch(r'-?[0-9]+', str(value or '').strip()) or int(value) == 0:
        raise ConfigurationError(f'{name}: expected a nonzero integer (negative group IDs are supported)')
    return int(value)


def normalized_ai_env(env):
    result = dict(env)
    for old, new in (('ENABLE_AI_REASONING', 'ENABLE_REASONING'), ('OLLAMA_BASE_URL', 'OLLAMA_URL')):
        if old not in result:
            continue
        if new in result and result[new].strip().lower() != result[old].strip().lower():
            raise ConfigurationError(f'{old} conflicts with {new}; migrate to {new}')
        warnings.warn(f'{old} is deprecated; use {new}', DeprecationWarning, stacklevel=2)
        result.setdefault(new, result[old])
    return result


@dataclass(frozen=True)
class Settings:
    enabled_chains: tuple[Chain, ...]
    rpc_urls: dict[Chain, str] = field(repr=False)
    etherscan_api_key: str = field(repr=False)
    notifications_enabled: bool
    telegram_token: str = field(default='', repr=False)
    telegram_chat_id: int | None = None
    db_path: str = str(Path(__file__).resolve().parents[2] / 'data/whale_tracker.db')
    polling_interval: int = 60
    transaction_threshold: float = 100.0
    request_timeout: float = 10.0
    enable_reasoning: bool = False
    llm_env: dict = field(default_factory=dict, repr=False)

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None):
        values = normalized_ai_env(os.environ if env is None else env)
        names = [v.strip().lower() for v in values.get('ENABLED_CHAINS', 'ethereum').split(',')]
        try:
            chains = tuple(Chain(v) for v in names)
        except ValueError:
            raise ConfigurationError('ENABLED_CHAINS: choose ethereum and/or arbitrum') from None
        if not chains or len(set(chains)) != len(chains):
            raise ConfigurationError('ENABLED_CHAINS: specify at least one chain, without duplicates')
        urls = {c: endpoint(values.get(f'{c.value.upper()}_RPC_URL', ''), f'{c.value.upper()}_RPC_URL') for c in chains}
        key = required(values.get('ETHERSCAN_API_KEY', ''), 'ETHERSCAN_API_KEY')
        notifications = boolean(values, 'NOTIFICATIONS_ENABLED', True)
        token = ''
        recipient = None
        if notifications:
            token = required(values.get('TELEGRAM_BOT_TOKEN', ''), 'TELEGRAM_BOT_TOKEN')
            if not re.fullmatch(r'[0-9]+:[A-Za-z0-9_-]+', token):
                raise ConfigurationError('TELEGRAM_BOT_TOKEN: expected a BotFather token')
            recipient = chat_id(values.get('TELEGRAM_CHAT_ID'))
        reasoning = boolean(values, 'ENABLE_REASONING', False)
        if reasoning:
            provider = values.get('LLM_PROVIDER', 'ollama').lower()
            if provider not in ('ollama', 'lmstudio', 'claude', 'openai'):
                raise ConfigurationError('LLM_PROVIDER: choose ollama, lmstudio, claude or openai')
            if provider in ('claude', 'openai'):
                required(values.get(f'{provider.upper()}_API_KEY', ''), f'{provider.upper()}_API_KEY')
            else:
                name = f'{provider.upper()}_URL'
                endpoint(values.get(name, 'http://localhost:11434' if provider == 'ollama' else 'http://localhost:1234/v1'), name)
            positive(values, 'MAX_REASONING_ITERATIONS', 5, int)
            positive(values, 'REASONING_TIMEOUT', 30, int)
        db = values.get('DB_PATH', cls.db_path).strip()
        if not db or db == ':memory:':
            raise ConfigurationError('DB_PATH: use a file path (connections are opened separately)')
        return cls(chains, urls, key, notifications, token, recipient, db,
                   positive(values, 'POLLING_INTERVAL', 60, int),
                   positive(values, 'TRANSACTION_THRESHOLD', 100),
                   positive(values, 'REQUEST_TIMEOUT', 10), reasoning, values)


# Compatibility exports for legacy utilities. No dotenv loading or client construction.
ETHERSCAN_API_KEY = None
ARBISCAN_API_KEY = None
TELEGRAM_BOT_TOKEN = None
ETHEREUM_RPC_URL = ''
ARBITRUM_RPC_URL = ''
DATABASE_NAME = 'whale_tracker'
LOG_LEVEL = 'INFO'
TRANSACTION_THRESHOLD = 100.0
POLLING_INTERVAL = 60
