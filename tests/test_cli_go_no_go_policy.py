import unittest
from unittest.mock import MagicMock, patch

from chamiclaw import cli


class _Cursor:
    def __init__(self, row):
        self._row = row

    def fetchone(self):
        return self._row


class _Ctx:
    def __init__(self, conn):
        self._conn = conn

    def __enter__(self):
        return self._conn

    def __exit__(self, exc_type, exc, tb):
        return False


class _FakeDbForReport:
    def __init__(self):
        self._connect_calls = 0

    def get_go_no_go_snapshot(self):
        return {"duplicate_order_signals": 0}

    def get_latest_strategy_version(self):
        return None

    def connect(self):
        self._connect_calls += 1
        conn = MagicMock()
        if self._connect_calls == 1:
            conn.execute.side_effect = [
                _Cursor((1,)),
                _Cursor((2,)),
                _Cursor((3,)),
                _Cursor((4,)),
                _Cursor((5,)),
            ]
        else:
            conn.execute.side_effect = [_Cursor(None)]
        return _Ctx(conn)


class GoNoGoPolicyCliTest(unittest.TestCase):
    def test_cmd_go_no_go_passes_policy_from_config(self):
        fake_db = MagicMock()
        fake_db.get_go_no_go_snapshot.return_value = {"duplicate_order_signals": 0}
        fake_cfg = {"go_no_go": {"min_recent_signals": 9}}

        with (
            patch("chamiclaw.cli._build_app", return_value=(fake_cfg, fake_db, None)),
            patch("chamiclaw.cli.build_go_no_go_payload", return_value={"verdict": "GO"}) as payload_builder,
            patch("builtins.print"),
        ):
            cli.cmd_go_no_go("config/config.yaml")

        payload_builder.assert_called_once_with(fake_db.get_go_no_go_snapshot.return_value, policy=fake_cfg["go_no_go"])

    def test_cmd_report_passes_policy_from_config(self):
        fake_db = _FakeDbForReport()
        fake_cfg = {"go_no_go": {"min_recent_signals": 11}}
        fake_state_machine = MagicMock()
        fake_state_machine.load.return_value = MagicMock(state="RUNNING", reason="", updated_at_utc="2026-01-01T00:00:00Z")

        with (
            patch("chamiclaw.cli._build_app", return_value=(fake_cfg, fake_db, None)),
            patch("chamiclaw.cli.SystemStateMachine", return_value=fake_state_machine),
            patch("chamiclaw.cli.build_go_no_go_payload", return_value={"verdict": "GO"}) as payload_builder,
            patch("chamiclaw.cli.load_go_no_go_validation_summary", return_value=None),
            patch("chamiclaw.cli.write_daily_report"),
            patch("builtins.print"),
        ):
            cli.cmd_report("config/config.yaml")

        payload_builder.assert_called_once_with(fake_db.get_go_no_go_snapshot(), policy=fake_cfg["go_no_go"])


if __name__ == "__main__":
    unittest.main()
