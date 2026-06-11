import asyncio
from datetime import datetime, timezone
import httpx

# Pilot API v3 endpoints (некоторые эндпоинты — /backend — используют cookie-авторизацию вместо Bearer)

PILOT_AUTH_URL = "/api/v3/auth/token"
PILOT_VEHICLES_URL = "/api/v3/vehicles"
PILOT_VEHICLE_STATUS_URL = "/api/v3/vehicles/status"
PILOT_REPORTS_URL = "/backend/ax/reports.php"  # POST с form-data и cookie PILOTID
PILOT_SENSOR_DIP_URL = "/api/v3/vehicles/sensors/dip"

# Единый HTTP-клиент на весь процесс. Connection pool разделяется между всеми запросами.
_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None:
        _client = httpx.AsyncClient(timeout=120.0)
    return _client


async def _close_client():
    global _client
    if _client is not None:
        await _client.aclose()
        _client = None


async def _log_sync(msg: str):
    def _write():
        with open("sync_debug.log", "a") as lf:
            lf.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] {msg}\n")
    await asyncio.to_thread(_write)


class PilotAuthError(Exception):
    pass


class PilotService:
    def __init__(self, base_url: str | None = None):
        from app.config import get_settings
        settings = get_settings()
        self.base_url = (base_url or settings.pilot_api_base_url).rstrip("/")

    async def _request(self, method: str, path: str, token: str | None = None, node_id: int = 0, cookies: dict | None = None, **kwargs) -> dict:
        # Для /backend/ax/reports.php авторизация через cookie (PILOTID)
        # Для основных API v3 — через Bearer token + X-Node
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if node_id:
            headers["X-Node"] = str(node_id)
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("X-Requested-With", "XMLHttpRequest")

        client = _get_client()
        client.cookies.clear()  # сбрасываем, т.к. куки передаём явно
        resp = await client.request(method, f"{self.base_url}{path}", headers=headers, cookies=cookies or {}, **kwargs)
        await _log_sync(f"[request] {method} {path} -> {resp.status_code} ({len(resp.content)} bytes)")
        data = resp.json()

        await _log_sync(f"[response] {path} -> {str(data)[:300]}")

        # Pilot API сигнализирует ошибку через success: false или code != 0
        if data.get("success") is False or data.get("code", 0) != 0:
            raise PilotAuthError(data.get("msg", "Unknown API error"))
        return data

    async def login(self, username: str, password: str) -> dict:
        """Аутентификация в Pilot API. Возвращает Bearer token + node_id (раздел сервера)."""
        data = await self._request("POST", PILOT_AUTH_URL, json={"username": username, "password": password})
        return {
            "token": data.get("token"),
            "node_id": data.get("node_id", 0),
        }

    async def get_vehicles(self, token: str, node_id: int = 0) -> list[dict]:
        data = await self._request("GET", PILOT_VEHICLES_URL, token=token, node_id=node_id)
        return data.get("data", [])

    async def get_vehicle_status(self, token: str, node_id: int, imei: str) -> dict | None:
        try:
            data = await self._request(
                "GET", f"{PILOT_VEHICLE_STATUS_URL}?imei={imei}",
                token=token, node_id=node_id,
            )
            vehicles = data.get("data", [])
            return vehicles[0] if vehicles else None
        except PilotAuthError:
            return None

    async def get_all_vehicle_statuses(self, token: str, node_id: int, imeis: list[str] | None = None) -> dict[str, dict]:
        """Возвращает статусы ТС: {imei: {lat, lon, ts}}.

        Делает параллельные запросы (с ограничением 10) для каждого imei.
        API требует imei или agent_id, без них возвращает ошибку.
        """
        if not imeis:
            return {}
        sem = asyncio.Semaphore(10)
        async def _fetch(imei: str) -> tuple[str, dict | None]:
            async with sem:
                try:
                    d = await self.get_vehicle_status(token, node_id, imei)
                    if d:
                        return imei, {"lat": d.get("lat"), "lon": d.get("lon"), "ts": int(d.get("unixtimestamp", 0))}
                except Exception:
                    pass
            return imei, None
        results = await asyncio.gather(*[_fetch(i) for i in imeis if i])
        return {imei: data for imei, data in results if data}

    async def fetch_refuel_reports_batch(
        self, token: str, node_id: int,
        veh_ids: list[int],
        start_str: str, stop_str: str,
        batch_size: int = 20,
        max_attempts: int = 3,
        on_retry=None,
    ) -> list[dict]:
        all_events = []
        for i in range(0, len(veh_ids), batch_size):
            batch = veh_ids[i:i + batch_size]
            attempt = 0
            while attempt < max_attempts:
                try:
                    batch_events = await self.get_refuel_report(token, node_id, batch, start_str, stop_str)
                    break
                except Exception as e:
                    attempt += 1
                    if on_retry:
                        result = await on_retry(e, attempt)
                        if result:
                            token, node_id = result
                            continue
                    if attempt >= max_attempts:
                        raise
                    await asyncio.sleep(1)
            all_events.extend(batch_events)
            await asyncio.sleep(0.5)
        return all_events

    async def get_refuel_report(
        self,
        token: str,
        node_id: int,
        veh_ids: list[int],
        start_date: str,
        stop_date: str,
    ) -> list[dict]:
        """
        Отчёт по заправкам из Pilot API (report_type=38).

        Внимание: этот эндпоинт — /backend/ax/reports.php — НЕ поддерживает Bearer token.
        Авторизация через cookie PILOTID + node.
        Параметры передаются как application/x-www-form-urlencoded (не JSON).

        start_date / stop_date: формат "DD.MM.YYYY HH:MM"
        Response: вложенная структура data.{date}.{ts_key}.[name, ts, start_level, refuel_amount, end_level, {lat, lon, address}]
        """
        veh_str = ",".join(str(v) for v in veh_ids)

        from datetime import datetime
        start = datetime.strptime(start_date, "%d.%m.%Y %H:%M")
        stop = datetime.strptime(stop_date, "%d.%m.%Y %H:%M")
        start_month = f"{start.month:02d}.{start.year}"
        stop_month = f"{stop.month:02d}.{stop.year}"
        pre_start = start.strftime("%d.%m.%Y")
        pre_stop = stop.strftime("%d.%m.%Y")

        cookies = {"PILOTID": token, "node": str(node_id)}
        data = await self._request(
            "POST", PILOT_REPORTS_URL,
            token=None, node_id=0, cookies=cookies,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "download": "0",
                "start_time": "00:00",
                "stop_time": "00:00",
                "veh_id": veh_str,
                "zones_id": "",
                "lines_id": "",
                "stopping_points_id": "",
                "drivers_id": "",
                "groups_id": "",
                "holidays": "",
                "lang": "ru",
                "explode": "1",
                "start_month": start_month,
                "stop_month": stop_month,
                "pre_start_date": pre_start,
                "pre_stop_date": pre_stop,
                "start_date": start_date,
                "stop_date": stop_date,
                "group": "1",
                "tags[]": "",
                "level[]": "",
                "event_group": "",
                "event_groups[]": "",
                "map_type": "1",
                "trailer": "",
                "last_ibutton_used": "0",
                "report_type": "38",
                "vehicle_not_moving_time": "1",
                "vehicles_has_covered_km": "1",
                "fillings": "on",
                "speed": "on",
                "rashod": "on",
                "stops": "on",
                "run": "on",
                "planned_stops": "on",
                "unplanned_stops": "on",
                "inside_bus_line": "on",
                "outside_bus_line": "on",
                "emp_name": "",
                "reason_for_opening": "",
                "report_mc_aid": "",
                "trip_types[]": ["1", "2"],
                "table_on_off": "on",
                "contr_time": "120",
                "limit_count": "99999",
                "contr_time_max": "0",
                "inspections_report_type": "0",
                "set_months_range": "1",
                "type": "1",
                "template": "1",
            },
        )

        return self._parse_refuel_report(data)

    def _parse_refuel_report(self, raw: dict) -> list[dict]:
        """
        Парсинг ответа report_type=38.

        Структура: data.{date_string}.{timestamp_key}.{array_of_values}
        Массив: [name, ts_unix, start_level, refuel_amount, end_level, {lat, lon, address}]
        """
        events = []
        raw_data = raw.get("data", {})
        if not isinstance(raw_data, dict):
            return events
        for date_group, entries in raw_data.items():
            if not isinstance(entries, dict):
                continue
            for ts_key, entry in entries.items():
                if not isinstance(entry, list) or len(entry) < 6:
                    continue
                name = str(entry[0]) if entry[0] else ""
                ts = int(entry[1]) if entry[1] else 0
                start_level = float(entry[2]) if entry[2] is not None else None
                refuel_amount = float(entry[3]) if entry[3] is not None else None
                end_level = float(entry[4]) if entry[4] is not None else None
                location = entry[5] if len(entry) > 5 and isinstance(entry[5], dict) else {}

                events.append({
                    "name": name,
                    "ts": ts,
                    "start_level": start_level,
                    "refuel_amount": refuel_amount,
                    "end_level": end_level,
                    "lat": float(location.get("lat", 0)) if location.get("lat") else None,
                    "lon": float(location.get("lon", 0)) if location.get("lon") else None,
                    "address": location.get("address") or "",
                })
        return events

    async def get_fuel_graph_report(
        self, token: str, node_id: int,
        veh_ids: list[int],
        start_date: str, stop_date: str,
    ) -> dict:
        """
        Отчёт по графику топлива (report_type=16).

        Возвращает: {levels: [{ts, val, name}], refuels: [{ts, level, amount, lat, lon, name}], drains: [{ts, amount, name}], sensor_names: [str]}

        Структура ответа: data.{date}.{veh_name}.{sensors: {Summary: [[ts, val], ...], ...}, fillings: [{...}], spills: [{...}]}
        """
        veh_str = ",".join(str(v) for v in veh_ids)

        from datetime import datetime
        start = datetime.strptime(start_date, "%d.%m.%Y %H:%M")
        stop = datetime.strptime(stop_date, "%d.%m.%Y %H:%M")
        start_month = f"{start.month:02d}.{start.year}"
        stop_month = f"{stop.month:02d}.{stop.year}"
        pre_start = start.strftime("%d.%m.%Y")
        pre_stop = stop.strftime("%d.%m.%Y")

        cookies = {"PILOTID": token, "node": str(node_id)}
        data = await self._request(
            "POST", PILOT_REPORTS_URL,
            token=None, node_id=0, cookies=cookies,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "download": "0",
                "start_time": "00:00",
                "stop_time": "00:00",
                "veh_id": veh_str,
                "zones_id": "",
                "lines_id": "",
                "stopping_points_id": "",
                "drivers_id": "",
                "groups_id": "",
                "holidays": "",
                "lang": "ru",
                "explode": "1",
                "start_month": start_month,
                "stop_month": stop_month,
                "pre_start_date": pre_start,
                "pre_stop_date": pre_stop,
                "start_date": start_date,
                "stop_date": stop_date,
                "group": "1",
                "tags[]": "",
                "level[]": "",
                "event_group": "",
                "event_groups[]": "",
                "map_type": "1",
                "trailer": "",
                "last_ibutton_used": "0",
                "report_type": "16",
                "vehicle_not_moving_time": "1",
                "vehicles_has_covered_km": "1",
                "fillings": "on",
                "spills": "on",
                "speed": "on",
                "rashod": "on",
                "stops": "on",
                "run": "on",
                "planned_stops": "on",
                "unplanned_stops": "on",
                "inside_bus_line": "on",
                "outside_bus_line": "on",
                "emp_name": "",
                "reason_for_opening": "",
                "report_mc_aid": "",
                "trip_types[]": ["1", "2"],
                "table_on_off": "on",
                "contr_time": "120",
                "limit_count": "99999",
                "contr_time_max": "0",
                "inspections_report_type": "0",
                "set_months_range": "1",
                "type": "1",
                "template": "1",
            },
        )

        return self._parse_fuel_graph(data)

    def _parse_fuel_graph(self, raw: dict) -> dict:
        """
        Парсинг ответа report_type=16.

        data.{date}.{veh_name}:
          - sensors: { "Summary": [[ts, val], ...], "Датчик1:-:...": [...] }
          - fillings: [{unixtimestamp, fuel_start, fuel, lat, lon}, ...]
          - spills:  [{unixtimestamp, fuel}, ...]
        """
        result = {"levels": [], "refuels": [], "drains": [], "sensor_names": []}
        raw_data = raw.get("data", {})
        if not isinstance(raw_data, dict):
            return result

        for date_group, entries in raw_data.items():
            if not isinstance(entries, dict):
                continue
            for veh_name, entry in entries.items():
                if not isinstance(entry, dict):
                    continue

                sensors = entry.get("sensors")
                if isinstance(sensors, dict):
                    all_keys = [k for k in sensors if k != "Summary" and isinstance(sensors[k], list)]
                    result["sensor_names"] = [k.split(":-:")[0].strip() if ":-:" in k else k for k in all_keys]

                    # Summary — агрегированный уровень топлива. Если нет — берём первый попавшийся сенсор
                    summary = sensors.get("Summary")
                    source = summary if isinstance(summary, list) and len(summary) > 0 else None
                    if not source:
                        for sname, sdata in sensors.items():
                            if isinstance(sdata, list) and len(sdata) > 0 and isinstance(sdata[0], (list, tuple)):
                                source = sdata
                                break
                    if source:
                        for point in source:
                            if isinstance(point, (list, tuple)) and len(point) >= 2:
                                ts = int(point[0])
                                val = float(point[1]) if point[1] is not None else None
                                if val is not None:
                                    result["levels"].append({
                                        "ts": ts,
                                        "val": val,
                                        "name": veh_name,
                                    })

                fillings = entry.get("fillings", [])
                if isinstance(fillings, list):
                    for f in fillings:
                        if not isinstance(f, dict):
                            continue
                        result["refuels"].append({
                            "ts": int(f.get("unixtimestamp", 0)),
                            "level": float(f["fuel_start"]) if f.get("fuel_start") else None,
                            "amount": float(f["fuel"]) if f.get("fuel") else None,
                            "lat": float(f.get("lat", 0)) if f.get("lat") else None,
                            "lon": float(f.get("lon", 0)) if f.get("lon") else None,
                            "name": veh_name,
                        })

                spills = entry.get("spills", [])
                if isinstance(spills, list):
                    for s in spills:
                        if not isinstance(s, dict):
                            continue
                        result["drains"].append({
                            "ts": int(s.get("unixtimestamp", 0)),
                            "amount": float(s["fuel"]) if s.get("fuel") else None,
                            "name": veh_name,
                        })

        result["levels"].sort(key=lambda x: x["ts"])
        result["refuels"].sort(key=lambda x: x["ts"])
        result["drains"].sort(key=lambda x: x["ts"])
        return result

    async def get_sensor_dip_history(
        self, token: str, node_id: int,
        imei: str, agent_id: int, ts_from: int, ts_to: int,
        tag_id: str | None = None,
    ) -> list[dict]:
        """
        Аналоговые сенсоры за период.

        Response: data[] — каждый сенсор содержит {id, name, work: [{ts, value, ...}]}
        ВНИМАНИЕ: параметр agent_id обязателен (помимо imei). Без него ts/te сдвигаются.
        """
        path = f"{PILOT_SENSOR_DIP_URL}?imei={imei}&agent_id={agent_id}&ts={ts_from}&te={ts_to}"
        if tag_id:
            path += f"&tag_id={tag_id}"
        try:
            data = await self._request("GET", path, token=token, node_id=node_id)
            return data.get("data", [])
        except PilotAuthError:
            return []

    async def get_discrete_sensor_data(
        self, token: str, node_id: int,
        imei: str, agent_id: int, ts_from: int, ts_to: int,
        tag_id: str | None = None,
    ) -> list[dict]:
        """Дискретные сенсоры (вкл/выкл, зажигание, двери) за период. Аналогичная структура dip."""
        path = f"/api/v3/vehicles/sensors/discrete?imei={imei}&agent_id={agent_id}&ts={ts_from}&te={ts_to}"
        if tag_id:
            path += f"&tag_id={tag_id}"
        try:
            data = await self._request("GET", path, token=token, node_id=node_id)
            return data.get("data", [])
        except PilotAuthError:
            return []

    async def get_raw_events(
        self, token: str, node_id: int,
        imei: str, agent_id: int, ts_from: int, ts_to: int,
    ) -> list[dict]:
        """
        GPS-точки (raw events) за период.

        Response: data.raw[] — массив точек с {lat, lon, ts, speed, sat, alt, ...}
        Внимание: ответ вложен глубже, чем у других эндпоинтов: data = {raw: [...]}
        """
        path = f"/api/v3/vehicles/events/raw?imei={imei}&agent_id={agent_id}&ts={ts_from}&te={ts_to}"
        try:
            data = await self._request("GET", path, token=token, node_id=node_id)
            raw = data.get("data", {})
            if isinstance(raw, dict):
                return raw.get("raw", [])
            return []
        except PilotAuthError:
            return []

    async def get_trip_summary(
        self, token: str, node_id: int,
        imei: str, agent_id: int, ts_from: int, ts_to: int,
    ) -> dict | None:
        """
        Сводка по поездке: пробег (can и gps) и скорости.

        Response: data {can: км, gps: км, maxspeed, avgspeed, ...}
        can_km — пробег по CAN-шине, gps_km — по GPS (точнее на коротких дистанциях).
        """
        path = f"/api/v3/vehicles/trips?imei={imei}&agent_id={agent_id}&ts={ts_from}&te={ts_to}"
        try:
            data = await self._request("GET", path, token=token, node_id=node_id)
            raw = data.get("data")
            if isinstance(raw, list):
                if not raw:
                    return None
                total_gps = 0.0
                total_can = 0.0
                maxspeed = 0.0
                total_duration = 0
                total_motion_duration = 0
                parking_count = 0
                for seg in raw:
                    gps = seg.get("gps") or 0
                    can = seg.get("can") or 0
                    total_gps += float(gps)
                    total_can += float(can)
                    maxspeed = max(maxspeed, float(seg.get("maxspeed") or 0))
                    ts = seg.get("ts")
                    te = seg.get("te")
                    if ts and te:
                        dur = int(te) - int(ts)
                        total_duration += dur
                        park_sum = sum(
                            p.get("duration", 0) or 0
                            for p in seg.get("parkings", [])
                        )
                        total_motion_duration += dur - park_sum
                    parking_count += len(seg.get("parkings", []))
                avgspeed = 0.0
                if total_duration > 0:
                    avgspeed = round(total_gps * 3600 / total_duration, 1)
                return {
                    "can_km": total_can,
                    "gps_km": total_gps,
                    "maxspeed": maxspeed,
                    "avgspeed": avgspeed,
                    "duration": total_duration,
                    "motion_duration": total_motion_duration,
                    "parking_count": parking_count,
                    "segment_count": len(raw),
                }
            if isinstance(raw, dict):
                return {
                    "can_km": raw.get("can"),
                    "gps_km": raw.get("gps"),
                    "maxspeed": raw.get("maxspeed"),
                    "avgspeed": raw.get("avgspeed"),
                    "can_start": raw.get("can_start"),
                    "can_end": raw.get("can_end"),
                    "gps_start": raw.get("gps_start"),
                    "gps_end": raw.get("gps_end"),
                }
            return None
        except PilotAuthError:
            return None

    async def get_track_stops(
        self, token: str, node_id: int,
        imei: str, agent_id: int, ts_from: int, ts_to: int,
    ) -> list[dict]:
        """
        Стоянки за период.

        Response от /api/v3/vehicles/track/stops может содержать:
          data.stops[] — короткие остановки (светофор, пробка)
          data.parkings[] — длительные стоянки (ночь, погрузка)

        Объединяем оба массива, отбрасываем короткие (< MIN_DURATION сек).
        """
        MIN_DURATION = 180  # 3 минуты — отсекаем светофоры/пробки
        path = f"/api/v3/vehicles/track/stops?imei={imei}&agent_id={agent_id}&ts={ts_from}&te={ts_to}"
        try:
            data = await self._request("GET", path, token=token, node_id=node_id)
            raw = data.get("data") or data
            if isinstance(raw, dict):
                stops = raw.get("stops") or []
                parkings = raw.get("parkings") or []
                if not isinstance(stops, list): stops = []
                if not isinstance(parkings, list): parkings = []
                stops_raw = stops + parkings
            elif isinstance(raw, list):
                stops_raw = raw
            else:
                stops_raw = []
            result = []
            for s in stops_raw:
                te = s.get("te")
                ts = s.get("ts", 0) or 0
                addr = s.get("address") or {}
                addr_str = (addr.get("street") or addr.get("city") or addr.get("house") or "") if isinstance(addr, dict) else str(addr)
                if te is None or (s.get("duration") is None and te >= ts_to - 120):
                    dur_now = int(datetime.now(timezone.utc).timestamp()) - ts
                    if dur_now < MIN_DURATION:
                        continue
                    result.append({
                        "lat": s.get("lat"), "lon": s.get("lon"),
                        "ts": ts, "te": None,
                        "duration": dur_now, "ongoing": True,
                        "address": addr_str,
                    })
                else:
                    dur = s.get("duration") or (te - ts)
                    if dur < MIN_DURATION:
                        continue
                    result.append({
                        "lat": s.get("lat"), "lon": s.get("lon"),
                        "ts": ts, "te": te,
                        "duration": dur, "ongoing": False,
                        "address": addr_str,
                    })
            return result
        except PilotAuthError:
            return []

    async def get_instant_status(
        self, token: str, node_id: int,
        imei: str, ts: int,
    ) -> dict | None:
        """
        Одометр и уровень топлива в конкретный момент времени.

        GET /api/v3/vehicles/instant-status?imei={imei}&ts={ts}
        Response: {code, msg, odometer, fuel_level, ts, imei}
        """
        path = f"/api/v3/vehicles/instant-status?imei={imei}&ts={ts}"
        try:
            return await self._request("GET", path, token=token, node_id=node_id)
        except PilotAuthError:
            return None

    async def get_track(
        self, token: str, node_id: int,
        imei: str, agent_id: int, ts_from: int, ts_to: int,
    ) -> list[dict]:
        """
        Трековые сегменты (альтернатива events/raw).

        Response: data[] — массив сегментов или data.tracks[]
        Этот эндпоинт возвращает уже готовые сегменты (линии между точками), а не сырые точки.
        """
        path = f"/api/v3/vehicles/track?imei={imei}&agent_id={agent_id}&ts={ts_from}&te={ts_to}"
        try:
            data = await self._request("GET", path, token=token, node_id=node_id)
            raw = data.get("data", [])
            if isinstance(raw, list):
                return raw
            if isinstance(raw, dict):
                return raw.get("tracks", [])
            return []
        except PilotAuthError:
            return []
