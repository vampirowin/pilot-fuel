# UI fuel ⛽

> **Система мониторинга и анализа топлива** — загрузка данных с Pilot GPS, сравнение с чеками, расчёт погрешностей и визуализация.

---

## Возможности

| Функция | Описание |
|---|---|
| **Авторизация** | Вход через Pilot API или локальный суперадмин |
| **Панель управления** | Общая статистика и счётчики критических событий |
| **Транспортные средства** | Иерархия Компания → Площадка → Папка с коллапсами, группировка ТС |
| **Заправки** | Иерархия Компания → Площадка → Папка → ТС, данные Pilot vs чек, погрешность |
| **Синхронизация** | Двухфазная (preview + apply): классификация конфликтов, замена/пропуск |
| **Ручной ввод** | Добавление заправок с авто-подбором Pilot-данных |
| **Разметка «Ложная»** | Отметка ошибочных показаний с указанием причины |
| **Графики** | Визуализация показаний датчика уровня топлива (Chart.js) |
| **Пороги точности** | Настраиваемые: норма (≤3%), предупреждение (≤10%), критично (>10%) |
| **Multi-tenant** | Компании, площадки, роли (superadmin / company_admin / user) |
| **Блокировка** | Отключение доступа пользователя из админки без удаления |

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
.\venv\Scripts\activate    # Windows

# Зависимости
pip install -r requirements.txt

# Настройки
cp .env.example .env
```

### Настройка .env

```env
DATABASE_URL=postgresql+asyncpg://postgres:postgres@localhost:5432/pilot_fuel
SECRET_KEY=your-secret-key
APP_PORT=9000
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
# → http://localhost:9000
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
│   ├── api/            # Маршруты (auth, vehicles, refuels, admin)
│   ├── models/         # SQLAlchemy модели (9 шт.)
│   ├── services/       # Сервисы (Pilot API client)
│   ├── templates/      # Jinja2 шаблоны
│   ├── static/         # CSS, JS
│   ├── __init__.py     # FastAPI app factory
│   ├── config.py       # Настройки
│   └── database.py     # SQLAlchemy engine + session
├── alembic/            # Миграции БД
├── run.py              # Точка входа
├── requirements.txt
└── .env
```

---

## Схема БД

```
client_accounts    — id, name
sites              — id, client_account_id (FK), name
users              — id, username, role, client_account_id (FK), site_id (FK), is_active
vehicles           — id, pilot_agent_id, imei, plate_number, folder,
                     client_account_id (FK), site_id (FK), sensor_count, has_fuel_sensor
fuel_sensors       — id, vehicle_id (FK), pilot_sensor_id, tag_id, is_active
pilot_refuels      — id, vehicle_id (FK), event_date, amount, sensor_levels, address
refuel_entries     — id, vehicle_id (FK), pilot_amount, actual_amount,
                     difference, error_percent, comparison_status,
                     is_false, is_deleted
settings           — id, key, value (норма 3%, предупреждение 10%)
sync_log           — id, vehicle_id, sync_type, status, records_affected, details
```

---

## API Endpoints

### Страницы

| Маршрут | Описание |
|---|---|
| `GET /` | Панель управления |
| `GET /vehicles` | Список ТС (иерархия) |
| `GET /refuels` | Заправки (иерархия + фильтры) |
| `GET /critical` | Критические события |
| `GET /admin/*` | Администрирование |
| `POST /logout` | Выход |

### API

| Маршрут | Описание |
|---|---|
| `POST /api/vehicles/sync` | Синхронизация ТС из Pilot |
| `POST /api/refuels/sync/preview` | Предпросмотр синхронизации заправок |
| `POST /api/refuels/sync/apply` | Применить синхронизацию |
| `POST /api/refuels/add` | Ручная заправка |
| `GET/POST /api/refuels/{id}/edit` | Редактирование заправки |
| `POST /api/refuels/{id}/mark-false` | Отметить как ложную |
| `POST /api/refuels/{id}/unmark-false` | Снять отметку |
| `POST /api/refuels/{id}/delete` | Удалить запись |
| `GET /api/critical-count` | Счётчик критических |
| `GET /api/admin/companies/{id}/sites` | Список площадок компании |

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
