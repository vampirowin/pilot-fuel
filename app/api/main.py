from datetime import date as _date

from fastapi import APIRouter, Request, Depends, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from app.dependencies import get_current_user, apply_vehicle_filter, apply_refuel_filter
from app.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func, desc
from app.models.refuel_entry import RefuelEntry
from app.models.vehicle import Vehicle
from app.models.sync_log import SyncLog
from app.models.user import User
from app.models.client_account import ClientAccount
from app.models.sync_failure import SyncFailure

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/", response_class=HTMLResponse)
async def dashboard(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vq = select(func.count(Vehicle.id)).where(Vehicle.is_active == True)
    vq = apply_vehicle_filter(vq, user, Vehicle)
    total_vehicles = (await db.execute(vq)).scalar() or 0

    rq = select(func.count(RefuelEntry.id)).where(RefuelEntry.is_deleted == False)
    rq = apply_refuel_filter(rq, user, RefuelEntry, Vehicle)
    total_refuels = (await db.execute(rq)).scalar() or 0

    cq = select(func.count(RefuelEntry.id)).where(
        RefuelEntry.is_deleted == False,
        RefuelEntry.is_false == False,
        RefuelEntry.comparison_status.in_(["pilot_missing", "unacceptable"]),
    )
    cq = apply_refuel_filter(cq, user, RefuelEntry, Vehicle)
    critical_count = (await db.execute(cq)).scalar() or 0

    return templates.TemplateResponse(request, "dashboard.html", {
        "total_vehicles": total_vehicles,
        "total_refuels": total_refuels,
        "critical_count": critical_count,
    })


@router.get("/critical", response_class=HTMLResponse)
async def critical_page(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    plate_search: str = Query(default=""),
):
    vq = select(Vehicle).where(Vehicle.is_active == True)
    vq = apply_vehicle_filter(vq, user, Vehicle)
    all_vehicles = (await db.execute(vq.order_by(Vehicle.plate_number))).scalars().all()
    vmap = {v.id: v for v in all_vehicles}

    matched_ids = set(v.id for v in all_vehicles)
    if plate_search:
        matched_ids = set()
        for v in all_vehicles:
            ps = plate_search.lower()
            if (v.plate_number and ps in v.plate_number.lower()) or (v.name and ps in v.name.lower()):
                matched_ids.add(v.id)

    query = select(RefuelEntry).where(
        RefuelEntry.is_deleted == False,
        RefuelEntry.is_false == False,
        RefuelEntry.comparison_status.in_(["pilot_missing", "unacceptable"]),
        RefuelEntry.vehicle_id.in_(matched_ids),
    ).order_by(RefuelEntry.event_date.asc())
    query = apply_refuel_filter(query, user, RefuelEntry, Vehicle)
    entries = (await db.execute(query)).scalars().all()

    grouped = {}
    for e in entries:
        grouped.setdefault(e.vehicle_id, []).append(e)
    sorted_groups = sorted(grouped.items(), key=lambda x: (vmap.get(x[0]).plate_number or "") if vmap.get(x[0]) else "")

    is_hx = request.headers.get("hx-request") == "true"
    is_boosted = request.headers.get("hx-boosted") == "true"

    if is_hx and not is_boosted:
        rendered = _render_critical_groups(sorted_groups, vmap)
        return HTMLResponse(rendered)

    critical_ids = set(e.vehicle_id for e in entries)
    vehicle_list = [v for v in all_vehicles if v.id in critical_ids or not plate_search]
    if plate_search and not entries:
        vehicle_list = [v for v in all_vehicles if v.id in matched_ids]

    return templates.TemplateResponse(request, "critical.html", {
        "sorted_groups": sorted_groups,
        "vmap": vmap,
        "plate_search": plate_search,
        "vehicle_list": vehicle_list,
    })


def _render_critical_groups(sorted_groups: list, vmap: dict) -> str:
    if not sorted_groups:
        return '<div class="card"><div class="empty-state"><svg width="64" height="64" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1"><path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z"/><line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/></svg><h3>Критических событий нет</h3><p>Все заправки имеют данные из Pilot или находятся в пределах нормы.</p></div></div>'

    from app.services.refuel_utils import STATUS_MAP as class_map, STATUS_LABELS as label_map

    h = ""
    for vid, entries in sorted_groups:
        v = vmap.get(vid)
        plate = v.plate_number or v.name or "—" if v else "—"
        gid = f"crit-{vid}"
        h += f'<div class="card vehicle-group" style="margin-top:12px"><div class="vehicle-group-title collapsible-header" onclick="toggleGroup(\'{gid}\')"><span class="arrow">&#9660;</span><span>{plate} <span class="vehicle-group-count">{len(entries)}</span></span></div><div class="collapsible-body" id="{gid}"><div class="table-container"><table><thead><tr><th>Дата</th><th>Pilot (л)</th><th>Чек (л)</th><th>Разница</th><th>Погрешность</th><th>Статус</th><th>Действия</th></tr></thead><tbody>'
        for e in entries:
            pa = f"{e.pilot_amount:.1f}" if e.pilot_amount is not None else "—"
            aa = f"{e.actual_amount:.1f}" if e.actual_amount is not None else "—"
            df = f"{e.difference:.1f}" if e.difference is not None else "—"
            er = f"{e.error_percent:.1f}%" if e.error_percent is not None else "—"
            ds = e.event_date.strftime("%d.%m.%Y %H:%M") if e.event_date else "—"
            date_ymd = e.event_date.strftime("%Y-%m-%d") if e.event_date else ""
            imei_val = v.imei if v and v.imei else ""
            sc = class_map.get(e.comparison_status, "")
            sl = label_map.get(e.comparison_status, e.comparison_status or "—")
            actions = f'<button class="btn btn-sm btn-secondary" hx-get="/api/refuels/{e.id}/edit" hx-target="#modal-container" hx-swap="innerHTML">Правка</button>'
            date_link = f'hx-get="/api/fuel-graph/modal?vehicle_id={e.vehicle_id}&imei={imei_val}&date_from={date_ymd}&date_to={date_ymd}" hx-target="#modal-container" hx-swap="innerHTML"'
            diff_style = ""
            if e.difference is not None:
                if e.difference < 0:
                    diff_style = ' style="color:var(--warning);font-weight:600"'
                elif e.difference > 0:
                    diff_style = ' style="color:var(--danger);font-weight:600"'
            h += f"<tr><td data-label=\"Дата\" style=\"cursor:pointer;text-decoration:underline dotted #888\" {date_link}>{ds}</td><td data-label=\"Pilot\">{pa}</td><td data-label=\"Чек\">{aa}</td><td data-label=\"Разница\"{diff_style}>{df}</td><td data-label=\"Погрешность\">{er}</td><td data-label=\"Статус\"><span class=\"status-badge {sc}\">{sl}</span></td><td>{actions}</td></tr>"
        h += "</tbody></table></div></div></div>"
    return h


@router.get("/api/critical-count")
async def critical_count(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    cq = select(func.count(RefuelEntry.id)).where(
        RefuelEntry.is_deleted == False,
        RefuelEntry.is_false == False,
        RefuelEntry.comparison_status.in_(["pilot_missing", "unacceptable"]),
    )
    cq = apply_refuel_filter(cq, user, RefuelEntry, Vehicle)
    count = (await db.execute(cq)).scalar() or 0
    return HTMLResponse(str(count))


@router.get("/sync-logs", response_class=HTMLResponse)
async def sync_logs_page(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    type: str = Query(default="auto"),
):
    sync_type = "auto_refuels" if type == "auto" else "refuels"
    query = select(SyncLog).where(SyncLog.sync_type == sync_type)
    if user.role != "superadmin":
        query = query.where(SyncLog.created_by == user.username)
    query = query.order_by(desc(SyncLog.started_at)).limit(100)
    logs = (await db.execute(query)).scalars().all()

    # build company name lookup: username → company name
    company_map = {}
    if user.role == "superadmin":
        admin_rows = (await db.execute(
            select(User, ClientAccount.name).join(
                ClientAccount, User.client_account_id == ClientAccount.id, isouter=True
            ).where(
                User.role == "company_admin",
                User.is_active == True,
            )
        )).all()
        company_map = {a.User.username: a.name or a.User.username for a in admin_rows}

    admins = []
    if user.role == "superadmin":
        token_admins = (await db.execute(
            select(User, ClientAccount.name).join(
                ClientAccount, User.client_account_id == ClientAccount.id, isouter=True
            ).where(
                User.role == "company_admin",
                User.is_active == True,
                User.pilot_token.isnot(None),
            ).order_by(ClientAccount.name)
        )).all()
        admins = [{"id": a.User.id, "company": a.name or a.User.username, "username": a.User.username} for a in token_admins]

    return templates.TemplateResponse(request, "sync_logs.html", {
        "logs": logs,
        "is_superadmin": user.role == "superadmin",
        "admins": admins,
        "company_map": company_map,
        "current_type": type,
    })


@router.post("/api/sync-logs/trigger", response_class=HTMLResponse)
async def trigger_sync(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    admin_ids: str = Query(default=""),
):
    if user.role not in ("superadmin", "company_admin"):
        return HTMLResponse('<div class="toast toast-error">Нет прав</div>')

    from app.scheduler import trigger_sync_all
    ids = [int(x) for x in admin_ids.split(",") if x.strip().isdigit()] if admin_ids else []
    results = await trigger_sync_all(admin_user_ids=ids if ids else None)

    lines = []
    for r in results:
        if r.get("status") == "error":
            lines.append(f'<div class="toast toast-error">{r["username"]}: {r.get("error", "?")}</div>')
        elif r.get("status") == "skipped":
            lines.append(f'<div class="toast toast-info">{r["username"]}: пропущено ({r.get("reason", "?")})</div>')
        else:
            lines.append(f'<div class="toast toast-success">{r["username"]}: new={r.get("new", 0)}, updated={r.get("updated", 0)}</div>')

    return templates.TemplateResponse(request, "sync_trigger_modal.html", {
        "results": results,
    })


@router.get("/api/sync-failures/banner", response_class=HTMLResponse)
async def sync_failure_banner(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role == "superadmin":
        results = (await db.execute(
            select(SyncFailure).where(
                SyncFailure.attempt == 3,
                SyncFailure.dismissed == False,
            ).order_by(desc(SyncFailure.updated_at)).limit(20)
        )).scalars().all()
        rows = []
        for sf in results:
            badge = f"Попытка {sf.attempt}/3"
            rows.append(f'<div class="toast toast-error" style="margin:4px 0">Компания #{sf.client_account_id}: {sf.last_error[:100]} <small>({badge})</small></div>')
        if not rows:
            return HTMLResponse("")
        banner = '<div class="card" style="border:2px solid var(--danger);margin-bottom:12px"><div style="display:flex;justify-content:space-between;align-items:center"><h3 style="margin:0;color:var(--danger)">⚠ Ошибки синхронизации</h3><button class="btn btn-sm btn-secondary" hx-post="/api/sync-failures/dismiss" hx-target="closest .card" hx-swap="outerHTML">Скрыть</button></div>' + "".join(rows) + "</div>"
        return HTMLResponse(banner)

    sf = (await db.execute(
        select(SyncFailure).where(
            SyncFailure.client_account_id == user.client_account_id,
            SyncFailure.sync_date == _date.today(),
            SyncFailure.attempt == 3,
            SyncFailure.dismissed == False,
        )
    )).scalar_one_or_none()
    if not sf:
        return HTMLResponse("")
    return HTMLResponse(
        f'<div class="toast toast-error" style="margin:8px 0" id="sync-fail-banner"><strong>⚠ Синхронизация не удалась</strong>: {sf.last_error[:150]} <small>(попытка {sf.attempt}/3)</small> '
        f'<button class="btn btn-sm btn-secondary" style="margin-left:8px" hx-post="/api/sync-failures/dismiss" hx-target="#sync-fail-banner" hx-swap="outerHTML">OK</button></div>'
    )


@router.post("/api/sync-failures/dismiss")
async def dismiss_sync_failure(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role == "superadmin":
        results = (await db.execute(
            select(SyncFailure).where(
                SyncFailure.attempt == 3,
                SyncFailure.dismissed == False,
            )
        )).scalars().all()
        for sf in results:
            sf.dismissed = True
        await db.commit()
        return HTMLResponse("")

    sf = (await db.execute(
        select(SyncFailure).where(
            SyncFailure.client_account_id == user.client_account_id,
            SyncFailure.sync_date == _date.today(),
            SyncFailure.attempt == 3,
            SyncFailure.dismissed == False,
        )
    )).scalar_one_or_none()
    if sf:
        sf.dismissed = True
        await db.commit()
    return HTMLResponse("")
