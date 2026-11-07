"""Receive Telegram updates when hosted behind a public HTTPS endpoint."""
import asyncio
import hmac
import logging

from bot.main import create_bot_and_dispatcher
from database.db import db

WEBHOOK_PATH = "/telegram/webhook"
logger = logging.getLogger(__name__)


class TelegramWebhook:
    def __init__(self, token):
        self.bot, self.dispatcher = create_bot_and_dispatcher(token)
        self.secret = hmac.digest(token.encode(), b"telegram-webhook-v1", "sha256").hex().encode()
        self.tasks = set()
        self.wake = asyncio.Event()
        self.worker = None

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
        self.worker = asyncio.create_task(self._worker())

    def matches(self, value):
        return hmac.compare_digest(self.secret, value.encode())

    async def enqueue(self, update):
        # Acknowledge delivery only after durable storage, before slow generation.
        await db.store_update(update)
        self.wake.set()

    async def _worker(self):
        while True:
            self.wake.clear()
            try:
                if len(self.tasks) < 128:
                    for update, lease_token in await db.claim_updates(128 - len(self.tasks)):
                        task = asyncio.create_task(self._handle(update, lease_token))
                        self.tasks.add(task)
                        task.add_done_callback(self._finished)
            except Exception as exc:
                logger.error("Could not read Telegram inbox (%s)", type(exc).__name__)
            try:
                await asyncio.wait_for(self.wake.wait(), timeout=30)
            except TimeoutError:
                pass

    def _finished(self, task):
        self.tasks.discard(task)
        self.wake.set()
        if not task.cancelled():
            error = task.exception()
            if error:
                logger.error("Telegram inbox completion failed (%s)", type(error).__name__)

    async def _handle(self, update, lease_token):
        try:
            await self.dispatcher.feed_raw_update(self.bot, update)
        except asyncio.CancelledError:
            await db.finish_update(update["update_id"], lease_token, "Interrupted")
            raise
        except Exception as exc:
            logger.error("Telegram update failed (%s)", type(exc).__name__)
            await db.finish_update(update["update_id"], lease_token, type(exc).__name__)
        else:
            await db.finish_update(update["update_id"], lease_token)

    async def close(self):
        if self.worker:
            self.worker.cancel()
            await asyncio.gather(self.worker, return_exceptions=True)
        tasks = tuple(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await self.bot.session.close()
