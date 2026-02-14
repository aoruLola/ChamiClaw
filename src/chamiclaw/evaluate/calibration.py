from __future__ import annotations

import math


def _clip_prob(x: float) -> float:
    return min(1.0 - 1e-6, max(1e-6, x))


def fit_platt(predictions: list[dict], iters: int = 300, lr: float = 0.05) -> dict:
    if not predictions:
        return {"a": 1.0, "b": 0.0}
    a, b = 1.0, 0.0
    for _ in range(iters):
        grad_a = 0.0
        grad_b = 0.0
        n = 0
        for row in predictions:
            p = float(row.get("fair_prob", 0.5))
            y = float(row.get("outcome", 0))
            p = _clip_prob(p)
            z = a * math.log(p / (1 - p)) + b
            z = max(-60.0, min(60.0, z))
            pred = 1.0 / (1.0 + math.exp(-z))
            grad = pred - y
            grad_a += grad * math.log(p / (1 - p))
            grad_b += grad
            n += 1
        if n == 0:
            break
        a -= lr * (grad_a / n)
        b -= lr * (grad_b / n)
    return {"a": a, "b": b}


def apply_platt(prob: float, params: dict) -> float:
    a = float(params.get("a", 1.0))
    b = float(params.get("b", 0.0))
    p = _clip_prob(prob)
    z = a * math.log(p / (1 - p)) + b
    z = max(-60.0, min(60.0, z))
    out = 1.0 / (1.0 + math.exp(-z))
    return min(1.0, max(0.0, out))


def fit_isotonic(predictions: list[dict]) -> list[tuple[float, float]]:
    pairs = sorted((float(r.get("fair_prob", 0.5)), float(r.get("outcome", 0))) for r in predictions)
    if not pairs:
        return []
    blocks: list[dict] = [{"sum_y": y, "count": 1, "lo": p, "hi": p} for p, y in pairs]
    i = 0
    while i < len(blocks) - 1:
        left = blocks[i]
        right = blocks[i + 1]
        if (left["sum_y"] / left["count"]) <= (right["sum_y"] / right["count"]):
            i += 1
            continue
        merged = {
            "sum_y": left["sum_y"] + right["sum_y"],
            "count": left["count"] + right["count"],
            "lo": left["lo"],
            "hi": right["hi"],
        }
        blocks[i : i + 2] = [merged]
        i = max(0, i - 1)
    out: list[tuple[float, float]] = []
    for b in blocks:
        out.append((float(b["hi"]), float(b["sum_y"] / b["count"])))
    return out


def apply_isotonic(prob: float, model: list[tuple[float, float]]) -> float:
    if not model:
        return min(1.0, max(0.0, prob))
    p = float(prob)
    for hi, yhat in model:
        if p <= hi:
            return min(1.0, max(0.0, yhat))
    return min(1.0, max(0.0, model[-1][1]))


def bucket_calibration(predictions: list[dict], bucket_width: float = 0.05) -> list[dict]:
    buckets: dict[float, list[dict]] = {}
    for p in predictions:
        prob = float(p.get("fair_prob", 0.5))
        bucket = min(1.0 - bucket_width, max(0.0, prob - (prob % bucket_width)))
        buckets.setdefault(bucket, []).append(p)

    summary: list[dict] = []
    for b, rows in sorted(buckets.items(), key=lambda x: x[0]):
        observed = [float(r.get("outcome", 0)) for r in rows]
        predicted = [float(r.get("fair_prob", 0.5)) for r in rows]
        obs_rate = sum(observed) / len(observed) if observed else 0.0
        pred_mean = sum(predicted) / len(predicted) if predicted else 0.0
        summary.append(
            {
                "bucket_start": b,
                "bucket_end": b + bucket_width,
                "count": len(rows),
                "predicted_mean": pred_mean,
                "observed_rate": obs_rate,
                "calibration_gap": obs_rate - pred_mean,
            }
        )
    return summary


def calibrate_predictions(predictions: list[dict], method: str = "isotonic") -> dict:
    method = (method or "isotonic").lower()
    if method == "platt":
        params = fit_platt(predictions)
        calibrated = [{"orig": p["fair_prob"], "calibrated": apply_platt(float(p["fair_prob"]), params)} for p in predictions]
        return {"method": "platt", "params": params, "calibrated": calibrated}
    model = fit_isotonic(predictions)
    calibrated = [{"orig": p["fair_prob"], "calibrated": apply_isotonic(float(p["fair_prob"]), model)} for p in predictions]
    return {"method": "isotonic", "params": {"segments": model}, "calibrated": calibrated}
