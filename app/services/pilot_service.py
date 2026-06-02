import asyncio
from datetime import datetime
import httpx

PILOT_AUTH_URL = "/api/v3/auth/token"
PILOT_VEHICLES_URL = "/api/v3/vehicles"
PILOT_VEHICLE_STATUS_URL = "/api/v3/vehicles/status"
PILOT_REPORTS_URL = "/backend/ax/reports.php"
PILOT_SENSOR_DIP_URL = "/api/v3/vehicles/sensors/dip"

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
        await _log_sync(f"[request] {method} {path} -> {resp.status_code} ({len(resp.content)} bytes)")
        data = resp.json()

        await _log_sync(f"[response] {path} -> {str(data)[:300]}")

        if data.get("success") is False or data.get("code", 0) != 0:
            raise PilotAuthError(data.get("msg", "Unknown API error"))
        return data

    async def login(self, username: str, password: str) -> dict:
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

    async def get_refuel_report(
        self,
        token: str,
        node_id: int,
        veh_ids: list[int],
        start_date: str,
        stop_date: str,
    ) -> list[dict]:
        """
        Fetch refuel report from Pilot API (report_type=38).

        start_date / stop_date: format DD.MM.YYYY HH:MM
        Returns flat list of events: [{name, ts, start_level, refuel_amount, end_level, lat, lon}]
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
        Fetch fuel graph report from Pilot API (report_type=16).

        start_date / stop_date: format DD.MM.YYYY HH:MM
        Returns dict with combined fuel graph data: {levels, refuels, drains}
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
        imei: str, ts_from: int, ts_to: int,
        tag_id: str | None = None,
    ) -> list[dict]:
        path = f"{PILOT_SENSOR_DIP_URL}?imei={imei}&ts={ts_from}&te={ts_to}"
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
        path = f"/api/v3/vehicles/events/raw?imei={imei}&agent_id={agent_id}&ts={ts_from}&te={ts_to}"
        try:
            data = await self._request("GET", path, token=token, node_id=node_id)
            raw = data.get("data", {})
            if isinstance(raw, dict):
                return raw.get("raw", [])
            return []
        except PilotAuthError:
            return []

    async def get_track(
        self, token: str, node_id: int,
        imei: str, agent_id: int, ts_from: int, ts_to: int,
    ) -> list[dict]:
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
