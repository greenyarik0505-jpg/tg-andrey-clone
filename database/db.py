import aiosqlite
import json
import os
import secrets
import time
from pathlib import Path
from typing import Dict, Any, List

from cryptography.fernet import Fernet, InvalidToken

from config import (
    ADMIN_ID, BOT_TOKEN, BUSINESS_USER_ID, DB_PATH, FIREBASE_PROJECT_ID,
    FIREBASE_SERVICE_ACCOUNT_JSON, FIRESTORE_DATABASE_ID, SETTINGS_ENCRYPTION_KEY,
)
from core.settings import NUMERIC_LIMITS, TOGGLES, number, reply_delays, validate_settings

ENCRYPTED_PREFIX = "fernet:"

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

CREATE TABLE IF NOT EXISTS business_requests (
    id TEXT PRIMARY KEY,
    connection_id TEXT UNIQUE NOT NULL,
    user_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS business_accounts (
    user_id INTEGER PRIMARY KEY,
    display_name TEXT NOT NULL,
    username TEXT,
    connection_id TEXT,
    is_enabled INTEGER,
    can_reply INTEGER,
    connected_at REAL,
    updated_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS telegram_updates (
    update_id INTEGER PRIMARY KEY,
    payload TEXT,
    status TEXT NOT NULL,
    lease_until REAL NOT NULL DEFAULT 0,
    lease_token TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
"""

DEFAULT_SETTINGS = {
    "custom_base_url": "",
    "custom_model_name": "",
    "custom_api_key": "",
    "temperature": "0.75",
    "min_delay": "6",
    "max_delay": "14",
    "simulate_typing": "1",
    "split_messages": "1",
    "human_takeover_minutes": "15",
    "is_paused": "0",
    "pause_until": "0",
    "custom_prompt": ""
}

class Database:
    backend = "sqlite"
    persistent = False

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.cipher = None
        self.ready = False

    def _load_cipher(self, encrypted_values_exist: bool):
        if SETTINGS_ENCRYPTION_KEY:
            return Fernet(SETTINGS_ENCRYPTION_KEY.encode("ascii"))
        path = Path(f"{self.db_path}.settings.key")
        if not path.exists():
            if encrypted_values_exist:
                raise RuntimeError("Missing settings encryption key. Restore the key used for this database.")
            try:
                with os.fdopen(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600), "wb") as file:
                    file.write(Fernet.generate_key())
            except FileExistsError:
                pass
        return Fernet(path.read_bytes().strip())

    def _decode(self, key, value):
        if key.endswith("_api_key") and value and value.startswith(ENCRYPTED_PREFIX):
            try:
                return self.cipher.decrypt(value[len(ENCRYPTED_PREFIX):].encode("ascii")).decode("utf-8")
            except InvalidToken:
                raise RuntimeError("Cannot decrypt provider keys with the configured encryption key.") from None
        return value

    def _encode(self, key, value):
        value = str(value)
        if key.endswith("_api_key") and value:
            return ENCRYPTED_PREFIX + self.cipher.encrypt(value.encode("utf-8")).decode("ascii")
        return value

    async def init(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("PRAGMA journal_mode=WAL")
            await db.executescript(CREATE_TABLES_SQL)
            await db.execute("BEGIN IMMEDIATE")
            await db.execute("PRAGMA secure_delete=ON")
            rows = dict(await db.execute_fetchall("SELECT key, value FROM settings"))
            owner = rows.get("business_owner_id")
            has_chats = (await db.execute_fetchall("SELECT EXISTS(SELECT 1 FROM chats)"))[0][0]
            verified = rows.get("business_owner_verified", "1" if owner and has_chats else "0") == "1"
            if not verified:
                owner = (owner or rows.get("business_user_id")) if has_chats else None
            if BUSINESS_USER_ID:
                if owner and owner != str(BUSINESS_USER_ID):
                    raise RuntimeError("BUSINESS_USER_ID does not match the database owner. Use a separate DB_PATH for another account.")
                owner, verified = str(BUSINESS_USER_ID), True
            if not rows.get("business_owner_id"):
                await db.execute("UPDATE chats SET is_enabled = 0")

            key_rows = [(key, value) for key, value in rows.items() if key.endswith("_api_key") and value]
            self.cipher = self._load_cipher(any(value.startswith(ENCRYPTED_PREFIX) for _, value in key_rows))
            migrated = {}
            for key, value in key_rows:
                if value.startswith(ENCRYPTED_PREFIX):
                    self._decode(key, value)
                else:
                    migrated[key] = value

            columns = await db.execute_fetchall("PRAGMA table_info(message_history)")
            if not any(column[1] == "telegram_message_id" for column in columns):
                await db.execute("ALTER TABLE message_history ADD COLUMN telegram_message_id INTEGER")
            if not any(column[1] == "connection_id" for column in columns):
                await db.execute("ALTER TABLE message_history ADD COLUMN connection_id TEXT")
            await db.execute(
                "CREATE UNIQUE INDEX IF NOT EXISTS history_telegram_id "
                "ON message_history(connection_id, chat_id, telegram_message_id)"
            )
            await db.execute("CREATE INDEX IF NOT EXISTS history_chat ON message_history(chat_id, id)")
            chat_columns = await db.execute_fetchall("PRAGMA table_info(chats)")
            if not any(column[1] == "revision" for column in chat_columns):
                await db.execute("ALTER TABLE chats ADD COLUMN revision INTEGER NOT NULL DEFAULT 0")
            if not any(column[1] == "last_reply_error" for column in chat_columns):
                await db.execute("ALTER TABLE chats ADD COLUMN last_reply_error TEXT NOT NULL DEFAULT ''")
                await db.execute("ALTER TABLE chats ADD COLUMN last_reply_error_at REAL NOT NULL DEFAULT 0")
            await db.execute("INSERT OR IGNORE INTO settings (key, value) VALUES ('settings_revision', '0')")
            await db.executemany("INSERT OR IGNORE INTO settings (key, value) VALUES (?, ?)", DEFAULT_SETTINGS.items())
            normalized = {key: str(number(rows, key)) for key in NUMERIC_LIMITS}
            normalized["max_delay"] = str(reply_delays(rows)[1])
            if not rows.get("reply_delay_v2") and (normalized["min_delay"], normalized["max_delay"]) == ("3.0", "8.0"):
                normalized.update(min_delay=DEFAULT_SETTINGS["min_delay"], max_delay=DEFAULT_SETTINGS["max_delay"])
            for key in TOGGLES:
                if rows.get(key) not in {"0", "1"}:
                    normalized[key] = DEFAULT_SETTINGS[key]
            await self._write_settings(db, {
                **migrated,
                **normalized,
                "business_owner_id": owner or "",
                "business_owner_verified": "1" if verified else "0",
                "reply_delay_v2": "1",
            })
            await db.commit()
            if migrated:
                await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        self.ready = True

    async def close(self):
        self.ready = False

    async def store_update(self, update):
        payload = self.cipher.encrypt(json.dumps(update).encode()).decode()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT OR IGNORE INTO telegram_updates (update_id, payload, status, created_at) VALUES (?, ?, 'pending', ?)",
                (update["update_id"], payload, time.time()),
            )
            await db.commit()

    async def claim_updates(self, limit=128, lease_seconds=180):
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            rows = await db.execute_fetchall(
                """SELECT update_id, payload FROM telegram_updates
                    WHERE status IN ('pending', 'processing') AND lease_until <= ? AND attempts < 3
                    ORDER BY update_id LIMIT ?""", (now, limit),
            )
            claimed = []
            for update_id, payload in rows:
                lease_token = secrets.token_hex(12)
                await db.execute(
                    """UPDATE telegram_updates SET status = 'processing', lease_until = ?,
                        lease_token = ?, attempts = attempts + 1 WHERE update_id = ?""",
                    (now + lease_seconds, lease_token, update_id),
                )
                claimed.append((json.loads(self.cipher.decrypt(payload.encode())), lease_token))
            await db.commit()
            return claimed

    async def finish_update(self, update_id, lease_token, error_type=None):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            rows = await db.execute_fetchall(
                "SELECT attempts FROM telegram_updates WHERE update_id = ? AND lease_token = ? AND status = 'processing'",
                (update_id, lease_token),
            )
            if not rows:
                return
            attempts = rows[0][0]
            if error_type == "Interrupted":
                await db.execute(
                    "UPDATE telegram_updates SET status = 'pending', lease_until = 0, attempts = ? WHERE update_id = ?",
                    (max(0, attempts - 1), update_id),
                )
            elif error_type:
                await db.execute(
                    "UPDATE telegram_updates SET status = ?, lease_until = ? WHERE update_id = ?",
                    ("failed" if attempts >= 3 else "pending", time.time() + attempts * 5, update_id),
                )
            else:
                await db.execute(
                    "UPDATE telegram_updates SET status = 'done', payload = NULL WHERE update_id = ?", (update_id,),
                )
            await db.commit()

    async def get_setting(self, key: str, default: str = "") -> str:
        async with aiosqlite.connect(self.db_path) as db:
            async with db.execute("SELECT value FROM settings WHERE key = ?", (key,)) as cursor:
                row = await cursor.fetchone()
                return self._decode(key, row[0]) if row and row[0] is not None else default

    async def get_all_settings(self) -> Dict[str, str]:
        async with aiosqlite.connect(self.db_path) as db:
            return await self._read_settings(db)

    async def _read_settings(self, connection):
        rows = await connection.execute_fetchall("SELECT key, value FROM settings")
        return {**DEFAULT_SETTINGS, **{
            key: self._decode(key, value)
            for key, value in rows if key in DEFAULT_SETTINGS and value is not None
        }}

    async def _write_settings(self, connection, settings):
        await connection.executemany(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            [(key, self._encode(key, value)) for key, value in settings.items()],
        )

    async def set_setting(self, key: str, value: str):
        await self.update_settings({key: value})

    async def update_settings(self, settings: Dict[str, Any]):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            current = await self._read_settings(db)
            settings = validate_settings(settings, current)
            await self._write_settings(db, settings)
            if any(current.get(key) != value for key, value in settings.items()):
                await db.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key = 'settings_revision'")
            await db.commit()

    async def toggle_pause(self):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            (row,) = await db.execute_fetchall("SELECT value FROM settings WHERE key = 'is_paused'")
            paused = row[0] != "1"
            await self._write_settings(db, {"is_paused": "1" if paused else "0"})
            await db.execute("UPDATE settings SET value = CAST(value AS INTEGER) + 1 WHERE key = 'settings_revision'")
            await db.commit()
            return paused

    async def business_owner(self):
        async with aiosqlite.connect(self.db_path) as db:
            rows = dict(await db.execute_fetchall(
                "SELECT key, value FROM settings WHERE key IN ('business_owner_id', 'business_owner_verified')"
            ))
        return int(rows["business_owner_id"]) if rows.get("business_owner_verified") == "1" else None

    async def add_business_request(self, connection_id, user_id, name):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            rows = dict(await db.execute_fetchall(
                "SELECT key, value FROM settings WHERE key IN ('business_owner_id', 'business_owner_verified')"
            ))
            if rows.get("business_owner_verified") == "1" or rows.get("business_owner_id") not in ("", str(user_id)):
                return None
            now = time.time()
            await db.execute("DELETE FROM business_requests WHERE created_at < ?", (now - 86400,))
            request_id = secrets.token_urlsafe(12)
            cursor = await db.execute(
                "INSERT OR IGNORE INTO business_requests (id, connection_id, user_id, name, created_at) VALUES (?, ?, ?, ?, ?)",
                (request_id, connection_id, user_id, name, now),
            )
            if cursor.rowcount == 0:
                await db.execute(
                    "UPDATE business_requests SET name = ?, created_at = ? WHERE connection_id = ? AND user_id = ?",
                    (name, now, connection_id, user_id),
                )
            await db.commit()
            return request_id if cursor.rowcount == 1 else None

    async def business_requests(self, request_id=None):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """SELECT id, connection_id, user_id, name FROM business_requests
                WHERE created_at >= ? AND (? IS NULL OR id = ?) ORDER BY created_at DESC LIMIT 8""",
                (time.time() - 86400, request_id, request_id),
            )
            return [dict(row) for row in rows]

    async def remove_business_request(self, connection_id):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("DELETE FROM business_requests WHERE connection_id = ?", (connection_id,))
            await db.commit()

    async def approve_business_request(self, request_id, user_id, account):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute("BEGIN IMMEDIATE")
            rows = dict(await db.execute_fetchall(
                "SELECT key, value FROM settings WHERE key IN ('business_owner_id', 'business_owner_verified')"
            ))
            if rows.get("business_owner_verified") == "1" or rows.get("business_owner_id") not in ("", str(user_id)):
                return False
            request = await db.execute_fetchall(
                "SELECT 1 FROM business_requests WHERE id = ? AND user_id = ? AND created_at >= ?",
                (request_id, user_id, time.time() - 86400),
            )
            if not request:
                return False
            await self._write_settings(db, {"business_owner_id": user_id, "business_owner_verified": "1"})
            await self._save_business_account(db, account)
            await db.execute("DELETE FROM business_requests")
            await db.commit()
            return True

    async def _save_business_account(self, connection, account):
        await connection.execute(
            """INSERT INTO business_accounts
                (user_id, display_name, username, connection_id, is_enabled, can_reply, connected_at, updated_at)
                VALUES (:user_id, :display_name, :username, :connection_id, :is_enabled, :can_reply, :connected_at, :updated_at)
                ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name, username = excluded.username,
                    connection_id = excluded.connection_id, is_enabled = excluded.is_enabled,
                    can_reply = excluded.can_reply, connected_at = excluded.connected_at, updated_at = excluded.updated_at
                WHERE business_accounts.connected_at IS NULL OR excluded.connected_at >= business_accounts.connected_at""",
            {**account, "updated_at": time.time()},
        )

    async def save_business_account(self, account):
        async with aiosqlite.connect(self.db_path) as db:
            await self._save_business_account(db, account)
            await db.commit()

    async def save_account_identity(self, user_id, display_name, username):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT INTO business_accounts (user_id, display_name, username, updated_at)
                    VALUES (?, ?, ?, ?) ON CONFLICT(user_id) DO UPDATE SET
                    display_name = excluded.display_name, username = excluded.username""",
                (user_id, display_name, username, time.time()),
            )
            await db.commit()

    async def business_account(self, user_id):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall("SELECT * FROM business_accounts WHERE user_id = ?", (user_id,))
            return dict(rows[0]) if rows else None

    async def last_business_connection(self):
        async with aiosqlite.connect(self.db_path) as db:
            rows = await db.execute_fetchall(
                "SELECT connection_id FROM message_history WHERE connection_id IS NOT NULL ORDER BY id DESC LIMIT 1"
            )
            return rows[0][0] if rows else None

    async def get_or_create_chat(self, chat_id: int, chat_title: str = "", username: str = "") -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            await db.execute(
                "INSERT INTO chats (chat_id, chat_title, username, is_enabled) VALUES (?, ?, ?, 1) "
                "ON CONFLICT(chat_id) DO UPDATE SET chat_title = excluded.chat_title, username = excluded.username",
                (chat_id, chat_title, username),
            )
            (row,) = await db.execute_fetchall("SELECT * FROM chats WHERE chat_id = ?", (chat_id,))
            await db.commit()
            return dict(row)

    async def set_chat_enabled(self, chat_id: int, is_enabled: bool):
        async with aiosqlite.connect(self.db_path) as db:
            cursor = await db.execute(
                """UPDATE chats SET revision = revision + CASE WHEN is_enabled != ? THEN 1 ELSE 0 END,
                    is_enabled = ? WHERE chat_id = ?""",
                (int(is_enabled), int(is_enabled), chat_id)
            )
            await db.commit()
            return cursor.rowcount == 1

    async def set_chat_error(self, chat_id, message):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "UPDATE chats SET last_reply_error = ?, last_reply_error_at = ? WHERE chat_id = ?",
                (message, time.time() if message else 0, chat_id),
            )
            await db.commit()

    async def list_chats(self) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute("SELECT * FROM chats ORDER BY MAX(last_human_message_time, last_bot_message_time) DESC, chat_id") as cursor:
                rows = await cursor.fetchall()
                return [dict(r) for r in rows]

    async def record_human_message(
        self, chat_id: int, sender_id: int, sender_name: str, text: str, is_outgoing: bool = False,
        *, message_id=None, connection_id=None, timestamp=None,
    ):
        timestamp = time.time() if timestamp is None else timestamp
        async with aiosqlite.connect(self.db_path) as db:
            if is_outgoing:
                await db.execute(
                    "UPDATE chats SET last_human_message_time = MAX(COALESCE(last_human_message_time, 0), ?) WHERE chat_id = ?",
                    (timestamp, chat_id),
                )
            cursor = None
            if text:
                cursor = await db.execute(
                    """INSERT OR IGNORE INTO message_history
                        (chat_id, sender_id, sender_name, text, timestamp, is_outgoing, telegram_message_id, connection_id)
                        SELECT ?, ?, ?, ?, ?, ?, ?, ?
                        WHERE EXISTS(SELECT 1 FROM chats WHERE chat_id = ? AND is_enabled = 1)
                        AND NOT EXISTS(SELECT 1 FROM settings WHERE key = 'is_paused' AND value = '1')""",
                    (chat_id, sender_id, sender_name, text, timestamp, int(is_outgoing), message_id, connection_id, chat_id),
                )
            await db.commit()
            return cursor.lastrowid if cursor is not None and cursor.rowcount == 1 else None

    async def record_bot_message(self, chat_id: int, text: str, *, message_id=None, connection_id=None):
        now = time.time()
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                """INSERT OR IGNORE INTO message_history
                (chat_id, sender_id, sender_name, text, timestamp, is_outgoing, telegram_message_id, connection_id)
                VALUES (?, 0, 'Bot', ?, ?, 1, ?, ?)""",
                (chat_id, text, now, message_id, connection_id),
            )
            await db.execute(
                "UPDATE chats SET last_bot_message_time = ? WHERE chat_id = ?",
                (now, chat_id)
            )
            await db.commit()

    async def get_recent_history(self, chat_id: int, limit: int = 15, *, connection_id=None) -> List[Dict[str, Any]]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            async with db.execute(
                """SELECT * FROM message_history WHERE chat_id = ? AND connection_id IS ?
                ORDER BY timestamp DESC, id DESC LIMIT ?""",
                (chat_id, connection_id, limit),
            ) as cursor:
                rows = await cursor.fetchall()
                # Return in chronological order
                return [dict(r) for r in reversed(rows)]

    async def reply_state(self, chat_id, connection_id):
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            rows = await db.execute_fetchall(
                """SELECT is_enabled, revision AS chat_revision,
                    COALESCE(last_human_message_time, 0) AS last_human_message_time,
                    (SELECT value FROM settings WHERE key = 'settings_revision') AS settings_revision,
                    (SELECT value FROM settings WHERE key = 'is_paused') AS is_paused,
                    (SELECT value FROM settings WHERE key = 'human_takeover_minutes') AS human_takeover_minutes,
                    (SELECT id FROM message_history WHERE chat_id = chats.chat_id AND connection_id = ?
                       AND is_outgoing = 0 ORDER BY timestamp DESC, id DESC LIMIT 1) AS latest_incoming
                    FROM chats WHERE chat_id = ?""",
                (connection_id, chat_id),
            )
            return dict(rows[0]) if rows else {}

    async def delete_business_messages(self, connection_id, chat_id, message_ids):
        async with aiosqlite.connect(self.db_path) as db:
            await db.executemany(
                "DELETE FROM message_history WHERE connection_id = ? AND chat_id = ? AND telegram_message_id = ?",
                [(connection_id, chat_id, message_id) for message_id in message_ids],
            )
            await db.commit()

    async def log_event(self, level: str, message: str, details: str = ""):
        async with aiosqlite.connect(self.db_path) as db:
            await db.execute(
                "INSERT INTO logs (timestamp, level, message, details) VALUES (?, ?, ?, ?)",
                (time.time(), level, message, details)
            )
            await db.commit()

    async def get_stats(self) -> Dict[str, Any]:
        async with aiosqlite.connect(self.db_path) as db:
            db.row_factory = aiosqlite.Row
            (stats,) = await db.execute_fetchall(
                """SELECT
                    (SELECT COUNT(*) FROM message_history WHERE is_outgoing = 1 AND sender_id = 0) AS total_sent,
                    (SELECT COUNT(*) FROM message_history WHERE is_outgoing = 0) AS total_received,
                    (SELECT COUNT(*) FROM chats) AS total_chats,
                    (SELECT COUNT(*) FROM chats WHERE is_enabled = 1) AS active_chats"""
            )
            return dict(stats)

if FIREBASE_PROJECT_ID:
    from database.firestore_db import FirestoreDatabase

    db = FirestoreDatabase(
        project=FIREBASE_PROJECT_ID,
        database=FIRESTORE_DATABASE_ID,
        namespace=BOT_TOKEN.split(":")[0] if BOT_TOKEN else str(ADMIN_ID),
        defaults=DEFAULT_SETTINGS,
        encryption_key=SETTINGS_ENCRYPTION_KEY,
        service_account_json=FIREBASE_SERVICE_ACCOUNT_JSON,
        owner_id=BUSINESS_USER_ID,
    )
else:
    db = Database()
