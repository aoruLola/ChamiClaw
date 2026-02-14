from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from chamiclaw.utils.time import utc_now_iso


def write_daily_report(path: str, payload: dict[str, Any]) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    data = {"generated_at_utc": utc_now_iso(), **payload}
    p.write_text(json.dumps(data, ensure_ascii=True, indent=2), encoding="utf-8")
