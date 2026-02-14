from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chamiclaw.utils.time import utc_now_iso


class JsonLogger:
    def __init__(self, file_path: str = "logs/events.jsonl") -> None:
        self.path = Path(file_path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def log(self, action: str, **fields: Any) -> None:
        payload = {"ts_utc": utc_now_iso(), "action": action, **fields}
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, ensure_ascii=True) + "\n")
