"""Tests for lead_reviews schema, constraints, migrations, and review helper functions."""
import os
import sys
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

# Add services/discovery to sys.path so config and db can be imported cleanly
DISCOVERY_DIR = Path(__file__).resolve().parent.parent
if str(DISCOVERY_DIR) not in sys.path:
    sys.path.insert(0, str(DISCOVERY_DIR))


class TestLeadReviews(unittest.TestCase):
    def setUp(self):
        # Create an isolated temporary database for each test
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_leads.db"
        os.environ["COLDCALL_DB"] = str(self.db_path)

        # Reload or re-import db and config with updated DB_PATH
        import config
        config.DB_PATH = self.db_path
        import db
        db.DB_PATH = self.db_path
        self.db = db

        # Initialize the database schema
        self.conn = self.db.init()

    def tearDown(self):
        self.conn.close()
        self.temp_dir.cleanup()
        os.environ.pop("COLDCALL_DB", None)

    def test_database_initialization_runs_twice(self):
        """Database init() must be idempotent and safe to run multiple times."""
        # Insert a test lead before second init and commit
        self.db.upsert_lead(self.conn, {"place_id": "test_place_init", "name": "Initial Studio"})
        self.conn.commit()
        lead = self.conn.execute("SELECT * FROM leads WHERE place_id='test_place_init'").fetchone()
        self.assertIsNotNone(lead)

        # Run init again on a second connection
        conn2 = self.db.init()
        try:
            tables = {r[0] for r in conn2.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}
            self.assertIn("leads", tables)
            self.assertIn("lead_reviews", tables)
            self.assertIn("tasks", tables)
            self.assertIn("runlog", tables)

            # Verify existing record still intact after second init
            lead_after = conn2.execute("SELECT * FROM leads WHERE place_id='test_place_init'").fetchone()
            self.assertIsNotNone(lead_after)
            self.assertEqual(lead_after["name"], "Initial Studio")
        finally:
            conn2.close()

    def test_existing_lead_records_remain_unchanged(self):
        """Lead records must not be altered or wiped when review records are created or updated."""
        self.db.upsert_lead(self.conn, {
            "place_id": "p_unchanged",
            "name": "Kadapa Photo Studio",
            "search_city": "Kadapa",
            "phone": "9876543210",
            "website": "https://example.com"
        })
        lead_before = dict(self.conn.execute("SELECT * FROM leads WHERE place_id='p_unchanged'").fetchone())
        lead_id = lead_before["id"]

        # Initialize review and update status
        self.db.init_review(self.conn, lead_id)
        self.db.set_review_status(self.conn, lead_id, "approved", actor="tester", note="Verified customer")

        lead_after = dict(self.conn.execute("SELECT * FROM leads WHERE id=?", (lead_id,)).fetchone())
        # Compare all original fields
        for key in ["id", "place_id", "name", "search_city", "phone", "website", "phone_digits"]:
            self.assertEqual(lead_before[key], lead_after[key])

    def test_review_record_not_duplicated(self):
        """A review record cannot be duplicated for the same lead_id."""
        self.db.upsert_lead(self.conn, {"place_id": "p_dupe", "name": "Tirupati Bakery"})
        lead_id = self.conn.execute("SELECT id FROM leads WHERE place_id='p_dupe'").fetchone()["id"]

        rev1 = self.db.init_review(self.conn, lead_id)
        self.assertIsNotNone(rev1)
        self.assertEqual(rev1["status"], "pending")

        # Second call to init_review returns existing record, does not create another row
        rev2 = self.db.init_review(self.conn, lead_id)
        self.assertEqual(rev1["id"], rev2["id"])

        count = self.conn.execute("SELECT COUNT(*) FROM lead_reviews WHERE lead_id=?", (lead_id,)).fetchone()[0]
        self.assertEqual(count, 1)

        # Direct SQL insert with duplicate lead_id must fail unique constraint
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO lead_reviews(lead_id, status, created_at, updated_at) VALUES(?, 'approved', 1, 1)",
                (lead_id,)
            )

    def test_status_changes_persist_with_history(self):
        """Review status changes persist, update timestamps, and record history."""
        self.db.upsert_lead(self.conn, {"place_id": "p_status", "name": "Tirupati Events"})
        lead_id = self.conn.execute("SELECT id FROM leads WHERE place_id='p_status'").fetchone()["id"]

        self.db.init_review(self.conn, lead_id)

        # Transition 1: review_later
        rev_later = self.db.set_review_status(self.conn, lead_id, "review_later", actor="owner", note="Need more info")
        self.assertEqual(rev_later["status"], "review_later")
        self.assertIsNotNone(rev_later["decision_ts"])

        # Transition 2: approved
        rev_approved = self.db.set_review_status(self.conn, lead_id, "approved", actor="owner")
        self.assertEqual(rev_approved["status"], "approved")

        # Fetch afresh from database
        rev_fresh = self.db.get_review(self.conn, lead_id)
        self.assertEqual(rev_fresh["status"], "approved")

        # Check history has all transitions
        history = json.loads(rev_fresh["history"])
        statuses = [h["status"] for h in history]
        self.assertEqual(statuses, ["pending", "review_later", "approved"])

    def test_invalid_statuses_are_rejected(self):
        """Invalid review status must be rejected in Python and SQL constraint."""
        self.db.upsert_lead(self.conn, {"place_id": "p_invalid", "name": "Kadapa Wear"})
        lead_id = self.conn.execute("SELECT id FROM leads WHERE place_id='p_invalid'").fetchone()["id"]

        with self.assertRaises(ValueError):
            self.db.set_review_status(self.conn, lead_id, "random_invalid_status")

        # Check DB CHECK constraint
        with self.assertRaises(sqlite3.IntegrityError):
            self.conn.execute(
                "INSERT INTO lead_reviews(lead_id, status, created_at, updated_at) VALUES(?, 'bad_status', 1, 1)",
                (lead_id,)
            )

    def test_unknown_lead_ids_handled_safely(self):
        """Operations with nonexistent lead IDs must return None safely and not corrupt the DB."""
        nonexistent_id = 999999
        self.assertIsNone(self.db.init_review(self.conn, nonexistent_id))
        self.assertIsNone(self.db.get_review(self.conn, nonexistent_id))
        self.assertIsNone(self.db.set_review_status(self.conn, nonexistent_id, "approved"))
        self.assertIsNone(self.db.set_notification_state(self.conn, nonexistent_id, "sent"))

    def test_notification_state_transitions(self):
        """Notification state transitions from unnotified to sent/failed cleanly."""
        self.db.upsert_lead(self.conn, {"place_id": "p_notif", "name": "Kadapa Sweets"})
        lead_id = self.conn.execute("SELECT id FROM leads WHERE place_id='p_notif'").fetchone()["id"]

        rev = self.db.init_review(self.conn, lead_id)
        self.assertEqual(rev["notification_state"], "unnotified")
        self.assertIsNone(rev["notified_at"])

        # Set to sent
        rev_sent = self.db.set_notification_state(self.conn, lead_id, "sent")
        self.assertEqual(rev_sent["notification_state"], "sent")
        self.assertIsNotNone(rev_sent["notified_at"])

        # Invalid notification state
        with self.assertRaises(ValueError):
            self.db.set_notification_state(self.conn, lead_id, "invalid_state")

    def test_get_pending_reviews(self):
        """get_pending_reviews accurately returns pending leads and respects unnotified_only filter."""
        # Insert two leads
        self.db.upsert_lead(self.conn, {"place_id": "p_p1", "name": "Business One", "search_city": "Tirupati"})
        self.db.upsert_lead(self.conn, {"place_id": "p_p2", "name": "Business Two", "search_city": "Kadapa"})
        self.conn.commit()

        id1 = self.conn.execute("SELECT id FROM leads WHERE place_id='p_p1'").fetchone()["id"]
        id2 = self.conn.execute("SELECT id FROM leads WHERE place_id='p_p2'").fetchone()["id"]

        self.db.init_review(self.conn, id1)
        self.db.init_review(self.conn, id2)

        # Initially, both are pending and unnotified
        pending = self.db.get_pending_reviews(self.conn)
        self.assertEqual(len(pending), 2)

        # Mark id1 as sent
        self.db.set_notification_state(self.conn, id1, "sent")

        # unnotified_only=True should only return id2
        unnotified = self.db.get_pending_reviews(self.conn, unnotified_only=True)
        self.assertEqual(len(unnotified), 1)
        self.assertEqual(unnotified[0]["lead_id"], id2)
        self.assertEqual(unnotified[0]["name"], "Business Two")

        # Approve id2 -> no longer pending
        self.db.set_review_status(self.conn, id2, "approved")
        pending_after = self.db.get_pending_reviews(self.conn)
        self.assertEqual(len(pending_after), 1)
        self.assertEqual(pending_after[0]["lead_id"], id1)

    def test_import_leads_from_csv(self):
        """import_leads_from_csv parses CSV, inserts leads, and initializes reviews."""
        # Use existing synthetic sample file
        sample_csv = DISCOVERY_DIR / "data" / "sample_top_leads.csv"
        if sample_csv.exists():
            imported, new_reviews = self.db.import_leads_from_csv(self.conn, sample_csv)
            self.assertGreater(imported, 0)
            self.assertGreater(new_reviews, 0)

            # Check that pending reviews exist
            pending = self.db.get_pending_reviews(self.conn)
            self.assertEqual(len(pending), new_reviews)

    def test_failed_notification_reset(self):
        """Failed notifications can be retrieved and reset to unnotified for retry."""
        self.db.upsert_lead(self.conn, {"place_id": "p_fail", "name": "Failing Lead", "search_city": "Tirupati"})
        self.conn.commit()
        lead_id = self.conn.execute("SELECT id FROM leads WHERE place_id='p_fail'").fetchone()["id"]

        self.db.init_review(self.conn, lead_id)
        self.db.set_notification_state(self.conn, lead_id, "failed")

        failed = self.db.get_failed_reviews(self.conn)
        self.assertEqual(len(failed), 1)
        self.assertEqual(failed[0]["lead_id"], lead_id)

        reset_count = self.db.reset_failed_notifications(self.conn)
        self.assertEqual(reset_count, 1)

        failed_after = self.db.get_failed_reviews(self.conn)
        self.assertEqual(len(failed_after), 0)

        review = self.db.get_review(self.conn, lead_id)
        self.assertEqual(review["notification_state"], "unnotified")


if __name__ == "__main__":
    unittest.main()
