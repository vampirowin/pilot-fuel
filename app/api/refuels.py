from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends, HTTPException, Query
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.refuel_entry import RefuelEntry
from app.models.pilot_refuel import PilotRefuel
from app.models.vehicle import Vehicle
from app.models.sync_log import SyncLog
from app.dependencies import get_current_username
from app.services.pilot_service import PilotService

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/refuels", response_class=HTMLResponse)
async def refuels_page(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    vehicle_id = request.query_params.get("vehicle_id")
    status_filter = request.query_params.get("status")

    query = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    if vehicle_id:
        query = query.where(RefuelEntry.vehicle_id == int(vehicle_id))
    if status_filter:
        query = query.where(RefuelEntry.comparison_status == status_filter)
    query = query.order_by(desc(RefuelEntry.event_date)).limit(100)

    entries = (await db.execute(query)).scalars().all()
    all_vehicles = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True).order_by(Vehicle.plate_number)
    )).scalars().all()

    vehicles_map = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in all_vehicles}

    return templates.TemplateResponse(request, "refuels.html", {
        "entries": entries,
        "all_vehicles": all_vehicles,
        "vehicles_map": vehicles_map,
        "selected_vehicle_id": int(vehicle_id) if vehicle_id else None,
        "selected_status": status_filter or "",
    })


@router.post("/api/refuels/sync", response_class=HTMLResponse)
async def sync_refuels(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=7),
    date_from: str = Query(default=None),
    date_to: str = Query(default=None),
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

    vehicles = (await db.execute(
        select(Vehicle).where(
            Vehicle.is_active == True,
            Vehicle.has_fuel_sensor == True,
        )
    )).scalars().all()
    if not vehicles:
        return HTMLResponse('<div class="card"><div class="empty-state"><p>Нет ТС для синхронизации.</p></div></div>')

    veh_ids = [v.pilot_agent_id for v in vehicles if v.pilot_agent_id]
    if not veh_ids:
        return HTMLResponse('<div class="card"><div class="empty-state"><p>Нет ТС с agent_id.</p></div></div>')

    try:
        raw_events = await pilot.get_refuel_report(token, node_id, veh_ids, start_str, stop_str)
    except Exception as e:
        return HTMLResponse(f'<div class="card"><div class="empty-state"><p>Ошибка Pilot API: {str(e)[:200]}</p></div></div>')

    name_map = {}
    for v in vehicles:
        key = (v.plate_number or "").strip().lower()
        if key:
            name_map[key] = v
        name_key = (v.name or "").strip().lower()
        if name_key and name_key != key:
            name_map[name_key] = v

    total_new = 0
    errors = []

    for ev in raw_events:
        ev_name = (ev.get("name") or "").strip().lower()
        v = name_map.get(ev_name)
        if not v:
            errors.append(f"ТС не найден: {ev.get('name', '?')}")
            continue

        ev_ts = _parse_timestamp(ev.get("ts"))
        if not ev_ts:
            continue

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

    entries = (await db.execute(
        select(RefuelEntry).where(RefuelEntry.is_deleted == False)
        .order_by(desc(RefuelEntry.event_date)).limit(100)
    )).scalars().all()

    vehicles_result = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True)
    )).scalars().all()
    vehicles_map = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in vehicles_result}

    html = ""
    if not entries:
        html = '<div class="card"><div class="empty-state"><p>Нет заправок.</p></div></div>'
    else:
        html = '<div class="card"><div class="table-container"><table><thead><tr><th>Дата</th><th>ТС</th><th>Pilot (л)</th><th>Чек (л)</th><th>Разница</th><th>Погрешность</th><th>Статус</th><th>Действия</th></tr></thead><tbody>'
        for e in entries:
            plate = vehicles_map.get(e.vehicle_id, {}).get("plate_number", "—")
            status_class = STATUS_MAP.get(e.comparison_status, "")
            status_label = STATUS_LABELS.get(e.comparison_status, e.comparison_status or "—")
            pilot_amt = f"{e.pilot_amount:.1f}" if e.pilot_amount is not None else "—"
            actual_amt = f"{e.actual_amount:.1f}" if e.actual_amount is not None else "—"
            diff = f"{e.difference:.1f}" if e.difference is not None else "—"
            err = f"{e.error_percent:.1f}%" if e.error_percent is not None else "—"
            date_str = e.event_date.strftime("%d.%m.%Y %H:%M") if e.event_date else "—"
            html += f"""<tr>
                <td>{date_str}</td>
                <td><strong>{plate}</strong></td>
                <td>{pilot_amt}</td>
                <td>{actual_amt}</td>
                <td>{diff}</td>
                <td>{err}</td>
                <td><span class="status-badge {status_class}">{status_label}</span></td>
                <td><button class="btn btn-sm btn-secondary" hx-get="/api/refuels/{e.id}/edit" hx-target="#modal-container" hx-swap="innerHTML">Правка</button></td>
            </tr>"""
        html += "</tbody></table></div></div>"

    if total_new:
        html += f'<div class="toast toast-success">Загружено {total_new} заправок</div>'
    if errors:
        html += f'<div class="toast toast-warning">Ошибки: {"; ".join(errors[:3])}</div>'

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
