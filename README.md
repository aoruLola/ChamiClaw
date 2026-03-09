# ChamiClaw

ChamiClaw is a Polymarket weather trading service focused on US daily precipitation markets. The current runtime is designed for low-frequency batch execution: weather market discovery, forecast aggregation, rule-based candidate ranking, optional OpenAI-compatible LLM review, risk checks, and automated execution.

## What Runs in Production

The production target is a single long-lived Linux container:

- FastAPI serves health and ops endpoints.
- APScheduler runs the weather jobs inside the container when `SCHEDULER_ENABLED=true`.
- SQLite and strategy params are stored on a mounted data volume.
- Generic webhook notifications send structured JSON for key events.

## Quick Start (Linux Docker)

1. Copy [`.env.example`](/E:/Project/ChamiClaw/.worktrees/weather-precip-auto/.env.example) to `.env` and fill in real credentials.
2. Keep `REPOSITORY_BACKEND=sqlite` and point `SQLITE_PATH` / `PARAMS_PATH` at the mounted data directory.
3. Enable scheduler and, if desired, webhook + LLM review.

```bash
cp .env.example .env
docker compose up --build -d
```

Health and ops endpoints:

```bash
curl http://127.0.0.1:8000/health
curl http://127.0.0.1:8000/ops/preflight
curl -X POST http://127.0.0.1:8000/ops/tick
curl -X POST "http://127.0.0.1:8000/ops/emergency/stop?pause_minutes=1440&reason=manual_stop"
```

Web UI:

```bash
open http://127.0.0.1:8000/ui/
```

The dashboard is a lightweight read-only control surface backed by the existing ops APIs. It shows service health, market pool quality, preflight checks, webhook status, and the latest weather batch summary.
The weather discovery path is tag-first: ChamiClaw resolves weather tags from Gamma, fetches tagged active events, and only falls back to `public-search` when no matching tags are available. The market pool stats in `/health`, `/ops/state`, and the Web UI surface the discovery mode, resolved tags, and rejection reasons so we can tell whether Gamma returned no weather events or our local filters rejected them.

## Important Environment Variables

Core runtime:

- `RUN_PROFILE=sim|live`
- `EXECUTION_DRY_RUN=true|false`
- `SCHEDULER_ENABLED=true|false`
- `REPOSITORY_BACKEND=sqlite`
- `SQLITE_PATH=/app/data/chamiclaw_t1.db`
- `PARAMS_PATH=/app/data/strategy_params.json`

Weather strategy:

- `WEATHER_ENABLED=true`
- `WEATHER_BATCH_MAX_CANDIDATES=12`
- `WEATHER_BATCH_MAX_ORDERS=6`
- `WEATHER_MARKET_REFRESH_MINUTES=360`
- `WEATHER_INFO_REFRESH_MINUTES=360`
- `WEATHER_STRATEGY_LOOP_MINUTES=720`
- `WEATHER_MAX_POSITION_PER_MARKET_USD=50`
- `WEATHER_MAX_BATCH_RISK_USD=200`
- `WEATHER_EVENT_TAG_SLUGS=weather,rain,precipitation,forecast`
- `WEATHER_EVENT_PAGE_SIZE=50`
- `WEATHER_EVENT_MAX_PAGES=5`
- `WEATHER_SEARCH_FALLBACK_ENABLED=true`
- `WEATHER_SEARCH_TERMS=rain,precipitation,rainfall,showers`
- `WEATHER_SEARCH_LIMIT_PER_TERM=10`

LLM review:

- `LLM_ENABLED=true|false`
- `LLM_BASE_URL=...`
- `LLM_API_KEY=...`
- `LLM_MODEL=...`
- `LLM_FAILSAFE_MODE=reject|min_size`

Webhook notifications:

- `WEBHOOK_ENABLED=true|false`
- `WEBHOOK_URL=https://claw.alyra.cn/Claw`
- `WEBHOOK_TIMEOUT_SECONDS=5`
- `WEBHOOK_MAX_RETRIES=1`
- `WEBHOOK_SERVICE_NAME=chamiclaw`
- `WEBHOOK_ENVIRONMENT=prod`

## Webhook Payload

The service sends generic `POST JSON` notifications. Payload shape:

```json
{
  "event_type": "weather_batch_completed",
  "ts": "2026-03-08T14:00:00+00:00",
  "service": "chamiclaw",
  "environment": "prod",
  "summary": "manual weather batch completed",
  "details": {
    "candidates": 3,
    "reviewed": 2,
    "executed": 1,
    "rejected": 1
  }
}
```

Current key events:

- `preflight_failed`
- `weather_batch_completed`
- `emergency_stop_triggered`
- `llm_review_failed`
- `execution_error`
- `startup_degraded`

Webhook delivery failures are logged and counted, but they do not stop trading or scheduling.

## Useful Ops Endpoints

- `GET /ui/`
- `GET /health`
- `GET /ops/state`
- `GET /ops/preflight`
- `GET /ops/notifications/health`
- `POST /ops/tick`
- `POST /ops/weather/batch/run`
- `GET /ops/weather/batch/last`
- `POST /ops/emergency/stop`

## Local Python Run

If you want to run without Docker:

```bash
python -m venv .venv
source .venv/bin/activate
pip install -e .[dev]
uvicorn chamiclaw.api.app:app --host 127.0.0.1 --port 8000 --reload
```

Or use the CLI:

```bash
chamiclaw preflight
chamiclaw run --profile sim --port 8000
```

