from __future__ import annotations

from datetime import datetime, timedelta, timezone

from chamiclaw.core.models import BacktestRequest, OptimizationTrial, StrategyParams
from chamiclaw.optimization.backtest import BacktestEngine
from chamiclaw.storage.repository import Repository


class OnlineTuner:
    def __init__(self, backtest_engine: BacktestEngine):
        self.backtest_engine = backtest_engine

    def run_window(
        self,
        *,
        repo: Repository,
        window_minutes: int,
        apply_best: bool,
        execution_dry_run: bool,
        run_profile: str,
        now: datetime | None = None,
    ) -> dict[str, object]:
        now_ts = now or datetime.now(timezone.utc)
        from_ts = now_ts - timedelta(minutes=max(window_minutes, 1))
        base_version = repo.get_current_params()
        trials: list[OptimizationTrial] = []

        for candidate in self._candidate_params(base_version.params):
            version = repo.save_params_version(candidate, source="online_tuner", make_current=False)
            report = self.backtest_engine.run(
                repo,
                BacktestRequest(from_ts=from_ts, to_ts=now_ts, params_version_id=version.version_id),
            )
            trial = OptimizationTrial(
                params_version_id=version.version_id,
                window_minutes=window_minutes,
                score=report.score,
                details={"win_rate": report.win_rate, "rr": report.rr, "max_drawdown_pct": report.max_drawdown_pct},
            )
            repo.save_optimization_trial(trial)
            trials.append(trial)

        best = max(trials, key=lambda item: item.score) if trials else None
        applied = False
        rolled_back = False
        reason = "apply_disabled"

        decline_streak = int(getattr(repo, "optimization_decline_streak", 0))
        last_applied_score = getattr(repo, "optimization_last_applied_score", None)
        last_applied_version_id = str(
            getattr(repo, "optimization_last_applied_version_id", repo.get_current_params().version_id)
        )
        if apply_best and best is not None:
            if run_profile == "live":
                reason = "live_mode_manual_confirmation_required"
            elif not execution_dry_run:
                reason = "auto_apply_requires_dry_run"
            else:
                reason = "applied_best"
                applied = repo.set_current_params_version(best.params_version_id) is not None
                if applied:
                    if last_applied_score is not None and best.score < float(last_applied_score):
                        decline_streak += 1
                    else:
                        decline_streak = 0
                    if decline_streak >= 2 and last_applied_version_id != best.params_version_id:
                        restored = repo.set_current_params_version(last_applied_version_id)
                        if restored is not None:
                            applied = False
                            rolled_back = True
                            reason = "rolled_back_after_two_declines"
                    else:
                        last_applied_score = best.score
                        last_applied_version_id = best.params_version_id

                    repo.save_optimization_meta(
                        decline_streak=decline_streak,
                        last_applied_score=last_applied_score,
                        last_applied_version_id=last_applied_version_id,
                    )

        return {
            "window_minutes": window_minutes,
            "candidate_trials": len(trials),
            "best_trial": best.model_dump(mode="json") if best else None,
            "applied": applied,
            "rolled_back": rolled_back,
            "reason": reason,
            "decline_streak": decline_streak,
        }

    @staticmethod
    def _candidate_params(base: StrategyParams) -> list[StrategyParams]:
        looser = base.model_copy(
            update={
                "a_change_5m_min_abs": max(base.a_change_5m_min_abs * 0.9, 0.001),
                "b_change_15m_min_abs": max(base.b_change_15m_min_abs * 0.9, 0.001),
            }
        )
        stricter = base.model_copy(
            update={
                "a_change_5m_min_abs": base.a_change_5m_min_abs * 1.1,
                "b_change_15m_min_abs": base.b_change_15m_min_abs * 1.1,
            }
        )
        return [base.model_copy(deep=True), looser, stricter]
