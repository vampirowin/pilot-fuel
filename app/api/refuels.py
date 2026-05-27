import asyncio
import math
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends, HTTPException, Query, Form, Path
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.refuel_entry import RefuelEntry
from app.models.pilot_refuel import PilotRefuel
from app.models.vehicle import Vehicle
from app.models.setting import Setting
from app.models.sync_log import SyncLog
from app.dependencies import get_current_username
from app.services.pilot_service import PilotService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
PER_PAGE = 10


def _render_vehicle_group(vehicle_id: int, entries: list, vmap: dict) -> str:
    plate = vmap.get(vehicle_id, {}).get("plate_number", "—")
    add_btn = f'<button class="btn btn-sm btn-secondary" hx-get="/api/refuels/add-form?vehicle_id={vehicle_id}" hx-target="#modal-container" hx-swap="innerHTML">+ Добавить</button>'
    h = f'<div class="card vehicle-group"><h3 class="vehicle-group-title"><span>{plate} <span class="vehicle-group-count">{len(entries)}</span></span>{add_btn}</h3><div class="table-container"><table><thead><tr><th>Дата</th><th>Pilot (л)</th><th>Чек (л)</th><th>Разница</th><th>Погрешность</th><th>Статус</th><th>Действия</th></tr></thead><tbody>'
    for e in entries:
        sc = STATUS_MAP.get(e.comparison_status, "")
        sl = STATUS_LABELS.get(e.comparison_status, e.comparison_status or "—")
        pa = f"{e.pilot_amount:.1f}" if e.pilot_amount is not None else "—"
        aa = f"{e.actual_amount:.1f}" if e.actual_amount is not None else "—"
        df = f"{e.difference:.1f}" if e.difference is not None else "—"
        er = f"{e.error_percent:.1f}%" if e.error_percent is not None else "—"
        ds = e.event_date.strftime("%d.%m.%Y %H:%M") if e.event_date else "—"
        h += f"<tr><td>{ds}</td><td>{pa}</td><td>{aa}</td><td>{df}</td><td>{er}</td><td><span class=\"status-badge {sc}\">{sl}</span></td><td><button class=\"btn btn-sm btn-secondary\" hx-get=\"/api/refuels/{e.id}/edit\" hx-target=\"#modal-container\" hx-swap=\"innerHTML\">Правка</button></td></tr>"
    h += "</tbody></table></div></div>"
    return h


def _pagination_html(page: int, total: int, qs: str) -> str:
    if total <= 1:
        return ""
    prev_d = "disabled" if page <= 1 else ""
    next_d = "disabled" if page >= total else ""
    qs_part = f"&{qs}" if qs else ""
    prev_url = f"/refuels?page={page-1}{qs_part}" if page > 1 else "#"
    next_url = f"/refuels?page={page+1}{qs_part}" if page < total else "#"
    return f"""<div class="pagination"><button class="chip {prev_d}" hx-get="{prev_url}" hx-target="#refuels-list" hx-push-url="true" {prev_d}>← Назад</button><span>стр. {page} из {total}</span><button class="chip {next_d}" hx-get="{next_url}" hx-target="#refuels-list" hx-push-url="true" {next_d}>Вперед →</button></div>"""


def _list_html(grouped: list, vmap: dict, page: int, total: int, qs: str) -> str:
    if not grouped:
        return '<div class="card"><div class="empty-state"><p>Нет заправок.</p></div></div>'
    start = (page - 1) * PER_PAGE
    h = ""
    for vid, entries in grouped[start:start + PER_PAGE]:
        h += _render_vehicle_group(vid, entries, vmap)
    h += _pagination_html(page, total, qs)
    return h


@router.get("/refuels", response_class=HTMLResponse)
async def refuels_page(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
):
    vehicle_id = request.query_params.get("vehicle_id")
    status_filter = request.query_params.get("status")
    date_from_str = request.query_params.get("date_from")
    date_to_str = request.query_params.get("date_to")

    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    df_str = date_from_str or month_start
    dt_str = date_to_str or today

    query = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    try:
        df = datetime.strptime(df_str, "%Y-%m-%d")
        query = query.where(RefuelEntry.event_date >= df)
    except ValueError:
        pass
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        query = query.where(RefuelEntry.event_date <= dt)
    except ValueError:
        pass
    if vehicle_id:
        query = query.where(RefuelEntry.vehicle_id == int(vehicle_id))
    if status_filter:
        query = query.where(RefuelEntry.comparison_status == status_filter)
    query = query.order_by(desc(RefuelEntry.event_date))

    entries = (await db.execute(query)).scalars().all()
    all_vehicles = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True).order_by(Vehicle.plate_number)
    )).scalars().all()

    vmap = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in all_vehicles}
    grouped = _group_by_vehicle(entries)
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    if page > total_pages:
        page = total_pages

    qparts = {}
    if df_str != month_start or dt_str != today:
        qparts["date_from"] = df_str
        qparts["date_to"] = dt_str
    if vehicle_id:
        qparts["vehicle_id"] = vehicle_id
    if status_filter:
        qparts["status"] = status_filter
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())

    rendered = _list_html(grouped, vmap, page, total_pages, qs)

    is_hx = request.headers.get("hx-request") == "true"
    if is_hx:
        return HTMLResponse(rendered)

    today_str = today
    month_start_str = month_start
    days7_str = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    days14_str = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    days30_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")
    days1_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")

    return templates.TemplateResponse(request, "refuels.html", {
        "list_html": rendered,
        "all_vehicles": all_vehicles,
        "selected_vehicle_id": int(vehicle_id) if vehicle_id else None,
        "selected_status": status_filter or "",
        "date_from": df_str,
        "date_to": dt_str,
        "today_str": today_str,
        "month_start_str": month_start_str,
        "days1_str": days1_str,
        "days7_str": days7_str,
        "days14_str": days14_str,
        "days30_str": days30_str,
        "page": page,
        "total_pages": total_pages,
    })


@router.get("/api/refuels/sync-modal", response_class=HTMLResponse)
async def sync_modal(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    all_vehicles = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True).order_by(Vehicle.plate_number)
    )).scalars().all()
    today = datetime.now().strftime("%Y-%m-%d")
    seven_ago = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    return templates.TemplateResponse(request, "sync_modal.html", {
        "all_vehicles": all_vehicles,
        "default_from": seven_ago,
        "default_to": today,
    })


@router.post("/api/refuels/sync", response_class=HTMLResponse)
async def sync_refuels(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=7),
    date_from: str = Form(default=None),
    date_to: str = Form(default=None),
    vehicle_id: str | None = Form(default=None),
):
    token = request.session.get("token")
    node_id = request.session.get("node_id", 0)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pilot = PilotService()
    now_local = datetime.now()
    if date_from and date_to:
        try:
            df = datetime.strptime(date_from, "%Y-%m-%d")
            dt = datetime.strptime(date_to, "%Y-%m-%d")
        except ValueError:
            df = now_local - timedelta(days=days)
            dt = now_local
    else:
        dt = now_local
        df = now_local - timedelta(days=days)

    start_str = df.strftime("%d.%m.%Y 00:00")
    stop_str = dt.strftime("%d.%m.%Y 23:59")

    q = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    if vehicle_id:
        q = q.where(Vehicle.id == int(vehicle_id))
    vehicles = (await db.execute(q)).scalars().all()
    if not vehicles:
        return HTMLResponse('<div class="card"><div class="empty-state"><p>Нет ТС для синхронизации.</p></div></div>')

    veh_ids = [v.pilot_agent_id for v in vehicles if v.pilot_agent_id]
    if not veh_ids:
        return HTMLResponse('<div class="card"><div class="empty-state"><p>Нет ТС с agent_id.</p></div></div>')

    ts = datetime.now().strftime('%d.%m %H:%M:%S')
    with open("sync_debug.log", "a") as lf:
        lf.write(f"[{ts}] veh_ids={veh_ids}, start={start_str}, stop={stop_str}\n")
        lf.write(f"[{ts}] vehicle_id param received: '{vehicle_id}' (type={type(vehicle_id).__name__})\n")
    BATCH_SIZE = 20
    all_events = []
    try:
        for i in range(0, len(veh_ids), BATCH_SIZE):
            batch = veh_ids[i:i + BATCH_SIZE]
            for attempt in range(3):
                try:
                    batch_events = await pilot.get_refuel_report(token, node_id, batch, start_str, stop_str)
                    break
                except Exception:
                    if attempt == 2:
                        raise
                    await asyncio.sleep(1)
            all_events.extend(batch_events)
            await asyncio.sleep(0.5)
    except Exception as e:
        with open("sync_debug.log", "a") as lf:
            lf.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] Pilot API error: {e}\n")
        return HTMLResponse(f'<div class="card"><div class="empty-state"><p>Ошибка Pilot API: {str(e)[:200]}</p></div></div>')
    raw_events = all_events
    with open("sync_debug.log", "a") as lf:
        lf.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] raw_events count={len(raw_events)}\n")
        if raw_events:
            lf.write(f"[{datetime.now().strftime('%d.%m %H:%M:%S')}] first event: {raw_events[0]}\n")

    total_new = 0
    errors = []

    for ev in raw_events:
        ev_name_lower = (ev.get("name") or "").strip().lower()
        v = None
        for db_v in vehicles:
            if db_v.plate_number and db_v.plate_number.strip().lower() in ev_name_lower:
                v = db_v
                break
        if not v:
            errors.append(f"ТС не найден: {ev.get('name', '?')}")
            continue

        ev_ts = _parse_timestamp(ev.get("ts"))
        if not ev_ts:
            continue
        if ev_ts.tzinfo is not None:
            ev_ts = ev_ts.replace(tzinfo=None)

        amount = ev.get("refuel_amount")
        if not amount or amount <= 0:
            continue

        start_lvl = ev.get("start_level")
        end_lvl = ev.get("end_level")

        existing = await db.execute(
            select(PilotRefuel).where(
                PilotRefuel.vehicle_id == v.id,
                PilotRefuel.event_date >= ev_ts - timedelta(seconds=30),
                PilotRefuel.event_date <= ev_ts + timedelta(seconds=30),
            )
        )
        if existing.scalar_one_or_none():
            continue

        pr = PilotRefuel(
            vehicle_id=v.id,
            event_date=ev_ts,
            amount=amount,
            start_level=start_lvl,
            end_level=end_lvl,
            address=(ev.get("address") or "")[:500],
            lat=ev.get("lat"),
            lon=ev.get("lon"),
            raw_data=ev,
        )
        db.add(pr)
        await db.flush()

        refuel_entry = RefuelEntry(
            vehicle_id=v.id,
            pilot_refuel_id=pr.id,
            event_date=ev_ts,
            pilot_amount=amount,
            source="pilot_sync",
        )
        db.add(refuel_entry)
        total_new += 1

    log = SyncLog(
        sync_type="refuels",
        status="completed" if not errors else "partial",
        records_affected=total_new,
        details="; ".join(errors) if errors else None,
        created_by=request.session.get("username"),
    )
    db.add(log)
    await db.commit()

    query = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    query = query.where(RefuelEntry.event_date >= df, RefuelEntry.event_date <= dt.replace(hour=23, minute=59, second=59))
    if vehicle_id:
        query = query.where(RefuelEntry.vehicle_id == int(vehicle_id))
    query = query.order_by(desc(RefuelEntry.event_date))
    entries = (await db.execute(query)).scalars().all()

    vehicles_result = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True)
    )).scalars().all()
    vmap = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in vehicles_result}

    grouped = _group_by_vehicle(entries)
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    qparts = {"date_from": date_from or df.strftime("%Y-%m-%d"), "date_to": date_to or dt.strftime("%Y-%m-%d")}
    if vehicle_id:
        qparts["vehicle_id"] = vehicle_id
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    html = _list_html(grouped, vmap, 1, total_pages, qs)

    if total_new:
        html += f'<div class="toast toast-success">Загружено {total_new} заправок</div>'
    if errors:
        html += f'<div class="toast toast-warning">Ошибки: {"; ".join(errors[:3])}</div>'
    if not total_new and raw_events:
        names = "; ".join(e.get("name", "?")[:40] for e in raw_events[:5])
        html += f'<div class="toast toast-warning">Pilot: {len(raw_events)} событий, ни одно не совпало. Имена: {names}</div>'
    if raw_events:
        html += f'<div class="toast toast-info">Всего от Pilot: {len(raw_events)} событий</div>'

    return HTMLResponse(html)


async def _get_thresholds(db: AsyncSession) -> tuple[float, float]:
    n, w = 3.0, 10.0
    rows = (await db.execute(select(Setting).where(Setting.key.in_(["normal_threshold", "warning_threshold"])))).scalars().all()
    for s in rows:
        if s.key == "normal_threshold":
            n = float(s.value)
        elif s.key == "warning_threshold":
            w = float(s.value)
    return n, w


def _calc_comparison(pilot_amount: float | None, actual_amount: float | None, n_th: float, w_th: float) -> tuple:
    if pilot_amount and actual_amount and pilot_amount > 0:
        diff = actual_amount - pilot_amount
        err = abs(diff) / pilot_amount * 100
        status = "normal" if err <= n_th else ("small_deviation" if err <= w_th else "unacceptable")
    else:
        diff = None
        err = None
        status = "pilot_missing" if actual_amount else None
    return diff, err, status


@router.get("/api/refuels/add-form", response_class=HTMLResponse)
async def add_refuel_form(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
    vehicle_id: int = Query(...),
):
    vehicle = await db.get(Vehicle, vehicle_id)
    all_v = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True).order_by(Vehicle.plate_number)
    )).scalars().all()
    return templates.TemplateResponse(request, "add_refuel_modal.html", {
        "vehicle": vehicle,
        "all_vehicles": all_v,
        "default_date": datetime.now().strftime("%Y-%m-%d"),
        "default_time": datetime.now().strftime("%H:%M"),
    })


@router.post("/api/refuels/add", response_class=HTMLResponse)
async def add_refuel(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
    vehicle_id: int = Form(...),
    event_date: str = Form(...),
    event_time: str = Form(...),
    actual_amount: float = Form(...),
    receipt_number: str = Form(default=""),
):
    try:
        dt = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        dt = datetime.now()

    n_th, w_th = await _get_thresholds(db)

    nearby = await db.execute(
        select(PilotRefuel).where(
            PilotRefuel.vehicle_id == vehicle_id,
            PilotRefuel.event_date >= dt - timedelta(hours=1),
            PilotRefuel.event_date <= dt + timedelta(hours=1),
        ).order_by(PilotRefuel.event_date)
    )
    nearby_refuel = nearby.scalar_one_or_none()

    if nearby_refuel:
        diff, err, status = _calc_comparison(nearby_refuel.amount, actual_amount, n_th, w_th)
        pilot_refuel_id = nearby_refuel.id
        pilot_amount = nearby_refuel.amount
    else:
        diff = err = None
        status = "pilot_missing"
        pilot_refuel_id = None
        pilot_amount = None

    entry = RefuelEntry(
        vehicle_id=vehicle_id,
        pilot_refuel_id=pilot_refuel_id,
        event_date=dt,
        pilot_amount=pilot_amount,
        actual_amount=actual_amount,
        receipt_number=receipt_number or None,
        source="manual",
        difference=diff,
        error_percent=err,
        comparison_status=status,
    )
    db.add(entry)

    log = SyncLog(
        sync_type="refuel_add",
        status="completed",
        records_affected=1,
        details=f"manual add for vehicle {vehicle_id}",
        created_by=request.session.get("username"),
    )
    db.add(log)
    await db.commit()

    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    q = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    try:
        df = datetime.strptime(month_start, "%Y-%m-%d")
        q = q.where(RefuelEntry.event_date >= df)
        q = q.where(RefuelEntry.event_date <= datetime.now().replace(hour=23, minute=59, second=59))
    except ValueError:
        pass
    q = q.order_by(desc(RefuelEntry.event_date))
    entries = (await db.execute(q)).scalars().all()

    all_v = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    )).scalars().all()
    vmap = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in all_v}
    grouped = _group_by_vehicle(entries)
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    qparts = {"date_from": month_start, "date_to": today}
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    html = _list_html(grouped, vmap, 1, total_pages, qs)
    html += '<div class="toast toast-success">Заправка добавлена</div>'
    return HTMLResponse(html)


@router.get("/api/refuels/{entry_id}/edit", response_class=HTMLResponse)
async def edit_refuel_form(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
    entry_id: int = Path(...),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    vehicle = await db.get(Vehicle, entry.vehicle_id)
    all_v = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True).order_by(Vehicle.plate_number)
    )).scalars().all()
    return templates.TemplateResponse(request, "edit_refuel_modal.html", {
        "entry": entry,
        "vehicle": vehicle,
        "all_vehicles": all_v,
        "default_date": entry.event_date.strftime("%Y-%m-%d") if entry.event_date else "",
        "default_time": entry.event_date.strftime("%H:%M") if entry.event_date else "",
    })


@router.post("/api/refuels/{entry_id}/edit", response_class=HTMLResponse)
async def edit_refuel(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
    entry_id: int = Path(...),
    actual_amount: float = Form(...),
    receipt_number: str = Form(default=""),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")

    n_th, w_th = await _get_thresholds(db)
    entry.actual_amount = actual_amount
    entry.receipt_number = receipt_number or None

    pilot_amount = entry.pilot_amount
    if entry.pilot_refuel_id and pilot_amount:
        diff, err, status = _calc_comparison(pilot_amount, actual_amount, n_th, w_th)
    else:
        diff = err = None
        status = "pilot_missing" if actual_amount else None

    entry.difference = diff
    entry.error_percent = err
    entry.comparison_status = status

    log = SyncLog(
        sync_type="refuel_edit",
        status="completed",
        records_affected=1,
        details=f"edited entry {entry_id}",
        created_by=request.session.get("username"),
    )
    db.add(log)
    await db.commit()

    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    q = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    try:
        df = datetime.strptime(month_start, "%Y-%m-%d")
        q = q.where(RefuelEntry.event_date >= df)
        q = q.where(RefuelEntry.event_date <= datetime.now().replace(hour=23, minute=59, second=59))
    except ValueError:
        pass
    q = q.order_by(desc(RefuelEntry.event_date))
    entries = (await db.execute(q)).scalars().all()

    all_v = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    )).scalars().all()
    vmap = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in all_v}
    grouped = _group_by_vehicle(entries)
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    qparts = {"date_from": month_start, "date_to": today}
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    html = _list_html(grouped, vmap, 1, total_pages, qs)
    html += '<div class="toast toast-success">Заправка обновлена</div>'
    return HTMLResponse(html)


def _parse_timestamp(val) -> datetime | None:
    if not val:
        return None
    try:
        ts = int(val)
    except (ValueError, TypeError):
        return None
    if ts > 1e12:
        ts //= 1000
    return datetime.fromtimestamp(ts, tz=timezone.utc)


def _parse_float(val) -> float | None:
    if val is None:
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def _group_by_vehicle(entries: list) -> list:
    seen = {}
    for e in entries:
        seen.setdefault(e.vehicle_id, []).append(e)
    return sorted(seen.items())


STATUS_MAP = {
    "normal": "status-normal",
    "small_deviation": "status-small-deviation",
    "unacceptable": "status-unacceptable",
    "pilot_missing": "status-pilot-missing",
    "false_reading": "status-false-reading",
}

STATUS_LABELS = {
    "normal": "Норма",
    "small_deviation": "Расхождение",
    "unacceptable": "Недопустимо",
    "pilot_missing": "Нет в Pilot",
    "false_reading": "Ложная",
}
