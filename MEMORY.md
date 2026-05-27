# pilot-fuel — Memory

## Project
Приложение для работы с датчиками уровня топлива через API Pilot GPS.

## Tech Stack
| Компонент | Технология |
|---|---|
| Backend | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) |
| Миграции | Alembic |
| БД | PostgreSQL 16 — новая БД `pilot_fuel` |
| Фронтенд | Jinja2 + HTMX |
| Графики | Chart.js |
| Auth | Через Pilot API (логин/пароль → токен) |
| HTTP клиент | httpx (async) |
| Дизайн | Тёмная тема (новая цветовая схема) |
| Порт | 9000 |

## Database Schema

```
users              — id, username, pilot_token, node_id, is_admin
vehicles           — id, pilot_agent_id, imei, plate_number, name, folder, is_active
fuel_sensors       — id, vehicle_id, pilot_sensor_id, name, tag_id, unit, is_active
pilot_refuels      — id, vehicle_id, sensor_id, event_date, amount, start_level,
                     end_level, odometer, address, lat, lon, raw_data (JSONB)
refuel_entries     — id, vehicle_id, pilot_refuel_id?, event_date,
                     pilot_amount?, actual_amount?, receipt_number,
                     source{pilot_sync|manual}, difference, error_percent,
                     comparison_status{normal|small_deviation|unacceptable|pilot_missing|false_reading},
                     is_false, false_reason, is_deleted, deleted_at
settings           — id, key, value, description
sync_log           — id, vehicle_id, sync_type, status, records_affected, details
```

> `fuel_level_readings` в БД не храним — подгружаем из Pilot API по запросу.

## Key Decisions

- **Auth**: через Pilot API (как в pilot-monitoring)
- **БД**: новая `pilot_fuel`, не трогаем `ural_monitor` и `lapa59_db`
- **Удаление**: гибридное — мягкое (is_deleted) + жёсткое (DELETE)
- **Ручные заправки без данных Pilot**: статус `pilot_missing` — критическое событие
- **Fuel level readings**: on-demand из Pilot API, не храним в БД
- **График датчика**: Chart.js, данные из `sensors/dip`

## Existing Projects (not to interfere)

| Проект | Порт | БД |
|---|---|---|
| pilot-monitoring | 5000 | JSON / память |
| task-ural-i | 7000 | ural_monitor |
| ural-monitor | 3000 | ural_monitor |
| lapa59 | 8000 | lapa59_db |
| PostgreSQL | 5432 | — |

## Implementation Status

### Phase 1 ✅ — Scaffold + DB + Auth
- ✅ FastAPI project structure created
- ✅ SQLAlchemy async + Alembic + `pilot_fuel` DB created
- ✅ All 7 models created (User, Vehicle, FuelSensor, PilotRefuel, RefuelEntry, Setting, SyncLog)
- ✅ Initial migration applied
- ✅ async PilotService (httpx) — login, get_vehicles, get_fuel_report, get_sensor_dip
- ✅ Auth via Pilot API (login/logout)
- ✅ Jinja2 + HTMX + dark amber theme base template
- ✅ Dashboard page with stats
- ✅ Vehicles page (list + sync from Pilot API)
- ✅ Refuels page (list with filters)
- ✅ Default settings seeded (normal_threshold=3%, warning_threshold=10%)
- ✅ Port: 9000

### Phase 2 — Vehicles & Sensors
- ✅ Sync vehicles from Pilot (`GET /api/v3/vehicles`)
- ⬜ Get fuel sensors from Pilot
- ⬜ Sensors page / display per vehicle

### Phase 3 — Refuels from Pilot
- ⬜ Get fuel report (`GET /api/v3/vehicles/fuel`)
- ⬜ Auto-create refuel_entries
- ⬜ Refuels data table with comparison

### Phase 4 — Receipt input + Comparison
- ⬜ HTMX form for receipt entry
- ⬜ Auto-match with Pilot refuel
- ⬜ Calculate diff + error%
- ⬜ Categorize by thresholds

### Phase 5 — Manual entries + Critical events
- ⬜ "Add manually" button + form
- ⬜ Status `pilot_missing` 🚨
- ⬜ Critical events dashboard

### Phase 6 — Admin
- ⬜ Threshold settings UI
- ⬜ Mark as false / soft delete / restore / hard delete
- ⬜ Re-sync (overwrite)
- ⬜ sync_log view

### Phase 7 — Sensor graph
- ⬜ Modal/page: vehicle + period
- ⬜ On-demand `sensors/dip` from Pilot
- ⬜ Chart.js fuel level graph

### Phase 8 — Polish
- ⬜ PWA
- ⬜ Docker
- ⬜ Excel export

## Workflow Rules

- **Save progress before every fix** — перед каждым исправлением/изменением кода сохранять текущий прогресс: закоммитить изменения в git или сделать бэкап файлов, чтобы можно было откатиться при ошибке.

## Project Structure

```
pilot-fuel/
├── app/
│   ├── __init__.py          # FastAPI app factory
│   ├── config.py            # Settings
│   ├── database.py          # SQLAlchemy engine + session
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── vehicle.py
│   │   ├── fuel_sensor.py
│   │   ├── pilot_refuel.py
│   │   ├── refuel_entry.py
│   │   ├── setting.py
│   │   └── sync_log.py
│   ├── services/
│   │   ├── __init__.py
│   │   └── pilot_service.py  # Async Pilot API client
│   ├── api/
│   │   ├── __init__.py
│   │   ├── auth.py
│   │   ├── vehicles.py
│   │   ├── refuels.py
│   │   └── admin.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── vehicles.html
│   │   ├── refuels.html
│   │   └── admin/
│   │       └── settings.html
│   └── static/
│       ├── css/
│       │   └── style.css
│       └── js/
│           └── main.js
├── alembic/
│   └── versions/
├── alembic.ini
├── requirements.txt
├── .env
├── MEMORY.md
├── AGENTS.md
└── run.py
```

## Ports
- pilot-fuel: **9000** (free)
- PostgreSQL: 5432 (shared)
