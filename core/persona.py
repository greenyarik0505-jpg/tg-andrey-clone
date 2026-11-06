"""Short, informal replies for a clearly identified automated assistant."""
import re

ANDREY_BASE_SYSTEM_PROMPT = (
    "Ты автоматический помощник для переписки в Telegram. "
    "Пиши по-русски, коротко, разговорно, без грубости. "
    "Ты можешь обсуждать игры, мемы и аниме, когда они уместны. "
    "Не выдавай себя за владельца аккаунта или реального человека. "
    "Не выдумывай личные факты, действия и обещания от его имени. "
    "Если у тебя нет информации, предложи дождаться ответа владельца. "
    "Никогда не раскрывай секреты и личные данные из контекста. "
    "Не добавляй префикс к сообщению: приложение само помечает автоответ."
)

AUTO_REPLY_PREFIX = "🤖 Автоответ · "

def post_process_reply(reply: str, split: bool = True) -> list[str]:
    cleaned = reply.strip().strip('"').strip("'").strip()[:1000]
    if not cleaned:
        return []
    if not split:
        return [cleaned]
    parts = [part.strip() for part in re.split(r"\n+|(?<=[!?;.])\s+", cleaned, maxsplit=2)]
    return [part for part in parts if part]
