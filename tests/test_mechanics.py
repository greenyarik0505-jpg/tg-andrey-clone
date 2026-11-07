"""Regression coverage for ownership, pending replies, history and persona."""
import asyncio
import json
import tempfile
import time
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from bot import business
from core.llm_client import LLMManager, ReplyError
from database.db import Database
from webapp import server


def account():
    return SimpleNamespace(
        id="friend-connection", user=SimpleNamespace(id=42, full_name="Greg 2", username="friend"),
        date=datetime.fromtimestamp(100, timezone.utc), is_enabled=True,
        rights=SimpleNamespace(can_reply=True),
    )


class FakeBot:
    def __init__(self):
        self.connection = account()
        self.sent = []

    async def get_business_connection(self, business_connection_id):
        return self.connection

    async def send_message(self, **kwargs):
        self.sent.append(kwargs)
        return SimpleNamespace(message_id=1000 + len(self.sent))

    async def send_chat_action(self, **kwargs):
        pass

    async def get_me(self):
        return SimpleNamespace(username="test_bot", can_connect_to_business=True)


def incoming(message_id=1, sender_id=7):
    return SimpleNamespace(
        business_connection_id="friend-connection",
        from_user=SimpleNamespace(id=sender_id, full_name="Admin" if sender_id == 7 else "Greg 2", is_bot=False),
        sender_business_bot=None,
        chat=SimpleNamespace(id=7, full_name="Admin", username="admin", type="private"),
        text="Привет", caption=None, message_id=message_id,
        date=datetime.now(timezone.utc),
    )


class MechanicsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.owner_patch = patch("database.db.BUSINESS_USER_ID", None)
        self.owner_patch.start()
        self.db = Database(str(Path(self.directory.name) / "test.db"))
        await self.db.init()
        self.bot = FakeBot()
        self.patches = [
            patch.object(business, "db", self.db),
            patch.object(server, "db", self.db),
            patch.object(business, "BATCH_WINDOW_SECONDS", 0.02),
        ]
        for context in self.patches:
            context.start()
        business.connections.clear()
        business.pending_chats.clear()
        await self.db.update_settings({
            "custom_base_url": "https://provider.example/v1",
            "custom_model_name": "model",
            "min_delay": "0", "max_delay": "0", "simulate_typing": "0",
        })
        request_id = await self.db.add_business_request("friend-connection", 42, "Greg 2 (@friend)")
        await business.approve_account(request_id, self.bot)

    async def asyncTearDown(self):
        for context in reversed(self.patches):
            context.stop()
        self.owner_patch.stop()
        business.connections.clear()
        business.pending_chats.clear()
        server.app.dependency_overrides.clear()
        await self.db.close()
        self.directory.cleanup()

    async def test_approval_persists_friend_identity(self):
        self.assertEqual(await self.db.business_owner(), 42)
        stored = await self.db.business_account(42)
        self.assertEqual(stored["display_name"], "Greg 2")
        self.assertEqual(stored["connection_id"], "friend-connection")
        self.assertIsNone(await self.db.add_business_request("stranger", 99, "Other"))
        restored = Database(self.db.db_path)
        await restored.init()
        self.assertEqual(await restored.business_owner(), 42)
        self.assertEqual((await restored.business_account(42))["username"], "friend")

    async def test_admin_is_correspondent_not_automation_owner(self):
        with patch.object(business.llm_manager, "reply", new=AsyncMock(return_value=["Андрей"])) as model:
            await business.on_business_message(incoming(), self.bot)
        self.assertEqual(len(self.bot.sent), 1)
        self.assertEqual(self.bot.sent[0]["chat_id"], 7)
        self.assertEqual(self.bot.sent[0]["business_connection_id"], "friend-connection")
        self.assertEqual(self.bot.sent[0]["text"], "Андрей")
        history = model.call_args.args[1]
        self.assertEqual(history[-1]["is_outgoing"], 0)
        await business.on_business_message(incoming(2, sender_id=42), self.bot)
        with patch.object(business.llm_manager, "reply", new=AsyncMock()) as model:
            await business.on_business_message(incoming(3), self.bot)
        model.assert_not_called()

    async def test_duplicate_and_bot_echo_do_not_send_twice(self):
        event = incoming()
        with patch.object(business.llm_manager, "reply", new=AsyncMock(return_value=["Круто"])) as model:
            await business.on_business_message(event, self.bot)
            await business.on_business_message(event, self.bot)
            echo = incoming(2000, sender_id=42)
            echo.sender_business_bot = SimpleNamespace(id=123)
            await business.on_business_message(echo, self.bot)
        self.assertEqual(model.call_count, 1)
        self.assertEqual((await self.db.get_stats())["total_sent"], 1)

    async def test_pause_cycle_cancels_an_already_generated_reply(self):
        async def generate(*args):
            await self.db.toggle_pause()
            await self.db.toggle_pause()
            return ["Не отправлять"]
        with patch.object(business.llm_manager, "reply", new=generate):
            await business.on_business_message(incoming(), self.bot)
        self.assertEqual(await self.db.get_setting("is_paused"), "0")
        self.assertEqual(self.bot.sent, [])

    async def test_chat_toggle_cycle_cancels_pending_reply(self):
        async def generate(*args):
            await self.db.set_chat_enabled(7, False)
            await self.db.set_chat_enabled(7, True)
            return ["Не отправлять"]
        with patch.object(business.llm_manager, "reply", new=generate):
            await business.on_business_message(incoming(), self.bot)
        self.assertEqual(self.bot.sent, [])

    async def test_owner_manual_reply_interrupts_generation(self):
        async def generate(*args):
            await business.on_business_message(incoming(2, sender_id=42), self.bot)
            return ["Не отправлять"]
        with patch.object(business.llm_manager, "reply", new=generate):
            await business.on_business_message(incoming(), self.bot)
        self.assertEqual(self.bot.sent, [])

    async def test_provider_failure_is_visible_in_chat_details(self):
        with patch.object(business.llm_manager, "reply", new=AsyncMock(side_effect=ReplyError("Провайдер ограничил запросы."))):
            await business.on_business_message(incoming(), self.bot)
        self.assertEqual(self.bot.sent, [])
        self.assertEqual((await self.db.list_chats())[0]["last_reply_error"], "Провайдер ограничил запросы.")

    async def test_newest_message_batches_preceding_messages(self):
        with patch.object(business, "BATCH_WINDOW_SECONDS", 0.15), patch.object(
            business.llm_manager, "reply", new=AsyncMock(return_value=["Пон"])
        ) as model:
            first = asyncio.create_task(business.on_business_message(incoming(1), self.bot))
            await asyncio.sleep(0.04)
            second = incoming(2)
            second.text = "Пойдём играть?"
            await business.on_business_message(second, self.bot)
            await first
        self.assertEqual(model.call_count, 1)
        self.assertEqual([item["text"] for item in model.call_args.args[1]], ["Привет", "Пойдём играть?"])

    async def test_durable_inbox_deduplicates_and_recovers_interrupted_work(self):
        update = {"update_id": 100, "message": {"text": "example"}}
        await self.db.store_update(update)
        await self.db.store_update(update)
        first = await self.db.claim_updates()
        self.assertEqual(len(first), 1)
        self.assertEqual(first[0][0], update)
        self.assertEqual(await self.db.claim_updates(), [])
        await self.db.finish_update(100, first[0][1], "Interrupted")
        second = await self.db.claim_updates()
        self.assertEqual(len(second), 1)
        await self.db.finish_update(100, first[0][1])
        self.assertEqual(await self.db.claim_updates(), [])
        await self.db.finish_update(100, second[0][1])
        await self.db.store_update(update)
        self.assertEqual(await self.db.claim_updates(), [])

    async def test_disconnect_changes_the_connection_status(self):
        self.bot.connection.is_enabled = False
        with patch.object(self.bot, "send_message", new=AsyncMock()):
            await business.on_business_connection(self.bot.connection, self.bot)
        server.app.state.telegram_bot = self.bot
        server.app.dependency_overrides[server.require_admin] = lambda: None
        with TestClient(server.app) as client:
            response = client.get("/api/connection").json()
        self.assertEqual(response["reply_status"], "disconnected")
        self.assertFalse(response["account"]["is_enabled"])

    async def test_connection_endpoint_separates_admin_account_persona(self):
        server.app.state.telegram_bot = self.bot
        server.app.dependency_overrides[server.require_admin] = lambda: None
        with TestClient(server.app) as client:
            response = client.get("/api/connection")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["account"]["user_id"], 42)
        self.assertNotEqual(data["admin"]["user_id"], 42)
        self.assertEqual(data["persona"]["name"], "Андрей")
        self.assertEqual(data["reply_status"], "ready")
        server.app.dependency_overrides.clear()
        with TestClient(server.app) as client:
            self.assertEqual(client.get("/api/connection").status_code, 401)

    async def test_persona_is_sent_through_provider_entry_point(self):
        captured = {}
        def provider(request):
            captured.update(json.loads(request.content))
            return httpx.Response(200, json={"choices": [{"message": {"content": "Андрей"}}]})
        model = LLMManager()
        model.client = httpx.AsyncClient(transport=httpx.MockTransport(provider))
        try:
            response = await model.reply(await self.db.get_all_settings(), [{"text": "Как тебя зовут?"}])
        finally:
            await model.close()
        self.assertEqual(response, ["Андрей"])
        prompt = captured["messages"][0]["content"]
        self.assertIn("Имя персонажа — Андрей", prompt)
        self.assertIn("собеседнику Андрея", prompt)
        self.assertIn("Как вам мой аккаунт в бравле?", prompt)


if __name__ == "__main__":
    unittest.main()
