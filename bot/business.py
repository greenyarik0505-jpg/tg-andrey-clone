"""Telegram Business routing for the configured account."""
import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from weakref import WeakValueDictionary

from aiogram import Bot, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.types import BusinessConnection, BusinessMessagesDeleted, InlineKeyboardButton, InlineKeyboardMarkup, Message

from config import ADMIN_ID
from core.llm_client import ReplyError, llm_manager
from core.settings import number, reply_delays
from database.db import db

logger = logging.getLogger(__name__)
business_router = Router()
connections: dict[str, BusinessConnection] = {}
BATCH_WINDOW_SECONDS = 2


@dataclass
class PendingChat:
    newest: tuple[float, int] = (0, 0)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


pending_chats = WeakValueDictionary()


def can_reply(conn):
    return conn is not None and conn.is_enabled and bool(conn.rights and conn.rights.can_reply)


def connection_record(conn):
    return {
        "user_id": conn.user.id,
        "display_name": conn.user.full_name,
        "username": conn.user.username,
        "connection_id": conn.id,
        "is_enabled": int(conn.is_enabled),
        "can_reply": int(bool(conn.rights and conn.rights.can_reply)),
        "connected_at": conn.date.timestamp(),
    }


class ConnectionApprovalError(Exception):
    def __init__(self, message, status_code=409):
        super().__init__(message)
        self.status_code = status_code


async def approve_account(request_id, bot):
    requests = await db.business_requests(request_id)
    if not requests:
        raise ConnectionApprovalError("Запрос уже обработан или истёк. Обновите состояние.")
    request = requests[0]
    connection = await fetch_connection(bot, request["connection_id"])
    if not connection or not connection.is_enabled or connection.user.id != request["user_id"]:
        raise ConnectionApprovalError("Подключение недоступно. Проверьте его в настройках Telegram.")
    if not await db.approve_business_request(request_id, connection.user.id, connection_record(connection)):
        raise ConnectionApprovalError("Запрос уже обработан или истёк. Обновите состояние.")
    connections[connection.id] = connection
    logger.info("Automation account approved for user %s", connection.user.id)
    return connection


async def fetch_connection(bot, connection_id):
    try:
        async with asyncio.timeout(8):
            return await bot.get_business_connection(business_connection_id=connection_id)
    except (TelegramAPIError, TimeoutError) as exc:
        logger.warning("Business connection lookup failed (%s)", type(exc).__name__)
        return None


def business_request_button(request_id, label):
    return InlineKeyboardButton(text=label, callback_data=f"business_approve:{request_id}")


async def request_approval(conn, bot):
    name = conn.user.full_name
    if conn.user.username:
        name += f" (@{conn.user.username})"
    request_id = await db.add_business_request(conn.id, conn.user.id, name)
    if request_id is None:
        return
    try:
        keyboard = InlineKeyboardMarkup(inline_keyboard=[[business_request_button(request_id, "Подтвердить аккаунт")]])
        await bot.send_message(
            ADMIN_ID,
            f"Запрос на автоматизацию чатов\n\n{name}\nTelegram ID: {conn.user.id}\n\n"
            "Подтвердите, если узнаёте этот аккаунт.",
            reply_markup=keyboard,
            parse_mode=None,
        )
    except TelegramAPIError as exc:
        logger.warning("Could not notify admin of connection request (%s)", type(exc).__name__)


async def get_connection(bot, connection_id, *, allow_request=False):
    if connection_id in connections:
        return connections[connection_id]
    conn = await fetch_connection(bot, connection_id)
    if conn is None:
        return None
    owner = await db.business_owner()
    if conn.user.id == owner:
        await db.save_business_account(connection_record(conn))
        return connections.setdefault(connection_id, conn)
    if allow_request and owner is None and conn.is_enabled:
        await request_approval(conn, bot)
    return None


@business_router.business_connection()
async def on_business_connection(conn: BusinessConnection, bot: Bot):
    if conn.id in connections:
        connections[conn.id] = conn
    if not conn.is_enabled:
        await db.remove_business_request(conn.id)
    owner = await db.business_owner()
    if owner is None and conn.is_enabled:
        await request_approval(conn, bot)
        return
    if conn.user.id != owner:
        return
    connections[conn.id] = conn
    await db.save_business_account(connection_record(conn))
    status = "подключён" if conn.is_enabled else "отключён"
    rights = "есть" if can_reply(conn) else "нет"
    try:
        await bot.send_message(ADMIN_ID, f"Автоматизация чатов: аккаунт {status}. Право отвечать: {rights}.", parse_mode=None)
    except TelegramAPIError as exc:
        logger.warning("Could not notify admin of connection change (%s)", type(exc).__name__)


def replies_allowed(state, received_at):
    if not state.get("is_enabled") or state.get("is_paused") == "1":
        return False
    last_manual = state["last_human_message_time"]
    return last_manual < received_at and time.time() - last_manual >= number(state, "human_takeover_minutes") * 60


async def wait_before_send(bot, chat_id, connection_id, delay, typing, still_allowed):
    deadline = time.monotonic() + delay
    if typing:
        while time.monotonic() < deadline:
            if not await still_allowed():
                return False
            try:
                async with asyncio.timeout(2):
                    await bot.send_chat_action(chat_id=chat_id, action="typing", business_connection_id=connection_id)
            except (TelegramAPIError, TimeoutError):
                pass
            await asyncio.sleep(max(0, min(3.5, deadline - time.monotonic())))
    else:
        await asyncio.sleep(delay)
    return await still_allowed()


@business_router.business_message()
async def on_business_message(message: Message, bot: Bot):
    conn_id = message.business_connection_id
    sender = message.from_user
    if (
        not conn_id or message.chat.type != ChatType.PRIVATE or not sender or sender.is_bot
        or message.sender_business_bot or message.message_id <= 0
    ):
        return

    conn = await get_connection(bot, conn_id, allow_request=True)
    if not conn or not conn.is_enabled:
        return

    chat_id = message.chat.id
    timestamp = message.date.timestamp()
    is_outgoing = sender.id == conn.user.id
    text = (message.text or message.caption or "").strip()
    chat = message.chat
    await db.get_or_create_chat(chat_id, chat.full_name or str(chat_id), chat.username or "")
    if is_outgoing:
        await db.record_human_message(
            chat_id, sender.id, sender.full_name, text, is_outgoing=True,
            message_id=message.message_id, connection_id=conn_id, timestamp=timestamp,
        )
        return
    if time.time() - timestamp > 300:
        return  # Discover chats from the backlog without replying to stale messages.

    pending = pending_chats.setdefault((conn_id, chat_id), PendingChat())
    marker = (timestamp, message.message_id)
    if marker <= pending.newest:
        return
    pending.newest = marker
    state = await db.reply_state(chat_id, conn_id)
    if not text or not can_reply(connections.get(conn_id)) or not replies_allowed(state, timestamp):
        return
    settings_revision = state["settings_revision"]
    chat_revision = state["chat_revision"]
    history_id = await db.record_human_message(
        chat_id, sender.id, sender.full_name, text, message_id=message.message_id,
        connection_id=conn_id, timestamp=timestamp,
    )
    if history_id is None:
        return

    async def still_allowed():
        state = await db.reply_state(chat_id, conn_id)
        return (
            pending.newest == marker
            and state.get("latest_incoming") == history_id
            and state.get("settings_revision") == settings_revision
            and state.get("chat_revision") == chat_revision
            and replies_allowed(state, timestamp)
            and can_reply(connections.get(conn_id))
        )

    # Give successive messages a short window to arrive before generating a reply.
    await asyncio.sleep(BATCH_WINDOW_SECONDS)
    async with pending.lock:
        if not await still_allowed():
            return
        history = await db.get_recent_history(chat_id, limit=12, connection_id=conn_id)
        history = [item for item in history if item["id"] != history_id][-11:]
        history.append({"text": text, "is_outgoing": 0})
        settings = await db.get_all_settings()
        if not await still_allowed():
            return
        try:
            chunks = await llm_manager.reply(settings, history)
        except ReplyError as exc:
            logger.warning("Automatic reply skipped: %s", exc)
            await db.set_chat_error(chat_id, str(exc))
            return

        minimum, maximum = reply_delays(settings)
        for index, chunk in enumerate(chunks):
            typing_time = len(chunk) / random.uniform(6, 10)
            low = minimum if index == 0 else min(minimum, 2)
            delay = min(maximum, max(low, typing_time + random.uniform(1, 3)))
            if not await wait_before_send(bot, chat_id, conn_id, delay, settings["simulate_typing"] == "1", still_allowed):
                return
            try:
                sent = await bot.send_message(
                    chat_id=chat_id, text=chunk, parse_mode=None,
                    business_connection_id=conn_id,
                )
            except TelegramAPIError as exc:
                logger.warning("Could not send automatic reply (%s)", type(exc).__name__)
                await db.set_chat_error(chat_id, "Telegram не принял ответ. Проверьте подключение и права.")
                return
            await db.record_bot_message(chat_id, chunk, message_id=sent.message_id, connection_id=conn_id)
            await db.set_chat_error(chat_id, "")


@business_router.deleted_business_messages()
async def on_deleted_messages(event: BusinessMessagesDeleted, bot: Bot):
    if await get_connection(bot, event.business_connection_id):
        await db.delete_business_messages(event.business_connection_id, event.chat.id, event.message_ids)


@business_router.edited_business_message()
async def on_edited_message(message: Message, bot: Bot):
    conn_id = message.business_connection_id
    if conn_id and await get_connection(bot, conn_id):
        await db.delete_business_messages(conn_id, message.chat.id, [message.message_id])
