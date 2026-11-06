"""Send normalized requests to the selected language model provider."""
import asyncio
import logging

import httpx

from config import DEFAULT_MODEL, MODEL_PROVIDERS
from core.persona import ANDREY_BASE_SYSTEM_PROMPT, post_process_reply
from core.settings import custom_endpoint, number, provider_key

logger = logging.getLogger(__name__)

OPENAI_ENDPOINTS = {
    "openai": "https://api.openai.com/v1/chat/completions",
    "deepseek": "https://api.deepseek.com/chat/completions",
    "groq": "https://api.groq.com/openai/v1/chat/completions",
    "openrouter": "https://openrouter.ai/api/v1/chat/completions",
}
MODEL_IDS = {"openrouter-auto": "openrouter/auto"}


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
        model = settings.get("active_model", DEFAULT_MODEL)
        provider = MODEL_PROVIDERS.get(model)
        if not provider:
            raise ReplyError("Выберите модель в настройках.")
        key = provider_key(settings, provider)
        if provider != "custom" and not key:
            raise ReplyError("Для выбранного провайдера нужен API-ключ.")

        prompt = ANDREY_BASE_SYSTEM_PROMPT
        if settings.get("custom_prompt"):
            prompt += "\nДополнительные пожелания к стилю, если они совместимы с правилами выше:\n" + settings["custom_prompt"]
        messages = [
            {"role": "assistant" if item.get("is_outgoing") else "user", "content": item["text"][:4096]}
            for item in history if item.get("text")
        ]
        first_user = next((index for index, message in enumerate(messages) if message["role"] == "user"), None)
        if first_user is None:
            raise ReplyError("Введите сообщение для генерации ответа.")
        messages = messages[first_user:]
        if provider == "custom":
            model = settings.get("custom_model_name", "").strip()
            if not model:
                raise ReplyError("Укажите название кастомной модели.")
            try:
                endpoint = custom_endpoint(settings.get("custom_base_url", ""))
            except ValueError as exc:
                raise ReplyError(str(exc)) from None
        else:
            endpoint = OPENAI_ENDPOINTS.get(provider)
        try:
            async with asyncio.timeout(35), self.slots:
                provider_model = MODEL_IDS.get(model, model) if provider != "custom" else model
                raw = await self._generate(
                    provider, provider_model, endpoint, key, prompt, messages, number(settings, "temperature"),
                )
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            logger.warning("LLM provider %s returned HTTP %d", provider, status)
            if status == 429:
                raise ReplyError("Провайдер ограничил запросы. Попробуйте позже.") from None
            if status in (401, 403):
                raise ReplyError("Провайдер отклонил запрос. Проверьте ключ и права доступа.") from None
            raise ReplyError(f"Провайдер отклонил запрос (HTTP {status}). Проверьте выбранную модель.") from None
        except (TimeoutError, httpx.TimeoutException):
            raise ReplyError("Провайдер не ответил вовремя.") from None
        except (httpx.HTTPError, AttributeError, KeyError, TypeError, ValueError, IndexError):
            raise ReplyError("Не удалось получить ответ от провайдера.") from None

        chunks = post_process_reply(raw, split=settings.get("split_messages", "1") == "1") if isinstance(raw, str) else []
        if not chunks:
            raise ReplyError("Модель вернула пустой ответ.")
        return chunks

    async def _generate(self, provider, model, endpoint, key, prompt, messages, temperature):
        if self.client is None:
            self.client = httpx.AsyncClient(timeout=25, follow_redirects=False, trust_env=False)
        if provider == "gemini":
            contents = [
                {"role": "model" if item["role"] == "assistant" else "user", "parts": [{"text": item["content"]}]}
                for item in messages
            ]
            thinking = {"thinkingLevel": "LOW"} if model == "gemini-3.8-flash" else {"thinkingBudget": 0}
            data = await self._post(
                f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent",
                {"x-goog-api-key": key},
                {"contents": contents, "systemInstruction": {"parts": [{"text": prompt}]},
                 "generationConfig": {"temperature": temperature, "maxOutputTokens": 1024, "thinkingConfig": thinking}},
            )
            parts = data["candidates"][0]["content"]["parts"]
            return "".join(part.get("text", "") for part in parts if not part.get("thought"))

        if provider == "anthropic":
            data = await self._post(
                "https://api.anthropic.com/v1/messages",
                {"x-api-key": key, "anthropic-version": "2023-06-01"},
                {"model": model, "system": prompt, "messages": messages,
                 "thinking": {"type": "between_tools"}, "max_tokens": 300},
            )
            return "".join(part["text"] for part in data["content"] if part.get("type") == "text")

        headers = {"Authorization": f"Bearer {key}"} if key else {}
        payload = {
            "model": model, "messages": [{"role": "system", "content": prompt}, *messages],
            "temperature": temperature,
            "max_tokens": 300,
        }
        if provider == "deepseek":
            payload["thinking"] = {"type": "disabled"}
        if provider == "groq":
            payload["reasoning_effort"] = "low"
            del payload["max_tokens"]
            payload["max_completion_tokens"] = 1024
        data = await self._post(endpoint, headers, payload)
        return data["choices"][0]["message"]["content"]

    async def _post(self, url, headers, payload):
        response = await self.client.post(url, json=payload, headers=headers)
        response.raise_for_status()
        return response.json()


llm_manager = LLMManager()
