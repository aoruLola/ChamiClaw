import os
import tempfile
import unittest
from pathlib import Path

from chamiclaw.db.sqlite import Database


class DatabaseConnectionLifecycleTest(unittest.TestCase):
    def test_connect_context_manager_closes_connection_on_exit(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "lifecycle.db"
            db = Database(str(db_path))
            db.init_schema("sql/schema.sql")

            with db.connect() as conn:
                conn.execute("SELECT 1")

            os.remove(db_path)
            self.assertFalse(db_path.exists())

    def test_connect_context_manager_commits_on_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "commit.db"
            db = Database(str(db_path))
            db.init_schema("sql/schema.sql")

            with db.connect() as conn:
                conn.execute(
                    """
                    INSERT INTO markets (
                      market_id, event_id, slug, question, description, end_time_utc,
                      liquidity_usd, volume_usd, rule_summary_json, tradable, tradable_reason, updated_at_utc
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        "m1",
                        "e1",
                        "slug-1",
                        "q1",
                        "",
                        "2099-01-01T00:00:00Z",
                        1.0,
                        1.0,
                        "{}",
                        1,
                        "ok",
                        "2099-01-01T00:00:00Z",
                    ),
                )

            with db.connect() as conn:
                row = conn.execute("SELECT COUNT(*) AS c FROM markets WHERE market_id='m1'").fetchone()
            self.assertEqual(int(row["c"]), 1)


if __name__ == "__main__":
    unittest.main()
