from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
from app.models.fuel_sensor import FuelSensor
from app.services.pilot_service import PilotService
from app.dependencies import get_current_username

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def group_by_folder(vehicles: list) -> list:
    groups = {}
    for v in vehicles:
        folder = v.get("folder") or "Без папки"
        groups.setdefault(folder, []).append(v)
    sorted_folders = sorted(groups.keys(), key=lambda x: (x == "Без папки", x))
    return [(f, groups[f]) for f in sorted_folders]


def build_vehicle_dict(v: Vehicle) -> dict:
    return {
        "id": v.id,
        "plate_number": v.plate_number,
        "imei": v.imei,
        "folder": v.folder,
        "sensor_count": v.sensor_count,
        "owner": v.owner,
        "location": v.location,
    }


def render_table_partial(vehicles: list, is_admin: bool = False) -> str:
    if not vehicles:
        return '''<div class="card"><div class="empty-state">
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>
          <h3>Нет транспортных средств</h3>
          <p>Нажмите «Синхронизировать», чтобы загрузить список ТС из Pilot.</p>
        </div></div>'''

    groups = group_by_folder(vehicles)
    html = ""
    gidx = 0
    for group_name, group_vehicles in groups:
        gidx += 1
        gid = f"g-{gidx}"
        html += f'''<div class="card" style="margin-top: 16px;">
          <div class="card-header collapsible-header" style="padding: 12px 20px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted);" onclick="toggleGroup('{gid}')">
            <span class="arrow">&#9660;</span>
            {group_name} <span style="font-weight: 400; color: var(--text-dim);">({len(group_vehicles)})</span>
          </div>
          <div class="collapsible-body" id="{gid}">
            <div class="table-container"><table>
              <thead><tr>
                <th>#</th><th>Госномер</th><th>IMEI</th><th>Датчики</th><th>Заправки</th><th>Собственник</th><th>Площадка</th>'''
        if is_admin:
            html += '<th style="width: 80px;">Действия</th>'
        html += '</tr></thead><tbody>'
        for idx, v in enumerate(group_vehicles, 1):
            badge = "status-normal" if v.get("sensor_count", 0) > 0 else "status-false-reading"
            html += f'''<tr id="v-{v["id"]}">
              <td style="color: var(--text-dim);">{idx}</td>
              <td><strong>{v.get("plate_number") or "—"}</strong></td>
              <td style="font-family:monospace;font-size:12px">{v.get("imei") or "—"}</td>
              <td><span class="status-badge {badge}">{v.get("sensor_count", 0)} датч.</span></td>
              <td><a href="/refuels?vehicle_id={v["id"]}" class="btn btn-sm btn-secondary">Заправки</a></td>
              <td>{v.get("owner") or "—"}</td>
              <td>{v.get("location") or "—"}</td>'''
            if is_admin:
                html += f'''<td><button class="btn btn-sm btn-danger" hx-post="/api/vehicles/{v["id"]}/toggle-sensor" hx-target="#v-{v["id"]}" hx-swap="outerHTML" hx-confirm="Пометить «{v.get("plate_number") or v["id"]}» как ТС без датчика топлива?">Нет датчика</button></td>'''
            html += '</tr>'
        html += '</tbody></table></div></div></div>'
    return html


@router.get("/vehicles", response_class=HTMLResponse)
async def vehicles_page(
    request: Request,
    plate: str = "",
    imei: str = "",
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    filters = []
    if plate:
        filters.append(Vehicle.plate_number.ilike(f"%{plate}%"))
    if imei:
        filters.append(Vehicle.imei.ilike(f"%{imei}%"))
    if filters:
        query = query.where(or_(*filters))
    query = query.order_by(Vehicle.folder, Vehicle.plate_number)
    result = await db.execute(query)
    db_vehicles = result.scalars().all()

    out = [build_vehicle_dict(v) for v in db_vehicles]
    is_admin = request.session.get("is_admin", False)

    return templates.TemplateResponse(request, "vehicles.html", {
        "plate": plate,
        "imei": imei,
        "is_admin": is_admin,
        "groups": group_by_folder(out) if out else [],
    })


@router.get("/api/vehicles/search", response_class=HTMLResponse)
async def search_vehicles(
    request: Request,
    plate: str = "",
    imei: str = "",
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    filters = []
    if plate:
        filters.append(Vehicle.plate_number.ilike(f"%{plate}%"))
    if imei:
        filters.append(Vehicle.imei.ilike(f"%{imei}%"))
    if filters:
        query = query.where(or_(*filters))
    query = query.order_by(Vehicle.folder, Vehicle.plate_number)
    result = await db.execute(query)
    db_vehicles = result.scalars().all()

    out = [build_vehicle_dict(v) for v in db_vehicles]
    is_admin = request.session.get("is_admin", False)
    return HTMLResponse(render_table_partial(out, is_admin))


@router.post("/api/vehicles/{vehicle_id}/toggle-sensor", response_class=HTMLResponse)
async def toggle_fuel_sensor(
    request: Request,
    vehicle_id: int,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    if not request.session.get("is_admin"):
        raise HTTPException(status_code=403, detail="Forbidden")
    result = await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))
    v = result.scalar_one_or_none()
    if not v:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    v.has_fuel_sensor = not v.has_fuel_sensor
    await db.commit()
    return HTMLResponse("")


@router.post("/api/vehicles/sync", response_class=HTMLResponse)
async def sync_vehicles(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    token = request.session.get("token")
    node_id = request.session.get("node_id", 0)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    service = PilotService()
    pilot_vehicles = await service.get_vehicles(token, node_id)

    out = []
    for pv in pilot_vehicles:
        agent_id = pv.get("agentid") or pv.get("id")
        imei = pv.get("imei", "")
        plate = pv.get("vehiclenumber", "")
        name = pv.get("name", "")
        folder = pv.get("folder", "")
        owner = pv.get("vv_sobstv", "") or pv.get("owner", "")
        location = pv.get("vv_ploshadka", "") or pv.get("location", "")
        sensors = pv.get("sensors", {})
        sensor_count = len(sensors) if isinstance(sensors, dict) else 0

        stmt = select(Vehicle).where(Vehicle.pilot_agent_id == agent_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.imei = imei
            existing.plate_number = plate
            existing.name = name
            existing.folder = folder
            existing.owner = owner
            existing.location = location
            existing.sensor_count = sensor_count
            existing.is_active = True
            vid = existing.id
        else:
            vehicle = Vehicle(
                pilot_agent_id=agent_id,
                imei=imei,
                plate_number=plate,
                name=name,
                folder=folder,
                owner=owner,
                location=location,
                sensor_count=sensor_count,
            )
            db.add(vehicle)
            await db.flush()
            vid = vehicle.id

        out.append({
            "id": vid,
            "plate_number": plate,
            "imei": imei,
            "folder": folder,
            "sensor_count": sensor_count,
            "owner": owner,
            "location": location,
        })

    await db.commit()
    is_admin = request.session.get("is_admin", False)
    return HTMLResponse(render_table_partial(out, is_admin))
