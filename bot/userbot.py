"""Optional MTProto Userbot Client using Telethon.
Allows running the digital clone directly under the friend's personal account
even if Telegram Business is not active.
"""
import os
import asyncio
import logging
from telethon import TelegramClient, events
from database.db import db
from core.persona import check_sensitive_content, post_process_reply
from core.llm_client import llm_manager
from config import ADMIN_ID

logger = logging.getLogger(__name__)

class AndreyUserbot:
    def __init__(self, session_name: str = "andrey_session", api_id: int = 0, api_hash: str = ""):
        self.session_name = session_name
        self.api_id = api_id
        self.api_hash = api_hash
        self.client = None

    async def start(self):
        if not self.api_id or not self.api_hash:
            logger.info("MTProto Userbot: API_ID or API_HASH not provided, running in Business mode only.")
            return

        self.client = TelegramClient(self.session_name, self.api_id, self.api_hash)
        await self.client.start()
        logger.info("MTProto Userbot successfully authenticated and running.")

        @self.client.on(events.NewMessage(incoming=True))
        async def on_userbot_incoming(event):
            if not event.is_private:
                return  # Only respond in private chats by default

            chat = await event.get_chat()
            chat_id = event.chat_id
            text = event.raw_text or ""
            sender = await event.get_sender()
            sender_name = getattr(sender, "first_name", "Friend") or "Friend"

            # Check pause
            settings = await db.get_all_settings()
            if settings.get("is_paused") == "1":
                return

            # Check chat whitelist
            chat_info = await db.get_or_create_chat(
                chat_id=chat_id,
                chat_title=getattr(chat, "title", sender_name) or sender_name,
                username=getattr(chat, "username", "") or ""
            )
            if not chat_info.get("is_enabled", 1):
                return

            # Check takeover
            takeover_mins = float(settings.get("human_takeover_minutes", 15))
            if event.date.timestamp() - chat_info.get("last_human_message_time", 0) < takeover_mins * 60:
                return

            # Record
            await db.record_human_message(chat_id, sender.id, sender_name, text, is_outgoing=False)

            # Generate reply
            prov = settings.get("active_provider", "gemini")
            model = settings.get("active_model", "gemini-2.5-flash")
            api_key = settings.get(f"{prov}_api_key", "")
            temp = float(settings.get("temperature", 0.75))
            history = await db.get_recent_history(chat_id, limit=10)

            try:
                raw_reply = await llm_manager.generate_response(
                    provider=prov,
                    model_name=model,
                    api_key=api_key,
                    history=history,
                    temperature=temp
                )
                chunks = post_process_reply(raw_reply)
                for chunk in chunks:
                    async with self.client.action(chat_id, "typing"):
                        await asyncio.sleep(len(chunk) * 0.1 + 1.5)
                    await event.reply(chunk)
                    await db.record_bot_message(chat_id, chunk)
            except Exception as e:
                logger.error(f"Userbot error: {e}")

        @self.client.on(events.NewMessage(outgoing=True))
        async def on_userbot_outgoing(event):
            # Human active in this chat!
            chat_id = event.chat_id
            await db.record_human_message(chat_id, event.sender_id, "Me (Human)", event.raw_text or "", is_outgoing=True)
