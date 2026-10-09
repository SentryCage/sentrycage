"""Telegram delivery with explicit outcomes and verified connection status."""
import asyncio
import logging
import os
from enum import Enum
from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError
from src.config.settings import chat_id as validate_chat_id

logger = logging.getLogger(__name__)


class DeliveryStatus(Enum):
    SENT = 'sent'
    NO_RECIPIENT = 'no_recipient'
    DISABLED = 'disabled'
    ERROR = 'error'

    def __bool__(self):
        return self is DeliveryStatus.SENT


class TelegramNotifier:
    def __init__(self, token=None, chat_id=None, *, enabled=None, bot=None, timeout=10):
        self.token = os.getenv('TELEGRAM_BOT_TOKEN', '') if token is None else token
        self.enabled = bool(self.token) if enabled is None else enabled
        self.timeout = timeout
        self.bot = (bot if bot is not None else Bot(token=self.token)) if self.enabled else None
        self.status = 'unverified' if self.enabled else 'disabled (explicit local-only mode)'
        self._subscribed_chats = []
        destination = os.getenv('TELEGRAM_CHAT_ID') if chat_id is None else chat_id
        if self.enabled and destination is not None:
            self.add_subscriber(destination)

    async def initialize(self):
        if not self.enabled:
            return
        try:
            await asyncio.wait_for(self.bot.initialize(), timeout=self.timeout)
        except (TelegramError, TimeoutError):
            self.status = 'failed'
            raise ConnectionError('Telegram token verification failed; check token and connectivity') from None
        self.status = 'connected (get_me verified)'

    @property
    def delivery_key(self):
        return 'telegram:' + ','.join(str(chat) for chat in sorted(self._subscribed_chats)) if self.enabled else 'disabled'

    def add_subscriber(self, chat_id):
        value = validate_chat_id(chat_id)
        if value not in self._subscribed_chats:
            self._subscribed_chats.append(value)

    def remove_subscriber(self, chat_id):
        value = validate_chat_id(chat_id)
        if value in self._subscribed_chats:
            self._subscribed_chats.remove(value)

    async def send_signal(self, signal, chat_id=None):
        if not self.enabled:
            return DeliveryStatus.DISABLED
        destinations = [validate_chat_id(chat_id)] if chat_id is not None else list(self._subscribed_chats)
        if not destinations:
            return DeliveryStatus.NO_RECIPIENT
        try:
            for destination in destinations:
                await asyncio.wait_for(self.bot.send_message(
                    chat_id=destination, text=signal.get_message(), parse_mode=ParseMode.HTML,
                    disable_web_page_preview=True), timeout=self.timeout)
        except (TelegramError, TimeoutError):
            logger.error('Telegram delivery failed; signal remains saved locally')
            return DeliveryStatus.ERROR
        return DeliveryStatus.SENT

    async def broadcast_message(self, message, parse_mode=None):
        if not self.enabled:
            return 0
        successful = 0
        for destination in self._subscribed_chats:
            try:
                await asyncio.wait_for(self.bot.send_message(
                    chat_id=destination, text=message, parse_mode=parse_mode), timeout=self.timeout)
                successful += 1
            except (TelegramError, TimeoutError):
                logger.error('Telegram broadcast delivery failed')
        return successful

    async def send_message(self, message, parse_mode=None):
        return (await self.broadcast_message(message, parse_mode)) > 0

    async def close(self):
        if self.bot is not None:
            await self.bot.shutdown()
