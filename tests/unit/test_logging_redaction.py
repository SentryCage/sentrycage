import logging
from urllib.parse import quote
from src.orchestration.logging_setup import SafeFormatter


def test_external_library_urls_and_configured_keys_are_redacted():
    token = '123:offline-secret'
    key = 'explorer-secret'
    rpc = 'https://rpc.invalid/private-key'
    record = logging.LogRecord('httpx', logging.INFO, '', 0,
                               f'https://api.telegram.org/bot{token}/getMe?apikey={key} {rpc} {quote(token, safe="")}', (), None)
    output = SafeFormatter([token, key, rpc]).format(record)
    assert '[REDACTED]' in output
    assert token not in output and key not in output and rpc not in output
    assert quote(token, safe='') not in output


def test_telegram_url_is_redacted_even_without_known_token():
    record = logging.LogRecord('httpx', logging.WARNING, '', 0,
                               'HTTP request https://api.telegram.org/bot999:unknown-token/getMe failed', (), None)
    assert '999:unknown-token' not in SafeFormatter().format(record)
