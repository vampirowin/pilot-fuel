import httpx


PILOT_AUTH_URL = "/api/v3/auth/token"
PILOT_VEHICLES_URL = "/api/v3/vehicles"
PILOT_VEHICLE_STATUS_URL = "/api/v3/vehicles/status"
PILOT_FUEL_REPORT_URL = "/api/v3/vehicles/fuel"
PILOT_SENSOR_DIP_URL = "/api/v3/vehicles/sensors/dip"


class PilotAuthError(Exception):
    pass


class PilotService:
    def __init__(self, base_url: str | None = None):
        from app.config import get_settings
        settings = get_settings()
        self.base_url = (base_url or settings.pilot_api_base_url).rstrip("/")

    async def _request(self, method: str, path: str, token: str | None = None, node_id: int = 0, **kwargs) -> dict:
        headers = kwargs.pop("headers", {})
        if token:
            headers["Authorization"] = f"Bearer {token}"
        if node_id:
            headers["X-Node-Id"] = str(node_id)
        headers.setdefault("Content-Type", "application/json")

        async with httpx.AsyncClient(timeout=30.0) as client:
            resp = await client.request(method, f"{self.base_url}{path}", headers=headers, **kwargs)
            data = resp.json()

        if data.get("code") != 0:
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

    async def get_fuel_report(
        self, token: str, node_id: int, imei: str,
        ts_from: int, ts_to: int,
    ) -> list[dict]:
        try:
            data = await self._request(
                "GET",
                f"{PILOT_FUEL_REPORT_URL}?imei={imei}&ts={ts_from}&te={ts_to}",
                token=token, node_id=node_id,
            )
            return data.get("data", [])
        except PilotAuthError:
            return []

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
