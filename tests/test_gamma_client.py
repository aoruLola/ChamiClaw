import json

from chamiclaw.clients.gamma import _parse_outcomes


def test_parse_outcomes_accepts_json_encoded_string():
    raw = json.dumps(["Yes", "No"])
    outcomes = _parse_outcomes(raw)
    assert outcomes == ["YES", "NO"]


def test_parse_outcomes_accepts_csv_string():
    outcomes = _parse_outcomes("yes, no")
    assert outcomes == ["YES", "NO"]

