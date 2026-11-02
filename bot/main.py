"""Aiogram 3 Bot Initialization and Dispatcher."""
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.client.default import DefaultBotProperties
from config import BOT_TOKEN
from bot.admin import admin_router
from bot.business import business_router

logger = logging.getLogger(__name__)

def create_bot_and_dispatcher(token: str) -> tuple[Bot, Dispatcher]:
    bot = Bot(token=token, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
    
    # Register routers
    dp.include_router(admin_router)
    dp.include_router(business_router)
    
    return bot, dp
