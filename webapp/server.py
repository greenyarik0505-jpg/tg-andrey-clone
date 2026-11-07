"""Serve the Mini App and protect administrative endpoints."""
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager
import asyncio
from hashlib import sha256
from pathlib import Path
from typing import Annotated
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from bot.webhook import WEBHOOK_PATH, TelegramWebhook
from bot.business import ConnectionApprovalError, approve_account, connections, get_connection
from config import ADMIN_ID, BOT_TOKEN, LOCAL_ADMIN_TOKEN, TELEGRAM_WEBHOOK_BASE_URL
from core.llm_client import ReplyError, llm_manager
from core.settings import NUMERIC_LIMITS, TOGGLES
from core.persona import PERSONA_NAME
from database.db import db

logger = logging.getLogger(__name__)
STATIC_DIR = Path(__file__).parent / "static"
LOOPBACK = {"127.0.0.1", "::1", "localhost"}
INIT_DATA_MAX_AGE = 3600


@asynccontextmanager
async def lifespan(app):
    if not db.ready:
        await db.init()
    webhook = TelegramWebhook(BOT_TOKEN) if BOT_TOKEN and TELEGRAM_WEBHOOK_BASE_URL else None
    app.state.telegram_webhook = webhook
    if webhook:
        app.state.telegram_bot = webhook.bot
    try:
        if webhook:
            await webhook.start(TELEGRAM_WEBHOOK_BASE_URL)
        yield
    finally:
        if webhook:
            await webhook.close()
        if not getattr(app.state, "unified_runner", False):
            await llm_manager.close()
            await db.close()


app = FastAPI(title="Telegram Business Dashboard", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)


def verify_init_data(raw):
    if not BOT_TOKEN or len(raw) > 8192:
        return None
    try:
        pairs = parse_qsl(raw, strict_parsing=True, max_num_fields=32, errors="strict")
        fields = dict(pairs)
        if len(fields) != len(pairs):
            return None
        signature = bytes.fromhex(fields.pop("hash"))
        payload = "\n".join(f"{key}={value}" for key, value in sorted(fields.items()))
        secret = hmac.digest(b"WebAppData", BOT_TOKEN.encode(), sha256)
        if not hmac.compare_digest(signature, hmac.digest(secret, payload.encode(), sha256)):
            return None
        age = time.time() - int(fields["auth_date"])
        user_id = json.loads(fields["user"])["id"]
        if -30 <= age <= INIT_DATA_MAX_AGE and type(user_id) is int:
            return user_id
    except (KeyError, TypeError, ValueError, OverflowError):
        return None
    return None


async def require_admin(request: Request):
    scheme, _, value = request.headers.get("Authorization", "").partition(" ")
    if scheme.lower() == "tma":
        user_id = verify_init_data(value)
        if user_id == ADMIN_ID:
            return
        if user_id is not None:
            raise HTTPException(403, "Доступ разрешён только администратору.")
    elif scheme.lower() == "bearer" and len(LOCAL_ADMIN_TOKEN) >= 24:
        if (
            request.client and request.client.host in LOOPBACK
            and request.url.hostname in LOOPBACK
            and hmac.compare_digest(value.encode(), LOCAL_ADMIN_TOKEN.encode())
        ):
            return
    raise HTTPException(401, "Откройте Mini App через Telegram или войдите с локальным ключом.")


api = APIRouter(prefix="/api", dependencies=[Depends(require_admin)])
SettingValue = Annotated[str, StringConstraints(max_length=4096)]


class SettingsUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    custom_base_url: SettingValue = None
    custom_model_name: SettingValue = None
    custom_api_key: SettingValue = None
    temperature: SettingValue = None
    min_delay: SettingValue = None
    max_delay: SettingValue = None
    simulate_typing: SettingValue = None
    split_messages: SettingValue = None
    human_takeover_minutes: SettingValue = None
    is_paused: SettingValue = None
    custom_prompt: SettingValue = None


class ChatToggle(BaseModel):
    model_config = ConfigDict(extra="forbid")

    chat_id: int = Field(strict=True, gt=0, lt=2**52)
    is_enabled: bool = Field(strict=True)


class TestMessage(BaseModel):
    message: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=4096)]


class ConnectionApproval(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_-]{16}$")]


@app.exception_handler(RequestValidationError)
async def bad_payload(request, exc):
    # Validation details may include secrets copied from the submitted body.
    return JSONResponse({"detail": "Проверьте формат и длину полей."}, status_code=422)


@app.middleware("http")
async def security_headers(request, call_next):
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self' https://telegram.org; "
        "style-src 'self' 'unsafe-inline'; img-src 'self' data:; "
        "object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    if request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store"
    return response


@app.get("/")
async def get_index():
    return FileResponse(STATIC_DIR / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/healthz")
async def health():
    return {"status": "ok"}


@app.post(WEBHOOK_PATH)
async def telegram_update(request: Request):
    webhook = request.app.state.telegram_webhook
    if webhook is None:
        raise HTTPException(404)
    if not webhook.matches(request.headers.get("X-Telegram-Bot-Api-Secret-Token", "")):
        raise HTTPException(403)
    try:
        update = await request.json()
    except (ValueError, UnicodeError):
        raise HTTPException(400) from None
    if not isinstance(update, dict) or type(update.get("update_id")) is not int:
        raise HTTPException(400)
    try:
        await webhook.enqueue(update)
    except Exception as exc:
        logger.error("Could not persist incoming Telegram update (%s)", type(exc).__name__)
        raise HTTPException(503) from None
    return {"ok": True}


@api.get("/settings")
async def get_settings():
    settings = await db.get_all_settings()
    public_fields = {
        *NUMERIC_LIMITS, *TOGGLES,
        "custom_prompt", "custom_base_url", "custom_model_name",
    }
    public = {key: settings[key] for key in public_fields}
    public["has_custom_api_key"] = bool(settings["custom_api_key"])
    return {"settings": public}


@api.post("/settings")
async def update_settings(payload: SettingsUpdate):
    try:
        await db.update_settings(payload.model_dump(exclude_unset=True))
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from None
    return {"status": "success", "message": "Настройки сохранены."}


@api.get("/chats")
async def get_chats():
    return {"chats": await db.list_chats()}


async def connection_status(request):
    bot = getattr(request.app.state, "telegram_bot", None)
    owner = await db.business_owner()
    account = await db.business_account(owner) if owner else None
    bot_info = {"configured": bool(BOT_TOKEN), "running": bot is not None, "username": None, "chat_access_enabled": None}
    telegram_available = False
    if bot:
        try:
            async with asyncio.timeout(8):
                identity = await bot.get_me()
                telegram_available = True
                bot_info.update(username=identity.username, chat_access_enabled=bool(identity.can_connect_to_business))
                if owner:
                    connection_id = account.get("connection_id") if account else await db.last_business_connection()
                    if connection_id:
                        await get_connection(bot, connection_id)
                        account = await db.business_account(owner)
                    if not account:
                        user_chat = await bot.get_chat(owner)
                        await db.save_account_identity(owner, user_chat.full_name, user_chat.username)
                        account = await db.business_account(owner)
        except Exception as exc:
            logger.warning("Could not refresh Telegram account metadata (%s)", type(exc).__name__)
    if owner:
        account = account or {"user_id": owner, "display_name": None, "username": None, "updated_at": None}
        live = connections.get(account.get("connection_id"))
        account["is_enabled"] = live.is_enabled if live else None
        account["can_reply"] = bool(live.rights and live.rights.can_reply) if live else None
    pending = await db.business_requests() if owner is None else []
    pending = [{key: item[key] for key in ("id", "name", "user_id")} for item in pending]
    settings = await db.get_all_settings()
    if not bot or not telegram_available:
        bot_info["running"] = False
        status, message = "no_bot", "Нет подтверждённой связи с Telegram. Проверьте токен бота и доступность сервиса."
    elif bot_info["chat_access_enabled"] is False:
        status, message = "missing_rights", "Включите Chat Access Mode для этого бота в BotFather."
    elif settings["is_paused"] == "1":
        status, message = "paused", "Автоответы на общей паузе."
    elif not owner:
        status, message = "needs_approval", (
            "Подтвердите аккаунт друга в карточке подключения." if pending else
            "Подключите бота на аккаунте Андрея через «Автоматизацию чатов». Если он уже добавлен, отправьте Андрею сообщение или переключите подключение в Telegram."
        )
    elif account["is_enabled"] is None:
        status, message = "waiting_for_connection", "Аккаунт подтверждён. Ожидаем событие подключения или сообщение из Telegram."
    elif not account["is_enabled"]:
        status, message = "disconnected", "Аккаунт отключил бота в Telegram. Проверьте «Автоматизацию чатов»."
    elif not account["can_reply"]:
        status, message = "missing_rights", "У бота нет права отвечать от имени подключённого аккаунта."
    elif not settings["custom_base_url"] or not settings["custom_model_name"]:
        status, message = "provider_missing", "Аккаунт подключён. Укажите Base URL и ID модели для ответов."
    else:
        status, message = "ready", "Аккаунт подключён, провайдер настроен. Бот отвечает в разрешённых чатах в стиле Андрея."
    return {
        "admin": {"user_id": ADMIN_ID}, "persona": {"name": PERSONA_NAME},
        "bot": bot_info, "account": account, "pending": pending,
        "storage": {"backend": db.backend, "persistent": db.persistent},
        "reply_status": status, "message": message,
    }


@api.get("/connection")
async def get_connection_status(request: Request):
    return await connection_status(request)


@api.post("/connection/approve")
async def approve_connection(payload: ConnectionApproval, request: Request):
    bot = getattr(request.app.state, "telegram_bot", None)
    if bot is None:
        raise HTTPException(503, "Telegram-бот не запущен.")
    try:
        await approve_account(payload.request_id, bot)
    except ConnectionApprovalError as exc:
        raise HTTPException(exc.status_code, str(exc)) from None
    return await connection_status(request)


@api.post("/chats/toggle")
async def toggle_chat(payload: ChatToggle):
    if not await db.set_chat_enabled(payload.chat_id, payload.is_enabled):
        raise HTTPException(404, "Чат не найден.")
    return {"status": "success", "chat_id": payload.chat_id, "is_enabled": payload.is_enabled}


@api.get("/stats")
async def get_stats():
    settings = await db.get_all_settings()
    return {
        "stats": await db.get_stats(),
        "active_model": settings["custom_model_name"] or None,
        "is_paused": settings["is_paused"] == "1",
    }


@api.post("/test")
async def test_persona(payload: TestMessage):
    try:
        chunks = await llm_manager.reply(await db.get_all_settings(), [{"text": payload.message}])
    except ReplyError as exc:
        raise HTTPException(502, str(exc)) from None
    return {"status": "success", "chunks": chunks}


app.include_router(api)
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
