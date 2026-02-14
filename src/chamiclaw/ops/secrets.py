from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class SecretAccess:
    role: str
    private_key_visible: bool


def get_runtime_role() -> str:
    """Runtime role boundary for key isolation.

    - research: analytics/scanning paths, private key must stay hidden
    - execution: order placement paths, private key can be loaded
    """
    role = os.getenv("CHAMICLAW_ROLE", "research").strip().lower()
    return role if role in {"research", "execution"} else "research"


def get_polymarket_private_key() -> str | None:
    """Return private key only for execution role."""
    if get_runtime_role() != "execution":
        return None
    value = os.getenv("POLYMARKET_PRIVATE_KEY", "").strip()
    return value or None


def get_secret_access_snapshot() -> SecretAccess:
    role = get_runtime_role()
    return SecretAccess(role=role, private_key_visible=get_polymarket_private_key() is not None)
