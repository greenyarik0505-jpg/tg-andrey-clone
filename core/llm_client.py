"""Multi-provider LLM connector supporting Gemini, OpenAI, DeepSeek, Claude, Groq, OpenRouter."""
import os
import httpx
import logging
from typing import List, Dict, Any, Optional
from core.persona import ANDREY_BASE_SYSTEM_PROMPT, FEW_SHOT_DIALOGS

logger = logging.getLogger(__name__)

class LLMManager:
    def __init__(self):
        self.client = httpx.AsyncClient(timeout=30.0)

    async def generate_response(
        self,
        provider: str,
        model_name: str,
        api_key: str,
        history: List[Dict[str, Any]],
        custom_system_prompt: Optional[str] = None,
        temperature: float = 0.75
    ) -> str:
        """Dispatches request to the active LLM provider."""
        system_prompt = custom_system_prompt if custom_system_prompt else ANDREY_BASE_SYSTEM_PROMPT

        # Build messages structure
        messages = [{"role": "system", "content": system_prompt}]
        
        # Inject few-shot examples
        for fs in FEW_SHOT_DIALOGS:
            messages.append({"role": "user", "content": fs["user"]})
            messages.append({"role": "assistant", "content": fs["reply"]})

        # Inject chat history
        for msg in history:
            role = "assistant" if msg.get("is_outgoing") else "user"
            content = msg.get("text", "")
            if content:
                messages.append({"role": role, "content": content})

        try:
            if provider == "gemini":
                return await self._call_gemini(model_name, api_key, messages, temperature)
            elif provider in ("openai", "deepseek", "groq", "openrouter"):
                return await self._call_openai_compatible(provider, model_name, api_key, messages, temperature)
            elif provider == "custom":
                return await self._call_custom_provider(model_name, api_key, messages, temperature)
            elif provider == "anthropic":
                return await self._call_anthropic(model_name, api_key, messages, temperature)
            else:
                return await self._call_gemini(model_name, api_key, messages, temperature)
        except Exception as e:
            logger.error(f"Error calling LLM provider {provider} ({model_name}): {e}")
            raise e

    async def _call_gemini(self, model: str, api_key: str, messages: List[Dict[str, str]], temperature: float) -> str:
        """Call Google Gemini using google.genai SDK or direct REST API."""
        if not api_key:
            # Fallback to env GEMINI_API_KEY if present
            api_key = os.getenv("GEMINI_API_KEY", "")
        if not api_key:
            raise ValueError("Gemini API key is not configured in settings or environment.")

        # Attempt SDK first
        try:
            from google import genai
            from google.genai import types
            
            client = genai.Client(api_key=api_key)
            
            # Format contents
            contents = []
            sys_instruct = ""
            for m in messages:
                if m["role"] == "system":
                    sys_instruct = m["content"]
                else:
                    role = "user" if m["role"] == "user" else "model"
                    contents.append(types.Content(role=role, parts=[types.Part.from_text(text=m["content"])]))

            config = types.GenerateContentConfig(
                temperature=temperature,
                system_instruction=sys_instruct if sys_instruct else None
            )
            response = await client.aio.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
            if response and response.text:
                return response.text
        except Exception as sdk_err:
            logger.warning(f"SDK call failed, falling back to REST: {sdk_err}")

        # REST API fallback
        url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"
        gemini_contents = []
        system_instruction = None
        for m in messages:
            if m["role"] == "system":
                system_instruction = {"parts": [{"text": m["content"]}]}
            else:
                role = "user" if m["role"] == "user" else "model"
                gemini_contents.append({"role": role, "parts": [{"text": m["content"]}]})

        payload = {
            "contents": gemini_contents,
            "generationConfig": {"temperature": temperature}
        }
        if system_instruction:
            payload["systemInstruction"] = system_instruction

        res = await self.client.post(url, json=payload, headers={"Content-Type": "application/json"})
        res.raise_for_status()
        data = res.json()
        candidates = data.get("candidates", [])
        if candidates and "content" in candidates[0]:
            parts = candidates[0]["content"].get("parts", [])
            if parts and "text" in parts[0]:
                return parts[0]["text"]
        return "пон"

    async def _call_openai_compatible(self, provider: str, model: str, api_key: str, messages: List[Dict[str, str]], temperature: float) -> str:
        """Calls OpenAI, DeepSeek, Groq, or OpenRouter via OpenAI Chat Completions endpoint."""
        base_urls = {
            "openai": "https://api.openai.com/v1/chat/completions",
            "deepseek": "https://api.deepseek.com/v1/chat/completions",
            "groq": "https://api.groq.com/openai/v1/chat/completions",
            "openrouter": "https://openrouter.ai/api/v1/chat/completions"
        }
        url = base_urls.get(provider, "https://api.openai.com/v1/chat/completions")
        
        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json"
        }
        if provider == "openrouter":
            headers["HTTP-Referer"] = "https://t.me"
            headers["X-Title"] = "Andrey Digital Twin"

        payload = {
            "model": model,
            "messages": messages,
            "temperature": temperature
        }

        res = await self.client.post(url, json=payload, headers=headers)
        res.raise_for_status()
        data = res.json()
        return data["choices"][0]["message"]["content"]

    async def _call_custom_provider(self, model: str, api_key: str, messages: List[Dict[str, str]], temperature: float) -> str:
        """Calls custom OpenAI-compatible endpoint."""
        from database.db import db
        settings = await db.get_all_settings()
        base_url = settings.get("custom_base_url", "").strip()
        custom_model = settings.get("custom_model_name", "").strip() or model
        custom_key = settings.get("custom_api_key", "").strip() or api_key

        if not base_url:
            raise ValueError("Кастомный Base URL не настроен в панели управления!")

        # Format URL
        url = base_url.rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"

        headers = {
            "Content-Type": "application/json"
        }
        if custom_key:
            headers["Authorization"] = f"Bearer {custom_key}"

        payload = {
            "model": custom_model,
            "messages": messages,
            "temperature": temperature
        }

        res = await self.client.post(url, json=payload, headers=headers)
        res.raise_for_status()
        data = res.json()
        return data["choices"][0]["message"]["content"]


    async def _call_anthropic(self, model: str, api_key: str, messages: List[Dict[str, str]], temperature: float) -> str:
        """Call Anthropic Messages API."""
        url = "https://api.anthropic.com/v1/messages"
        headers = {
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json"
        }
        system_text = ""
        user_msgs = []
        for m in messages:
            if m["role"] == "system":
                system_text = m["content"]
            else:
                user_msgs.append({"role": m["role"], "content": m["content"]})

        payload = {
            "model": model if model != "claude-3-5-sonnet" else "claude-3-5-sonnet-20241022",
            "max_tokens": 150,
            "system": system_text,
            "messages": user_msgs,
            "temperature": temperature
        }
        res = await self.client.post(url, json=payload, headers=headers)
        res.raise_for_status()
        data = res.json()
        return data["content"][0]["text"]

llm_manager = LLMManager()
