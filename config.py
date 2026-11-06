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
