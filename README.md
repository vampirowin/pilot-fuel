# UI fuel

> **Система мониторинга и анализа топлива** — загрузка данных с Pilot GPS, сравнение с чеками, расчёт погрешностей и визуализация.

---

## Возможности

| Функция | Описание |
|---|---|
| **Авторизация** | Вход через Pilot API или локальный суперадмин |
| **Панель управления** | Статистика, счётчики критических, графики (Chart.js) |
| **Транспорт** | Иерархия Компания → Площадка → Тип ТС с коллапсами, цветные бейджи, поиск, bulk-операции, статус датчика, per-vehicle пороги |
| **Заправки** | Иерархия Компания → Площадка → Тип ТС → ТС, данные Pilot vs чек, погрешность, статус, сортировка, поиск по номеру, комментарии, исключение из статистики |
| **Синхронизация** | Ручная (preview + apply) и автоматическая ежедневная (APScheduler, 03:00 MSK) |
| **Автосинхронизация** | APScheduler, force-overwrite, перелогин при 401 (если сохранён пароль), логирование в SyncLog с vehicle_updates |
| **Лог синхронизации** | Таблица с деталями, vehicle-уровень, раскрытие «Подробнее», компания вместо логина |
| **График топлива** | Chart.js: drag-to-zoom, колёсико, auto-scale Y, loading/error/empty стейты, кластеризация событий, карта местоположения (Leaflet, ленивая загрузка) |
| **Чеки на графике** | Оранжевые точки на линии уровня с подписью объёма |
| **Карта заправок** | Leaflet (лениво), клик на маркер → карта 200px с адресом |
| **Ручной ввод** | Добавление заправок с авто-подбором Pilot, отслеживание `created_by` с tooltip при наведении |
| **Импорт чеков** | Из буфера обмена (табуляция), предпросмотр, классификация new/update/identical/conflict |
| **Профиль** | Выбор часового пояса (13 российских + основные мировые) |
| **Фильтры** | По компании (superadmin), площадке, типу ТС, статусу, дате, госномеру |
| **Разметка «Ложная»** | Отметка ошибочных показаний в модалке |
| **Пороги точности** | Глобальные + per-vehicle override, абсолютная разница (литры), кнопка пересчёта всех записей |
| **Multi-tenant** | Компании, площадки, роли (superadmin / company_admin / user) |
| **Блокировка** | Отключение доступа без удаления |
| **ТС без датчиков** | Отдельная страница с иерархией и bulk-возвратом |

---

## Технологии

| Компонент | Технология |
|---|---|
| **Backend** | FastAPI (async) |
| **ORM** | SQLAlchemy 2.0 (async) |
| **Миграции** | Alembic |
| **База данных** | PostgreSQL 16 |
| **Фронтенд** | Jinja2 + HTMX |
| **Графики** | Chart.js |
| **Карты** | Leaflet (ленивая загрузка, OpenStreetMap) |
| **Планировщик** | APScheduler (AsyncioIOScheduler) |
| **HTTP клиент** | httpx (async) |
| **Дизайн** | Тёмная тема (амбер) |

---

## Быстрый старт

### Требования

- Python 3.13+
- PostgreSQL 16
- Учётная запись Pilot GPS

### Установка

```bash
# Клонировать
git clone https://github.com/vampirowin/pilot-fuel.git
cd pilot-fuel

# Виртуальное окружение
python -m venv venv
source venv/bin/activate    # Linux
.\venv\Scripts\activate     # Windows

# Зависимости
pip install -r requirements.txt

# Настройки
cp .env.example .env
```

### Настройка .env

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/pilot_fuel
SECRET_KEY=your-secret-key
APP_PORT=9001
```

### База данных

```bash
# Создать БД
psql -U postgres -c "CREATE DATABASE pilot_fuel;"

# Миграции
alembic upgrade head

# Сидирование (пороги точности + суперадмин)
python seed.py
```

### Запуск

```bash
python run.py
# → http://localhost:9001
```

---

## Роли пользователей

| Роль | Доступ |
|---|---|
| **superadmin** | Всё видит, управляет компаниями/пользователями/настройками. Вход: `/admin/login` |
| **company_admin** | Синхронизирует ТС и заправки своей компании, управляет датчиками |
| **user** | Просмотр и ручной ввод данных своей компании/площадки |

---

## Структура проекта

```
pilot-fuel/
├── app/
│   ├── api/            # Маршруты (auth, vehicles, refuels, admin, main, fuel_graph)
│   ├── models/         # SQLAlchemy модели (10 шт.)
│   ├── services/       # Сервисы (Pilot API client)
│   ├── templates/      # Jinja2 шаблоны (20+)
│   ├── static/         # CSS, JS
│   ├── __init__.py     # FastAPI app factory + lifespan
│   ├── config.py       # Настройки
│   ├── database.py     # SQLAlchemy engine + async session
│   ├── scheduler.py    # APScheduler (ежедневная синхронизация)
│   └── dependencies.py # Хелперы фильтрации
├── alembic/            # Миграции БД
├── run.py              # Точка входа
├── requirements.txt
├── seed.py             # Сидирование
├── MEMORY.md           # Памятка
└── AGENTS.md           # Правила для AI
```

---

## Схема БД

```
client_accounts    — id, name
sites              — id, client_account_id (FK), name
users              — id, username, role, pilot_token, pilot_node_id, pilot_password,
                     client_account_id (FK), site_id (FK), is_active, timezone
vehicles           — id, pilot_agent_id, imei, plate_number, name, folder,
                     sensor_count, has_fuel_sensor, sensor_status,
                     enable_abs_threshold, normal_threshold_pct, warning_threshold_pct,
                     normal_threshold_abs, warning_threshold_abs,
                     client_account_id (FK), site_id (FK), is_active
fuel_sensors       — id, vehicle_id (FK), pilot_sensor_id, name, tag_id, unit, is_active
pilot_refuels      — id, vehicle_id (FK), event_date, amount, start_level, end_level,
                     address, lat, lon, raw_data (JSONB)
refuel_entries     — id, vehicle_id (FK), pilot_refuel_id (FK), event_date, pilot_amount,
                     actual_amount, receipt_number, source, difference, error_percent,
                     comparison_status, is_false, false_reason, is_deleted, created_by,
                     comment, exclude_from_stats, created_at, updated_at
settings           — id, key, value (норма 3%, предупреждение 10%)
sync_log           — id, vehicle_id, sync_type, status, records_affected, details,
                     details_json (JSONB), created_by, started_at, completed_at
```

---

## API Endpoints

### Страницы

| Маршрут | Описание |
|---|---|
| `GET /` | Панель управления |
| `GET /vehicles` | Список ТС (иерархия + поиск) |
| `GET /refuels` | Заправки (иерархия + фильтры) |
| `GET /critical` | Критические события (иерархия) |
| `GET /sync-logs` | Лог автосинхронизации |
| `GET /admin/*` | Администрирование |
| `POST /logout` | Выход |

### API

| Маршрут | Описание |
|---|---|
| `GET /profile` | Профиль пользователя (часовой пояс) |
| `POST /profile` | Сохранить часовой пояс |
| `POST /api/vehicles/sync` | Синхронизация ТС из Pilot |
| `POST /api/vehicles/{id}/delete` | Удаление ТС (админ, каскад) |
| `GET /api/vehicles/search` | Поиск ТС (HTMX) |
| `POST /api/vehicles/{id}/toggle-sensor` | Переключить датчик ТС |
| `POST /api/vehicles/bulk-remove-sensor` | Bulk: убрать датчики |
| `GET/POST /api/vehicles/{id}/thresholds` | Per-vehicle пороги (модалка) |
| `POST /api/vehicles/{id}/sensor-status` | Смена статуса датчика |
| `GET /admin/settings/recalculate` | Пересчитать все записи по текущим порогам |
| `POST /api/refuels/sync/preview` | Предпросмотр синхронизации |
| `POST /api/refuels/sync/apply` | Применить синхронизацию |
| `POST /api/refuels/add` | Ручная заправка |
| `GET/POST /api/refuels/{id}/edit` | Редактирование |
| `POST /api/refuels/{id}/mark-false` | Отметить ложной |
| `POST /api/refuels/{id}/unmark-false` | Снять отметку |
| `POST /api/refuels/{id}/delete` | Удалить заправку |
| `GET /api/refuels/{id}/detail` | Детали заправки (модалка) |
| `GET /api/refuels/import-checks-form` | Форма импорта чеков |
| `POST /api/refuels/import-checks-preview` | Предпросмотр импорта |
| `POST /api/refuels/import-checks-apply` | Применить импорт |
| `GET /api/fuel-graph/modal` | Модалка графика топлива |
| `GET /api/fuel-graph/data` | Данные графика (JSON) |
| `GET /api/critical-count` | Счётчик критических |
| `POST /api/sync-logs/trigger` | Запустить синхронизацию |
| `GET /api/admin/vehicles-no-sensor` | ТС без датчиков |
| `GET /api/admin/vehicles-no-sensor/search` | Поиск среди ТС без датчиков |
| `POST /api/admin/vehicles-no-sensor/bulk-restore` | Bulk: вернуть датчики |

---

## Пороги точности

### Глобальные (админ-панель `/admin/settings`)

| Статус | Условие |
|---|---|
| ✅ **Норма** | Погрешность ≤ 3% |
| ⚠️ **Расхождение** | 3% < погрешность ≤ 10% |
| 🚨 **Недопустимо** | Погрешность > 10% |

### Per-vehicle override

Для каждого ТС можно задать свои проценты или включить **проверку по абсолютной разнице** (литры):

- Приоритет: absolute normal → absolute warning → процент normal → процент warning → unacceptable
- Если включён absolute и разница в литрах ≤ `norm_abs` → норма, ≤ `warn_abs` → расхождение
- Если absolute выключен или литровый порог превышен → сравнивается по процентам
- Null-поля → fallback на глобальные настройки

Настраивается через кнопку **«Пороги»** на странице `/vehicles` (superadmin/company_admin).

---

*Сделано для внутреннего мониторинга автопарка.*
