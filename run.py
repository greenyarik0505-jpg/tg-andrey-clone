"""Unified Application Runner: Starts Aiogram Bot and FastAPI Mini App concurrently."""
import asyncio
import logging
import os
import uvicorn
from dotenv import load_dotenv

load_dotenv()

from config import BOT_TOKEN, WEBAPP_HOST, WEBAPP_PORT, ADMIN_ID
from database.db import db
from bot.main import create_bot_and_dispatcher
from webapp.server import app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger("AndreyClone")

async def start_webapp_server():
    """Starts the FastAPI WebApp server using uvicorn."""
    config = uvicorn.Config(
        app=app,
        host=WEBAPP_HOST,
        port=WEBAPP_PORT,
        log_level="info",
        access_log=False
    )
    server = uvicorn.Server(config)
    logger.info(f"🌐 Mini App Dashboard running on http://{WEBAPP_HOST}:{WEBAPP_PORT}")
    await server.serve()

async def start_bot(token: str):
    """Starts the Aiogram 3 Telegram Bot."""
    bot, dp = create_bot_and_dispatcher(token)
    logger.info(f"🤖 Telegram Bot starting polling for Admin ID {ADMIN_ID}...")
    try:
        # Drop pending updates to avoid backlog
        await bot.delete_webhook(drop_pending_updates=True)
        await dp.start_polling(bot)
    except Exception as e:
        logger.error(f"Telegram Bot error: {e}")
    finally:
        await bot.session.close()

async def main():
    # 1. Initialize SQLite Database
    await db.init()
    logger.info("📦 Database initialized successfully.")

    token = os.getenv("BOT_TOKEN", BOT_TOKEN).strip()
    
    tasks = [start_webapp_server()]

    if token:
        tasks.append(start_bot(token))
    else:
        logger.warning(
            "⚠️ BOT_TOKEN is not set in environment or .env!\n"
            "FastAPI Mini App is running. You can test Andrey's AI Persona at http://localhost:8080\n"
            "To activate Telegram integration, specify BOT_TOKEN in .env or run: $env:BOT_TOKEN='your_token'; python run.py"
        )

    await asyncio.gather(*tasks)

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        logger.info("Shutdown requested. Exiting...")
