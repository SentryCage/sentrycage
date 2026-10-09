"""Read-only service checks: no SQLite writes, messages or LLM inference."""
import requests
from src.fetching.blockchain_client import BlockchainClient
from src.fetching.explorer_api import ExplorerAPIClient
from src.notifications.telegram_bot import TelegramNotifier
from src.config.llm_config import LLMConfig


async def run_doctor(settings, *, blockchain=None, explorer=None, notifier=None, session=None):
    failures = 0
    rpc = blockchain if blockchain is not None else BlockchainClient(settings)
    api = explorer if explorer is not None else ExplorerAPIClient(settings.etherscan_api_key, settings.request_timeout)
    telegram = notifier if notifier is not None else TelegramNotifier(
        token=settings.telegram_token, chat_id=settings.telegram_chat_id,
        enabled=settings.notifications_enabled, timeout=settings.request_timeout)
    http = session if session is not None else requests.Session()
    print('CONFIG: valid; capabilities: Ethereum, Arbitrum, optional Telegram and AI')
    try:
        for chain in settings.enabled_chains:
            try:
                block = rpc.get_latest_block_number(chain)
                actual_id = rpc.get_chain_id(chain)
                if actual_id != ExplorerAPIClient.CHAIN_IDS[chain]:
                    raise ValueError('RPC belongs to another chain')
                print(f'RPC {chain.value}: operational; latest block {block}; chain ID verified')
            except Exception as error:
                failures += 1
                print(f'RPC {chain.value}: FAILED ({type(error).__name__}); check endpoint, chain and connectivity')
            try:
                result = api._make_request(chain, 'eth_blockNumber')
                if not isinstance(result.get('result'), str) or int(result['result'], 16) < 0:
                    raise ValueError('Invalid explorer block result')
                print(f'EXPLORER {chain.value}: operational (read-only block query)')
            except Exception as error:
                failures += 1
                print(f'EXPLORER {chain.value}: FAILED ({type(error).__name__}); check key, service access and connectivity')
        if settings.notifications_enabled:
            try:
                await telegram.initialize()
                import asyncio
                await asyncio.wait_for(telegram.bot.get_chat(settings.telegram_chat_id), settings.request_timeout)
                print('TELEGRAM: token and destination verified; sending permission/delivery untested; no message sent')
            except Exception as error:
                failures += 1
                print(f'TELEGRAM: FAILED ({type(error).__name__}); check token and bot access to configured chat')
        else:
            print('TELEGRAM: explicitly disabled')
        if settings.enable_reasoning:
            config = LLMConfig(settings.llm_env)
            try:
                if config.provider in ('claude', 'openai'):
                    failures += 1
                    print('AI: configured cloud provider; operational state UNVERIFIED (no inference or billable request)')
                else:
                    url = config.ollama_url if config.provider == 'ollama' else config.lmstudio_url
                    route = '/api/tags' if config.provider == 'ollama' else '/models'
                    response = http.get(url.rstrip('/') + route, timeout=settings.request_timeout)
                    response.raise_for_status()
                    data = response.json()
                    names = [m.get('name') for m in data['models']] if config.provider == 'ollama' else [m.get('id') for m in data['data']]
                    model = config.ollama_model if config.provider == 'ollama' else config.lmstudio_model
                    if model not in names:
                        raise ValueError('Configured model is not available')
                    print('AI: primary local model listed; inference and fallback providers untested; no generation requested')
            except Exception as error:
                failures += 1
                print(f'AI: FAILED ({type(error).__name__}); check service URL and installed model')
        else:
            print('AI: disabled; no provider constructed or contacted')
        return 3 if failures else 0
    finally:
        if notifier is None:
            await telegram.close()
        if explorer is None:
            api.session.close()
        if session is None:
            http.close()
