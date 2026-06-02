# AGENTS.md — pilot-fuel

## Project Rules

0. **Save progress before every fix** — перед каждым исправлением/изменением сохранять текущий прогресс (git commit или бэкап).

## Project Rules

1. **Use skills & agents** — for complex tasks, load relevant skills and delegate to specialist agents (frontend-specialist, backend-specialist, database-architect, debugger, etc.).

2. **MCPs are available** — use Chrome DevTools (cdt), clipboard-vision, and any other enabled MCP tools when they help with the task.

3. **Ask before destructive actions** — always confirm with the user before:
   - Dropping/recreating tables or databases
   - Deleting files (outside temp/auto-generated)
   - Force-pushing or resetting git
   - Changing ports or infrastructure
   - Installing global packages or system dependencies
   - Any action that could affect other projects (ural_monitor, lapa59_db, etc.)

4. **Don't auto-start the app** — tell the user to restart when needed with instructions like "перезапусти приложение".

5. **Edit existing files** — don't create new files unless explicitly required.

6. **No comments in code** — unless the why is non-obvious.

7. **Fix root causes** — don't paper over errors with try/except or fallbacks.

8. **Database safety** — use the `pilot_fuel` database. Never modify `ural_monitor` or `lapa59_db`.

## Tech Stack
- FastAPI (async) + SQLAlchemy 2.0 (async) + Alembic
- PostgreSQL 16 on localhost:5432
- Jinja2 + HTMX + Chart.js
- httpx (async HTTP client for Pilot API)
- Port: 9000

## Key Files
- `MEMORY.md` — full plan, schema, decisions, project structure
- `run.py` — app entry point
- `app/config.py` — configuration
- `app/services/pilot_service.py` — async Pilot API client
- `docs/pilot-api-v3.yaml` — Pilot GPS API v3 OpenAPI spec (reference for endpoint params/responses)

## Pilot API v3 — Sensor Endpoints (used in track map)
- `GET /api/v3/vehicles/status` — current position + all sensor values (`sensors[]` with `name, dig_value, hum_value`)
- `GET /api/v3/vehicles/sensors/dip` — analog sensor history for period
- `GET /api/v3/vehicles/sensors/discrete` — discrete sensor history for period
- `GET /api/v3/vehicles/instant-status?imei=...&ts=...` — fuel_level + odometer at a timestamp
- `GET /api/v3/vehicles/status-by-time?agent_id=...&ts=...` — sensor snapshot at a timestamp
- `GET /api/v3/vehicles/events/raw` — raw GPS points (lat, lon, ts, speed, sat, alt)
