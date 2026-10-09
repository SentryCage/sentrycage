from unittest.mock import AsyncMock
import pytest
from telegram.error import TelegramError
from src.config.settings import ConfigurationError
from src.notifications.telegram_bot import TelegramNotifier, DeliveryStatus
from src.signals.signal_generator import Signal, SignalType, SignalStrength


def signal():
    return Signal(SignalType.TRANSFER, SignalStrength.HIGH, '0xabc', '0xabc', None,
                  'whale', 'ethereum', 500, 'Synthetic fixture')


async def test_configured_negative_chat_and_verified_status():
    bot = AsyncMock()
    notifier = TelegramNotifier(token='123:offline', chat_id=-100123, bot=bot)
    assert notifier.status == 'unverified'
    await notifier.initialize()
    assert notifier.status == 'connected (get_me verified)'
    assert await notifier.send_signal(signal()) is DeliveryStatus.SENT
    assert bot.send_message.await_args.kwargs['chat_id'] == -100123
    assert bot.send_message.await_args.kwargs['parse_mode'] == 'HTML'


async def test_no_recipients_never_reports_success(monkeypatch):
    monkeypatch.delenv('TELEGRAM_CHAT_ID', raising=False)
    bot = AsyncMock()
    notifier = TelegramNotifier(token='123:offline', bot=bot)
    assert await notifier.send_signal(signal()) is DeliveryStatus.NO_RECIPIENT
    bot.send_message.assert_not_awaited()


async def test_explicit_disabled_never_constructs_or_contacts_bot():
    notifier = TelegramNotifier(token='', enabled=False)
    assert notifier.bot is None
    assert await notifier.send_signal(signal()) is DeliveryStatus.DISABLED


async def test_delivery_and_connection_errors_are_visible_and_sanitized():
    bot = AsyncMock()
    notifier = TelegramNotifier(token='123:offline', chat_id=123, bot=bot)
    bot.send_message.side_effect = TelegramError('secret-from-provider')
    assert await notifier.send_signal(signal()) is DeliveryStatus.ERROR
    bot.initialize.side_effect = TelegramError('secret-from-provider')
    with pytest.raises(ConnectionError, match='verification failed') as error:
        await notifier.initialize()
    assert 'secret-from-provider' not in str(error.value)
    assert notifier.status == 'failed'


@pytest.mark.parametrize('destination', [0, 'hello', '1.5'])
def test_invalid_chat_rejected(destination):
    with pytest.raises(ConfigurationError):
        TelegramNotifier(token='123:offline', chat_id=destination, bot=AsyncMock())


async def test_hanging_send_has_bounded_timeout():
    import asyncio
    bot = AsyncMock()

    async def hanging_send(**kwargs):
        await asyncio.Future()

    bot.send_message.side_effect = hanging_send
    notifier = TelegramNotifier(token='123:offline', chat_id=123, bot=bot, timeout=0.02)
    assert await asyncio.wait_for(notifier.send_signal(signal()), timeout=1) is DeliveryStatus.ERROR


async def test_real_sdk_bot_lifecycle_verifies_get_me_and_closes_requests():
    from telegram import Bot, User
    from telegram.request import BaseRequest
    from unittest.mock import patch
    request = AsyncMock(spec=BaseRequest)
    updates = AsyncMock(spec=BaseRequest)
    bot = Bot(token='123:offline', request=request, get_updates_request=updates)
    notifier = TelegramNotifier(token='123:offline', chat_id=123, bot=bot)
    with patch.object(Bot, 'get_me', new_callable=AsyncMock, return_value=User(123, 'Offline', True)) as get_me:
        await notifier.initialize()
        get_me.assert_awaited_once()
        assert notifier.status == 'connected (get_me verified)'
        await notifier.close()
    request.shutdown.assert_awaited_once()
    updates.shutdown.assert_awaited_once()
