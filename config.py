"""Configuration and constants for the Andrey AI-Clone Bot."""
import os

# Master Admin ID
ADMIN_ID = 7150170715

# Default Bot Token (can be overridden via environment or .env)
BOT_TOKEN = os.getenv("BOT_TOKEN", "")

# WebApp Host & Port
WEBAPP_HOST = os.getenv("WEBAPP_HOST", "0.0.0.0")
WEBAPP_PORT = int(os.getenv("PORT", os.getenv("WEBAPP_PORT", "8080")))
WEBAPP_URL = os.getenv("WEBAPP_URL", "")  # e.g., https://your-service.onrender.com

# Database Path
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot_data.db")

# Default Persona Settings
DEFAULT_MODEL = "gemini-2.5-flash"
SUPPORTED_MODELS = [
    {"id": "gemini-2.5-flash", "provider": "gemini", "name": "Google Gemini 2.5 Flash"},
    {"id": "gemini-3.8-flash", "provider": "gemini", "name": "Google Gemini 3.8 Flash"},
    {"id": "gpt-4o-mini", "provider": "openai", "name": "OpenAI GPT-4o Mini"},
    {"id": "gpt-4o", "provider": "openai", "name": "OpenAI GPT-4o"},
    {"id": "deepseek-chat", "provider": "deepseek", "name": "DeepSeek V3 (Chat)"},
    {"id": "deepseek-reasoner", "provider": "deepseek", "name": "DeepSeek R1"},
    {"id": "claude-3-5-sonnet", "provider": "anthropic", "name": "Anthropic Claude 3.5 Sonnet"},
    {"id": "groq-llama3", "provider": "groq", "name": "Groq Llama 3.3 70B"},
    {"id": "openrouter-auto", "provider": "openrouter", "name": "OpenRouter Auto"},
    {"id": "custom-model", "provider": "custom", "name": "⚙️ Кастомный провайдер (OpenAI-compatible)"}
]

