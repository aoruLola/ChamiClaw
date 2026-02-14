import unittest
from unittest.mock import patch

from chamiclaw import cli


class CliStateSetTest(unittest.TestCase):
    def test_state_set_invokes_state_machine_transition(self):
        with (
            patch("chamiclaw.cli.SystemStateMachine") as sm_cls,
            patch("builtins.print"),
        ):
            sm = sm_cls.return_value
            sm.transition.return_value = type(
                "State",
                (),
                {"state": "RUNNING", "reason": "manual_recover", "updated_at_utc": "2026-01-01T00:00:00Z"},
            )()
            cli.cmd_state_set("RUNNING", "manual_recover")
            sm.transition.assert_called_once_with("RUNNING", "manual_recover")

    def test_parser_accepts_state_set_command(self):
        parser = cli.build_parser()
        args = parser.parse_args(["state-set", "--state", "RUNNING", "--reason", "manual_recover"])
        self.assertEqual(args.command, "state-set")
        self.assertEqual(args.state, "RUNNING")
        self.assertEqual(args.reason, "manual_recover")


if __name__ == "__main__":
    unittest.main()
