"""Persistent Firestore storage with transactions for bot state."""
import hashlib
import json
import secrets
import time

from cryptography.fernet import Fernet
from google.cloud import firestore
from google.cloud.firestore_v1.base_query import FieldFilter
from google.oauth2 import service_account

from core.settings import validate_settings


class FirestoreDatabase:
    backend = "firebase"
    persistent = True

    def __init__(self, *, project, database, namespace, defaults, encryption_key,
                 service_account_json="", owner_id=None, credentials=None):
        if not encryption_key:
            raise RuntimeError("SETTINGS_ENCRYPTION_KEY is required for Firestore storage.")
        self.cipher = Fernet(encryption_key.encode("ascii"))
        if service_account_json:
            try:
                info = json.loads(service_account_json)
                credentials = service_account.Credentials.from_service_account_info(info)
            except (ValueError, KeyError, TypeError):
                raise RuntimeError("Invalid FIREBASE_SERVICE_ACCOUNT_JSON.") from None
        self.client = firestore.AsyncClient(project=project, database=database, credentials=credentials)
        self.root = self.client.collection("telegram_bots").document(namespace)
        self.meta = self.root.collection("state").document("settings")
        self.chats = self.root.collection("chats")
        self.accounts = self.root.collection("accounts")
        self.requests = self.root.collection("requests")
        self.updates = self.root.collection("updates")
        self.defaults = defaults
        self.owner_id = owner_id
        self.ready = False

    def _decode(self, data):
        result = dict(data)
        for key, value in result.items():
            if key.endswith("_api_key") and value:
                if not value.startswith("fernet:"):
                    raise RuntimeError("Unencrypted provider key found in Firestore.")
                result[key] = self.cipher.decrypt(value[7:].encode()).decode()
        return result

    def _encode(self, data):
        return {
            key: "fernet:" + self.cipher.encrypt(value.encode()).decode()
            if key.endswith("_api_key") and value else value
            for key, value in data.items()
        }

    def _settings(self, data):
        return self._decode({key: data.get(key, value) for key, value in self.defaults.items()})

    async def init(self):
        @firestore.async_transactional
        async def initialize(transaction):
            snapshot = await self.meta.get(transaction=transaction)
            stored = snapshot.to_dict() or {}
            self._settings(stored)
            owner = stored.get("business_owner_id", "")
            if self.owner_id:
                if owner and str(owner) != str(self.owner_id):
                    raise RuntimeError("Configured Business account differs from the Firestore owner.")
                owner = str(self.owner_id)
            transaction.set(self.meta, {
                **{key: value for key, value in self.defaults.items() if key not in stored},
                "business_owner_id": owner,
                "business_owner_verified": "1" if self.owner_id else stored.get("business_owner_verified", "0"),
                "settings_revision": str(stored.get("settings_revision", "0")),
            }, merge=True)
        await initialize(self.client.transaction())
        self.ready = True

    async def close(self):
        import inspect
        result = self.client.close()
        if inspect.isawaitable(result):
            await result
        self.ready = False

    async def store_update(self, update):
        reference = self.updates.document(str(update["update_id"]))

        @firestore.async_transactional
        async def store(transaction):
            existing = await reference.get(transaction=transaction)
            if not existing.exists:
                transaction.set(reference, {
                    "payload": self.cipher.encrypt(json.dumps(update).encode()).decode(),
                    "status": "pending", "lease_until": 0, "attempts": 0, "created_at": time.time(),
                })
        await store(self.client.transaction())

    async def claim_updates(self, limit=128, lease_seconds=180):
        query = self.updates.where(filter=FieldFilter("status", "in", ["pending", "processing"])).where(
            filter=FieldFilter("lease_until", "<=", time.time())
        ).order_by("lease_until").limit(limit)
        claimed = []
        async for snapshot in query.stream():
            reference = snapshot.reference

            @firestore.async_transactional
            async def claim(transaction):
                current = (await reference.get(transaction=transaction)).to_dict()
                now = time.time()
                if not current or current["status"] not in ("pending", "processing") or current["lease_until"] > now or current["attempts"] >= 3:
                    return None
                token = secrets.token_hex(12)
                transaction.update(reference, {
                    "status": "processing", "lease_until": now + lease_seconds,
                    "lease_token": token, "attempts": current["attempts"] + 1,
                })
                return json.loads(self.cipher.decrypt(current["payload"].encode())), token
            result = await claim(self.client.transaction())
            if result:
                claimed.append(result)
        return claimed

    async def finish_update(self, update_id, lease_token, error_type=None):
        reference = self.updates.document(str(update_id))

        @firestore.async_transactional
        async def finish(transaction):
            current = (await reference.get(transaction=transaction)).to_dict()
            if not current or current.get("lease_token") != lease_token or current["status"] != "processing":
                return
            if error_type == "Interrupted":
                transaction.update(reference, {"status": "pending", "lease_until": 0, "attempts": max(0, current["attempts"] - 1)})
            elif error_type:
                transaction.update(reference, {
                    "status": "failed" if current["attempts"] >= 3 else "pending",
                    "lease_until": time.time() + current["attempts"] * 5,
                })
            else:
                transaction.update(reference, {"status": "done", "payload": firestore.DELETE_FIELD})
        await finish(self.client.transaction())

    async def get_setting(self, key, default=""):
        data = (await self.meta.get()).to_dict() or {}
        return self._decode({key: data.get(key, default)})[key]

    async def get_all_settings(self):
        return self._settings((await self.meta.get()).to_dict() or {})

    async def set_setting(self, key, value):
        await self.update_settings({key: value})

    async def update_settings(self, settings):
        @firestore.async_transactional
        async def update(transaction):
            data = (await self.meta.get(transaction=transaction)).to_dict() or {}
            current = self._settings(data)
            changes = validate_settings(settings, current)
            if not changes:
                return
            if any(current.get(key) != value for key, value in changes.items()):
                changes["settings_revision"] = str(int(data.get("settings_revision", "0")) + 1)
            transaction.update(self.meta, self._encode(changes))
        await update(self.client.transaction())

    async def toggle_pause(self):
        @firestore.async_transactional
        async def toggle(transaction):
            data = (await self.meta.get(transaction=transaction)).to_dict()
            paused = data["is_paused"] != "1"
            transaction.update(self.meta, {
                "is_paused": "1" if paused else "0",
                "settings_revision": str(int(data.get("settings_revision", "0")) + 1),
            })
            return paused
        return await toggle(self.client.transaction())

    async def business_owner(self):
        data = (await self.meta.get()).to_dict() or {}
        return int(data["business_owner_id"]) if data.get("business_owner_verified") == "1" and data.get("business_owner_id") else None

    def _request_ref(self, connection_id):
        return self.requests.document(hashlib.sha256(connection_id.encode()).hexdigest())

    async def add_business_request(self, connection_id, user_id, name):
        reference = self._request_ref(connection_id)

        @firestore.async_transactional
        async def add(transaction):
            state = (await self.meta.get(transaction=transaction)).to_dict()
            old = (await reference.get(transaction=transaction)).to_dict()
            if state.get("business_owner_verified") == "1" or state.get("business_owner_id", "") not in ("", str(user_id)):
                return None
            is_new = not old or old["created_at"] < time.time() - 86400
            request_id = secrets.token_urlsafe(12) if is_new else old["id"]
            transaction.set(reference, {
                "id": request_id, "connection_id": connection_id, "user_id": user_id,
                "name": name, "created_at": time.time(),
            })
            return request_id if is_new else None
        return await add(self.client.transaction())

    async def business_requests(self, request_id=None):
        query = self.requests
        if request_id is not None:
            query = query.where(filter=FieldFilter("id", "==", request_id)).limit(1)
        else:
            query = query.order_by("created_at", direction=firestore.Query.DESCENDING).limit(8)
        return [
            snapshot.to_dict() async for snapshot in query.stream()
            if snapshot.to_dict()["created_at"] >= time.time() - 86400
        ]

    async def remove_business_request(self, connection_id):
        await self._request_ref(connection_id).delete()

    async def approve_business_request(self, request_id, user_id, account):
        reference = self._request_ref(account["connection_id"])

        @firestore.async_transactional
        async def approve(transaction):
            state = (await self.meta.get(transaction=transaction)).to_dict()
            request = (await reference.get(transaction=transaction)).to_dict()
            if state.get("business_owner_verified") == "1" or state.get("business_owner_id", "") not in ("", str(user_id)):
                return False
            if not request or request["id"] != request_id or request["user_id"] != user_id or request["created_at"] < time.time() - 86400:
                return False
            transaction.update(self.meta, {"business_owner_id": str(user_id), "business_owner_verified": "1"})
            transaction.set(self.accounts.document(str(user_id)), {**account, "updated_at": time.time()})
            transaction.delete(reference)
            return True
        return await approve(self.client.transaction())

    async def save_business_account(self, account):
        reference = self.accounts.document(str(account["user_id"]))

        @firestore.async_transactional
        async def save(transaction):
            old = (await reference.get(transaction=transaction)).to_dict() or {}
            if old.get("connected_at") is None or account["connected_at"] >= old["connected_at"]:
                transaction.set(reference, {**account, "updated_at": time.time()})
        await save(self.client.transaction())

    async def save_account_identity(self, user_id, display_name, username):
        await self.accounts.document(str(user_id)).set({
            "user_id": user_id, "display_name": display_name, "username": username,
        }, merge=True)

    async def business_account(self, user_id):
        return (await self.accounts.document(str(user_id)).get()).to_dict()

    async def last_business_connection(self):
        owner = await self.business_owner()
        if owner:
            account = await self.business_account(owner)
            return account.get("connection_id") if account else None
        return None

    @staticmethod
    def _new_chat(chat_id, chat_title, username):
        return {
            "chat_id": chat_id, "chat_title": chat_title, "username": username,
            "is_enabled": 1, "revision": 0, "last_human_message_time": 0,
            "last_bot_message_time": 0, "notes": "", "latest_incoming": None,
            "latest_incoming_order": [0, 0], "total_sent": 0, "total_received": 0,
            "last_reply_error": "", "last_reply_error_at": 0,
        }

    async def get_or_create_chat(self, chat_id, chat_title="", username=""):
        reference = self.chats.document(str(chat_id))

        @firestore.async_transactional
        async def get(transaction):
            old = (await reference.get(transaction=transaction)).to_dict()
            data = {**(old or self._new_chat(chat_id, chat_title, username)), "chat_title": chat_title, "username": username}
            transaction.set(reference, data)
            return data
        return await get(self.client.transaction())

    async def set_chat_enabled(self, chat_id, is_enabled):
        reference = self.chats.document(str(chat_id))

        @firestore.async_transactional
        async def toggle(transaction):
            old = (await reference.get(transaction=transaction)).to_dict()
            if not old:
                return False
            transaction.update(reference, {
                "is_enabled": int(is_enabled),
                "revision": old.get("revision", 0) + int(bool(old["is_enabled"]) != is_enabled),
            })
            return True
        return await toggle(self.client.transaction())

    async def list_chats(self):
        data = [snapshot.to_dict() async for snapshot in self.chats.stream()]
        return sorted(data, key=lambda chat: max(chat.get("last_human_message_time", 0), chat.get("last_bot_message_time", 0)), reverse=True)

    async def set_chat_error(self, chat_id, message):
        await self.chats.document(str(chat_id)).update({
            "last_reply_error": message, "last_reply_error_at": time.time() if message else 0,
        })

    def _message_ref(self, chat_id, connection_id, message_id):
        prefix = hashlib.sha256((connection_id or "local").encode()).hexdigest()[:24]
        suffix = str(message_id) if message_id is not None else secrets.token_hex(12)
        return self.chats.document(str(chat_id)).collection("history").document(f"{prefix}_{suffix}")

    async def record_human_message(self, chat_id, sender_id, sender_name, text, is_outgoing=False,
                                   *, message_id=None, connection_id=None, timestamp=None):
        timestamp = time.time() if timestamp is None else timestamp
        chat_ref = self.chats.document(str(chat_id))
        message_ref = self._message_ref(chat_id, connection_id, message_id)

        @firestore.async_transactional
        async def record(transaction):
            state = (await self.meta.get(transaction=transaction)).to_dict()
            chat = (await chat_ref.get(transaction=transaction)).to_dict()
            existing = await message_ref.get(transaction=transaction)
            if not chat:
                return None
            if is_outgoing:
                transaction.update(chat_ref, {"last_human_message_time": max(chat.get("last_human_message_time", 0), timestamp)})
            if not text or not chat["is_enabled"] or state["is_paused"] == "1" or existing.exists:
                return None
            transaction.set(message_ref, {
                "chat_id": chat_id, "sender_id": sender_id, "sender_name": sender_name,
                "text": text, "timestamp": timestamp, "is_outgoing": int(is_outgoing),
                "telegram_message_id": message_id, "connection_id": connection_id,
            })
            if not is_outgoing:
                fields = {"total_received": chat.get("total_received", 0) + 1}
                order = [timestamp, message_id or 0]
                if order >= chat.get("latest_incoming_order", [0, 0]):
                    fields.update(latest_incoming=message_ref.id, latest_incoming_order=order)
                transaction.update(chat_ref, fields)
            return message_ref.id
        return await record(self.client.transaction())

    async def record_bot_message(self, chat_id, text, *, message_id=None, connection_id=None):
        chat_ref = self.chats.document(str(chat_id))
        message_ref = self._message_ref(chat_id, connection_id, message_id)

        @firestore.async_transactional
        async def record(transaction):
            chat = (await chat_ref.get(transaction=transaction)).to_dict()
            existing = await message_ref.get(transaction=transaction)
            if existing.exists:
                return
            now = time.time()
            transaction.set(message_ref, {
                "chat_id": chat_id, "sender_id": 0, "sender_name": "Андрей · автоответ",
                "text": text, "timestamp": now, "is_outgoing": 1,
                "telegram_message_id": message_id, "connection_id": connection_id,
            })
            transaction.update(chat_ref, {"last_bot_message_time": now, "total_sent": chat.get("total_sent", 0) + 1})
        await record(self.client.transaction())

    async def get_recent_history(self, chat_id, limit=15, *, connection_id=None):
        query = self.chats.document(str(chat_id)).collection("history").where(
            filter=FieldFilter("connection_id", "==", connection_id)
        ).order_by("timestamp", direction=firestore.Query.DESCENDING).limit(limit)
        rows = [{"id": snapshot.id, **snapshot.to_dict()} async for snapshot in query.stream()]
        return list(reversed(rows))

    async def reply_state(self, chat_id, connection_id):
        state = (await self.meta.get()).to_dict() or {}
        chat = (await self.chats.document(str(chat_id)).get()).to_dict()
        if not chat:
            return {}
        return {
            "is_enabled": chat["is_enabled"], "chat_revision": chat.get("revision", 0),
            "settings_revision": str(state.get("settings_revision", "0")),
            "last_human_message_time": chat.get("last_human_message_time", 0),
            "is_paused": state.get("is_paused", "0"),
            "human_takeover_minutes": state.get("human_takeover_minutes", "15"),
            "latest_incoming": chat.get("latest_incoming"),
        }

    async def delete_business_messages(self, connection_id, chat_id, message_ids):
        chat_ref = self.chats.document(str(chat_id))
        for message_id in message_ids:
            reference = self._message_ref(chat_id, connection_id, message_id)

            @firestore.async_transactional
            async def remove(transaction):
                message = (await reference.get(transaction=transaction)).to_dict()
                chat = (await chat_ref.get(transaction=transaction)).to_dict()
                if not message or not chat:
                    return
                fields = {}
                if not message["is_outgoing"]:
                    fields["total_received"] = max(0, chat.get("total_received", 0) - 1)
                    if chat.get("latest_incoming") == reference.id:
                        fields["latest_incoming"] = None
                elif message["sender_id"] == 0:
                    fields["total_sent"] = max(0, chat.get("total_sent", 0) - 1)
                transaction.delete(reference)
                if fields:
                    transaction.update(chat_ref, fields)
            await remove(self.client.transaction())

    async def log_event(self, level, message, details=""):
        await self.root.collection("logs").add({
            "timestamp": time.time(), "level": level, "message": message, "details": details,
        })

    async def get_stats(self):
        chats = await self.list_chats()
        return {
            "total_sent": sum(chat.get("total_sent", 0) for chat in chats),
            "total_received": sum(chat.get("total_received", 0) for chat in chats),
            "total_chats": len(chats),
            "active_chats": sum(bool(chat["is_enabled"]) for chat in chats),
        }
