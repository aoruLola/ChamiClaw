from __future__ import annotations

import ast
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable


@dataclass
class Settings:
    raw: dict[str, Any]

    @property
    def db_path(self) -> str:
        return self.raw.get("database", {}).get("path", "data/chamiclaw.db")


def _parse_scalar(text: str) -> Any:
    lowered = text.lower()
    if lowered == "true":
        return True
    if lowered == "false":
        return False
    if lowered in {"null", "none"}:
        return None
    if (text.startswith('"') and text.endswith('"')) or (text.startswith("'") and text.endswith("'")):
        return text[1:-1]
    try:
        if "." in text:
            return float(text)
        return int(text)
    except ValueError:
        pass
    if text.startswith("[") and text.endswith("]"):
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return ast.literal_eval(text)
    return text


def _load_yaml_simple(path: Path) -> dict[str, Any]:
    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any]]] = [(-1, root)]

    for line in path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        if ":" not in stripped:
            raise ValueError(f"Invalid YAML line: {line}")
        indent = len(line) - len(line.lstrip(" "))
        key, _, raw_value = stripped.partition(":")
        key = key.strip()
        value = raw_value.strip()

        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value == "":
            child: dict[str, Any] = {}
            parent[key] = child
            stack.append((indent, child))
        else:
            parent[key] = _parse_scalar(value)
    return root


def _load_dotenv(dotenv_path: Path) -> None:
    if not dotenv_path.exists():
        return
    for line in dotenv_path.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        key = key.strip()
        value = value.strip()
        if key and key not in os.environ:
            os.environ[key] = value


def _set_nested(raw: dict[str, Any], path: list[str], value: Any) -> None:
    node = raw
    for key in path[:-1]:
        if key not in node or not isinstance(node[key], dict):
            node[key] = {}
        node = node[key]
    node[path[-1]] = value


def _apply_env_overrides(raw: dict[str, Any]) -> None:
    mapping: list[tuple[str, list[str], Callable[[str], Any]]] = [
        ("CHAMICLAW_DB_PATH", ["database", "path"], str),
        ("CHAMICLAW_GAMMA_BASE", ["apis", "gamma_base"], str),
        ("CHAMICLAW_CLOB_BASE", ["apis", "clob_base"], str),
        ("CHAMICLAW_CLOB_PROFILE", ["apis", "clob_profile"], str),
        ("CHAMICLAW_SCAN_INTERVAL_SEC", ["scan", "interval_sec"], int),
        ("CHAMICLAW_MAX_MARKETS_PER_SCAN", ["scan", "max_markets_per_scan"], int),
        ("CHAMICLAW_MAX_CYCLE_RUNTIME_SEC", ["scan", "max_cycle_runtime_sec"], float),
        ("CHAMICLAW_ORDERBOOK_PATH_TEMPLATE", ["scan", "orderbook_path_template"], str),
        ("CHAMICLAW_ORDERBOOK_TIMEOUT_SEC", ["scan", "orderbook_timeout_sec"], float),
        ("CHAMICLAW_ENABLE_ORDERBOOK", ["scan", "enable_orderbook"], lambda v: v.lower() in {"1", "true", "yes", "on"}),
        ("CHAMICLAW_ORDERBOOK_TRADABLE_ONLY", ["scan", "orderbook_for_tradable_only"], lambda v: v.lower() in {"1", "true", "yes", "on"}),
        ("CHAMICLAW_MAX_ORDERBOOK_CALLS_PER_CYCLE", ["scan", "max_orderbook_calls_per_cycle"], int),
        ("CHAMICLAW_LLM_MODE", ["llm", "mode"], str),
        ("CHAMICLAW_LLM1_ENDPOINT", ["llm", "llm1_endpoint"], str),
        ("CHAMICLAW_LLM2_ENDPOINT", ["llm", "llm2_endpoint"], str),
        ("CHAMICLAW_LLM_TIMEOUT_SEC", ["llm", "request_timeout_sec"], float),
        ("CHAMICLAW_ACCOUNT_EQUITY_USD", ["risk", "account_equity_usd"], float),
        ("CHAMICLAW_CROSS_MARKET_GAP_BPS", ["signal", "cross_market_gap_bps"], float),
        ("CHAMICLAW_TERM_STRUCTURE_GAP_BPS", ["signal", "term_structure_gap_bps"], float),
        ("CHAMICLAW_ENABLE_CROSS_MARKET_SIGNAL", ["signal", "enable_cross_market_signal"], lambda v: v.lower() in {"1", "true", "yes", "on"}),
        ("CHAMICLAW_ENABLE_TERM_STRUCTURE_SIGNAL", ["signal", "enable_term_structure_signal"], lambda v: v.lower() in {"1", "true", "yes", "on"}),
        ("CHAMICLAW_STRUCTURAL_CONFLICT_GAP_BPS", ["signal", "structural_conflict_gap_bps"], float),
        ("CHAMICLAW_ORDER_TIMEOUT_SEC", ["execution", "order_timeout_sec"], int),
        ("CHAMICLAW_MAX_RETRIES", ["execution", "max_retries"], int),
        ("CHAMICLAW_DRY_RUN", ["execution", "dry_run"], lambda v: v.lower() in {"1", "true", "yes", "on"}),
        ("CHAMICLAW_RECON_QTY_TOLERANCE", ["reconcile", "qty_tolerance"], float),
        ("CHAMICLAW_RECON_PAUSE_THRESHOLD", ["reconcile", "pause_mismatch_threshold"], int),
        ("CHAMICLAW_RECON_ENDPOINT", ["reconcile", "exchange_positions_endpoint"], str),
        ("CHAMICLAW_CALIBRATION_METHOD", ["evaluate", "calibration_method"], str),
        ("CHAMICLAW_DISCORD_WEBHOOK", ["ops", "discord_webhook"], str),
        ("CHAMICLAW_CLOB_SUBMIT_ENDPOINT", ["apis", "clob_endpoints", "submit_order"], str),
        ("CHAMICLAW_CLOB_STATUS_ENDPOINT", ["apis", "clob_endpoints", "order_status"], str),
        ("CHAMICLAW_CLOB_CANCEL_ENDPOINT", ["apis", "clob_endpoints", "cancel_order"], str),
        ("CHAMICLAW_CLOB_ORDERBOOK_ENDPOINT", ["apis", "clob_endpoints", "orderbook"], str),
        ("CHAMICLAW_CLOB_POSITIONS_ENDPOINT", ["apis", "clob_endpoints", "positions"], str),
    ]
    for env_key, path, caster in mapping:
        raw_value = os.getenv(env_key)
        if raw_value is None or raw_value == "":
            continue
        _set_nested(raw, path, caster(raw_value))


def load_settings(config_path: str = "config/config.yaml", dotenv_path: str = ".env") -> Settings:
    _load_dotenv(Path(dotenv_path))
    path = Path(config_path)
    with path.open("r", encoding="utf-8") as f:
        if path.suffix.lower() == ".json":
            raw = json.load(f)
        elif path.suffix.lower() in {".yaml", ".yml"}:
            raw = _load_yaml_simple(path)
        else:
            raise ValueError("Unsupported config format. Use YAML or JSON config file.")
    _apply_env_overrides(raw)
    return Settings(raw=raw)
