"""Receive Telegram updates when hosted behind a public HTTPS endpoint."""
import asyncio
import hmac
import logging

from bot.main import create_bot_and_dispatcher

WEBHOOK_PATH = "/telegram/webhook"
logger = logging.getLogger(__name__)


class TelegramWebhook:
    def __init__(self, token):
        self.bot, self.dispatcher = create_bot_and_dispatcher(token)
        self.secret = hmac.digest(token.encode(), b"telegram-webhook-v1", "sha256").hex().encode()
        self.tasks = set()

    async def start(self, base_url):
        if not base_url.startswith("https://"):
            raise ValueError("TELEGRAM_WEBHOOK_BASE_URL must use HTTPS.")
        await self.bot.set_webhook(
            url=base_url + WEBHOOK_PATH,
            secret_token=self.secret.decode(),
            allowed_updates=self.dispatcher.resolve_used_update_types(),
            drop_pending_updates=False,
        )
        logger.info("Telegram webhook registered.")

    def matches(self, value):
        return hmac.compare_digest(self.secret, value.encode())

    def enqueue(self, update):
        if len(self.tasks) >= 128:
            return False
        task = asyncio.create_task(self._handle(update))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return True

    async def _handle(self, update):
        try:
            await self.dispatcher.feed_raw_update(self.bot, update)
        except Exception as exc:
            logger.error("Telegram update failed (%s)", type(exc).__name__)

    async def close(self):
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.bot.session.close()
