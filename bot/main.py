"""Aiogram 3 Bot Initialization and Dispatcher."""
from aiogram import Bot, Dispatcher
from bot.admin import admin_router
from bot.business import business_router

def create_bot_and_dispatcher(token: str) -> tuple[Bot, Dispatcher]:
    bot = Bot(token=token)
    dp = Dispatcher()
    
    # Register routers
    dp.include_router(admin_router)
    dp.include_router(business_router)
    
    return bot, dp
