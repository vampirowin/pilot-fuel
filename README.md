# UI fuel ⛽

> **Система мониторинга и анализа топлива** — загрузка данных с Pilot GPS, сравнение с чеками, расчёт погрешностей и визуализация.

---

## Возможности

| Функция | Описание |
|---|---|
| **Авторизация** | Вход через Pilot API или локальный суперадмин |
| **Панель управления** | Статистика, счётчики критических, графики (Chart.js) |
| **Транспорт** | Иерархия Компания → Площадка → Тип ТС с коллапсами, цветные бейджи, поиск, bulk-операции |
| **Заправки** | Иерархия Компания → Площадка → Тип ТС → ТС, данные Pilot vs чек, погрешность, статус, сортировка, поиск по номеру |
| **Синхронизация** | Двухфазная (preview + apply): классификация (new/conflict/identical/info), замена/пропуск, детали новых записей |
| **Ручной ввод** | Добавление заправок с авто-подбором Pilot, отслеживание `created_by` с tooltip при наведении |
| **Импорт чеков** | Из буфера обмена (табуляция), предпросмотр, классификация new/update/identical/conflict |
| **Профиль** | Выбор часового пояса (13 российских + основные мировые) |
| **Фильтры** | По компании (superadmin), площадке, типу ТС, статусу, дате, госномеру |
| **Разметка «Ложная»** | Отметка ошибочных показаний в модалке |
| **Пороги точности** | Настраиваемые: норма, предупреждение, критично |
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
│   ├── api/            # Маршруты (auth, vehicles, refuels, admin, main)
│   ├── models/         # SQLAlchemy модели (9 шт.)
│   ├── services/       # Сервисы (Pilot API client)
│   ├── templates/      # Jinja2 шаблоны (20+)
│   ├── static/         # CSS, JS
│   ├── __init__.py     # FastAPI app factory
│   ├── config.py       # Настройки
│   └── database.py     # SQLAlchemy engine + session
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
users              — id, username, role, pilot_token, client_account_id (FK), site_id (FK), is_active
vehicles           — id, pilot_agent_id, imei, plate_number, name, folder,
                     sensor_count, has_fuel_sensor, client_account_id (FK), site_id (FK)
fuel_sensors       — id, vehicle_id (FK), pilot_sensor_id, name, tag_id, unit, is_active
pilot_refuels      — id, vehicle_id (FK), event_date, amount, start_level, end_level, address
refuel_entries     — id, vehicle_id (FK), pilot_refuel_id (FK), event_date, pilot_amount,
                     actual_amount, receipt_number, source, difference, error_percent,
                     comparison_status, is_false, false_reason, is_deleted, created_by
settings           — id, key, value (норма 3%, предупреждение 10%)
sync_log           — id, vehicle_id, sync_type, status, records_affected, details
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
| `GET /api/fuel-graph/modal` | График топлива (Chart.js) |
| `GET /api/critical-count` | Счётчик критических |
| `GET /api/admin/vehicles-no-sensor` | ТС без датчиков |
| `GET /api/admin/vehicles-no-sensor/search` | Поиск среди ТС без датчиков |
| `POST /api/admin/vehicles-no-sensor/bulk-restore` | Bulk: вернуть датчики |

---

## Пороги точности

| Статус | Условие |
|---|---|
| ✅ **Норма** | Погрешность ≤ 3% |
| ⚠️ **Расхождение** | 3% < погрешность ≤ 10% |
| 🚨 **Недопустимо** | Погрешность > 10% |
| ❓ **Нет в Pilot** | Только ручная заправка |

Настраиваются в админ-панели.

---

*Сделано для внутреннего мониторинга автопарка.*
