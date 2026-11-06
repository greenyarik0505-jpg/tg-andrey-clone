"""Configuration and constants for the Andrey AI-Clone Bot."""
import os
from dotenv import load_dotenv

load_dotenv()

# Master Admin ID
ADMIN_ID = int(os.getenv("ADMIN_ID", "7150170715"))
BUSINESS_USER_ID = int(os.getenv("BUSINESS_USER_ID") or "0") or None

# Default Bot Token (can be overridden via environment or .env)
BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
LOCAL_ADMIN_TOKEN = os.getenv("LOCAL_ADMIN_TOKEN", "").strip()

# WebApp Host & Port
WEBAPP_HOST = os.getenv("WEBAPP_HOST") or ("0.0.0.0" if os.getenv("PORT") else "127.0.0.1")
WEBAPP_PORT = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))
WEBAPP_URL = os.getenv("WEBAPP_URL") or os.getenv("RENDER_EXTERNAL_URL", "")
TELEGRAM_WEBHOOK_BASE_URL = (os.getenv("TELEGRAM_WEBHOOK_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL", "")).rstrip("/")

# Database Path
DB_PATH = os.getenv("DB_PATH") or os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db")
SETTINGS_ENCRYPTION_KEY = os.getenv("SETTINGS_ENCRYPTION_KEY", "").strip()
CUSTOM_PROVIDER_ORIGINS = {
    "https://api.openai.com", "https://openrouter.ai", "https://api.groq.com",
    "https://api.deepseek.com", "https://api.together.xyz", "https://api.mistral.ai",
    *(item.strip().rstrip("/") for item in os.getenv("CUSTOM_PROVIDER_ORIGINS", "").split(",") if item.strip()),
}

# Default Persona Settings
DEFAULT_MODEL = "gemini-2.5-flash"
SUPPORTED_MODELS = [
    {"id": "gemini-2.5-flash", "provider": "gemini", "name": "Google Gemini 2.5 Flash"},
    {"id": "gemini-3.8-flash", "provider": "gemini", "name": "Google Gemini 3.8 Flash"},
    {"id": "gpt-4o-mini", "provider": "openai", "name": "OpenAI GPT-4o Mini"},
    {"id": "gpt-4o", "provider": "openai", "name": "OpenAI GPT-4o"},
    {"id": "deepseek-flash", "provider": "deepseek", "name": "DeepSeek Flash"},
    {"id": "deepseek-v4-pro", "provider": "deepseek", "name": "DeepSeek V4 Pro"},
    {"id": "claude-sonnet-5-5", "provider": "anthropic", "name": "Anthropic Claude Sonnet 5.5"},
    {"id": "openai/gpt-oss-20b", "provider": "groq", "name": "Groq GPT-OSS 20B"},
    {"id": "openrouter-auto", "provider": "openrouter", "name": "OpenRouter Auto"},
    {"id": "custom-model", "provider": "custom", "name": "⚙️ Кастомный провайдер (OpenAI-compatible)"}
]

MODEL_PROVIDERS = {model["id"]: model["provider"] for model in SUPPORTED_MODELS}
LEGACY_MODELS = {
    "deepseek-chat": "deepseek-flash",
    "deepseek-reasoner": "deepseek-v4-pro",
    "claude-3-5-sonnet": "claude-sonnet-5-5",
    "groq-llama3": "openai/gpt-oss-20b",
}
API_KEY_FIELDS = {f"{provider}_api_key" for provider in MODEL_PROVIDERS.values()}

