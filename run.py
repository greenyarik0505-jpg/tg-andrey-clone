"""Run the Telegram bot and Mini App in one process."""
import asyncio
import logging

import uvicorn

from bot.main import create_bot_and_dispatcher
from config import BOT_TOKEN, TELEGRAM_WEBHOOK_BASE_URL, WEBAPP_HOST, WEBAPP_PORT
from core.llm_client import llm_manager
from database.db import db
from webapp.server import app

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
logger = logging.getLogger(__name__)


async def start_bot(bot, dispatcher):
    async with bot.context():
        if (await bot.get_webhook_info()).url:
            raise RuntimeError("This bot token is already used by a webhook. Use a different token for local polling.")
        await dispatcher.start_polling(
            bot, handle_signals=False, close_bot_session=False, tasks_concurrency_limit=128,
        )


async def main():
    await db.init()
    app.state.unified_runner = bool(BOT_TOKEN and not TELEGRAM_WEBHOOK_BASE_URL)
    server = uvicorn.Server(
        uvicorn.Config(
            app, host=WEBAPP_HOST, port=WEBAPP_PORT,
            access_log=False, proxy_headers=False,
        )
    )
    web_task = asyncio.create_task(server.serve())
    if not app.state.unified_runner:
        if not BOT_TOKEN:
            logger.info("BOT_TOKEN is unset. Only the Mini App server will start.")
        await web_task
        return

    bot, dispatcher = create_bot_and_dispatcher(BOT_TOKEN)
    app.state.telegram_bot = bot
    bot_task = asyncio.create_task(start_bot(bot, dispatcher))
    try:
        done, _ = await asyncio.wait({web_task, bot_task}, return_when=asyncio.FIRST_COMPLETED)
        for task in done:
            await task
        if bot_task in done and not server.should_exit:
            raise RuntimeError("Telegram polling stopped unexpectedly.")
    finally:
        server.should_exit = True
        if not bot_task.done():
            try:
                await dispatcher.stop_polling()
            except RuntimeError:
                bot_task.cancel()
        await asyncio.gather(web_task, bot_task, return_exceptions=True)
        await llm_manager.close()
        await db.close()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Shutdown requested.")
