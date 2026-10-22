"""Tests for Coldwire Telegram Lead Review Bot.

Mocks Telegram interactions to test authorization, formatting, inline callbacks,
duplicate protection, failure handling, and state persistence against a temporary SQLite DB.
"""
import asyncio
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Add services paths to sys.path
SERVICES_DIR = Path(__file__).resolve().parent.parent.parent
DISCOVERY_DIR = SERVICES_DIR / "discovery"
BOT_DIR = SERVICES_DIR / "telegram_bot"

for p in (str(SERVICES_DIR), str(DISCOVERY_DIR), str(BOT_DIR)):
    if p not in sys.path:
        sys.path.insert(0, p)

import db
from telegram_bot import config, bot, formatter


class MockUser:
    def __init__(self, id: int, username: str = "tester"):
        self.id = id
        self.username = username


class MockChat:
    def __init__(self, id: int, type: str = "private"):
        self.id = id
        self.type = type


class MockMessage:
    def __init__(self, chat: MockChat, from_user: MockUser = None, text: str = ""):
        self.chat = chat
        self.from_user = from_user
        self.text = text
        self.replies = []

    async def reply_text(self, text: str, reply_markup=None, parse_mode=None):
        self.replies.append({
            "text": text,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
        })
        return self


class MockCallbackQuery:
    def __init__(self, data: str, from_user: MockUser, message: MockMessage):
        self.data = data
        self.from_user = from_user
        self.message = message
        self.answers = []
        self.edited_markups = []

    async def answer(self, text: str = "", show_alert: bool = False):
        self.answers.append({"text": text, "show_alert": show_alert})

    async def edit_message_reply_markup(self, reply_markup=None):
        self.edited_markups.append(reply_markup)


class MockBot:
    def __init__(self):
        self.sent_messages = []
        self.should_fail = False

    async def send_message(self, chat_id: int, text: str, reply_markup=None, parse_mode=None):
        if self.should_fail:
            raise ConnectionError("Simulated Telegram API network timeout")
        record = {
            "chat_id": chat_id,
            "text": text,
            "reply_markup": reply_markup,
            "parse_mode": parse_mode,
        }
        self.sent_messages.append(record)
        return record


class MockContext:
    def __init__(self, bot: MockBot, conn=None):
        self.bot = bot
        self.bot_data = {"db_conn": conn} if conn else {}


class MockUpdate:
    def __init__(self, user: MockUser, chat: MockChat, message: MockMessage = None, callback_query: MockCallbackQuery = None):
        self.effective_user = user
        self.effective_chat = chat
        self.message = message
        self.callback_query = callback_query


class TestTelegramBot(unittest.TestCase):
    def setUp(self):
        # Configure test authorization
        self.owner_id = 987654321
        config.AUTHORIZED_USER_ID = self.owner_id
        bot.AUTHORIZED_USER_ID = self.owner_id

        # Isolated test database
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_leads.db"
        os.environ["COLDCALL_DB"] = str(self.db_path)
        db.DB_PATH = self.db_path

        self.conn = db.init()
        self.mock_bot = MockBot()
        self.context = MockContext(self.mock_bot, conn=self.conn)

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()
        os.environ.pop("COLDCALL_DB", None)

    def _insert_lead(self, name="Tirupati Studio", city="Tirupati", phone="9876543210", website="", ig=""):
        row = {
            "name": name,
            "search_city": city,
            "phone": phone,
            "website": website,
            "instagram": ig,
            "primary_cat": "Wedding Photography",
            "score": 115,
            "segment": "A_hot",
            "rating": 4.8,
            "reviews": 120,
            "pitch_angle": "Their website is an Instagram page.",
        }
        db.upsert_lead(self.conn, row)
        self.conn.commit()
        lead_id = self.conn.execute("SELECT id FROM leads WHERE name=?", (name,)).fetchone()[0]
        db.init_review(self.conn, lead_id)
        return lead_id

    def test_authorized_user_access_start(self):
        """Authorized user in a private chat can run /start successfully."""
        user = MockUser(self.owner_id)
        chat = MockChat(self.owner_id, type="private")
        msg = MockMessage(chat, from_user=user)
        update = MockUpdate(user, chat, message=msg)

        asyncio.run(bot.start_command(update, self.context))

        self.assertEqual(len(msg.replies), 1)
        self.assertIn("Welcome to Coldwire", msg.replies[0]["text"])

    def test_unauthorized_user_is_denied(self):
        """Unauthorized user is denied and receives no lead or command data."""
        attacker = MockUser(111222333)
        chat = MockChat(111222333, type="private")
        msg = MockMessage(chat, from_user=attacker)
        update = MockUpdate(attacker, chat, message=msg)

        asyncio.run(bot.start_command(update, self.context))
        self.assertEqual(len(msg.replies), 1)
        self.assertIn("Access denied", msg.replies[0]["text"])

        # Try pending command
        msg_pending = MockMessage(chat, from_user=attacker)
        update_pending = MockUpdate(attacker, chat, message=msg_pending)
        asyncio.run(bot.pending_command(update_pending, self.context))
        self.assertEqual(len(msg_pending.replies), 1)
        self.assertIn("Access denied", msg_pending.replies[0]["text"])

    def test_private_chat_restriction(self):
        """Commands sent from group, supergroup, or channel chats are strictly rejected."""
        user = MockUser(self.owner_id)
        group_chat = MockChat(-1001234567, type="supergroup")
        msg = MockMessage(group_chat, from_user=user)
        update = MockUpdate(user, group_chat, message=msg)

        asyncio.run(bot.pending_command(update, self.context))
        self.assertEqual(len(msg.replies), 1)
        self.assertIn("Access denied", msg.replies[0]["text"])

    def test_lead_formatting_all_fields_and_draft_marker(self):
        """Lead message formatting contains evidence, suggested service, and explicit DRAFT outreach label."""
        lead = {
            "name": "Kadapa Jewels & Boutiques",
            "primary_cat": "Fashion Boutique",
            "search_city": "Kadapa",
            "area": "Nagarajupalle",
            "phone": "+919876543210",
            "whatsapp": "https://wa.me/919876543210",
            "email": "contact@kadapajewels.example",
            "website": "https://instagram.com/kadapajewels",
            "instagram": "https://instagram.com/kadapajewels",
            "ig_followers": 8500,
            "score": 120,
            "segment": "A_hot",
            "rating": 4.9,
            "reviews": 85,
            "pitch_angle": "Their website is an Instagram page. Renting portfolio.",
        }

        formatted = formatter.format_lead_message(lead)

        self.assertIn("Kadapa Jewels & Boutiques", formatted)
        self.assertIn("Fashion Boutique", formatted)
        self.assertIn("Nagarajupalle, Kadapa", formatted)
        self.assertIn("+919876543210", formatted)
        self.assertIn("8,500 followers", formatted)
        self.assertIn("Lead Score:* 120", formatted)
        self.assertIn("Recommended Service:", formatted)
        self.assertIn("DRAFT OUTREACH (NOT SENT — FOR REVIEW ONLY)", formatted)

    def test_lead_formatting_missing_optional_fields(self):
        """Formatting handles missing optional fields cleanly without errors or 'None' text."""
        lead = {
            "name": "Tirupati Simple Baker",
            "search_city": "Tirupati",
        }

        formatted = formatter.format_lead_message(lead)
        self.assertIn("Tirupati Simple Baker", formatted)
        self.assertIn("Tirupati", formatted)
        self.assertNotIn("None", formatted)
        self.assertIn("DRAFT OUTREACH", formatted)

    def test_approve_reject_review_later_callbacks(self):
        """Callbacks update status, record timestamp, log decision history, and update keyboard."""
        lead_id = self._insert_lead(name="Tirupati Candid Moments", city="Tirupati")
        user = MockUser(self.owner_id)
        chat = MockChat(self.owner_id, type="private")

        # 1. Test Approve
        msg1 = MockMessage(chat, from_user=user)
        cb_approve = MockCallbackQuery(f"review:approve:{lead_id}", from_user=user, message=msg1)
        up_approve = MockUpdate(user, chat, callback_query=cb_approve)

        asyncio.run(bot.handle_callback(up_approve, self.context))

        rev = db.get_review(self.conn, lead_id)
        self.assertEqual(rev["status"], "approved")
        self.assertIsNotNone(rev["decision_ts"])
        self.assertEqual(len(cb_approve.answers), 1)
        self.assertIn("Approved", cb_approve.answers[0]["text"])
        self.assertEqual(len(cb_approve.edited_markups), 1)

        # 2. Test Reject
        cb_reject = MockCallbackQuery(f"review:reject:{lead_id}", from_user=user, message=msg1)
        up_reject = MockUpdate(user, chat, callback_query=cb_reject)

        asyncio.run(bot.handle_callback(up_reject, self.context))
        rev = db.get_review(self.conn, lead_id)
        self.assertEqual(rev["status"], "rejected")

        # 3. Test Review Later
        cb_later = MockCallbackQuery(f"review:review_later:{lead_id}", from_user=user, message=msg1)
        up_later = MockUpdate(user, chat, callback_query=cb_later)

        asyncio.run(bot.handle_callback(up_later, self.context))
        rev = db.get_review(self.conn, lead_id)
        self.assertEqual(rev["status"], "review_later")

        # Verify history holds all transitions
        hist = json.loads(rev["history"])
        statuses = [h["status"] for h in hist]
        self.assertEqual(statuses, ["pending", "approved", "rejected", "review_later"])

    def test_repeated_callback_protection(self):
        """Clicking the same decision twice is caught by idempotency protection and does not duplicate history."""
        lead_id = self._insert_lead(name="Kadapa Bakery Co", city="Kadapa")
        user = MockUser(self.owner_id)
        chat = MockChat(self.owner_id, type="private")
        msg = MockMessage(chat, from_user=user)

        cb1 = MockCallbackQuery(f"review:approve:{lead_id}", from_user=user, message=msg)
        up1 = MockUpdate(user, chat, callback_query=cb1)
        asyncio.run(bot.handle_callback(up1, self.context))

        rev1 = db.get_review(self.conn, lead_id)
        hist_len_1 = len(json.loads(rev1["history"]))

        # Click Approve second time
        cb2 = MockCallbackQuery(f"review:approve:{lead_id}", from_user=user, message=msg)
        up2 = MockUpdate(user, chat, callback_query=cb2)
        asyncio.run(bot.handle_callback(up2, self.context))

        self.assertIn("already marked as approved", cb2.answers[0]["text"])
        rev2 = db.get_review(self.conn, lead_id)
        hist_len_2 = len(json.loads(rev2["history"]))
        self.assertEqual(hist_len_1, hist_len_2)

    def test_duplicate_notification_prevention(self):
        """send_pending only dispatches unnotified leads; second call dispatches nothing."""
        self._insert_lead(name="Tirupati Event Planners", city="Tirupati")
        user = MockUser(self.owner_id)
        chat = MockChat(self.owner_id, type="private")
        msg1 = MockMessage(chat, from_user=user)
        up1 = MockUpdate(user, chat, message=msg1)

        # First run -> dispatches lead
        sent_first = asyncio.run(bot.send_pending_command(up1, self.context))
        self.assertEqual(sent_first, 1)
        self.assertEqual(len(self.mock_bot.sent_messages), 1)

        # Second run -> should find 0 unnotified leads
        msg2 = MockMessage(chat, from_user=user)
        up2 = MockUpdate(user, chat, message=msg2)
        sent_second = asyncio.run(bot.send_pending_command(up2, self.context))
        self.assertEqual(sent_second, 0)
        self.assertEqual(len(self.mock_bot.sent_messages), 1)
        self.assertIn("No unnotified pending leads found", msg2.replies[0]["text"])

    def test_send_failure_and_retry_workflow(self):
        """Simulate Telegram network failure: lead state is marked 'failed' and can be retried."""
        lead_id = self._insert_lead(name="Kadapa Interior Works", city="Kadapa")
        user = MockUser(self.owner_id)
        chat = MockChat(self.owner_id, type="private")
        msg1 = MockMessage(chat, from_user=user)
        up1 = MockUpdate(user, chat, message=msg1)

        # Simulate network failure during send
        self.mock_bot.should_fail = True
        sent_count = asyncio.run(bot.send_pending_command(up1, self.context))
        self.assertEqual(sent_count, 0)

        # Verify state in DB is 'failed' (NOT 'sent')
        rev = db.get_review(self.conn, lead_id)
        self.assertEqual(rev["notification_state"], "failed")

        # Now fix network and test /retry_failed
        self.mock_bot.should_fail = False
        msg_retry = MockMessage(chat, from_user=user)
        up_retry = MockUpdate(user, chat, message=msg_retry)

        retried_count = asyncio.run(bot.retry_failed_command(up_retry, self.context))
        self.assertEqual(retried_count, 1)

        rev_after = db.get_review(self.conn, lead_id)
        self.assertEqual(rev_after["notification_state"], "sent")
        self.assertEqual(len(self.mock_bot.sent_messages), 1)


if __name__ == "__main__":
    unittest.main()
