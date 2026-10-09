import pytest
from src.config.settings import Settings, ConfigurationError
from src.config.wallet_registry import Chain


def live_env(**changes):
    return dict(ETHERSCAN_API_KEY='offline-key', ETHEREUM_RPC_URL='https://rpc.invalid',
                TELEGRAM_BOT_TOKEN='123:offline', TELEGRAM_CHAT_ID='-100123', **changes)


def test_defaults_and_negative_recipient():
    settings = Settings.from_env(live_env())
    assert settings.enabled_chains == (Chain.ETHEREUM,)
    assert settings.telegram_chat_id == -100123
    assert not settings.enable_reasoning
    assert 'offline-key' not in repr(settings)


@pytest.mark.parametrize('name,value', [
    ('ETHERSCAN_API_KEY', ''), ('ETHEREUM_RPC_URL', 'https://rpc/your_key'),
    ('TELEGRAM_CHAT_ID', '0'), ('TELEGRAM_CHAT_ID', 'hello'),
    ('ENABLED_CHAINS', 'polygon'), ('ENABLED_CHAINS', 'ethereum,ethereum'),
    ('ENABLED_CHAINS', ''), ('ENABLED_CHAINS', 'ethereum,arbitrum'),
    ('POLLING_INTERVAL', '0'), ('REQUEST_TIMEOUT', 'nan'),
    ('TRANSACTION_THRESHOLD', 'abc'), ('NOTIFICATIONS_ENABLED', 'yes'),
    ('ENABLE_REASONING', 'invalid'),
])
def test_rejects_invalid_settings_without_secret_values(name, value):
    env = live_env()
    env[name] = value
    with pytest.raises(ConfigurationError) as error:
        Settings.from_env(env)
    assert name in str(error.value) or name == 'ENABLED_CHAINS'
    assert 'offline-key' not in str(error.value)


def test_disabled_notifications_and_selected_arbitrum():
    settings = Settings.from_env(dict(ENABLED_CHAINS='arbitrum', ARBITRUM_RPC_URL='https://rpc.invalid',
                                      ETHERSCAN_API_KEY='offline-key', NOTIFICATIONS_ENABLED='false'))
    assert settings.enabled_chains == (Chain.ARBITRUM,)
    assert settings.telegram_chat_id is None


def test_legacy_names_are_migrated_and_conflicts_rejected():
    with pytest.warns(DeprecationWarning):
        assert not Settings.from_env(live_env(ENABLE_AI_REASONING='false')).enable_reasoning
    with pytest.raises(ConfigurationError, match='conflicts'):
        Settings.from_env(live_env(ENABLE_AI_REASONING='true', ENABLE_REASONING='false'))


@pytest.mark.parametrize('changes', [dict(ENABLE_REASONING='true', LLM_PROVIDER='wrong'),
                                     dict(ENABLE_REASONING='true', LLM_PROVIDER='claude'),
                                     dict(ENABLE_REASONING='true', MAX_REASONING_ITERATIONS='0')])
def test_ai_configuration_validated(changes):
    with pytest.raises(ConfigurationError):
        Settings.from_env(live_env(**changes))
