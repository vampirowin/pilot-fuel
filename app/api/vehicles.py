import time
import asyncio
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Request, Depends, HTTPException, Form, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
from app.models.client_account import ClientAccount
from app.models.site import Site
from app.models.refuel_entry import RefuelEntry
from app.services.pilot_service import PilotService
from app.dependencies import get_current_user, apply_vehicle_filter
from app.models.vehicle import SENSOR_STATUSES
from app.services.refuel_utils import get_effective_thresholds as _get_effective_thresholds, calc_comparison as _calc_comparison, resolve_pilot_credentials as _resolve_pilot_credentials
from app.models.setting import Setting
from app.models.trip_summary import TripSummary
from app.timezone_utils import get_user_timezone

logger = logging.getLogger(__name__)

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

STATUS_CACHE_TTL = 300  # 5 min background refresh
_status_cache: dict[str, tuple[float, list[dict]]] = {}
_status_pending: dict[str, asyncio.Event] = {}


async def refresh_company_statuses(ca_id: int, ca_name: str = "") -> int:
    """Фоновое обновление кэша статусов для одной компании. Возвращает число ТС."""
    from app.database import async_session
    from app.models.user import User
    from sqlalchemy import select

    cache_key = str(ca_id)
    async with async_session() as db:
        admin = (
            await db.execute(
                select(User).where(
                    User.client_account_id == ca_id,
                    User.role == "company_admin",
                    User.is_active == True,
                    User.pilot_token.isnot(None),
                ).limit(1)
            )
        ).scalar_one_or_none()
        if not admin:
            return 0

        vehicles = (
            await db.execute(
                select(Vehicle).where(
                    Vehicle.is_active == True,
                    Vehicle.has_fuel_sensor == True,
                    Vehicle.client_account_id == ca_id,
                )
            )
        ).scalars().all()
        if not vehicles:
            return 0

    try:
        ps = PilotService()
        imeis = [v.imei for v in vehicles if v.imei]
        if not imeis:
            return 0
        raw = await ps.get_all_vehicle_statuses(admin.pilot_token, admin.pilot_node_id or 0, imeis)
        now_ts = datetime.now(timezone.utc).timestamp()
        vehicle_map = {v.imei: v.id for v in vehicles if v.imei}
        result_data = []
        for imei, s in raw.items():
            vid = vehicle_map.get(imei)
            if not vid:
                continue
            ts = s.get("ts")
            if ts and now_ts - ts < 1200:
                st = "online"
            elif ts and now_ts - ts < 3600:
                st = "warning"
            else:
                st = "offline"
            result_data.append({
                "vehicle_id": vid,
                "lat": s.get("lat"),
                "lon": s.get("lon"),
                "ts": ts,
                "status": st,
            })
        _status_cache[cache_key] = (time.time(), result_data)
        logger.info("Status cache refreshed for company %s (%s): %d vehicles", ca_name, cache_key, len(result_data))
        return len(result_data)
    except Exception as exc:
        logger.warning("Status refresh failed for company %s (%s): %s", ca_name, cache_key, exc)
        return 0


def group_by_folder(vehicles: list) -> list:
    groups = {}
    for v in vehicles:
        folder = v.get("folder") or "Без папки"
        groups.setdefault(folder, []).append(v)
    sorted_folders = sorted(groups.keys(), key=lambda x: (x == "Без папки", x))
    return [(f, groups[f]) for f in sorted_folders]


def build_vehicle_dict(v: Vehicle, company_name: str = "", site_name: str = "", status_info: dict | None = None, trip_summary: dict | None = None) -> dict:
    si = (status_info or {}).get(v.imei) if status_info else None
    trip_dur = None
    if trip_summary:
        sec = trip_summary.get("motion_seconds") or 0
        if sec:
            h = sec // 3600
            m = (sec % 3600) // 60
            trip_dur = f"{h}ч {m}м"
    return {
        "id": v.id,
        "pilot_agent_id": v.pilot_agent_id,
        "imei": v.imei,
        "plate_number": v.plate_number,
        "name": v.name,
        "folder": v.folder,
        "sensor_count": v.sensor_count,
        "has_fuel_sensor": v.has_fuel_sensor,
        "sensor_status": v.sensor_status,
        "status_info": si,
        "status_dot": _status_dot_html(si.get("status") if si else None),
        "company_name": company_name,
        "site_name": site_name,
        "trip_duration": trip_dur,
    }


def build_nested_groups(vehicles: list) -> list:
    tree = {}
    for v in vehicles:
        cname = v.get("company_name") or "Без компании"
        sname = v.get("site_name") or "Без площадки"
        folder = v.get("folder") or "Без папки"
        tree.setdefault(cname, {}).setdefault(sname, {}).setdefault(folder, []).append(v)
    result = []
    for cname in sorted(tree.keys(), key=lambda x: (x == "Без компании", x)):
        site_names = list(tree[cname].keys())
        all_sites_placeholder = all(s == "Без площадки" for s in site_names)
        ctotal = 0
        sites = []

        if all_sites_placeholder:
            # Flatten site level but keep folder grouping
            folder_groups = {}
            for sname in site_names:
                for fname, fvehicles in tree[cname][sname].items():
                    folder_groups.setdefault(fname, []).extend(fvehicles)
            sorted_folders = sorted(folder_groups.items(), key=lambda x: (x[0] == "Без папки", x[0]))
            all_placeholder = all(f == "Без папки" for f, _ in sorted_folders)
            if all_placeholder:
                flat = []
                for _, v in sorted_folders:
                    flat.extend(v)
                ctotal = len(flat)
                sites.append(("__flat__", 0, [("__flat__", flat)]))
            else:
                ctotal = sum(len(v) for _, v in sorted_folders)
                sites.append(("__flat__", 0, sorted_folders))
        else:
            for sname in sorted(site_names, key=lambda x: (x == "Без площадки", x)):
                folder_names = list(tree[cname][sname].keys())
                all_folders_placeholder = all(f == "Без папки" for f in folder_names)
                stotal = 0
                if all_folders_placeholder:
                    # Flatten folder level — collect all vehicles across all placeholder folders
                    flat_vehicles = []
                    for fname in folder_names:
                        flat_vehicles.extend(tree[cname][sname][fname])
                    stotal = len(flat_vehicles)
                    sites.append((sname, stotal, [("__flat__", flat_vehicles)]))
                else:
                    folders = []
                    for fname in sorted(folder_names, key=lambda x: (x == "Без папки", x)):
                        fvehicles = tree[cname][sname][fname]
                        folders.append((fname, fvehicles))
                        stotal += len(fvehicles)
                    sites.append((sname, stotal, folders))
                ctotal += stotal
        result.append((cname, ctotal, sites))
    return result


def _sensor_status_badge(v: dict) -> str:
    status = v.get("sensor_status", "normal")
    label = SENSOR_STATUSES.get(status, status)
    css = {"normal": "status-normal", "broken": "status-unacceptable", "stock": "status-small-deviation"}
    cls = css.get(status, "")
    return f'<span class="status-badge {cls}">{label}</span>'


def _sensor_status_select(v: dict) -> str:
    current = v.get("sensor_status", "normal")
    opts = "".join(
        f'<option value="{k}" {"selected" if k == current else ""}>{label}</option>'
        for k, label in SENSOR_STATUSES.items()
    )
    return f'<select name="status" class="sensor-status-select status-{current}" data-vehicle-id="{v["id"]}" hx-post="/api/vehicles/{v["id"]}/sensor-status" hx-trigger="change" hx-swap="innerHTML" hx-target="closest td">{opts}</select>'


STATUS_DOT_CLASSES = {"online": "status-dot-on", "warning": "status-dot-warn", "offline": "status-dot-off"}

def _status_dot_html(status: str | None) -> str:
    if not status:
        return '<span class="status-dot status-dot-off" title="Нет данных"><span class="status-dot-label">Нет данных</span></span>'
    cls = STATUS_DOT_CLASSES.get(status, "status-dot-off")
    labels = {"online": "Онлайн", "warning": ">20 мин", "offline": "Офлайн"}
    label = labels.get(status, "Нет данных")
    return f'<span class="status-dot {cls}" title="{label}"><span class="status-dot-label">{label}</span></span>'

def _sensor_cell(v: dict, editable: bool) -> str:
    if editable:
        return _sensor_status_select(v)
    return _sensor_status_badge(v)


def _vehicle_row(v: dict, idx: int, can_act: bool, today_str: str) -> str:
    sensor = _sensor_cell(v, can_act)
    si = v.get("status_info") or {}
    dot = v.get("status_dot") or _status_dot_html(None)
    lat = si.get("lat") or ""
    lon = si.get("lon") or ""
    ts = si.get("ts") or ""
    status_val = si.get("status") or ""
    cells = ""
    if can_act:
        cells += f'<td data-label=""><input type="checkbox" form="bulk-vehicle-form" name="vehicle_ids" value="{v["id"]}"></td>'
    cells += f'<td data-label="#" style="color:var(--text-dim)">{idx}</td>'
    cells += f'<td data-label="Статус">{dot}</td>'
    cells += f'<td data-label="Госномер"><strong style="cursor:pointer" data-lat="{lat}" data-lon="{lon}" data-ts="{ts}" data-status="{status_val}" onclick="openLocationMap(this)">{v.get("plate_number") or "—"}</strong></td>'
    cells += f'<td data-label="Датчик" class="sensor-cell">{sensor}</td>'
    cells += f'<td data-label="Заправки"><a href="/refuels?vehicle_id={v["id"]}" class="btn btn-sm btn-secondary">Заправки</a></td>'
    cells += f'<td data-label="Компания">{v.get("company_name") or "—"}</td>'
    cells += f'<td data-label="График"><button class="btn btn-sm btn-secondary" hx-get="/api/fuel-graph/modal?vehicle_id={v["id"]}&imei={v.get("imei") or ""}" hx-target="#modal-container" hx-swap="innerHTML">График</button></td>'
    cells += f'<td data-label="Трек"><button class="btn btn-sm btn-track" hx-get="/api/vehicles/{v["id"]}/track-modal?imei={v.get("imei") or ""}&date_from={today_str}&date_to={today_str}" hx-target="#modal-container" hx-swap="innerHTML">Трек</button></td>'
    if can_act:
        cells += f'<td data-label="Действия"><button class="btn btn-sm btn-secondary" hx-get="/api/vehicles/{v["id"]}/thresholds" hx-target="#modal-container" hx-swap="innerHTML">Пороги</button> <button class="btn btn-sm btn-danger" hx-post="/api/vehicles/{v["id"]}/delete" hx-target="#vehicles-table" hx-swap="innerHTML" hx-confirm="Удалить ТС {v.get("plate_number") or "—"} и все его запросы?">Удалить</button></td>'
    else:
        cells += '<td data-label="Действия"></td>'
    return f'<tr id="v-{v["id"]}">{cells}</tr>'


def render_nested_partial(nested_groups: list, can_act: bool, today_str: str) -> str:
    if not nested_groups:
        return '<div class="card"><div class="empty-state"><h3>Нет транспортных средств</h3><p>Нажмите «Синхронизировать», чтобы загрузить список ТС из Pilot.</p></div></div>'

    cidx = 0
    sidx = 0
    fidx = 0
    html = ""
    for cname, ctotal, sites in nested_groups:
        cidx += 1
        cid = f"c-{cidx}"
        html += f'<div class="card level-company" style="margin-top: 16px;"><div class="card-header collapsible-header" onclick="toggleGroup(\'{cid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-company">{cname}</span><span class="level-count">{ctotal}</span></div><div class="collapsible-body" id="{cid}">'
        for sname, stotal, folders in sites:
            if sname == "__flat__":
                for fname, vehicles in folders:
                    if fname == "__flat__":
                        html += '<div class="table-container" style="margin-top:12px"><table><thead><tr>'
                        if can_act:
                            html += '<th style="width:32px;"><input type="checkbox" onchange="var e=this;document.querySelectorAll(\'#bulk-vehicle-form input[name=vehicle_ids]\').forEach(function(c){c.checked=e.checked})"></th>'
                        html += '<th>#</th><th>Статус</th><th>Госномер</th><th>Датчик</th><th>Заправки</th><th>Компания</th><th>График</th><th>Трек</th><th>Действия</th></tr></thead><tbody>'
                        for idx, v in enumerate(vehicles, 1):
                            html += _vehicle_row(v, idx, can_act, today_str)
                        html += '</tbody></table></div>'
                    else:
                        fidx += 1
                        fid = f"f-{cidx}-{sidx}-{fidx}"
                        html += f'<div class="level-folder" style="margin:4px 0;border:1px solid var(--border);border-radius:6px"><div class="collapsible-header" onclick="toggleGroup(\'{fid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-folder">{fname}</span><span class="level-count">{len(vehicles)}</span></div><div class="collapsible-body" id="{fid}"><div class="table-container"><table><thead><tr>'
                        if can_act:
                            html += '<th style="width:32px;"><input type="checkbox" onchange="var e=this;document.querySelectorAll(\'#bulk-vehicle-form input[name=vehicle_ids]\').forEach(function(c){c.checked=e.checked})"></th>'
                        html += '<th>#</th><th>Статус</th><th>Госномер</th><th>Датчик</th><th>Заправки</th><th>Компания</th><th>График</th><th>Трек</th><th>Действия</th></tr></thead><tbody>'
                        for idx, v in enumerate(vehicles, 1):
                            html += _vehicle_row(v, idx, can_act, today_str)
                        html += '</tbody></table></div></div></div>'
            else:
                sidx += 1
                sid = f"s-{cidx}-{sidx}"
                html += f'<div class="card level-site" style="margin: 8px 0;"><div class="card-header collapsible-header" onclick="toggleGroup(\'{sid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-site">{sname}</span><span class="level-count">{stotal}</span></div><div class="collapsible-body" id="{sid}">'
                for fname, vehicles in folders:
                    if fname == "__flat__":
                        html += '<div class="table-container"><table><thead><tr>'
                        if can_act:
                            html += '<th style="width:32px;"><input type="checkbox" onchange="var e=this;document.querySelectorAll(\'#bulk-vehicle-form input[name=vehicle_ids]\').forEach(function(c){c.checked=e.checked})"></th>'
                        html += '<th>#</th><th>Статус</th><th>Госномер</th><th>Датчик</th><th>Заправки</th><th>Компания</th><th>График</th><th>Трек</th><th>Действия</th></tr></thead><tbody>'
                        for idx, v in enumerate(vehicles, 1):
                            html += _vehicle_row(v, idx, can_act, today_str)
                        html += '</tbody></table></div>'
                    else:
                        fidx += 1
                        fid = f"f-{cidx}-{sidx}-{fidx}"
                        html += f'<div class="level-folder" style="margin:4px 0;border:1px solid var(--border);border-radius:6px"><div class="collapsible-header" onclick="toggleGroup(\'{fid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-folder">{fname}</span><span class="level-count">{len(vehicles)}</span></div><div class="collapsible-body" id="{fid}"><div class="table-container"><table><thead><tr>'
                        if can_act:
                            html += '<th style="width:32px;"><input type="checkbox" onchange="var e=this;document.querySelectorAll(\'#bulk-vehicle-form input[name=vehicle_ids]\').forEach(function(c){c.checked=e.checked})"></th>'
                        html += '<th>#</th><th>Статус</th><th>Госномер</th><th>Датчик</th><th>Заправки</th><th>Компания</th><th>График</th><th>Трек</th><th>Действия</th></tr></thead><tbody>'
                        for idx, v in enumerate(vehicles, 1):
                            html += _vehicle_row(v, idx, can_act, today_str)
                        html += '</tbody></table></div></div></div>'
                html += '</div></div>'
        html += '</div></div>'
    return html


def render_table_partial(vehicles: list, is_superadmin: bool = False, is_company_admin: bool = False, today_str: str = "") -> str:
    can_act = is_superadmin or is_company_admin
    if not vehicles:
        return '<div class="card"><div class="empty-state"><h3>Нет транспортных средств</h3><p>Нажмите «Синхронизировать», чтобы загрузить список ТС из Pilot.</p></div></div>'
    return render_nested_partial(build_nested_groups(vehicles), can_act, today_str)


@router.get("/vehicles", response_class=HTMLResponse)
async def vehicles_page(
    request: Request,
    plate: str = "",
    sensor_status: list[str] = Query(default=[]),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    query = apply_vehicle_filter(query, user, Vehicle)
    if plate:
        query = query.where(Vehicle.plate_number.ilike(f"%{plate}%"))
    if sensor_status:
        query = query.where(Vehicle.sensor_status.in_(sensor_status))
    query = query.order_by(Vehicle.folder, Vehicle.plate_number)
    result = await db.execute(query)
    db_vehicles = result.scalars().all()

    company_ids = {v.client_account_id for v in db_vehicles if v.client_account_id}
    companies = {}
    if company_ids:
        for c in (await db.execute(select(ClientAccount).where(ClientAccount.id.in_(company_ids)))).scalars().all():
            companies[c.id] = c.name

    site_ids = {v.site_id for v in db_vehicles if v.site_id}
    sites = {}
    if site_ids:
        for s in (await db.execute(select(Site).where(Site.id.in_(site_ids)))).scalars().all():
            sites[s.id] = s.name

    vehicle_ids = [v.id for v in db_vehicles]
    latest_trips = {}
    if vehicle_ids:
        from sqlalchemy import func
        subq = select(TripSummary.vehicle_id, func.max(TripSummary.date).label("max_date")).where(TripSummary.vehicle_id.in_(vehicle_ids)).group_by(TripSummary.vehicle_id).subquery()
        trip_rows = await db.execute(select(TripSummary).join(subq, (TripSummary.vehicle_id == subq.c.vehicle_id) & (TripSummary.date == subq.c.max_date)))
        for t in trip_rows.scalars().all():
            latest_trips[t.vehicle_id] = {"duration_seconds": t.duration_seconds, "motion_seconds": t.motion_seconds}

    is_hx = request.headers.get("hx-request") == "true"
    is_boosted = request.headers.get("hx-boosted") == "true"

    out = [build_vehicle_dict(v, companies.get(v.client_account_id, ""), sites.get(v.site_id, ""), None, latest_trips.get(v.id)) for v in db_vehicles]
    is_su = user.role == "superadmin"
    is_ca = user.role == "company_admin"

    user_tz = get_user_timezone(user)
    today_str = datetime.now(timezone.utc).astimezone(user_tz).strftime("%Y-%m-%d")

    if is_hx and not is_boosted:
        return HTMLResponse(render_table_partial(out, is_su, is_ca, today_str))

    return templates.TemplateResponse(request, "vehicles.html", {
        "nested_groups": build_nested_groups(out),
        "is_superadmin": is_su,
        "is_company_admin": is_ca,
        "plate": plate,
        "sensor_status": sensor_status,
        "today_str": today_str,
        "search_url": "/vehicles",
        "search_target": "#vehicles-table",
    })


@router.get("/api/vehicles/status-batch", response_class=JSONResponse)
async def vehicle_status_batch(
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cache_key = str(user.client_account_id or 0)
    now = time.time()
    cached = _status_cache.get(cache_key)
    if cached and now - cached[0] < STATUS_CACHE_TTL:
        return JSONResponse(cached[1])

    # Cold start — делаем прямой запрос (фон еще не прогрел кэш)
    event = _status_pending.get(cache_key)
    if event:
        await event.wait()
        cached = _status_cache.get(cache_key)
        return JSONResponse(cached[1] if cached else [])

    event = asyncio.Event()
    _status_pending[cache_key] = event
    try:
        count = await refresh_company_statuses(user.client_account_id or 0)
        if count:
            cached = _status_cache.get(cache_key, (0, []))
        else:
            cached = (now, [])
        return JSONResponse(cached[1])
    finally:
        _status_pending.pop(cache_key, None)
        event.set()


@router.get("/api/vehicles/{vehicle_id}/location", response_class=JSONResponse)
async def vehicle_location(
    vehicle_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    """Прямой запрос к Pilot за текущим местоположением одного ТС."""
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    if user.role != "superadmin":
        if not user.client_account_id or vehicle.client_account_id != user.client_account_id:
            raise HTTPException(404, "Vehicle not found")
        if user.site_id and vehicle.site_id != user.site_id:
            raise HTTPException(404, "Vehicle not found")

    token, node_id = await _resolve_pilot_credentials(user, db)
    if not token or not node_id:
        return JSONResponse({"lat": None, "lon": None, "ts": None, "status": "offline"})

    try:
        ps = PilotService()
        raw = await ps.get_vehicle_status(token, node_id, vehicle.imei)
        if not raw:
            return JSONResponse({"lat": None, "lon": None, "ts": None, "status": "offline", "sensors": []})
        now_ts = datetime.now(timezone.utc).timestamp()
        ts = raw.get("unixtimestamp")
        ts_int = int(ts) if ts else None
        if ts_int and now_ts - ts_int < 1200:
            st = "online"
        elif ts_int and now_ts - ts_int < 3600:
            st = "warning"
        else:
            st = "offline"
        sensors = raw.get("sensors", [])
        sensor_list = []
        if isinstance(sensors, list):
            for s in sensors:
                name = str(s.get("name", "")).strip()
                if not name:
                    continue
                sensor_list.append({
                    "name": name,
                    "dig_value": s.get("dig_value"),
                    "hum_value": s.get("hum_value"),
                })
        if ts_int and vehicle.imei:
            try:
                inst = await ps.get_instant_status(token, node_id, vehicle.imei, ts_int)
                if inst:
                    iraw = inst.get("data") or inst
                    odo = iraw.get("odometer")
                    if odo is not None:
                        sensor_list.append({"name": "Пробег", "dig_value": float(odo), "hum_value": None})
            except Exception:
                pass
        return JSONResponse({
            "lat": raw.get("lat"),
            "lon": raw.get("lon"),
            "ts": ts_int,
            "status": st,
            "speed": raw.get("speed"),
            "alt": raw.get("alt"),
            "sat": raw.get("sat"),
            "sensors": sensor_list,
        })
    except Exception:
        logger.warning("Failed to fetch location for vehicle %d", vehicle_id, exc_info=True)
        return JSONResponse({"lat": None, "lon": None, "ts": None, "status": "offline"})


@router.get("/api/vehicles/{vehicle_id}/thresholds", response_class=HTMLResponse)
async def vehicle_thresholds_form(
    request: Request,
    vehicle_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    v = await db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "ТС не найдено")
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(403)
    if user.role == "company_admin" and v.client_account_id != user.client_account_id:
        raise HTTPException(404, "ТС не найдено")

    global_n_pct, global_w_pct, _, _, _ = await _get_effective_thresholds(db)

    return templates.TemplateResponse(request, "vehicle_thresholds_modal.html", {
        "vehicle_id": v.id,
        "plate": v.plate_number or v.name or "—",
        "global_n_pct": f"{global_n_pct:.1f}",
        "global_w_pct": f"{global_w_pct:.1f}",
        "n_pct": f"{v.normal_threshold_pct:.1f}" if v.normal_threshold_pct is not None else "",
        "w_pct": f"{v.warning_threshold_pct:.1f}" if v.warning_threshold_pct is not None else "",
        "enable_abs": v.enable_abs_threshold,
        "n_abs": f"{v.normal_threshold_abs:.1f}" if v.normal_threshold_abs is not None else "",
        "w_abs": f"{v.warning_threshold_abs:.1f}" if v.warning_threshold_abs is not None else "",
    })


@router.post("/api/vehicles/{vehicle_id}/thresholds", response_class=HTMLResponse)
async def vehicle_thresholds_save(
    request: Request,
    vehicle_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    normal_threshold_pct: str = Form(default=""),
    warning_threshold_pct: str = Form(default=""),
    enable_abs: str = Form(default=""),
    normal_threshold_abs: str = Form(default=""),
    warning_threshold_abs: str = Form(default=""),
):
    v = await db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "ТС не найдено")
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(403)
    if user.role == "company_admin" and v.client_account_id != user.client_account_id:
        raise HTTPException(404, "ТС не найдено")

    v.normal_threshold_pct = float(normal_threshold_pct) if normal_threshold_pct.strip() else None
    v.warning_threshold_pct = float(warning_threshold_pct) if warning_threshold_pct.strip() else None
    v.enable_abs_threshold = enable_abs == "1"
    v.normal_threshold_abs = float(normal_threshold_abs) if v.enable_abs_threshold and normal_threshold_abs.strip() else None
    v.warning_threshold_abs = float(warning_threshold_abs) if v.enable_abs_threshold and warning_threshold_abs.strip() else None

    # Recalculate all entries for this vehicle
    await db.flush()
    n_pct, w_pct, n_abs, w_abs, en_abs = await _get_effective_thresholds(db, v.id)
    entries = (await db.execute(
        select(RefuelEntry).where(
            RefuelEntry.vehicle_id == v.id,
            RefuelEntry.is_deleted == False,
        )
    )).scalars().all()
    for e in entries:
        if e.is_false:
            continue
        if e.pilot_amount and e.actual_amount and e.pilot_amount > 0:
            diff, err, status = _calc_comparison(e.pilot_amount, e.actual_amount, n_pct, w_pct, n_abs, w_abs, en_abs)
            e.difference = diff
            e.error_percent = err
            e.comparison_status = status
        elif e.actual_amount is not None:
            e.difference = None
            e.error_percent = None
            e.comparison_status = "pilot_missing"
    await db.commit()

    return HTMLResponse('<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Пороги сохранены</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="this.closest(\'.modal-overlay\').remove()">OK</button></div></div></div>')


@router.post("/api/vehicles/{vehicle_id}/sensor-status", response_class=HTMLResponse)
async def update_sensor_status(
    request: Request,
    vehicle_id: int,
    status: str = Form("normal"),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if status not in SENSOR_STATUSES:
        raise HTTPException(status_code=400, detail="Invalid status")
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    v = result.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if user.role == "company_admin" and v.client_account_id != user.client_account_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    v.sensor_status = status
    await db.commit()
    dv = build_vehicle_dict(v)
    return HTMLResponse(_sensor_status_select(dv))


@router.post("/api/vehicles/bulk-remove-sensor", response_class=HTMLResponse)
async def bulk_remove_sensor(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    vehicle_ids: list[int] = Form(default=[]),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")
    if vehicle_ids:
        query = select(Vehicle).where(Vehicle.id.in_(vehicle_ids))
        if user.role == "company_admin":
            query = query.where(Vehicle.client_account_id == user.client_account_id)
        vehicles = (await db.execute(query)).scalars().all()
        for v in vehicles:
            v.has_fuel_sensor = False
        await db.commit()

    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    query = apply_vehicle_filter(query, user, Vehicle)
    query = query.order_by(Vehicle.folder, Vehicle.plate_number)
    result = await db.execute(query)
    db_vehicles = result.scalars().all()

    company_ids = {v.client_account_id for v in db_vehicles if v.client_account_id}
    companies = {}
    if company_ids:
        for c in (await db.execute(select(ClientAccount).where(ClientAccount.id.in_(company_ids)))).scalars().all():
            companies[c.id] = c.name

    site_ids = {v.site_id for v in db_vehicles if v.site_id}
    sites = {}
    if site_ids:
        for s in (await db.execute(select(Site).where(Site.id.in_(site_ids)))).scalars().all():
            sites[s.id] = s.name

    out = [build_vehicle_dict(v, companies.get(v.client_account_id, ""), sites.get(v.site_id, "")) for v in db_vehicles]
    is_su = user.role == "superadmin"
    is_ca = user.role == "company_admin"
    return HTMLResponse(render_table_partial(out, is_su, is_ca))


@router.post("/api/vehicles/sync", response_class=HTMLResponse)
async def sync_vehicles(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403)

    token = user.pilot_token or request.session.get("token")
    node_id = user.pilot_node_id or request.session.get("node_id", 0)
    if not token:
        token, node_id = await _resolve_pilot_credentials(user, db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    service = PilotService()
    pilot_vehicles = await service.get_vehicles(token, node_id)

    for i, pv in enumerate(pilot_vehicles[:3]):
        logger.info(f"Pilot vehicle {i}: keys={list(pv.keys())}, folder={repr(pv.get('folder', ''))}")

    out = []
    for pv in pilot_vehicles:
        agent_id = pv.get("agentid") or pv.get("id")
        imei = pv.get("imei", "")
        plate = pv.get("vehiclenumber", "")
        name = pv.get("name", "")
        folder = pv.get("folder", "")
        sensors = pv.get("sensors", {})
        sensor_count = len(sensors) if isinstance(sensors, (dict, list)) else 0

        stmt = select(Vehicle).where(Vehicle.pilot_agent_id == agent_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.imei = imei
            existing.plate_number = plate
            existing.name = name
            existing.folder = folder
            existing.sensor_count = sensor_count
            existing.is_active = True
            if user.client_account_id and not existing.client_account_id:
                existing.client_account_id = user.client_account_id
            vid = existing.id
        else:
            vehicle = Vehicle(
                pilot_agent_id=agent_id,
                imei=imei,
                plate_number=plate,
                name=name,
                folder=folder,
                sensor_count=sensor_count,
                client_account_id=user.client_account_id,
            )
            db.add(vehicle)
            await db.flush()
            vid = vehicle.id

        sensor_status = existing.sensor_status if existing else "normal"
        out.append({"id": vid, "plate_number": plate, "imei": imei, "folder": folder, "sensor_count": sensor_count, "sensor_status": sensor_status, "company_name": ""})

    await db.commit()
    is_su = user.role == "superadmin"
    return HTMLResponse(render_table_partial(out, is_su))


@router.post("/api/vehicles/{vehicle_id}/delete", response_class=HTMLResponse)
async def delete_vehicle(
    request: Request,
    vehicle_id: int,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    v = result.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    if user.role == "company_admin" and v.client_account_id != user.client_account_id:
        raise HTTPException(status_code=403, detail="Forbidden")
    await db.delete(v)
    await db.commit()

    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    query = apply_vehicle_filter(query, user, Vehicle)
    query = query.order_by(Vehicle.folder, Vehicle.plate_number)
    result = await db.execute(query)
    db_vehicles = result.scalars().all()

    company_ids = {v.client_account_id for v in db_vehicles if v.client_account_id}
    companies = {}
    if company_ids:
        for c in (await db.execute(select(ClientAccount).where(ClientAccount.id.in_(company_ids)))).scalars().all():
            companies[c.id] = c.name

    site_ids = {v.site_id for v in db_vehicles if v.site_id}
    sites = {}
    if site_ids:
        for s in (await db.execute(select(Site).where(Site.id.in_(site_ids)))).scalars().all():
            sites[s.id] = s.name

    out = [build_vehicle_dict(v, companies.get(v.client_account_id, ""), sites.get(v.site_id, "")) for v in db_vehicles]
    is_su = user.role == "superadmin"
    is_ca = user.role == "company_admin"
    return HTMLResponse(render_table_partial(out, is_su, is_ca))
