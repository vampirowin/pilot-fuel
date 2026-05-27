from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
from app.models.fuel_sensor import FuelSensor
from app.services.pilot_service import PilotService
from app.dependencies import get_current_username

router = APIRouter()


def render_vehicles_table(vehicles: list) -> str:
    if not vehicles:
        return """<div class="card"><div class="empty-state">
          <svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg>
          <h3>Нет транспортных средств</h3>
          <p>Нажмите «Синхронизировать», чтобы загрузить список ТС из Pilot.</p>
        </div></div>"""

    rows = ""
    for v in vehicles:
        plate = v.get("plate_number") or "—"
        name = v.get("name") or "—"
        imei = v.get("imei") or "—"
        folder = v.get("folder") or "—"
        vtype = v.get("vehicle_type") or "—"
        s_count = len(v.get("sensors", []))
        badge_class = "status-normal" if s_count > 0 else "status-false-reading"
        rows += f"""<tr>
            <td><strong>{plate}</strong></td>
            <td>{name}</td>
            <td style="font-family:monospace;font-size:12px">{imei}</td>
            <td>{folder}</td>
            <td>{vtype}</td>
            <td><span class="status-badge {badge_class}">{s_count} датч.</span></td>
            <td><a href="/refuels?vehicle_id={v["id"]}" class="btn btn-sm btn-secondary">Заправки</a></td>
        </tr>"""

    return f"""<div class="card"><div class="table-container"><table>
        <thead><tr>
            <th>Госномер</th><th>Название</th><th>IMEI</th><th>Папка</th><th>Тип</th><th>Датчики</th><th>Действия</th>
        </tr></thead>
        <tbody>{rows}</tbody>
    </table></div></div>"""


@router.get("/vehicles", response_class=HTMLResponse)
async def vehicles_page(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(select(Vehicle).where(Vehicle.is_active == True).order_by(Vehicle.plate_number))
    db_vehicles = result.scalars().all()

    out = []
    for v in db_vehicles:
        sensors_res = await db.execute(
            select(FuelSensor).where(FuelSensor.vehicle_id == v.id, FuelSensor.is_active == True)
        )
        sensors = sensors_res.scalars().all()
        out.append({
            "id": v.id,
            "plate_number": v.plate_number,
            "name": v.name,
            "imei": v.imei,
            "folder": v.folder,
            "vehicle_type": v.vehicle_type,
            "sensor_count": len(sensors),
        })

    username = request.session.get("username", "")
    return HTMLResponse(TABLE_PAGE.format(table=render_vehicles_table(out), username=username))

TABLE_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pilot-fuel — Транспорт</title>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<link rel="stylesheet" href="/static/css/style.css">
</head>
<body>
<div class="app-layout">
  <aside class="sidebar">
    <div class="sidebar-header">
      <svg class="sidebar-logo" width="32" height="32" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 22V8l7-5v14l-7 5zM10 3l7 5v14l-7-5V3zM17 8l4 2v12l-4-2V8z"/></svg>
      <span class="sidebar-title">pilot-fuel</span>
    </div>
    <nav class="sidebar-nav">
      <a href="/" class="nav-item"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 9l9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/><polyline points="9 22 9 12 15 12 15 22"/></svg><span>Главная</span></a>
      <a href="/vehicles" class="nav-item active"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg><span>Транспорт</span></a>
      <a href="/refuels" class="nav-item"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 22V8l7-5v14l-7 5zM10 3l7 5v14l-7-5V3zM17 8l4 2v12l-4-2V8z"/></svg><span>Заправки</span></a>
      <a href="/critical" class="nav-item nav-item-warning"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><span>Критические</span></a>
    </nav>
    <div class="sidebar-footer">
      <div class="sidebar-user">
        <span>{username}</span>
        <a href="/logout" class="nav-item logout-btn"><svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9 21H5a2 2 0 0 1-2-2V5a2 2 0 0 1 2-2h4"/><polyline points="16 17 21 12 16 7"/><line x1="21" y1="12" x2="9" y2="12"/></svg><span>Выйти</span></a>
      </div>
    </div>
  </aside>
  <main class="main-content">
    <div class="page-header">
      <h1>Транспортные средства</h1>
      <div class="page-header-actions">
        <button class="btn btn-primary" hx-post="/api/vehicles/sync" hx-target="#vehicles-table" hx-swap="innerHTML" hx-indicator="#sync-spinner">
          <span class="btn-text">Синхронизировать</span>
          <span class="btn-loading">Синхронизация...</span>
        </button>
      </div>
    </div>
    <div id="vehicles-table">{table}</div>
  </main>
</div>
</body>
</html>"""


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
        vtype = pv.get("type", "")

        stmt = select(Vehicle).where(Vehicle.pilot_agent_id == agent_id)
        existing = (await db.execute(stmt)).scalar_one_or_none()
        if existing:
            existing.imei = imei
            existing.plate_number = plate
            existing.name = name
            existing.folder = folder
            existing.vehicle_type = vtype
            existing.is_active = True
            vid = existing.id
        else:
            vehicle = Vehicle(
                pilot_agent_id=agent_id,
                imei=imei,
                plate_number=plate,
                name=name,
                folder=folder,
                vehicle_type=vtype,
            )
            db.add(vehicle)
            await db.flush()
            vid = vehicle.id

        out.append({
            "id": vid,
            "plate_number": plate,
            "name": name,
            "imei": imei,
            "folder": folder,
            "vehicle_type": vtype,
            "sensors": [],
        })

    await db.commit()
    return HTMLResponse(render_vehicles_table(out))
