"""Telegram Business Connection and Message Dispatcher."""
import asyncio
import random
import time
import logging
from aiogram import Router, F, Bot
from aiogram.types import BusinessConnection, Message
from config import ADMIN_ID
from database.db import db
from core.persona import check_sensitive_content, post_process_reply
from core.llm_client import llm_manager

logger = logging.getLogger(__name__)
business_router = Router()

@business_router.business_connection()
async def on_business_connection(conn: BusinessConnection, bot: Bot):
    """Fires when a user connects or disconnects the bot in Telegram Business settings."""
    user = conn.user
    status = "ПОДКЛЮЧЕН" if conn.is_enabled else "ОТКЛЮЧЕН"
    logger.info(f"Business connection {conn.id}: User {user.id} (@{user.username}) -> {status}")
    
    # Save connection ID to settings
    await db.set_setting("business_connection_id", conn.id)
    await db.set_setting("business_user_id", str(user.id))
    
    # Notify Master Admin
    try:
        await bot.send_message(
            chat_id=ADMIN_ID,
            text=(
                f"🔗 <b>Telegram Business обновление!</b>\n\n"
                f"• Пользователь: {user.full_name} (@{user.username})\n"
                f"• ID: <code>{user.id}</code>\n"
                f"• Статус: <b>{status}</b>\n"
                f"• Connection ID: <code>{conn.id}</code>\n"
                f"• Можно отвечать: {'Да' if conn.can_reply else 'Нет'}"
            ),
            parse_mode="HTML"
        )
    except Exception as e:
        logger.error(f"Failed to notify admin of business connection: {e}")

@business_router.business_message()
async def on_business_message(message: Message, bot: Bot):
    """Handles incoming and outgoing messages across connected private chats."""
    conn_id = message.business_connection_id
    if not conn_id:
        return

    chat_id = message.chat.id
    sender_id = message.from_user.id if message.from_user else 0
    sender_name = message.from_user.full_name if message.from_user else "Unknown"
    text = message.text or message.caption or ""
    
    business_user_id = await db.get_setting("business_user_id", "")
    is_outgoing = str(sender_id) == business_user_id

    # 1. Record message to history
    chat_info = await db.get_or_create_chat(
        chat_id=chat_id,
        chat_title=message.chat.title or sender_name,
        username=message.chat.username or ""
    )
    await db.record_human_message(chat_id, sender_id, sender_name, text, is_outgoing=is_outgoing)

    # 2. If the message was sent by the friend himself (outgoing), activate human takeover lockout
    if is_outgoing:
        logger.info(f"Human friend active in chat {chat_id}, resetting takeover timer.")
        return

    # 3. Check global pause
    settings = await db.get_all_settings()
    if settings.get("is_paused") == "1":
        logger.info("Bot is globally paused, skipping message.")
        return

    # 4. Check if this chat is enabled in whitelist/blacklist
    if not chat_info.get("is_enabled", 1):
        logger.info(f"Chat {chat_id} is disabled, ignoring.")
        return

    # 5. Check Human Takeover (Did Andrey send a message recently?)
    takeover_mins = float(settings.get("human_takeover_minutes", 15))
    last_human_time = chat_info.get("last_human_message_time", 0)
    if time.time() - last_human_time < (takeover_mins * 60):
        logger.info(f"Human takeover active for chat {chat_id} (last message {int(time.time() - last_human_time)}s ago). Skipping.")
        return

    # 6. Sensitive content filter (Safety & Emergency trigger)
    if settings.get("filter_sensitive") == "1" and check_sensitive_content(text):
        logger.warning(f"Sensitive content detected in chat {chat_id}: {text}")
        try:
            await bot.send_message(
                chat_id=ADMIN_ID,
                text=(
                    f"🚨 <b>Внимание! Срочный триггер в переписке</b>\n\n"
                    f"• Чат: {chat_info.get('chat_title')} (@{chat_info.get('username')})\n"
                    f"• Отправитель: {sender_name} (ID: {sender_id})\n"
                    f"• Сообщение: <i>{text}</i>\n\n"
                    f"⚠️ Бот временно заглушен в этом чате. Проверь переписку!"
                ),
                parse_mode="HTML"
            )
        except Exception as e:
            logger.error(f"Failed to alert admin: {e}")
        return

    if not text:
        # Ignore media without caption for now (stickers, photos)
        return

    # 7. Generate response using active LLM
    prov = settings.get("active_provider", "gemini")
    model = settings.get("active_model", "gemini-2.5-flash")
    api_key = settings.get(f"{prov}_api_key", "")
    temp = float(settings.get("temperature", 0.75))
    custom_sys = settings.get("custom_prompt") or None

    history = await db.get_recent_history(chat_id, limit=12)

    try:
        raw_reply = await llm_manager.generate_response(
            provider=prov,
            model_name=model,
            api_key=api_key,
            history=history,
            custom_system_prompt=custom_sys,
            temperature=temp
        )
    except Exception as e:
        logger.error(f"Error generating LLM reply: {e}")
        return

    chunks = post_process_reply(raw_reply)

    # 8. Human Delay & Typing Simulation
    min_delay = float(settings.get("min_delay", 3))
    max_delay = float(settings.get("max_delay", 8))
    simulate_typing = settings.get("simulate_typing") == "1"

    for i, chunk in enumerate(chunks):
        # Calculate human-like pause
        delay = random.uniform(min_delay, max_delay) if i == 0 else random.uniform(1.5, 3.5)
        
        if simulate_typing:
            try:
                await bot.send_chat_action(
                    chat_id=chat_id,
                    action="typing",
                    business_connection_id=conn_id
                )
            except Exception as e:
                logger.debug(f"Could not send typing action: {e}")
        
        await asyncio.sleep(delay)

        # Send chunk
        try:
            await bot.send_message(
                chat_id=chat_id,
                text=chunk,
                business_connection_id=conn_id
            )
            await db.record_bot_message(chat_id, chunk)
        except Exception as send_err:
            logger.error(f"Failed to send business message chunk: {send_err}")
            break
