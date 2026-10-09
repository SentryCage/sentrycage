"""Redact configured credentials, including Telegram URLs emitted by HTTP libraries."""
import logging
import re
from urllib.parse import quote


class SafeFormatter(logging.Formatter):
    def __init__(self, secrets=()):
        super().__init__('%(levelname)s: %(message)s')
        self.secrets = sorted({value for secret in secrets if secret
                               for value in (str(secret), quote(str(secret), safe=''))}, key=len, reverse=True)

    def format(self, record):
        message = super().format(record)
        for secret in self.secrets:
            message = message.replace(secret, '[REDACTED]')
        return re.sub(r'bot[0-9]+:[A-Za-z0-9_-]+', 'bot[REDACTED]', message)


def configure_logging(settings=None, secrets=()):
    values = list(secrets)
    if settings is not None:
        values.extend((settings.etherscan_api_key, settings.telegram_token, *settings.rpc_urls.values()))
        values.extend(settings.llm_env.get(name) for name in
                      ('CLAUDE_API_KEY', 'OPENAI_API_KEY', 'OLLAMA_URL', 'LMSTUDIO_URL'))
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(SafeFormatter(values))
    root.setLevel(logging.INFO)
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('httpcore').setLevel(logging.WARNING)
