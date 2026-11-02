"""Admin handlers and inline controls for Master Admin (7150170715)."""
import time
from aiogram import Router, F
from aiogram.filters import Command, CommandStart
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton, WebAppInfo
)
from config import ADMIN_ID, WEBAPP_URL, SUPPORTED_MODELS
from database.db import db
from core.persona import post_process_reply, ANDREY_BASE_SYSTEM_PROMPT
from core.llm_client import llm_manager

admin_router = Router()

def is_admin(user_id: int) -> bool:
    return user_id == ADMIN_ID

def get_main_keyboard(webapp_url: str = "") -> InlineKeyboardMarkup:
    buttons = []
    if webapp_url and webapp_url.startswith("https://"):
        buttons.append([InlineKeyboardButton(text="🚀 Открыть Mini App Админку", web_app=WebAppInfo(url=webapp_url))])
    
    buttons.append([
        InlineKeyboardButton(text="🤖 Выбрать модель", callback_data="admin_models"),
        InlineKeyboardButton(text="📊 Статистика", callback_data="admin_stats")
    ])
    buttons.append([
        InlineKeyboardButton(text="💬 Активные чаты", callback_data="admin_chats"),
        InlineKeyboardButton(text="⏸ Пауза / Пуск", callback_data="admin_toggle_pause")
    ])
    buttons.append([
        InlineKeyboardButton(text="🔑 Настройка ключей", callback_data="admin_keys_menu"),
        InlineKeyboardButton(text="⚡ Тест ответа", callback_data="admin_test_prompt")
    ])
    return InlineKeyboardMarkup(inline_keyboard=buttons)

@admin_router.message(CommandStart())
async def cmd_start(message: Message):
    if not is_admin(message.from_user.id):
        # Friend or third-party user
        await message.answer(
            "👋 Привет! Это бот-двойник Андрея.\n"
            "Чтобы подключить бота к ответам в твоих переписках:\n"
            "1. Зайди в Настройки Telegram ➔ Telegram Business ➔ Чат-боты\n"
            "2. Выбери этого бота и включи доступ к нужным чатам."
        )
        return

    # Master Admin interface
    settings = await db.get_all_settings()
    active_model = settings.get("active_model", "gemini-2.5-flash")
    is_paused = settings.get("is_paused", "0") == "1"
    status_text = "⏸ НА ПАУЗЕ" if is_paused else "🟢 АКТИВЕН"
    
    webapp_url = await db.get_setting("webapp_url", WEBAPP_URL)

    text = (
        f"👑 <b>Панель управления AI-Двойником (Андрей)</b>\n\n"
        f"• <b>Статус автоответов:</b> {status_text}\n"
        f"• <b>Активная модель:</b> <code>{active_model}</code>\n"
        f"• <b>Режим:</b> Telegram Business + TMA\n"
        f"• <b>Имитация:</b> Школьник 11-12 лет (Brawl Stars, Standoff, Аниме)\n\n"
        f"Управляй системой через Mini App или кнопками ниже:"
    )
    await message.answer(text, reply_markup=get_main_keyboard(webapp_url), parse_mode="HTML")

@admin_router.message(Command("status"))
async def cmd_status(message: Message):
    if not is_admin(message.from_user.id):
        return
    settings = await db.get_all_settings()
    stats = await db.get_stats()
    
    text = (
        f"📊 <b>Текущий статус системы:</b>\n"
        f"• Модель: <code>{settings.get('active_model')}</code>\n"
        f"• Провайдер: <code>{settings.get('active_provider')}</code>\n"
        f"• Задержка ответа: {settings.get('min_delay')}–{settings.get('max_delay')} сек\n"
        f"• Human Takeover: {settings.get('human_takeover_minutes')} минут\n"
        f"• Всего чатов: {stats['total_chats']} (активных: {stats['active_chats']})\n"
        f"• Отправлено сообщений: {stats['total_sent']}\n"
        f"• Получено сообщений: {stats['total_received']}\n"
    )
    await message.answer(text, parse_mode="HTML")

@admin_router.message(Command("setkey"))
async def cmd_setkey(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        await message.answer(
            "Формат: <code>/setkey [provider] [key]</code>\n"
            "Примеры:\n"
            "<code>/setkey gemini AIzaSy...</code>\n"
            "<code>/setkey openai sk-proj-...</code>\n"
            "<code>/setkey deepseek sk-...</code>\n"
            "<code>/setkey anthropic sk-ant-...</code>\n"
            "<code>/setkey groq gsk_...</code>\n"
            "<code>/setkey openrouter sk-or-...</code>",
            parse_mode="HTML"
        )
        return
    
    prov = parts[1].lower()
    key = parts[2].strip()
    setting_name = f"{prov}_api_key"
    await db.set_setting(setting_name, key)
    await message.answer(f"✅ Ключ для <b>{prov}</b> сохранен!", parse_mode="HTML")

@admin_router.message(Command("custom"))
async def cmd_custom(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=3)
    if len(parts) < 3:
        await message.answer(
            "Формат команды кастомного провайдера:\n"
            "<code>/custom [Base_URL] [Model_Name] [API_Key]</code>\n\n"
            "Пример:\n"
            "<code>/custom https://openrouter.ai/api/v1 mistralai/mistral-large sk-or-v1-...</code>",
            parse_mode="HTML"
        )
        return
    base_url = parts[1].strip()
    model_name = parts[2].strip()
    api_key = parts[3].strip() if len(parts) > 3 else ""

    await db.set_setting("active_model", "custom-model")
    await db.set_setting("active_provider", "custom")
    await db.set_setting("custom_base_url", base_url)
    await db.set_setting("custom_model_name", model_name)
    if api_key:
        await db.set_setting("custom_api_key", api_key)

    await message.answer(
        f"✅ <b>Кастомный провайдер активирован!</b>\n"
        f"• Base URL: <code>{base_url}</code>\n"
        f"• Модель: <code>{model_name}</code>\n"
        f"• Ключ: {'Сохранен' if api_key else 'Без ключа'}",
        parse_mode="HTML"
    )

@admin_router.message(Command("model"))
async def cmd_model(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        # Show selection keyboard
        kb_buttons = []
        for m in SUPPORTED_MODELS:
            kb_buttons.append([InlineKeyboardButton(text=f"🔹 {m['name']}", callback_data=f"set_model_{m['id']}")])
        await message.answer("Выбери нейросеть для генерации ответов:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))
        return
    
    chosen = parts[1].strip()
    # Match supported
    match = next((m for m in SUPPORTED_MODELS if m["id"] == chosen), None)
    if match:
        await db.set_setting("active_model", match["id"])
        await db.set_setting("active_provider", match["provider"])
        await message.answer(f"✅ Активная модель переключена на <b>{match['name']}</b>", parse_mode="HTML")
    else:
        await message.answer(f"⚠️ Неизвестная модель. Доступные: {', '.join([m['id'] for m in SUPPORTED_MODELS])}")

@admin_router.message(Command("test"))
async def cmd_test(message: Message):
    if not is_admin(message.from_user.id):
        return
    parts = message.text.split(maxsplit=1)
    if len(parts) < 2:
        await message.answer("Формат: <code>/test Привет как дела</code>", parse_mode="HTML")
        return
    
    prompt_text = parts[1]
    wait_msg = await message.answer("⏳ Андрей думает...")
    
    settings = await db.get_all_settings()
    prov = settings.get("active_provider", "gemini")
    model = settings.get("active_model", "gemini-2.5-flash")
    api_key = settings.get(f"{prov}_api_key", "")
    temp = float(settings.get("temperature", 0.75))
    custom_sys = settings.get("custom_prompt") or None

    try:
        raw_res = await llm_manager.generate_response(
            provider=prov,
            model_name=model,
            api_key=api_key,
            history=[{"is_outgoing": 0, "text": prompt_text}],
            custom_system_prompt=custom_sys,
            temperature=temp
        )
        chunks = post_process_reply(raw_res)
        res_text = (
            f"🎯 <b>Тестовый ответ Андрея ({model}):</b>\n\n"
            + "\n".join([f"💬 <code>{c}</code>" for c in chunks])
        )
        await wait_msg.edit_text(res_text, parse_mode="HTML")
    except Exception as e:
        await wait_msg.edit_text(f"❌ Ошибка генерации: {e}")

@admin_router.callback_query(F.data.startswith("set_model_"))
async def cb_set_model(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    model_id = callback.data.replace("set_model_", "")
    match = next((m for m in SUPPORTED_MODELS if m["id"] == model_id), None)
    if match:
        await db.set_setting("active_model", match["id"])
        await db.set_setting("active_provider", match["provider"])
        await callback.message.edit_text(f"✅ Активная модель установлена: <b>{match['name']}</b>", parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(F.data == "admin_models")
async def cb_admin_models(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    kb_buttons = []
    for m in SUPPORTED_MODELS:
        kb_buttons.append([InlineKeyboardButton(text=f"🔹 {m['name']}", callback_data=f"set_model_{m['id']}")])
    kb_buttons.append([InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")])
    await callback.message.edit_text("Выбери нейросеть для ответов:", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb_buttons))
    await callback.answer()

@admin_router.callback_query(F.data == "admin_stats")
async def cb_admin_stats(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    stats = await db.get_stats()
    text = (
        f"📊 <b>Статистика бота:</b>\n\n"
        f"• Всего подключенных чатов: {stats['total_chats']}\n"
        f"• Активных чатов (автоответ): {stats['active_chats']}\n"
        f"• Отправлено сообщений: {stats['total_sent']}\n"
        f"• Принято сообщений: {stats['total_received']}\n"
    )
    kb = InlineKeyboardMarkup(inline_keyboard=[[InlineKeyboardButton(text="◀️ Назад", callback_data="admin_back")]])
    await callback.message.edit_text(text, reply_markup=kb, parse_mode="HTML")
    await callback.answer()

@admin_router.callback_query(F.data == "admin_toggle_pause")
async def cb_toggle_pause(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    curr = await db.get_setting("is_paused", "0")
    new_val = "0" if curr == "1" else "1"
    await db.set_setting("is_paused", new_val)
    status_str = "⏸ Бот поставлен на паузу" if new_val == "1" else "🟢 Бот возобновил автоответы"
    await callback.answer(status_str, show_alert=True)
    
    webapp_url = await db.get_setting("webapp_url", WEBAPP_URL)
    await callback.message.edit_reply_markup(reply_markup=get_main_keyboard(webapp_url))

@admin_router.callback_query(F.data == "admin_back")
async def cb_admin_back(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    settings = await db.get_all_settings()
    active_model = settings.get("active_model", "gemini-2.5-flash")
    is_paused = settings.get("is_paused", "0") == "1"
    status_text = "⏸ НА ПАУЗЕ" if is_paused else "🟢 АКТИВЕН"
    webapp_url = await db.get_setting("webapp_url", WEBAPP_URL)
    
    text = (
        f"👑 <b>Панель управления AI-Двойником (Андрей)</b>\n\n"
        f"• <b>Статус автоответов:</b> {status_text}\n"
        f"• <b>Активная модель:</b> <code>{active_model}</code>\n"
        f"• <b>Режим:</b> Telegram Business + TMA\n\n"
        f"Управляй системой через Mini App или кнопками ниже:"
    )
    await callback.message.edit_text(text, reply_markup=get_main_keyboard(webapp_url), parse_mode="HTML")
    await callback.answer()
