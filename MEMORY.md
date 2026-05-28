# UI fuel — Memory

## Project
Приложение для работы с датчиками уровня топлива через API Pilot GPS.

## Tech Stack
| Компонент | Технология |
|---|---|
| Backend | FastAPI (async) |
| ORM | SQLAlchemy 2.0 (async) |
| Миграции | Alembic |
| БД | PostgreSQL 16 — `pilot_fuel` на localhost:5432 |
| Фронтенд | Jinja2 + HTMX |
| Графики | Chart.js |
| Auth | Pilot API (логин/пароль → токен) + локальный superadmin |
| HTTP клиент | httpx (async) |
| Дизайн | Тёмная тема (новая цветовая схема) |
| Порт | 9001 |

## Database Schema

```
client_accounts    — id, name, created_at
sites              — id, client_account_id (FK), name, created_at
users              — id, username, role {superadmin|company_admin|user},
                     pilot_token?, pilot_node_id?, password_hash?,
                     client_account_id (FK), site_id (FK)
vehicles           — id, pilot_agent_id, imei, plate_number, name, folder,
                     sensor_count, has_fuel_sensor,
                     client_account_id (FK), site_id (FK), is_active
fuel_sensors       — id, vehicle_id (FK), pilot_sensor_id, name, tag_id, unit, is_active
pilot_refuels      — id, vehicle_id (FK), sensor_id (FK), event_date, amount,
                     start_level, end_level, odometer, address, lat, lon, raw_data (JSONB)
refuel_entries     — id, vehicle_id (FK), pilot_refuel_id (FK), event_date,
                     pilot_amount?, actual_amount?, receipt_number,
                     source{pilot_sync|manual}, difference, error_percent,
                     comparison_status{normal|small_deviation|unacceptable|pilot_missing|false_reading},
                     is_false, false_reason, is_deleted, deleted_at
settings           — id, key, value, description
sync_log           — id, vehicle_id, sync_type, status, records_affected, details
```

## Key Decisions

- **Multi-tenant**: изоляция по `client_account_id`. Superadmin видит всё.
- **Суперадмин**: локальный логин/пароль (из `.env`), не имеет доступа к Pilot API. Создаётся через `seed.py`.
- **Роли**: `superadmin` (всё видит, управляет компаниями/пользователями), `company_admin` (синхронизирует ТС и заправки своей компании), `user` (только просмотр/правка данных своей компании)
- **Площадки (Site)**: дочерние сущности компании. Пользователь может быть привязан к конкретной площадке (видит только ТС этой площадки) или ко всем.
- **Owner/location из Pilot не тянем** — показываем название компании как собственника.
- **Привязка ТС к площадке**: вручную через админку (выбрать площадку → выбрать свободные ТС → привязать).
- **Удаление**: жёсткое (DELETE). Для pilot_sync записей — только superadmin.
- **Fuel level readings**: on-demand из Pilot API, не храним в БД.
- **Репозиторий**: `https://github.com/vampirowin/pilot-fuel` (публичный)

## Суперадмин
- Логин: `eddikwin` (из `.env`)
- Пароль: `Iwgtsd6giw!` (из `.env`)
- Вход: `/admin/login`

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
- ✅ FastAPI project structure
- ✅ SQLAlchemy async + Alembic + `pilot_fuel` DB
- ✅ All models + initial migration
- ✅ async PilotService (httpx) — login, get_vehicles, get_fuel_report, get_sensor_dip
- ✅ Auth: Pilot API login + superadmin login
- ✅ Jinja2 + HTMX + dark amber theme
- ✅ Dashboard, Vehicles, Refuels pages
- ✅ Default settings seeded

### Phase 2 ✅ — Vehicles & Sensors
- ✅ Sync vehicles from Pilot (with client_account_id tagging)
- ✅ Vehicle search / folder grouping
- ✅ Admin: toggle sensor, vehicles without sensor

### Phase 3 ✅ — Refuels from Pilot
- ✅ Batch sync from Pilot (batch 20, 3 retries)
- ✅ Auto-create refuel_entries
- ✅ Refuels data table with comparison
- ✅ Cookie auth for reports.php
- ✅ Pagination (10 vehicle groups, HTMX)

### Phase 4 ✅ — Receipt input + Comparison
- ✅ HTMX form for receipt entry (add/edit modals)
- ✅ Auto-match with Pilot refuel (±1h)
- ✅ Calculate diff + error%
- ✅ Threshold-based categorization

### Phase 5 ✅ — Manual entries + Critical events
- ✅ Manual entry add
- ✅ Status `pilot_missing`
- ✅ Critical events dashboard + counter

### Phase 6 ✅ — Multi-tenant Admin
- ✅ Superadmin with local auth (eddikwin)
- ✅ ClientAccount (companies) CRUD
- ✅ Site (площадки) CRUD, vehicle-site assignment
- ✅ User management (role/company/site assignment)
- ✅ Filtering all queries by role/company/site
- ✅ Vehicles without sensor page (checkboxes + bulk return, доступен company_admin)
- ✅ Threshold settings
- ✅ Vehicles page: 3-level collapsible hierarchy (Company → Site → Folder)
- ✅ Bulk toggle: checkbox-select + "ТС без датчиков" / "Вернуть" actions
- ✅ Real-time search on site-vehicle page with checkbox persistence across HTMX swaps
- ✅ Visual hierarchy: amber left-border accents per level (company→site→folder)

### Phase 7 — Sensor graph
- ⬜ On-demand `sensors/dip` from Pilot
- ⬜ Chart.js fuel level graph

### Phase 8 — Polish
- ✅ GitHub repo (vampirowin/pilot-fuel)
- ✅ README.md
- ⬜ PWA, Docker, Excel export

## Workflow Rules

- **Save progress before every fix** — перед каждым исправлением/изменением кода сохранять текущий прогресс.

## Project Structure

```
pilot-fuel/
├── app/
│   ├── __init__.py
│   ├── config.py
│   ├── database.py
│   ├── models/
│   │   ├── __init__.py
│   │   ├── user.py
│   │   ├── vehicle.py
│   │   ├── client_account.py
│   │   ├── site.py
│   │   ├── fuel_sensor.py
│   │   ├── pilot_refuel.py
│   │   ├── refuel_entry.py
│   │   ├── setting.py
│   │   └── sync_log.py
│   ├── services/
│   │   └── pilot_service.py
│   ├── api/
│   │   ├── auth.py
│   │   ├── main.py
│   │   ├── vehicles.py
│   │   ├── refuels.py
│   │   └── admin.py
│   ├── templates/
│   │   ├── base.html
│   │   ├── login.html
│   │   ├── admin_login.html
│   │   ├── dashboard.html
│   │   ├── vehicles.html
│   │   ├── refuels.html
│   │   ├── critical.html
│   │   ├── sync_modal.html
│   │   ├── sync_preview.html
│   │   ├── sync_result.html
│   │   ├── add_refuel_modal.html
│   │   ├── edit_refuel_modal.html
│   │   ├── mark_false_modal.html
│   │   ├── vehicles_search.html
│   │   └── admin/
│   │       ├── companies.html
│   │       ├── company_edit.html
│   │       ├── sites.html
│   │       ├── site_edit.html
│   │       ├── site_vehicles.html
│   │       ├── users.html
│   │       ├── user_edit.html
│   │       ├── settings.html
│   │       └── vehicles_no_sensor.html
│   └── static/
│       ├── css/style.css
│       └── js/main.js
├── alembic/
│   └── versions/
├── alembic.ini
├── requirements.txt
├── .env
├── seed.py
├── MEMORY.md
├── AGENTS.md
└── run.py
```

## Recent Changes

### 2026-05-28 — Vehicles page redesign + Refuel sync overhaul

**Vehicles page:**
- Все уровни иерархии (Company / Site / Folder) получили `<span class="level-badge">` с цветной подложкой:
  - **Company** — amber `#fbbf24` на тёмном фоне
  - **Site** — blue `#93c5fd` (отделяется от amber)
  - **Folder** — gray `#d1d5db` (третичный уровень)
- Badge имеют `border-radius: 20px`, `border: 1px solid`, внутренний padding
- Хедеры выровнены через flex: стрелка слева → badge по центру → счётчик справа
- Изменения в `vehicles.html` (styles + template) и `vehicles.py` (`render_nested_partial`)

**Refuel sync — новая двухфазная синхронизация:**
- **Loading indicator** в `sync_modal.html` — спиннер + "Загружаем данные из Pilot..." на время запроса
- **Time window** изменён с 30 секунд на **±1 час** для поиска дубликатов
- **Preview endpoint** `POST /api/refuels/sync/preview`:
  - Запрашивает Pilot API, классифицирует события: new / conflict / false_conflict / identical
  - Возвращает сводку (ск badges) + таблицу конфликтов с радио "Заменить" / "Пропустить"
- **Apply endpoint** `POST /api/refuels/sync/apply`:
  - Перезапрашивает Pilot API с теми же параметрами
  - Применяет выбор пользователя: новые добавляет, замены обновляют Pilot-поля (сохраняя `actual_amount`, `receipt_number`, `is_false`)
  - Идентичные записи пропускаются
- **Новые шаблоны**: `sync_preview.html`, `sync_result.html`

### 2026-05-28 — Fixes + Refuels hierarchy + User blocking

**Исправления:**
- **Баг dashboard**: убрано `site_id.is_(None)` из фильтров — пользователь с привязкой к площадке видит только ТС этой площадки
- **Ошибка входа**: неверный логин/пароль теперь показывается как `alert-error` на странице логина (был голый текст)
- **Кнопки синхронизации** спрятаны для обычных пользователей (dashboard, vehicles, refuels)

**Новое:**
- **Иерархия заправок**: Company → Site → Folder → ТС, как на странице ТС (функции `build_refuel_hierarchy` + `_render_refuel_hierarchy` в `refuels.py`)
- **Блокировка пользователей**: поле `is_active` в модели User, проверка при входе и в `get_current_user`, управление из админки (users.html + user_edit.html)
- **Бренд**: UI → UI fuel (заголовки, title, sidebar)

### 2026-05-28 — Hierarchy header fix + refuels sort/filters

**Иерархия (Транспорт + Заправки):**
- Исправлено: заголовки иерархии используют `.level-badge` с названием внутри цветной пилюли (amber — компания, blue — площадка, gray — тип ТС)
- Тип ТС выровнен влево (`text-align: left`)
- В иерархию заправок добавлены такие же level-badge

**Сортировка + фильтры в Заправках:**
- Сортировка по умолчанию: от старых к новым (event_date ASC)
- Фильтр "Компания" — только для суперадмина
- Фильтр "Площадка" — для всех пользователей

**Сортировка ТС внутри иерархии:**
- Внутри папок ТС сортируются по госномеру (plate), а не по vehicle_id
- Пагинация в заправках также сортирует группы по plate

**sync_debug.log**: удалён из отслеживания git, добавлен в .gitignore

## Ports
- UI fuel: **9001** (9000 занят zombie PID 32440)
- PostgreSQL: 5432 (shared)
