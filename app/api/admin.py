from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
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


def render_no_sensor_partial(vehicles: list) -> str:
    if not vehicles:
        return '''<div class="card"><div class="empty-state">
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>
          <h3>Нет исключённых ТС</h3>
          <p>Все транспортные средства имеют датчики топлива.</p>
        </div></div>'''

    groups = group_by_folder(vehicles)
    html = ""
    gidx = 0
    for group_name, group_vehicles in groups:
        gidx += 1
        gid = f"ns-{gidx}"
        html += f'''<div class="card" style="margin-top: 16px;">
          <div class="card-header collapsible-header" style="padding: 12px 20px; border-bottom: 1px solid var(--border); font-weight: 600; font-size: 14px; text-transform: uppercase; letter-spacing: 0.5px; color: var(--text-muted);" onclick="toggleGroup('{gid}')">
            <span class="arrow">&#9660;</span>
            {group_name} <span style="font-weight: 400; color: var(--text-dim);">({len(group_vehicles)})</span>
          </div>
          <div class="collapsible-body" id="{gid}">
            <div class="table-container"><table>
              <thead><tr>
                <th>#</th><th>Госномер</th><th>IMEI</th><th>Действия</th>
              </tr></thead>
              <tbody>'''
        for idx, v in enumerate(group_vehicles, 1):
            html += f'''<tr id="v-{v["id"]}">
              <td style="color: var(--text-dim);">{idx}</td>
              <td><strong>{v.get("plate_number") or "—"}</strong></td>
              <td style="font-family:monospace;font-size:12px">{v.get("imei") or "—"}</td>
              <td><button class="btn btn-sm btn-primary" hx-post="/api/vehicles/{v['id']}/toggle-sensor" hx-target="#v-{v['id']}" hx-swap="outerHTML" hx-confirm="Вернуть &laquo;{v.get('plate_number') or v['id']}&raquo; в список ТС с датчиками?">Вернуть</button></td>
            </tr>'''
        html += '</tbody></table></div></div></div>'
    return html


@router.get("/admin/vehicles-no-sensor", response_class=HTMLResponse)
async def vehicles_no_sensor(
    request: Request,
    plate: str = "",
    imei: str = "",
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    if not request.session.get("is_admin"):
        return RedirectResponse(url="/", status_code=302)

    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == False)
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

    out = [{
        "id": v.id,
        "plate_number": v.plate_number,
        "imei": v.imei,
        "folder": v.folder,
    } for v in db_vehicles]

    return templates.TemplateResponse(request, "vehicles_no_sensor.html", {
        "plate": plate,
        "imei": imei,
        "total_count": len(out),
        "groups": group_by_folder(out) if out else [],
    })


@router.get("/api/admin/vehicles-no-sensor/search", response_class=HTMLResponse)
async def search_no_sensor(
    request: Request,
    plate: str = "",
    imei: str = "",
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    if not request.session.get("is_admin"):
        return HTMLResponse("Forbidden", status_code=403)

    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == False)
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

    out = [{
        "id": v.id,
        "plate_number": v.plate_number,
        "imei": v.imei,
        "folder": v.folder,
    } for v in db_vehicles]

    return HTMLResponse(render_no_sensor_partial(out))
