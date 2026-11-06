"""Private Telegram controls for the configured administrator."""
from html import escape
from urllib.parse import urlsplit

from aiogram import Bot, F, Router
from aiogram.enums import ChatType
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup, Message, WebAppInfo

from bot.business import business_request_button, can_reply, fetch_connection
from config import ADMIN_ID, API_KEY_FIELDS, LEGACY_MODELS, SUPPORTED_MODELS, WEBAPP_URL
from core.llm_client import ReplyError, llm_manager
from core.settings import number, reply_delays
from database.db import db

admin_router = Router()
admin_router.message.filter(F.chat.type == ChatType.PRIVATE)
admin_router.callback_query.filter(F.from_user.id == ADMIN_ID, F.message.chat.type == ChatType.PRIVATE)


def is_admin(message):
    return message.from_user is not None and message.from_user.id == ADMIN_ID


def webapp_button():
    try:
        url = urlsplit(WEBAPP_URL)
        if url.scheme == "https" and url.hostname and not url.username and not url.password:
            return InlineKeyboardButton(text="🚀 Открыть Mini App", web_app=WebAppInfo(url=WEBAPP_URL))
    except ValueError:
        pass
    return None


def back_keyboard():
    return InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="‹ Назад", callback_data="admin_back")]])


def main_keyboard():
    button = webapp_button()
    rows = [[button]] if button else []
    rows.extend([
        [InlineKeyboardButton(text="Модели", callback_data="admin_models"),
         InlineKeyboardButton(text="Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton(text="Чаты", callback_data="admin_chats"),
         InlineKeyboardButton(text="Пауза / Пуск", callback_data="admin_toggle_pause")],
        [InlineKeyboardButton(text="Подключение", callback_data="admin_business"),
         InlineKeyboardButton(text="Команды", callback_data="admin_help")],
    ])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def model_keyboard():
    rows = [
        [InlineKeyboardButton(text=model["name"], callback_data=f"set_model_{model['id']}")]
        for model in SUPPORTED_MODELS
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows + back_keyboard().inline_keyboard)


async def home_text():
    settings = await db.get_all_settings()
    status = "⏸ Пауза" if settings["is_paused"] == "1" else "🟢 Автоответы разрешены"
    return (
        f"Автоматизация чатов Telegram\n\n{status}\n"
        f"Модель: {settings['active_model']}\n\n"
        "Автоответы работают в разрешённых в Telegram чатах и помечаются в сообщениях."
    )


async def stats_text():
    settings = await db.get_all_settings()
    stats = await db.get_stats()
    minimum, maximum = reply_delays(settings)
    takeover = number(settings, "human_takeover_minutes")
    return (
        f"Модель: {settings['active_model']}\n"
        f"Задержка: {minimum:g}–{maximum:g} с\n"
        f"Пауза после ручного ответа: {takeover:g} мин\n"
        f"Чаты с автоответами: {stats['active_chats']} из {stats['total_chats']}\n"
        f"Ответы бота в истории: {stats['total_sent']}\n"
        f"Входящие в истории: {stats['total_received']}"
    )


@admin_router.message(CommandStart())
async def cmd_start(message: Message):
    if is_admin(message):
        await message.answer(await home_text(), reply_markup=main_keyboard())
    else:
        owner = await db.business_owner()
        if message.from_user and owner in (None, message.from_user.id):
            await message.answer("Откройте Настройки → Автоматизация чатов и добавьте этого бота в выбранные чаты.")
        else:
            await message.answer("Это бот автоматических ответов Telegram.")


async def business_panel():
    owner = await db.business_owner()
    if owner is not None:
        return f"Разрешённый аккаунт: {owner}\nПодключение и список доступных чатов меняются в настройках Telegram.", back_keyboard()
    requests = await db.business_requests()
    if not requests:
        return "Запросов пока нет. Добавьте бота через Настройки → Автоматизация чатов на нужном аккаунте.", back_keyboard()
    rows = [
        [business_request_button(item["id"], f"{item['name'][:35]} · {item['user_id']}")]
        for item in requests
    ]
    return "Выберите знакомый аккаунт для подтверждения.", InlineKeyboardMarkup(
        inline_keyboard=rows + back_keyboard().inline_keyboard
    )


@admin_router.message(Command("business"))
async def cmd_business(message: Message):
    if is_admin(message):
        text, keyboard = await business_panel()
        await message.answer(text, reply_markup=keyboard)


@admin_router.callback_query(F.data == "admin_business")
async def cb_business(callback: CallbackQuery):
    await callback.answer()
    text, keyboard = await business_panel()
    await callback.message.edit_text(text, reply_markup=keyboard)


@admin_router.callback_query(F.data.regexp(r"^business_approve:[A-Za-z0-9_-]{16}$"))
async def cb_approve_business(callback: CallbackQuery, bot: Bot):
    request_id = callback.data.split(":")[1]
    requests = await db.business_requests(request_id)
    if not requests:
        await callback.answer("Запрос уже обработан или истёк.", show_alert=True)
        return
    request = requests[0]
    connection = await fetch_connection(bot, request["connection_id"])
    if not connection or not connection.is_enabled or connection.user.id != request["user_id"]:
        await callback.answer("Подключение недоступно. Проверьте его в настройках Telegram.", show_alert=True)
        return
    if not await db.approve_business_request(request_id, connection.user.id):
        await callback.answer("Запрос уже обработан или истёк.", show_alert=True)
        return
    await callback.answer("Аккаунт подтверждён.")
    status = "Автоответы готовы в разрешённых чатах." if can_reply(connection) else "Разрешите боту отвечать в настройках Telegram."
    await callback.message.edit_text(f"Подтверждён аккаунт {request['name']}.\n{status}", reply_markup=back_keyboard())


@admin_router.message(Command("status"))
async def cmd_status(message: Message):
    if is_admin(message):
        await message.answer(await stats_text())


@admin_router.message(Command("setkey"))
async def cmd_setkey(message: Message):
    if not is_admin(message):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) != 3 or f"{parts[1].lower()}_api_key" not in API_KEY_FIELDS:
        await message.answer("Формат: /setkey провайдер ключ. Ключ можно также задать в Mini App.")
        return

    provider = parts[1].lower()
    try:
        await db.set_setting(f"{provider}_api_key", parts[2])
    except ValueError as exc:
        await message.answer(str(exc))
        return
    await remove_secret_message(message)
    await message.answer(f"Ключ для {provider} сохранён.")


async def remove_secret_message(message):
    try:
        await message.delete()
    except TelegramAPIError:
        pass


@admin_router.message(Command("custom"))
async def cmd_custom(message: Message):
    if not is_admin(message):
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer("Формат: /custom URL название_модели [ключ]")
        return

    changes = {"active_model": "custom-model", "custom_base_url": parts[1], "custom_model_name": parts[2]}
    if len(parts) == 4:
        changes["custom_api_key"] = parts[3]
    try:
        await db.update_settings(changes)
    except ValueError as exc:
        await message.answer(str(exc))
        return
    if "custom_api_key" in changes:
        await remove_secret_message(message)
    await message.answer("Кастомный провайдер активирован.")


@admin_router.message(Command("model"))
async def cmd_model(message: Message):
    if not is_admin(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) == 1:
        await message.answer("Выберите модель.", reply_markup=model_keyboard())
        return
    model = parts[1].strip()
    model = LEGACY_MODELS.get(model, model)
    try:
        await db.set_setting("active_model", model)
    except ValueError as exc:
        await message.answer(str(exc), reply_markup=model_keyboard())
        return
    await message.answer(f"Активная модель: {model}")


@admin_router.message(Command("test"))
async def cmd_test(message: Message):
    if not is_admin(message):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) != 2 or not parts[1].strip():
        await message.answer("Формат: /test Привет, как дела?")
        return
    wait_msg = await message.answer("Запрашиваю тестовый ответ…")
    try:
        chunks = await llm_manager.reply(await db.get_all_settings(), [{"text": parts[1]}])
    except ReplyError as exc:
        await wait_msg.edit_text(str(exc))
        return
    content = "\n\n".join(escape(chunk) for chunk in chunks)
    await wait_msg.edit_text(f"<b>Тестовый автоответ</b>\n\n{content}", parse_mode="HTML")


@admin_router.message(Command("pause", "unpause"))
async def cmd_pause(message: Message, command: CommandObject):
    if is_admin(message):
        paused = command.command.lower() == "pause"
        await db.set_setting("is_paused", "1" if paused else "0")
        await message.answer("Автоответы на паузе." if paused else "Автоответы возобновлены.")


@admin_router.callback_query(F.data.startswith("set_model_"))
async def cb_set_model(callback: CallbackQuery):
    model = callback.data.removeprefix("set_model_")
    model = LEGACY_MODELS.get(model, model)
    try:
        await db.set_setting("active_model", model)
    except ValueError as exc:
        await callback.answer(str(exc), show_alert=True)
        return
    await callback.answer("Модель сохранена")
    await callback.message.edit_text(await home_text(), reply_markup=main_keyboard())


@admin_router.callback_query(F.data == "admin_models")
async def cb_admin_models(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text("Выберите модель.", reply_markup=model_keyboard())


@admin_router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(await stats_text(), reply_markup=back_keyboard())


@admin_router.callback_query(F.data == "admin_chats")
async def cb_admin_chats(callback: CallbackQuery):
    await callback.answer()
    chats = await db.list_chats()
    lines = [f"{'●' if chat['is_enabled'] else '○'} {chat['chat_title'] or chat['chat_id']}" for chat in chats[:20]]
    if len(chats) > 20:
        lines.append(f"И ещё {len(chats) - 20} в Mini App")
    lines.append("Управление доступом к чатам находится в Mini App." if chats else "Подключённых чатов пока нет.")
    button = webapp_button()
    keyboard = back_keyboard()
    if button:
        keyboard.inline_keyboard.insert(0, [button])
    await callback.message.edit_text("\n".join(lines), reply_markup=keyboard)


@admin_router.callback_query(F.data.in_({"admin_help", "admin_keys_menu", "admin_test_prompt"}))
async def cb_admin_help(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(
        "Настройки доступны в Mini App.\n\n"
        "/status — статистика\n/business — подключение аккаунта\n/model — модель\n/test текст — пробный ответ\n"
        "/pause и /unpause — управление автоответами\n/setkey и /custom — настройка провайдера",
        reply_markup=back_keyboard(),
    )


@admin_router.callback_query(F.data == "admin_toggle_pause")
async def cb_toggle_pause(callback: CallbackQuery):
    paused = await db.toggle_pause()
    await callback.answer("Автоответы на паузе" if paused else "Автоответы возобновлены")
    await callback.message.edit_text(await home_text(), reply_markup=main_keyboard())


@admin_router.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery):
    await callback.answer()
    await callback.message.edit_text(await home_text(), reply_markup=main_keyboard())
