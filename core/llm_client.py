"""Send chat completions to the configured OpenAI-compatible provider."""
import asyncio
import logging

import httpx

from core.persona import build_persona_prompt, post_process_reply
from core.settings import custom_endpoint, number

logger = logging.getLogger(__name__)


class ReplyError(Exception):
    """A provider error that can safely be displayed to the admin."""


class LLMManager:
    def __init__(self):
        self.client = None
        self.slots = asyncio.Semaphore(8)

    async def close(self):
        if self.client is not None:
            await self.client.aclose()
            self.client = None

    async def reply(self, settings, history):
        model = settings.get("custom_model_name", "")
        base_url = settings.get("custom_base_url", "")
        if not model or not base_url:
            raise ReplyError("Укажите Base URL и ID модели в Mini App или через /custom.")
        try:
            endpoint = custom_endpoint(base_url)
        except ValueError as exc:
            raise ReplyError(str(exc)) from None

        prompt = build_persona_prompt(settings.get("custom_prompt", ""))
        messages = [
            {"role": "assistant" if item.get("is_outgoing") else "user", "content": item["text"][:4096]}
            for item in history if item.get("text")
        ]
        first_user = next((index for index, message in enumerate(messages) if message["role"] == "user"), None)
        if first_user is None:
            raise ReplyError("Введите сообщение для генерации ответа.")

        key = settings.get("custom_api_key")
        headers = {"Authorization": f"Bearer {key}"} if key else {}
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": prompt}, *messages[first_user:]],
            "temperature": number(settings, "temperature"),
            "max_tokens": 300,
        }
        try:
            async with asyncio.timeout(35), self.slots:
                if self.client is None:
                    self.client = httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False)
                response = await self.client.post(endpoint, headers=headers, json=payload)
                response.raise_for_status()
                raw = response.json()["choices"][0]["message"]["content"]
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("Custom LLM provider returned HTTP %d", status)
            if status == 429:
                raise ReplyError("Провайдер ограничил запросы. Попробуйте позже.") from None
            if status in (401, 403):
                raise ReplyError("Провайдер отклонил запрос. Проверьте ключ и права доступа.") from None
            raise ReplyError(f"Провайдер отклонил запрос (HTTP {status}). Проверьте Base URL и ID модели.") from None
        except (TimeoutError, httpx.TimeoutException):
            raise ReplyError("Провайдер не ответил вовремя.") from None
        except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError, IndexError):
            raise ReplyError("Не удалось получить ответ от провайдера.") from None

        chunks = post_process_reply(raw, split=settings.get("split_messages", "1") == "1") if isinstance(raw, str) else []
        if not chunks:
            raise ReplyError("Модель вернула пустой ответ.")
        return chunks


llm_manager = LLMManager()
