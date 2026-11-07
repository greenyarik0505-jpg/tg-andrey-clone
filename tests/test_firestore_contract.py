"""Opt-in checks against a temporary namespace in the selected Firebase project."""
import asyncio
import os
import secrets
import time
import unittest

from cryptography.fernet import Fernet

from database.db import DEFAULT_SETTINGS
from database.firestore_db import FirestoreDatabase


@unittest.skipUnless(os.getenv("FIRESTORE_TEST_PROJECT") and os.getenv("FIRESTORE_TEST_CREDENTIALS"), "Firestore integration credentials are not configured")
class FirestoreContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_state_survives_new_client_and_transactions_are_idempotent(self):
        namespace = "integration-" + secrets.token_hex(8)
        key = Fernet.generate_key().decode()

        def new_client():
            return FirestoreDatabase(
                project=os.environ["FIRESTORE_TEST_PROJECT"],
                database=os.environ.get("FIRESTORE_TEST_DATABASE", "bot-state"),
                namespace=namespace,
                defaults=DEFAULT_SETTINGS,
                encryption_key=key,
                service_account_json=os.environ["FIRESTORE_TEST_CREDENTIALS"],
            )

        first, restored = new_client(), new_client()
        try:
            await first.init()
            request = await first.add_business_request("connection-test", 42, "Андрей (@friend)")
            self.assertIsNotNone(request)
            self.assertIsNone(await first.add_business_request("connection-test", 42, "Андрей (@friend)"))
            self.assertEqual((await first.business_requests(request))[0]["user_id"], 42)
            account = {
                "user_id": 42, "display_name": "Андрей", "username": "friend",
                "connection_id": "connection-test", "is_enabled": 1, "can_reply": 1, "connected_at": time.time(),
            }
            self.assertTrue(await first.approve_business_request(request, 42, account))
            self.assertFalse(await first.approve_business_request(request, 42, account))
            await first.update_settings({
                "custom_base_url": "https://provider.example/v1",
                "custom_model_name": "test-model",
                "custom_api_key": "test-key",
            })
            raw = (await first.meta.get()).to_dict()
            self.assertNotEqual(raw["custom_api_key"], "test-key")
            await restored.init()
            self.assertEqual(await restored.business_owner(), 42)
            self.assertEqual((await restored.business_account(42))["display_name"], "Андрей")
            self.assertEqual((await restored.get_all_settings())["custom_api_key"], "test-key")
            await asyncio.gather(first.toggle_pause(), restored.toggle_pause())
            self.assertEqual(await first.get_setting("is_paused"), "0")
            await first.get_or_create_chat(7, "Собеседник")
            results = await asyncio.gather(*[
                store.record_human_message(7, 7, "Собеседник", "Привет", message_id=1, connection_id="connection-test")
                for store in (first, restored)
            ])
            self.assertEqual(sum(result is not None for result in results), 1)
            await first.record_bot_message(7, "Привет", message_id=2, connection_id="connection-test")
            await restored.record_bot_message(7, "Привет", message_id=2, connection_id="connection-test")
            history = await restored.get_recent_history(7, connection_id="connection-test")
            self.assertEqual(len(history), 2)
            self.assertEqual((await first.get_stats())["total_received"], 1)
            self.assertEqual((await first.get_stats())["total_sent"], 1)
            await first.record_human_message(7, 42, "Андрей", "", is_outgoing=True)
            self.assertGreater((await restored.reply_state(7, "connection-test"))["last_human_message_time"], 0)
            await first.delete_business_messages("connection-test", 7, [1, 1, 2])
            stats = await first.get_stats()
            self.assertEqual(stats["total_received"], 0)
            self.assertEqual(stats["total_sent"], 0)
            await first.set_chat_error(7, "Проверочная ошибка")
            self.assertEqual((await restored.list_chats())[0]["last_reply_error"], "Проверочная ошибка")
            update = {"update_id": 100, "message": {"text": "private test message"}}
            await first.store_update(update)
            await restored.store_update(update)
            claimed = await asyncio.gather(first.claim_updates(), restored.claim_updates())
            jobs = [job for group in claimed for job in group]
            self.assertEqual(len(jobs), 1)
            self.assertEqual(jobs[0][0], update)
            stored_job = (await first.updates.document("100").get()).to_dict()
            self.assertNotIn("private test message", stored_job["payload"])
            await restored.finish_update(100, jobs[0][1], "Interrupted")
            recovered = await first.claim_updates()
            self.assertEqual(len(recovered), 1)
            await first.finish_update(100, recovered[0][1])
            self.assertEqual(await restored.claim_updates(), [])
        finally:
            await first.client.recursive_delete(first.root)
            await restored.close()
            await first.close()


if __name__ == "__main__":
    unittest.main()
