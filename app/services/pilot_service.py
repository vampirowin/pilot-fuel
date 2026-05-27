from datetime import datetime

import httpx

PILOT_AUTH_URL = "/api/v3/auth/token"
PILOT_VEHICLES_URL = "/api/v3/vehicles"
PILOT_VEHICLE_STATUS_URL = "/api/v3/vehicles/status"
PILOT_REPORTS_URL = "/backend/ax/reports.php"
PILOT_SENSOR_DIP_URL = "/api/v3/vehicles/sensors/dip"


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
            headers["X-Node-Id"] = str(node_id)
        headers.setdefault("Content-Type", "application/json")
        headers.setdefault("X-Requested-With", "XMLHttpRequest")

        async with httpx.AsyncClient(timeout=120.0) as client:
            resp = await client.request(method, f"{self.base_url}{path}", headers=headers, cookies=cookies, **kwargs)
            with open("sync_debug.log", "a") as lf:
                lf.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] [request] {method} {path} -> {resp.status_code} ({len(resp.content)} bytes)\n")
            data = resp.json()

        if data.get("code") != 0 and not data.get("success"):
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
                "stales": "on",
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
                "limit_count": "0",
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
            with open("sync_debug.log", "a") as lf:
                lf.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] [parse] data не словарь, а {type(raw_data).__name__}\n")
            return events
        with open("sync_debug.log", "a") as lf:
            lf.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] [parse] date_groups={len(raw_data)}\n")
        for date_group, entries in raw_data.items():
            with open("sync_debug.log", "a") as lf:
                lf.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] [parse] группа '{date_group[:50]}...': {'словарь' if isinstance(entries, dict) else 'не словарь'}, ключей={len(entries) if isinstance(entries, dict) else 'N/A'}\n")
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
