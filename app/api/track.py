import math
import zoneinfo
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
from app.models.user import User
from app.dependencies import get_current_user
from app.services.pilot_service import PilotService
from app.timezone_utils import get_user_timezone

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/api/vehicles/{vehicle_id}/track-modal", response_class=HTMLResponse)
async def track_modal(
    request: Request,
    vehicle_id: int,
    imei: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Возвращает HTML модалки карты (без данных — данные подгружаются через /points)."""
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    if user.role not in ("superadmin",):
        if not user.client_account_id or vehicle.client_account_id != user.client_account_id:
            raise HTTPException(404, "Vehicle not found")
        if user.site_id and vehicle.site_id != user.site_id:
            raise HTTPException(404, "Vehicle not found")

    user_tz = get_user_timezone(user)
    local_now = datetime.now(timezone.utc).astimezone(user_tz)
    today = local_now.strftime("%Y-%m-%d")
    days7 = (local_now - timedelta(days=7)).strftime("%Y-%m-%d")

    actual_imei = imei or vehicle.imei or ""

    return templates.TemplateResponse(request, "track_modal.html", {
        "vehicle_id": vehicle_id,
        "imei": actual_imei,
        "plate": vehicle.plate_number or vehicle.name or "—",
        "date_from": date_from or days7,
        "date_to": date_to or today,
        "today_str": today,
        "days7_str": days7,
        "user_timezone": str(user_tz),
    })


@router.get("/api/vehicles/{vehicle_id}/points")
async def vehicle_points(
    request: Request,
    vehicle_id: int,
    date_from: str = Query(...),
    date_to: str = Query(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """
    Основной API для карты трека.

    Возвращает: {points, refuels, stops, trip, sensors_info, plate, truncated}

    Token fallback: user.pilot_token → session → company_admin той же компании.
    """
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    if user.role not in ("superadmin",):
        if not user.client_account_id or vehicle.client_account_id != user.client_account_id:
            raise HTTPException(404, "Vehicle not found")
        if user.site_id and vehicle.site_id != user.site_id:
            raise HTTPException(404, "Vehicle not found")

    # Token fallback (auth/pilot_service.py → get_vehicles использует свой token)
    token = user.pilot_token or request.session.get("token")
    node_id = user.pilot_node_id or request.session.get("node_id", 0)
    if not token:
        if vehicle.client_account_id:
            admin = (
                await db.execute(
                    select(User).where(
                        User.client_account_id == vehicle.client_account_id,
                        User.role == "company_admin",
                        User.pilot_token.isnot(None),
                    )
                )
            ).scalar_one_or_none()
            if admin and admin.pilot_token:
                token = admin.pilot_token
                node_id = admin.pilot_node_id or node_id
    if not token:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    try:
        df = datetime.strptime(date_from, "%Y-%m-%d")
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(400, "Invalid date format, use YYYY-MM-DD")

    ts_from = int(df.replace(tzinfo=timezone.utc).timestamp())
    ts_to = int(dt.replace(tzinfo=timezone.utc).timestamp())

    # Pilot API не отдаёт больше ~90 дней за раз
    truncated = False
    ninety_days = 90 * 24 * 3600
    if ts_to - ts_from > ninety_days:
        ts_from = ts_to - ninety_days
        truncated = True

    plate = vehicle.plate_number or vehicle.name or ""
    pilot = PilotService()
    points = []
    if vehicle.imei and vehicle.pilot_agent_id:
        try:
            points = await pilot.get_raw_events(token, node_id, vehicle.imei, vehicle.pilot_agent_id, ts_from, ts_to)
        except Exception:
            points = []

    # Заправки — из локальной БД (синхронизируются scheduler-ом из Pilot)
    refuels = []
    try:
        from app.models.pilot_refuel import PilotRefuel
        from sqlalchemy import and_
        refuel_rows = await db.execute(
            select(PilotRefuel).where(
                PilotRefuel.vehicle_id == vehicle_id,
                PilotRefuel.lat.isnot(None),
                PilotRefuel.lon.isnot(None),
                PilotRefuel.event_date >= df,
                PilotRefuel.event_date <= dt,
            ).order_by(PilotRefuel.event_date)
        )
        for pr in refuel_rows.scalars().all():
            refuels.append({
                "lat": pr.lat,
                "lon": pr.lon,
                "ts": int(pr.event_date.timestamp()),
                "amount": pr.amount,
                "address": pr.address or "",
            })
    except Exception:
        refuels = []

    # Параллельные запросы к Pilot API (сенсоры, пробег, стоянки)
    sensors_info = []
    trip = None
    stops = []
    if vehicle.imei and vehicle.pilot_agent_id:
        import asyncio
        import bisect
        sensor_data = []
        discrete_data = []
        try:
            # asyncio.gather + return_exceptions — чтобы один отказавший запрос не убил остальные
            # asyncio.wait_for — чтобы не ждать вечно при недоступности Pilot API
            results = await asyncio.gather(
                asyncio.wait_for(pilot.get_sensor_dip_history(token, node_id, vehicle.imei, vehicle.pilot_agent_id, ts_from, ts_to), timeout=10),
                asyncio.wait_for(pilot.get_discrete_sensor_data(token, node_id, vehicle.imei, vehicle.pilot_agent_id, ts_from, ts_to), timeout=10),
                asyncio.wait_for(pilot.get_trip_summary(token, node_id, vehicle.imei, vehicle.pilot_agent_id, ts_from, ts_to), timeout=5),
                asyncio.wait_for(pilot.get_track_stops(token, node_id, vehicle.imei, vehicle.pilot_agent_id, ts_from, ts_to), timeout=5),
                return_exceptions=True,
            )
            if isinstance(results[0], list): sensor_data = results[0]
            if isinstance(results[1], list): discrete_data = results[1]
            if isinstance(results[2], dict): trip = results[2]
            if isinstance(results[3], list): stops = results[3]
        except Exception:
            pass
        try:
            # Сливаем dip + discrete в один sensor_map (один сенсор может быть и там, и там)
            sensor_map = {}
            for s in sensor_data:
                sid = s.get("id")
                name = s.get("name", "")
                vals = []
                for w in s.get("work", []):
                    ts = w.get("ts")
                    te = w.get("te")
                    val = w.get("value")
                    if val is not None:
                        if ts:
                            vals.append({"ts": ts, "value": float(val)})
                    elif ts and te:
                        vals.append({"ts": ts, "value": 1.0})
                        vals.append({"ts": te, "value": 0.0})
                if vals:
                    vals.sort(key=lambda x: x["ts"])
                    sensor_map[sid] = {"name": name, "values": vals}
            for s in discrete_data:
                sid = s.get("id")
                name = s.get("name", "")
                vals = []
                for w in s.get("work", []):
                    ts = w.get("ts")
                    val = w.get("value")
                    if ts and val is not None:
                        vals.append({"ts": ts, "value": float(val)})
                if vals:
                    vals.sort(key=lambda x: x["ts"])
                    if sid in sensor_map:
                        sensor_map[sid]["values"].extend(vals)
                        sensor_map[sid]["values"].sort(key=lambda x: x["ts"])
                    else:
                        sensor_map[sid] = {"name": name, "values": vals}
            # Для каждой GPS-точки — привязываем ближайшие по времени значения сенсоров (bisect)
            for pt in points:
                pt_ts = pt.get("ts")
                if pt_ts is None:
                    continue
                pt_sensors = {}
                for sid, sdata2 in sensor_map.items():
                    ts_list = [v["ts"] for v in sdata2["values"]]
                    idx = bisect.bisect_left(ts_list, pt_ts)
                    nearest = None
                    if idx == 0:
                        nearest = sdata2["values"][0]
                    elif idx >= len(ts_list):
                        nearest = sdata2["values"][-1]
                    else:
                        before = sdata2["values"][idx - 1]
                        after = sdata2["values"][idx]
                        nearest = before if (pt_ts - before["ts"]) <= (after["ts"] - pt_ts) else after
                    if nearest is not None:
                        pt_sensors[str(sid)] = nearest["value"]
                pt["sensors"] = pt_sensors
            sensors_info = [{"id": sid, "name": sdata2["name"]} for sid, sdata2 in sensor_map.items()]

            # Пробег (одометр) — через instant-status для первой точки + cumulative haversine
            if points and vehicle.imei:
                try:
                    sorted_pts = sorted(points, key=lambda p: p.get("ts", 0))
                    first_ts = sorted_pts[0].get("ts")
                    if first_ts:
                        status_data = await pilot.get_instant_status(token, node_id, vehicle.imei, first_ts)
                        raw = status_data.get("data") if status_data else None
                        if raw is None: raw = status_data
                        if raw and raw.get("odometer") is not None:
                            base_odo = float(raw["odometer"])
                            cum_dist = 0.0
                            prev = None
                            for pt in sorted_pts:
                                if prev is not None:
                                    dlat = math.radians(float(pt["lat"]) - float(prev["lat"]))
                                    dlon = math.radians(float(pt["lon"]) - float(prev["lon"]))
                                    a = math.sin(dlat / 2) ** 2 + math.cos(math.radians(float(prev["lat"]))) * math.cos(math.radians(float(pt["lat"]))) * math.sin(dlon / 2) ** 2
                                    cum_dist += 6371 * 2 * math.asin(math.sqrt(a))
                                if pt.get("sensors") is None:
                                    pt["sensors"] = {}
                                pt["sensors"]["_mileage"] = round(base_odo + cum_dist, 1)
                                prev = pt
                            sensors_info.append({"id": "_mileage", "name": "Пробег"})
                except Exception:
                    pass
        except Exception:
            pass

    return {"points": points, "refuels": refuels, "stops": stops, "trip": trip, "sensors_info": sensors_info, "plate": plate, "truncated": truncated}


@router.get("/api/vehicles/{vehicle_id}/track-data")
async def track_data(
    request: Request,
    vehicle_id: int,
    date_from: str = Query(...),
    date_to: str = Query(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Альтернативный эндпоинт — возвращает готовые сегменты трека (через /api/v3/vehicles/track)."""
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    if user.role not in ("superadmin",):
        if not user.client_account_id or vehicle.client_account_id != user.client_account_id:
            raise HTTPException(404, "Vehicle not found")
        if user.site_id and vehicle.site_id != user.site_id:
            raise HTTPException(404, "Vehicle not found")

    token = user.pilot_token or request.session.get("token")
    node_id = user.pilot_node_id or request.session.get("node_id", 0)
    if not token:
        if vehicle.client_account_id:
            admin = (
                await db.execute(
                    select(User).where(
                        User.client_account_id == vehicle.client_account_id,
                        User.role == "company_admin",
                        User.pilot_token.isnot(None),
                    )
                )
            ).scalar_one_or_none()
            if admin and admin.pilot_token:
                token = admin.pilot_token
                node_id = admin.pilot_node_id or node_id
    if not token:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    try:
        df = datetime.strptime(date_from, "%Y-%m-%d")
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(400, "Invalid date format, use YYYY-MM-DD")

    ts_from = int(df.replace(tzinfo=timezone.utc).timestamp())
    ts_to = int(dt.replace(tzinfo=timezone.utc).timestamp())

    pilot = PilotService()
    segments = []
    if vehicle.imei:
        try:
            segments = await pilot.get_track(token, node_id, vehicle.imei, vehicle.pilot_agent_id, ts_from, ts_to)
        except Exception:
            segments = []

    return {"segments": segments, "plate": vehicle.plate_number or vehicle.name or "—"}
