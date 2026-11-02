import aiosqlite
import json
import time
from typing import Optional, Dict, Any, List
from config import DB_PATH, DEFAULT_MODEL

CREATE_TABLES_SQL = """
CREATE TABLE IF NOT EXISTS settings (
    key TEXT PRIMARY KEY,
    value TEXT
);

CREATE TABLE IF NOT EXISTS chats (
    chat_id INTEGER PRIMARY KEY,
    chat_title TEXT,
    username TEXT,
    is_enabled INTEGER DEFAULT 1,
    last_human_message_time REAL DEFAULT 0,
    last_bot_message_time REAL DEFAULT 0,
    notes TEXT DEFAULT ''
);

CREATE TABLE IF NOT EXISTS message_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id INTEGER,
    sender_id INTEGER,
    sender_name TEXT,
    text TEXT,
    timestamp REAL,
    is_outgoing INTEGER
);

CREATE TABLE IF NOT EXISTS logs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp REAL,
    level TEXT,
    message TEXT,
    details TEXT
);
"""

DEFAULT_SETTINGS = {
    "active_model": DEFAULT_MODEL,
    "active_provider": "gemini",
    "openai_api_key": "",
    "gemini_api_key": "",
    "anthropic_api_key": "",
    "deepseek_api_key": "",
    "groq_api_key": "",
    "openrouter_api_key": "",
    "custom_base_url": "",
    "custom_model_name": "",
    "custom_api_key": "",
    "temperature": "0.75",
    "min_delay": "3",
    "max_delay": "8",
    "simulate_typing": "1",
    "split_messages": "1",
    "human_takeover_minutes": "15",
    "is_paused": "0",
    "pause_until": "0",
    "filter_sensitive": "1",
    "custom_prompt": ""
}

class Database:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executescript(CREATE_TABLES_SQL)
            # Ensure default settings exist
            for key, val in DEFAULT_SETTINGS.items():
                await db.execute(
                    "INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)",
                    (key, val)
                )
            await db.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return row[0] if row else default

    async def get_all_settings(self) -> Dict[str, str]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT key, value FROM settings") as cursor:
                rows = await cursor.fetchall()
                res = {k: v for k, v in DEFAULT_SETTINGS.items()}
                for k, v in rows:
                    res[k] = v
                return res

    async def set_setting(self, key: str, value: str):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                (key, str(value))
            )
            await db.commit()

    async def update_settings(self, settings: Dict[str, Any]):
        async with aiosqlite.connect(self.db_path) as db:
            for k, v in settings.items():
                await db.execute(
                    "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
                    (k, str(v))
                )
            await db.commit()

    async def get_or_create_chat(self, chat_id: int, chat_title: str = "", username: str = "") -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM chats WHERE chat_id = ?", (chat_id,)) as cursor:
                row = await cursor.fetchone()
                if row:
                    return dict(row)
                # Create new chat (default enabled)
                await db.execute(
                    "INSERT INTO chats (chat_id, chat_title, username, is_enabled) VALUES (?, ?, ?, 1)",
                    (chat_id, chat_title, username)
                )
                await db.commit()
                return {
                    "chat_id": chat_id,
                    "chat_title": chat_title,
                    "username": username,
                    "is_enabled": 1,
                    "last_human_message_time": 0,
                    "last_bot_message_time": 0,
                    "notes": ""
                }

    async def set_chat_enabled(self, chat_id: int, is_enabled: bool):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE chats SET is_enabled = ? WHERE chat_id = ?",
                (1 if is_enabled else 0, chat_id)
            )
            await db.commit()

    async def list_chats(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM chats ORDER BY last_human_message_time DESC") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def record_human_message(self, chat_id: int, sender_id: int, sender_name: str, text: str, is_outgoing: bool = False):
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO message_history (chat_id, sender_id, sender_name, text, timestamp, is_outgoing) VALUES (?, ?, ?, ?, ?, ?)",
                (chat_id, sender_id, sender_name, text, now, 1 if is_outgoing else 0)
            )
            if is_outgoing:
                # If the human himself sent an outgoing message, update last_human_message_time for takeover
                await db.execute(
                    "UPDATE chats SET last_human_message_time = ? WHERE chat_id = ?",
                    (now, chat_id)
                )
            await db.commit()

    async def record_bot_message(self, chat_id: int, text: str):
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO message_history (chat_id, sender_id, sender_name, text, timestamp, is_outgoing) VALUES (?, ?, ?, ?, ?, 1)",
                (chat_id, 0, "Bot (Andrey)", text, now)
            )
            await db.execute(
                "UPDATE chats SET last_bot_message_time = ? WHERE chat_id = ?",
                (now, chat_id)
            )
            await db.commit()

    async def get_recent_history(self, chat_id: int, limit: int = 15) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                "SELECT * FROM message_history WHERE chat_id = ? ORDER BY timestamp DESC LIMIT ?",
                (chat_id, limit)
            ) as cursor:
                rows = await cursor.fetchall()
                # Return in chronological order
                return [dict(r) for r in reversed(rows)]

    async def log_event(self, level: str, message: str, details: str = ""):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO logs (timestamp, level, message, details) VALUES (?, ?, ?, ?)",
                (time.time(), level, message, details)
            )
            await db.commit()

    async def get_stats(self) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT COUNT(*) FROM message_history WHERE is_outgoing = 1") as c1:
                total_sent = (await c1.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM message_history WHERE is_outgoing = 0") as c2:
                total_received = (await c2.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM chats") as c3:
                total_chats = (await c3.fetchone())[0]
            async with db.execute("SELECT COUNT(*) FROM chats WHERE is_enabled = 1") as c4:
                active_chats = (await c4.fetchone())[0]
            return {
                "total_sent": total_sent,
                "total_received": total_received,
                "total_chats": total_chats,
                "active_chats": active_chats
            }

db = Database()
