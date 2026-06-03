# REUSE GUIDE — pilot-fuel patterns

Быстрый доступ к ключевым решениям и паттернам для повторного использования в других проектах.

---

## 1. FastAPI async app + lifespan

Файл: `app/__init__.py`

```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    start_scheduler()
    yield
    await stop_scheduler()
    await engine.dispose()

def create_app() -> FastAPI:
    app = FastAPI(title="UI fuel", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=..., max_age=48*3600)
    app.mount("/static", StaticFiles(directory="app/static"), name="static")
    app.include_router(router)
    return app
```

---

## 2. Async SQLAlchemy 2.0

Файл: `app/database.py`

```python
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

engine = create_async_engine("postgresql+asyncpg://user:pass@localhost/db")
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

class Base(DeclarativeBase):
    pass

async def get_db():
    async with async_session() as session:
        yield session
```

**Запросы:**
```python
from sqlalchemy import select
result = await db.execute(select(Model).where(Model.field == value))
row = result.scalar_one_or_none()
rows = result.scalars().all()
```

**Одна запись по ID:**
```python
vehicle = await db.get(Vehicle, vehicle_id)
```

---

## 3. Pilot API v3 — HTTP-клиент (httpx async)

Файл: `app/services/pilot_service.py`

### 3.1. Единый клиент (синглтон)

```python
import httpx
_client: httpx.AsyncClient | None = None

def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=120.0)
    return _client
```

### 3.2. Базовый метод запроса

```python
class PilotService:
    def __init__(self, base_url: str | None = None):
        settings = get_settings()
        self.base_url = (base_url or settings.pilot_api_base_url).rstrip("/")

    async def _request(self, method, path, token=None, node_id=0, cookies=None, **kwargs):
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if node_id:
            headers["X-Node"] = str(node_id)
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("X-Requested-With", "XMLHttpRequest")

        client = _get_client()
        client.cookies.clear()
        resp = await client.request(method, f"{self.base_url}{path}", headers=headers, cookies=cookies or {}, **kwargs)
        data = resp.json()
        if data.get("success") is False or data.get("code", 0) != 0:
            raise PilotAuthError(data.get("msg", "Unknown API error"))
        return data
```

### 3.3. Аутентификация

```python
async def login(self, username: str, password: str) -> dict:
    data = await self._request("POST", "/api/v3/auth/token", json={"username": username, "password": password})
    return {"token": data.get("token"), "node_id": data.get("node_id", 0)}
```

### 3.4. Авторизация через cookie (для reports.php)

Некоторые эндпоинты Pilot требуют `cookie: PILOTID` вместо `Authorization: Bearer`:
```python
cookies = {"PILOTID": token, "node": str(node_id)}
data = await self._request("POST", path, token=None, node_id=0, cookies=cookies, ...)
```

---

## 4. Pilot API v3 — Карта эндпоинтов

| Назначение | Метод | Path | Параметры | Выход |
|---|---|---|---|---|
| Авторизация | POST | `/api/v3/auth/token` | `{username, password}` | `{token, node_id}` |
| Список ТС | GET | `/api/v3/vehicles` | — | `data[]` |
| Статус ТС | GET | `/api/v3/vehicles/status?imei=...` | `imei` | `data[0]` |
| GPS-точки | GET | `/api/v3/vehicles/events/raw` | `imei, agent_id, ts, te` | `data.raw[]` |
| Аналог. сенсоры | GET | `/api/v3/vehicles/sensors/dip` | `imei, agent_id, ts, te, tag_id?` | `data[]` |
| Дискр. сенсоры | GET | `/api/v3/vehicles/sensors/discrete` | `imei, agent_id, ts, te, tag_id?` | `data[]` |
| Пробег (trips) | GET | `/api/v3/vehicles/trips` | `imei, agent_id, ts, te` | `data{can, gps, ...}` |
| Стоянки (stops) | GET | `/api/v3/vehicles/track/stops` | `imei, agent_id, ts, te` | `data{stops[], parkings[]}` |
| Трек (сегменты) | GET | `/api/v3/vehicles/track` | `imei, agent_id, ts, te` | `data[]` |
| Отчёт по топливу | POST | `/backend/ax/reports.php` | cookie + form data | см. парсеры |

**Параметры времени:** `ts` (from) и `te` (to) — Unix timestamp (int).

---

## 5. Запросы к Pilot API v3 — конкретные вызовы

### 5.1. Сенсоры (аналоговые + дискретные) с bisect-матчингом

Файл: `app/api/track.py:145-216`

```python
import bisect

# 1. Параллельные запросы с timeouts
results = await asyncio.gather(
    asyncio.wait_for(pilot.get_sensor_dip_history(...), timeout=10),
    asyncio.wait_for(pilot.get_discrete_sensor_data(...), timeout=10),
    asyncio.wait_for(pilot.get_trip_summary(...), timeout=5),
    asyncio.wait_for(pilot.get_track_stops(...), timeout=5),
    return_exceptions=True,
)

# 2. Сбор сенсоров в словарь { id: {name, values: [{ts, value}]} }
sensor_map = {}
for s in sensor_data:
    vals = [{"ts": w["ts"], "value": float(w["value"])} for w in s.get("work", []) if w.get("ts") and w.get("value") is not None]
    vals.sort(key=lambda x: x["ts"])
    sensor_map[s["id"]] = {"name": s["name"], "values": vals}

# 3. Для каждой GPS-точки — ближайшее значение сенсора по времени
for pt in points:
    for sid, sdata in sensor_map.items():
        ts_list = [v["ts"] for v in sdata["values"]]
        idx = bisect.bisect_left(ts_list, pt["ts"])
        if idx == 0:
            nearest = sdata["values"][0]
        elif idx >= len(ts_list):
            nearest = sdata["values"][-1]
        else:
            before, after = sdata["values"][idx-1], sdata["values"][idx]
            nearest = before if (pt["ts"] - before["ts"]) <= (after["ts"] - pt["ts"]) else after
        pt["sensors"][str(sid)] = nearest["value"]
```

### 5.2. Отчёт по заправкам (report_type=38)

Файл: `app/services/pilot_service.py:88-201`

```python
async def get_refuel_report(self, token, node_id, veh_ids, start_date, stop_date):
    cookies = {"PILOTID": token, "node": str(node_id)}
    data = await self._request("POST", PILOT_REPORTS_URL,
        token=None, node_id=0, cookies=cookies,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
        data={"report_type": "38", "fillings": "on", ...})
    return self._parse_refuel_report(data)
```

**Парсинг:** Вложенная структура `data.{date}.{ts_key}.[name, ts, start_level, refuel_amount, end_level, {lat, lon, address}]`.

### 5.3. График топлива (report_type=16)

Файл: `app/services/pilot_service.py:203-351`

Парсит `data.{date}.{veh_name}.{sensors, fillings[], spills[]}`.

---

## 6. HTMX — основные шаблоны

### 6.1. Поиск с debounce

```html
<input hx-get="/refuels" hx-trigger="keyup changed delay:300ms, search"
       hx-target="#refuels-list" hx-swap="innerHTML"
       hx-include="closest .filters-bar">
```

### 6.2. Открытие модалки

```html
<button hx-get="/api/refuels/{id}/edit" hx-target="#modal-container" hx-swap="innerHTML">
  Правка
</button>
```

### 6.3. Сохранение формы + автозакрытие модалки

```html
<form hx-post="/api/refuels/{id}/edit" hx-swap="none"
      hx-on::after-request="if(event.detail.successful) this.closest('.modal-overlay').remove()">
```

### 6.4. HX-Trigger — обновить список после сохранения

**Backend (Python):**
```python
return HTMLResponse("", headers={"HX-Trigger": "refresh-refuels-list"})
```

**Frontend (JS):**
```javascript
document.addEventListener('refresh-refuels-list', function() {
  htmx.ajax('GET', window.location.href, {target: '#refuels-list', swap: 'innerHTML'});
});
```

### 6.5. HX-Boost (SPA-like навигация)

```html
<nav hx-boost="true">
  <a href="/vehicles" class="nav-item">Транспорт</a>
  ...
</nav>
```

HX-запросы идут с заголовком `HX-Request: true` (без `HX-Boosted`). Бэкенд проверяет:
```python
is_hx = request.headers.get("hx-request") == "true"
is_boosted = request.headers.get("hx-boosted") == "true"
if is_hx and not is_boosted:
    return HTMLResponse(rendered_fragment)
```

**ВАЖНО:** `htmx.ajax()` не используй `select:` — он фильтрует ответ, и если сервер возвращает сырой фрагмент без обёртки, результат будет пустым.

---

## 7. Аутентификация (сессии + Pilot token)

Файл: `app/api/auth.py`, `app/dependencies.py`

```python
# Вход через Pilot API
result = await service.login(username, password)
request.session["username"] = username
request.session["token"] = result["token"]
request.session["node_id"] = result["node_id"]

# Middleware
app.add_middleware(SessionMiddleware, secret_key=..., max_age=...)

# Защита эндпоинта
def get_current_username(request: Request) -> str:
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return username

async def get_current_user(request: Request, db: AsyncSession = Depends(get_db)) -> User:
    username = get_current_username(request)
    user = await db.execute(select(User).where(User.username == username))
    return user.scalar_one_or_none()
```

### Фильтрация по ролям (company_admin)

```python
def apply_vehicle_filter(query, user, model_vehicle=Vehicle):
    if user.role == "superadmin":
        return query  # видит всё
    if user.client_account_id:
        query = query.where(model_vehicle.client_account_id == user.client_account_id)
        if user.site_id:
            query = query.where(model_vehicle.site_id == user.site_id)
    else:
        query = query.where(False)
    return query
```

### Token fallback (админ компании)

Если у пользователя нет своего token, можно взять у админа его компании:
```python
admin = await db.execute(
    select(User).where(
        User.client_account_id == vehicle.client_account_id,
        User.role == "company_admin",
        User.pilot_token.isnot(None),
    )
).scalar_one_or_none()
token = admin.pilot_token
```

---

## 8. Планировщик (APScheduler async)

Файл: `app/scheduler.py`

```python
from apscheduler.schedulers.asyncio import AsyncIOScheduler

_scheduler: AsyncIOScheduler | None = None

def start_scheduler():
    global _scheduler
    if _scheduler is not None:
        return
    _scheduler = AsyncIOScheduler(timezone="Europe/Moscow")
    _scheduler.add_job(sync_all_companies, "cron", hour=3, minute=0, id="daily_sync")
    _scheduler.start()

async def stop_scheduler():
    if _scheduler:
        _scheduler.shutdown(wait=False)
```

**Re-login при 401:** Если Pilot возвращает 401, планировщик пробует перелогиниться с сохранённым паролем и обновляет токен в БД.

---

## 9. Часовые пояса

Файл: `app/timezone_utils.py`

```python
import zoneinfo
from datetime import datetime, timezone

def get_user_timezone(user=None) -> zoneinfo.ZoneInfo:
    if user and user.timezone:
        return zoneinfo.ZoneInfo(user.timezone)
    return zoneinfo.ZoneInfo("Europe/Moscow")

def utc_to_tz(dt: datetime, tz: zoneinfo.ZoneInfo) -> datetime:
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(tz)

def format_dt(dt: datetime, fmt: str, user=None) -> str:
    tz = get_user_timezone(user)
    return utc_to_tz(dt, tz).strftime(fmt)

def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)
```

---

## 10. Иерархия для таблицы заправок

Файл: `app/api/refuels.py:46-94`

Строит дерево `Компания → Площадка → Папка → ТС → записи`. Сортировка: сначала не-пустые ключи, потом `"Без компании"` и т.п.

```python
def build_refuel_hierarchy(entries: list, vmap: dict) -> list:
    tree = {}
    for entry in entries:
        vin = vmap.get(entry.vehicle_id, {})
        cname = vin.get("company") or "Без компании"
        sname = vin.get("site") or "Без площадки"
        folder = vin.get("folder") or "Без папки"
        tree.setdefault(cname, {}).setdefault(sname, {}).setdefault(folder, {}).setdefault(...)
    # результат: [(company, count, [(site, count, [(folder, count, [(vid, plate, entries)])])])]
```

---

## 11. PWA (manifest + service worker)

Файл: `app/__init__.py:37-54`

```python
@app.get("/manifest.json", include_in_schema=False)
async def manifest():
    return FileResponse(os.path.join(STATIC_DIR, "manifest.json"), media_type="application/manifest+json")

@app.get("/sw.js", include_in_schema=False)
async def service_worker():
    response = FileResponse(os.path.join(STATIC_DIR, "sw.js"), media_type="application/javascript")
    response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
    response.headers["Service-Worker-Allowed"] = "/"
    return response
```

---

## 12. Карта треков (Leaflet + параллельные API)

Файл: `app/templates/track_modal.html`

### Async загрузка Leaflet

```javascript
function loadMap() {
    if (map) return;
    if (window.L) { initMap(); return; }
    // link rel=stylesheet для leaflet.css
    // script src=leaflet.js → onload = initMap
}
```

### initMap + attachMapHandlers

```javascript
function initMap() {
    map = L.map('tm-map').setView([55.75, 37.61], 6);
    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', { maxZoom: 18 }).addTo(map);
    attachMapHandlers();
    setTimeout(function() { if (map) map.invalidateSize(); }, 100);
    load();
}
function attachMapHandlers() {
    map.on('zoomend', function() { /* показать/скрыть точки */ });
    map.on('click', function(e) { /* ближайшая точка + popup */ });
    window.addEventListener('resize', function() { map.invalidateSize(); });
}
```

**ВАЖНО:** `map.on()` и `addEventListener('resize')` вызывать ТОЛЬКО после `L.map()` — внутри `initMap()` или `attachMapHandlers()`.

### Стрелки направления (▲) каждые 8 сегментов

```javascript
function bearing(lat1, lon1, lat2, lon2) {
    var dLon = (lon2 - lon1) * Math.PI / 180;
    var y = Math.sin(dLon) * Math.cos(lat2 * Math.PI / 180);
    var x = Math.cos(lat1 * Math.PI / 180) * Math.sin(lat2 * Math.PI / 180) -
            Math.sin(lat1 * Math.PI / 180) * Math.cos(lat2 * Math.PI / 180) * Math.cos(dLon);
    return (Math.atan2(y, x) * 180 / Math.PI + 360) % 360;
}
// Использование:
var bear = bearing(s.lat1, s.lon1, s.lat2, s.lon2);
L.marker([(s.lat1+s.lat2)/2, (s.lon1+s.lon2)/2], {
    icon: L.divIcon({html: '<div style="transform:rotate('+bear+'deg);font-size:14px">▲</div>', iconSize: [14,14], iconAnchor: [7,7], className: ''}),
    interactive: false
}).addTo(map);
```

### Фильтрация сегментов (gap + distance)

```javascript
var GAP_THRESHOLD = 900;  // 15 мин
var DIST_THRESHOLD = 5000;  // 5 км
if (gap > GAP_THRESHOLD) continue;
if (map.distance([lat1,lon1],[lat2,lon2]) > DIST_THRESHOLD) continue;
```

### Точки GPS (yellow dots) при zoom ≥ 14

```javascript
var dotGroup = L.layerGroup();
L.circleMarker([lat, lon], {radius: 3, color: '#fbbf24', ...}).addTo(dotGroup);
dotGroup.addTo(map);
if (map.getZoom() < 14) map.removeLayer(dotGroup);
// В zoomend:
map.on('zoomend', function() {
    if (map.getZoom() >= 14) map.addLayer(window._tmDotGroup);
    else map.removeLayer(window._tmDotGroup);
});
```

---

## 13. Refuel sync flow (Pilot → local DB)

Файл: `app/scheduler.py:67-271`

```python
# 1. Получить список ТС компании с fuel_sensor
# 2. Бачами по 20 → get_refuel_report(report_type=38)
# 3. Для каждого события:
#    a. _match_vehicle(ev, vehicles) — сопоставить по имени/agent_id
#    b. _parse_timestamp(ev["ts"])
#    c. Проверить, есть ли уже PilotRefuel ±1 час
#    d. Если есть — обновить (amount, дату), пересчитать RefuelEntry.comparison_status
#    e. Если нет — создать PilotRefuel + RefuelEntry
# 4. Сохранить SyncLog с деталями
```

---

## 14. Импорт чеков (Excel)

Файл: `app/api/refuels.py` (search for `import-checks`, `upload_excel`)

Загрузка Excel-файла, парсинг строк, матчинг по дате±1 час + vehicle_id, создание/обновление `RefuelEntry`.

---

## 15. Полезные мелочи

### Загрузка значений фильтров из localStorage (collapsible sections)

```javascript
function toggleGroup(id) {
    const body = document.getElementById(id);
    const isCollapsed = body.classList.toggle('collapsed');
    localStorage.setItem('folder:' + id, isCollapsed ? '1' : '0');
}
document.querySelectorAll('.collapsible-body').forEach(function(el) {
    if (localStorage.getItem('folder:' + el.id) === '1') el.classList.add('collapsed');
});
```

### Chart.js в модалке

Файл: `app/api/fuel_graph.py`, `app/templates/fuel_graph_modal.html`

Инициализировать Chart.js после вставки модалки: слушать `htmx:afterSettle` или `hx-on::after-request`.

### Full-screen modal для Leaflet карты

```html
<div class="modal" style="width:95vw;max-width:100%;max-height:92vh;">
    <div id="tm-map" style="height:calc(92vh - 170px);min-height:300px"></div>
</div>
```
