from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from chamiclaw.ops.state_machine import SystemState, SystemStateMachine
from chamiclaw.utils.time import utc_now_iso


@dataclass
class DrillResult:
    scenario: str
    recommended_state: str
    reason: str
    action_taken: str
    next_step: str


def _record_drill(result: DrillResult, path: str = "reports/drills.jsonl") -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "ts_utc": utc_now_iso(),
        "scenario": result.scenario,
        "recommended_state": result.recommended_state,
        "reason": result.reason,
        "action_taken": result.action_taken,
        "next_step": result.next_step,
    }
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=True) + "\n")


def run_failure_drill(scenario: str, apply_state: bool = False) -> DrillResult:
    sm = SystemStateMachine()

    if scenario == "api-failure":
        target = "PAUSED"
        reason = "drill: API failure / exchange unavailable"
        next_step = "检查交易所 API 连通性与认证；恢复后手动切回 RUNNING。"
    elif scenario == "reconcile-mismatch":
        target = "PAUSED"
        reason = "drill: reconcile mismatch detected"
        next_step = "先执行对账修复并确认仓位一致，再恢复 RUNNING。"
    elif scenario == "drawdown-limit":
        target = "HALTED"
        reason = "drill: daily drawdown limit breached"
        next_step = "人工复盘风险参数与仓位，确认后再解除 HALTED。"
    else:
        raise ValueError(f"unsupported scenario: {scenario}")

    action_taken = "dry-run"
    if apply_state:
        sm.transition(target, reason)
        action_taken = f"state-transitioned-to-{target}"

    result = DrillResult(
        scenario=scenario,
        recommended_state=target,
        reason=reason,
        action_taken=action_taken,
        next_step=next_step,
    )
    _record_drill(result)
    return result


def run_all_drills(apply_state: bool = False) -> list[DrillResult]:
    scenarios = ["api-failure", "reconcile-mismatch", "drawdown-limit"]
    return [run_failure_drill(s, apply_state=apply_state) for s in scenarios]


def current_state() -> SystemState:
    return SystemStateMachine().load()
