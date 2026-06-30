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

8. **Pilot API reference** — when adding new Pilot API functions, always check `v3.en.yaml` in the project root for endpoint params, schemas, and response formats.

9. **Database safety** — use the `pilot_fuel` database. Never modify `ural_monitor` or `lapa59_db`.

10. **VPS**: `ssh -i ~/.ssh/lapa-vps root@80.78.247.177`. Приложение в Docker-стеке lapa-gps (`/opt/lapa-gps/infra/docker-compose.prod.yml`). nginx → `pilot-fuel:9001`.

## Tech Stack
- FastAPI (async) + SQLAlchemy 2.0 (async) + Alembic
- PostgreSQL 18 (на VPS хосте, не Docker)
- Jinja2 + HTMX + Chart.js
- httpx (async HTTP client for Pilot API)
- Port: 9001 (internal, Docker)
- Домен: fuel.ural-i.ru (Let's Encrypt SSL)
- VPS: 80.78.247.177, Ubuntu 26.04

## Key Files
- `MEMORY.md` — full plan, schema, decisions, project structure
- `run.py` — app entry point
- `app/config.py` — configuration
- `app/services/pilot_service.py` — async Pilot API client
- `v3.en.yaml` — Pilot GPS API v3 OpenAPI spec (reference for endpoint params/responses)
- `REUSE_GUIDE.md` — reusable code patterns (Pilot API calls, sensor matching, etc.)
- `Dockerfile` — container image (python:3.13-slim)

## Pilot API v3 — Sensor Endpoints (used in track map)
- `GET /api/v3/vehicles/status` — current position + all sensor values (`sensors[]` with `name, dig_value, hum_value`)
- `GET /api/v3/vehicles/sensors/dip` — analog sensor history for period
- `GET /api/v3/vehicles/sensors/discrete` — discrete sensor history for period
- `GET /api/v3/vehicles/instant-status?imei=...&ts=...` — odometer + fuel_level at a timestamp
- `GET /api/v3/vehicles/status-by-time?agent_id=...&ts=...` — sensor snapshot at a timestamp
- `GET /api/v3/vehicles/events/raw` — raw GPS points (lat, lon, ts, speed, sat, alt)
- `GET /api/v3/vehicles/odo-fuel?imei=...&agent_id=...&ts=...&te=...` — odometer + fuel points for a period
