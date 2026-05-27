# pilot-fuel ⛽

> **Система анализа данных датчиков уровня топлива** — загрузка данных с Pilot GPS, сравнение с чеками, расчёт погрешностей и визуализация.

---

## Возможности

| Функция | Описание |
|---|---|
| **Авторизация** | Вход через Pilot API, автоматическое получение токена |
| **Панель управления** | Общая статистика и счётчики критических событий |
| **Транспортные средства** | Загрузка и отображение ТС из Pilot, группировка по папкам |
| **Заправки** | Таблица с разбивкой по ТС: данные Pilot vs факт по чеку, разница, погрешность |
| **Синхронизация** | Пакетная загрузка заправок из Pilot API (batch 20, 3 retries) |
| **Ручной ввод** | Добавление ручных заправок с авто-подбором Pilot-данных |
| **Разметка «Ложная»** | Отметка ошибочных показаний с указанием причины |
| **Графики** | Визуализация показаний датчика уровня топлива (Chart.js) |
| **Пороги точности** | Настраиваемые: норма (≤3%), предупреждение (≤10%), критично (>10%) |
| **Администрирование** | Управление датчиками, порогами, просмотр лога синхронизации |

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
# source venv/bin/activate # Linux/macOS

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

# Сидирование (пороги точности)
python -c "from app.database import engine; from app.seed import seed_settings; import asyncio; asyncio.run(seed_settings())"
```

### Запуск

```bash
python run.py
# → http://localhost:9000
```

---

## Структура проекта

```
pilot-fuel/
├── app/
│   ├── api/            # Маршруты (auth, vehicles, refuels, admin)
│   ├── models/         # SQLAlchemy модели (7 шт.)
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
users              — id, username, pilot_token, node_id, is_admin
vehicles           — id, pilot_agent_id, imei, plate_number, folder, is_active
fuel_sensors       — id, vehicle_id, pilot_sensor_id, tag_id, is_active
pilot_refuels      — id, vehicle_id, event_date, amount, sensor_levels, адрес
refuel_entries     — id, vehicle_id, pilot_amount, actual_amount,
                     difference, error_percent, comparison_status,
                     is_false, is_deleted
settings           — id, key, value (норма 3%, предупреждение 10%)
sync_log           — id, vehicle_id, sync_type, status, records_affected
```

---

## API Endpoints

### Страницы

| Маршрут | Описание |
|---|---|
| `GET /` | Панель управления |
| `GET /vehicles` | Список ТС |
| `GET /refuels` | Заправки (с пагинацией и фильтрами) |
| `GET /critical` | Критические события |
| `GET /admin/settings` | Настройки (admin) |
| `POST /logout` | Выход |

### API

| Маршрут | Описание |
|---|---|
| `POST /api/vehicles/sync` | Синхронизация ТС из Pilot |
| `POST /api/refuels/sync` | Синхронизация заправок |
| `POST /api/refuels/add` | Ручная заправка |
| `GET/POST /api/refuels/{id}/edit` | Редактирование заправки |
| `POST /api/refuels/{id}/mark-false` | Отметить как ложную |
| `POST /api/refuels/{id}/unmark-false` | Снять отметку |
| `POST /api/refuels/{id}/delete` | Удалить запись |
| `GET /api/refuels/{id}/mark-false-form` | Форма причины |
| `GET /api/refuels/{id}/graph` | График датчика |
| `GET /api/critical-count` | Счётчик критических |

---

## Пороги точности

| Статус | Условие |
|---|---|
| ✅ **Норма** | Погрешность ≤ 3% |
| ⚠️ **Отклонение** | 3% < погрешность ≤ 10% |
| 🚨 **Критично** | Погрешность > 10% |
| ❓ **Нет данных Pilot** | Только ручная заправка |

Настраиваются в админ-панели.

---

## Лицензия

MIT

---

*Сделано для внутреннего мониторинга автопарка.*
