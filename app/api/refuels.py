from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse
from sqlalchemy import select, desc
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.refuel_entry import RefuelEntry
from app.models.vehicle import Vehicle
from app.dependencies import get_current_username
from app.services.pilot_service import PilotService

router = APIRouter()


@router.get("/refuels", response_class=HTMLResponse)
async def refuels_page(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    username = request.session.get("username", "")
    vehicle_id = request.query_params.get("vehicle_id")
    status_filter = request.query_params.get("status")

    query = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    if vehicle_id:
        query = query.where(RefuelEntry.vehicle_id == int(vehicle_id))
    if status_filter:
        query = query.where(RefuelEntry.comparison_status == status_filter)
    query = query.order_by(desc(RefuelEntry.event_date)).limit(100)

    result = await db.execute(query)
    entries = result.scalars().all()

    vehicles_result = await db.execute(select(Vehicle).where(Vehicle.is_active == True).order_by(Vehicle.plate_number))
    vehicles = vehicles_result.scalars().all()

    rows = ""
    for e in entries:
        v = next((x for x in vehicles if x.id == e.vehicle_id), None)
        plate = v.plate_number if v else "—"
        status_class = STATUS_MAP.get(e.comparison_status, "")
        status_label = STATUS_LABELS.get(e.comparison_status, e.comparison_status or "—")
        pilot_amt = f"{e.pilot_amount:.1f}" if e.pilot_amount is not None else "—"
        actual_amt = f"{e.actual_amount:.1f}" if e.actual_amount is not None else "—"
        diff = f"{e.difference:.1f}" if e.difference is not None else "—"
        err = f"{e.error_percent:.1f}%" if e.error_percent is not None else "—"
        date_str = e.event_date.strftime("%d.%m.%Y %H:%M") if e.event_date else "—"

        rows += f"""<tr>
            <td>{date_str}</td>
            <td><strong>{plate}</strong></td>
            <td>{pilot_amt}</td>
            <td>{actual_amt}</td>
            <td>{diff}</td>
            <td>{err}</td>
            <td><span class="status-badge {status_class}">{status_label}</span></td>
            <td>
                <button class="btn btn-sm btn-secondary" hx-get="/api/refuels/{e.id}/edit" hx-target="#modal-container" hx-swap="innerHTML">Правка</button>
            </td>
        </tr>"""

    filter_options = ""
    for val, label in [("", "Все"), ("normal", "Норма"), ("small_deviation", "Расхождение"), ("unacceptable", "Недопустимо"), ("pilot_missing", "Нет в Pilot"), ("false_reading", "Ложная")]:
        sel = " selected" if status_filter == val else ""
        filter_options += f"<option value=\"{val}\"{sel}>{label}</option>"

    vehicle_options = "<option value=\"\">Все ТС</option>"
    for v in vehicles:
        sel = " selected" if vehicle_id and int(vehicle_id) == v.id else ""
        vehicle_options += f"<option value=\"{v.id}\"{sel}>{v.plate_number or v.name}</option>"

    return HTMLResponse(REFUELS_PAGE.format(
        rows=rows,
        username=username,
        filter_options=filter_options,
        vehicle_options=vehicle_options,
    ))

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
    "pilot_missing": "Нет в Pilot 🚨",
    "false_reading": "Ложная",
}

REFUELS_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pilot-fuel — Заправки</title>
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
      <a href="/vehicles" class="nav-item"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="1" y="3" width="15" height="13"/><polygon points="16 8 20 8 23 11 23 16 16 16 16 8"/><circle cx="5.5" cy="18.5" r="2.5"/><circle cx="18.5" cy="18.5" r="2.5"/></svg><span>Транспорт</span></a>
      <a href="/refuels" class="nav-item active"><svg width="20" height="20" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M3 22V8l7-5v14l-7 5zM10 3l7 5v14l-7-5V3zM17 8l4 2v12l-4-2V8z"/></svg><span>Заправки</span></a>
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
      <h1>Заправки</h1>
      <div class="page-header-actions">
        <button class="btn btn-primary" hx-get="/api/refuels/add-form" hx-target="#modal-container" hx-swap="innerHTML">+ Добавить заправку</button>
        <button class="btn btn-secondary" hx-post="/api/refuels/sync" hx-target="#refuels-list" hx-swap="innerHTML">
          <span class="btn-text">Синхр. из Pilot</span>
          <span class="btn-loading">Загрузка...</span>
        </button>
      </div>
    </div>

    <div class="filters-bar">
      <div class="form-group">
        <label>Транспорт</label>
        <select id="filter-vehicle" onchange="window.location='?vehicle_id='+this.value+'&status='+document.getElementById('filter-status').value">
          {vehicle_options}
        </select>
      </div>
      <div class="form-group">
        <label>Статус</label>
        <select id="filter-status" onchange="window.location='?status='+this.value+'&vehicle_id='+document.getElementById('filter-vehicle').value">
          {filter_options}
        </select>
      </div>
    </div>

    <div id="refuels-list">
      <div class="card">
        <div class="table-container">
          <table>
            <thead>
              <tr>
                <th>Дата</th><th>ТС</th><th>Pilot (л)</th><th>Чек (л)</th><th>Разница</th><th>Погрешность</th><th>Статус</th><th>Действия</th>
              </tr>
            </thead>
            <tbody>
              {rows}
            </tbody>
          </table>
        </div>
      </div>
    </div>

    <div id="modal-container"></div>
  </main>
</div>
</body>
</html>"""
