"""FastAPI backend for Telegram Mini App."""
import os
from typing import Dict, Any, Optional
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from database.db import db
from config import SUPPORTED_MODELS, ADMIN_ID
from core.persona import post_process_reply
from core.llm_client import llm_manager

app = FastAPI(title="Andrey AI Clone Dashboard")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if not os.path.exists(STATIC_DIR):
    os.makedirs(STATIC_DIR, exist_ok=True)

class SettingsUpdate(BaseModel):
    active_model: Optional[str] = None
    active_provider: Optional[str] = None
    openai_api_key: Optional[str] = None
    gemini_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    deepseek_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    openrouter_api_key: Optional[str] = None
    custom_base_url: Optional[str] = None
    custom_model_name: Optional[str] = None
    custom_api_key: Optional[str] = None
    temperature: Optional[str] = None
    min_delay: Optional[str] = None
    max_delay: Optional[str] = None
    simulate_typing: Optional[str] = None
    split_messages: Optional[str] = None
    human_takeover_minutes: Optional[str] = None
    is_paused: Optional[str] = None
    filter_sensitive: Optional[str] = None
    custom_prompt: Optional[str] = None

class ChatToggle(BaseModel):
    chat_id: int
    is_enabled: bool

class TestMessage(BaseModel):
    message: str

def mask_key(k: str) -> str:
    if not k:
        return ""
    if len(k) <= 8:
        return "********"
    return k[:4] + "****" + k[-4:]

@app.get("/")
async def get_index():
    index_file = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_file):
        return FileResponse(index_file)
    return HTMLResponse("<h1>Mini App Static File Not Found</h1>")

@app.get("/api/settings")
async def get_settings():
    settings = await db.get_all_settings()
    # Mask secret keys for safe UI inspection unless user explicitly replaces
    masked = dict(settings)
    for key in ["openai_api_key", "gemini_api_key", "anthropic_api_key", "deepseek_api_key", "groq_api_key", "openrouter_api_key", "custom_api_key"]:
        raw = masked.get(key, "")
        masked[f"{key}_masked"] = mask_key(raw)
        masked[f"has_{key}"] = bool(raw)
    
    return {
        "settings": masked,
        "models": SUPPORTED_MODELS
    }

@app.post("/api/settings")
async def update_settings(payload: SettingsUpdate):
    data = payload.model_dump(exclude_unset=True)
    # Don't overwrite api keys if passed masked or empty
    for k in list(data.keys()):
        if k.endswith("_api_key") and (not data[k] or "****" in data[k]):
            del data[k]
    
    # If active_model changed, automatically set active_provider
    if "active_model" in data:
        model_id = data["active_model"]
        match = next((m for m in SUPPORTED_MODELS if m["id"] == model_id), None)
        if match:
            data["active_provider"] = match["provider"]

    await db.update_settings(data)
    return {"status": "success", "message": "Настройки успешно сохранены!"}

@app.get("/api/chats")
async def get_chats():
    chats = await db.list_chats()
    return {"chats": chats}

@app.post("/api/chats/toggle")
async def toggle_chat(payload: ChatToggle):
    await db.set_chat_enabled(payload.chat_id, payload.is_enabled)
    return {"status": "success", "chat_id": payload.chat_id, "is_enabled": payload.is_enabled}

@app.get("/api/stats")
async def get_stats():
    stats = await db.get_stats()
    settings = await db.get_all_settings()
    return {
        "stats": stats,
        "active_model": settings.get("active_model", "gemini-2.5-flash"),
        "is_paused": settings.get("is_paused", "0") == "1",
        "admin_id": ADMIN_ID
    }

@app.post("/api/test")
async def test_persona(payload: TestMessage):
    settings = await db.get_all_settings()
    prov = settings.get("active_provider", "gemini")
    model = settings.get("active_model", "gemini-2.5-flash")
    api_key = settings.get(f"{prov}_api_key", "")
    temp = float(settings.get("temperature", 0.75))
    custom_sys = settings.get("custom_prompt") or None

    try:
        raw_res = await llm_manager.generate_response(
            provider=prov,
            model_name=model,
            api_key=api_key,
            history=[{"is_outgoing": 0, "text": payload.message}],
            custom_system_prompt=custom_sys,
            temperature=temp
        )
        chunks = post_process_reply(raw_res)
        return {"status": "success", "raw": raw_res, "chunks": chunks}
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# Mount static folder
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
