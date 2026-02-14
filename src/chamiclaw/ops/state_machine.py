from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from chamiclaw.utils.time import utc_now_iso


@dataclass
class SystemState:
    state: str
    reason: str
    updated_at_utc: str


class SystemStateMachine:
    def __init__(self, path: str = "data/system_state.json") -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> SystemState:
        if not self.path.exists():
            return SystemState(state="RUNNING", reason="bootstrap", updated_at_utc=utc_now_iso())
        data = json.loads(self.path.read_text(encoding="utf-8"))
        return SystemState(
            state=data.get("state", "RUNNING"),
            reason=data.get("reason", "unknown"),
            updated_at_utc=data.get("updated_at_utc", utc_now_iso()),
        )

    def save(self, state: SystemState) -> None:
        self.path.write_text(
            json.dumps(
                {
                    "state": state.state,
                    "reason": state.reason,
                    "updated_at_utc": state.updated_at_utc,
                },
                ensure_ascii=True,
                indent=2,
            ),
            encoding="utf-8",
        )

    def transition(self, target: str, reason: str) -> SystemState:
        s = SystemState(state=target, reason=reason, updated_at_utc=utc_now_iso())
        self.save(s)
        return s
