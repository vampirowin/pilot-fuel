import asyncio
import html
import json
import math
import zoneinfo
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends, HTTPException, Query, Form, Path
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, desc, text as sa_text
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.refuel_entry import RefuelEntry
from app.models.pilot_refuel import PilotRefuel
from app.models.vehicle import Vehicle
from app.models.user import User
from app.models.client_account import ClientAccount
from app.models.site import Site
from app.models.setting import Setting
from app.models.sync_log import SyncLog
from app.dependencies import get_current_user, apply_refuel_filter, apply_vehicle_filter
from app.services.pilot_service import PilotService
from app.config import get_settings
from app.timezone_utils import format_dt, get_user_timezone, utc_to_tz, utcnow

async def _resolve_pilot_credentials(user, db) -> tuple[str | None, int]:
    token = user.pilot_token
    node_id = user.pilot_node_id or 0
    if token:
        return token, node_id
    if user.role == "superadmin":
        admin = (await db.execute(
            select(User).where(User.role == "company_admin", User.pilot_token.isnot(None))
        )).scalar_one_or_none()
        if admin and admin.pilot_token:
            return admin.pilot_token, admin.pilot_node_id or 0
    return None, 0


router = APIRouter()
templates = Jinja2Templates(directory="app/templates")
PER_PAGE = 10


def build_refuel_hierarchy(entries: list, vmap: dict) -> list:
    tree = {}
    for entry in entries:
        vin = vmap.get(entry.vehicle_id, {})
        cname = vin.get("company") or "Без компании"
        sname = vin.get("site") or "Без площадки"
        folder = vin.get("folder") or "Без папки"
        plate = vin.get("plate_number") or "—"
        tree.setdefault(cname, {}).setdefault(sname, {}).setdefault(folder, {}).setdefault(
            entry.vehicle_id, {"plate": plate, "entries": []}
        )["entries"].append(entry)

    result = []
    for cname in sorted(tree.keys(), key=lambda x: (x == "Без компании", x)):
        site_names = list(tree[cname].keys())
        all_placeholder = all(s == "Без площадки" for s in site_names)
        ctotal = 0
        if all_placeholder:
            flat = []
            for sname in site_names:
                for fname, vdata in tree[cname][sname].items():
                    for vid, vd in sorted(vdata.items(), key=lambda x: x[1]["plate"]):
                        flat.append((vid, vd["plate"], vd["entries"]))
            ctotal = sum(len(v[2]) for v in flat)
            result.append((cname, ctotal, [("__flat__", 0, [("__flat__", ctotal, flat)])]))
        else:
            sites = []
            for sname in sorted(site_names, key=lambda x: (x == "Без площадки", x)):
                folder_names = list(tree[cname][sname].keys())
                all_f_placeholder = all(f == "Без папки" for f in folder_names)
                stotal = 0
                if all_f_placeholder:
                    flat = []
                    for fname in folder_names:
                        for vid, vd in sorted(tree[cname][sname][fname].items(), key=lambda x: x[1]["plate"]):
                            flat.append((vid, vd["plate"], vd["entries"]))
                    stotal = sum(len(v[2]) for v in flat)
                    sites.append((sname, stotal, [("__flat__", stotal, flat)]))
                else:
                    folders = []
                    for fname in sorted(folder_names, key=lambda x: (x == "Без папки", x)):
                        vl = [(vid, vd["plate"], vd["entries"]) for vid, vd in sorted(tree[cname][sname][fname].items(), key=lambda x: x[1]["plate"])]
                        ft = sum(len(v[2]) for v in vl)
                        folders.append((fname, ft, vl))
                        stotal += ft
                    sites.append((sname, stotal, folders))
                ctotal += stotal
            result.append((cname, ctotal, sites))
    return result


def _render_refuel_hierarchy(nested_groups: list, vmap: dict, user, page: int = 1, date_from: str = "", date_to: str = "", n_th: float = 3.0, w_th: float = 10.0, user_names: dict | None = None) -> str:
    if not nested_groups:
        return '<div class="card"><div class="empty-state"><p>Нет заправок.</p></div></div>'
    html = ""
    cidx = 0
    for cname, ctotal, sites in nested_groups:
        cidx += 1
        cid = f"rc-{cidx}"
        html += f'<div class="card level-company" style="margin-top:16px"><div class="card-header collapsible-header" onclick="toggleGroup(\'{cid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-company">{cname}</span><span class="level-count">{ctotal}</span></div><div class="collapsible-body" id="{cid}">'
        sidx = 0
        for sname, stotal, folders in sites:
            sidx += 1
            if sname == "__flat__":
                for fname, ftotal, vehicles_list in folders:
                    for vid, plate, entries in vehicles_list:
                        html += _render_vehicle_group(vid, entries, vmap, page, date_from, date_to, user, n_th, w_th, user_names)
            else:
                sid = f"rs-{cidx}-{sidx}"
                html += f'<div class="card level-site" style="margin-top:8px"><div class="card-header collapsible-header" onclick="toggleGroup(\'{sid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-site">{sname}</span><span class="level-count">{stotal}</span></div><div class="collapsible-body" id="{sid}">'
                fidx = 0
                for fname, ftotal, vehicles_list in folders:
                    fidx += 1
                    if fname == "__flat__":
                        for vid, plate, entries in vehicles_list:
                            html += _render_vehicle_group(vid, entries, vmap, page, date_from, date_to, user, n_th, w_th, user_names)
                    else:
                        fid = f"rf-{cidx}-{sidx}-{fidx}"
                        html += f'<div class="level-folder" style="margin:4px 0;border:1px solid var(--border);border-radius:6px"><div class="collapsible-header" onclick="toggleGroup(\'{fid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-folder">{fname}</span><span class="level-count">{ftotal}</span></div><div class="collapsible-body" id="{fid}">'
                        for vid, plate, entries in vehicles_list:
                            html += _render_vehicle_group(vid, entries, vmap, page, date_from, date_to, user, n_th, w_th, user_names)
                        html += '</div></div>'
                html += '</div></div>'
        html += '</div></div>'
    return html


def _render_vehicle_group(vehicle_id: int, entries: list, vmap: dict, page: int = 1, date_from: str = "", date_to: str = "", user = None, n_th: float = 3.0, w_th: float = 10.0, user_names: dict | None = None) -> str:
    plate = vmap.get(vehicle_id, {}).get("plate_number", "—")
    imei_val = vmap.get(vehicle_id, {}).get("imei", "")
    add_btn = f'<button class="btn btn-sm btn-secondary" hx-get="/api/refuels/add-form?vehicle_id={vehicle_id}&page={page}&date_from={date_from}&date_to={date_to}" hx-target="#modal-container" hx-swap="innerHTML">+ Добавить</button>'
    graph_btn = f'<button class="btn btn-sm btn-secondary" hx-get="/api/fuel-graph/modal?vehicle_id={vehicle_id}&imei={imei_val}" hx-target="#modal-container" hx-swap="innerHTML">График</button>'
    gid = f"vg-{vehicle_id}"
    h = f'<div class="card vehicle-group"><div class="vehicle-group-title collapsible-header" onclick="toggleGroup(\'{gid}\')"><span class="arrow">&#9660;</span><span>{plate} <span class="vehicle-group-count">{len(entries)}</span></span>{graph_btn}{add_btn}</div><div class="collapsible-body" id="{gid}"><div class="table-container"><table><thead><tr><th>Дата</th><th>Pilot (л)</th><th>Чек (л)</th><th>Разница</th><th>Погрешность</th><th>Статус</th><th>Прим.</th><th>Действия</th></tr></thead><tbody>'
    for e in entries:
        rc = ' class="row-false"' if e.is_false else ""
        if e.is_false:
            sc = "status-false-reading"
            sl = "Ложная"
        else:
            sc = STATUS_MAP.get(e.comparison_status, "")
            sl = STATUS_LABELS.get(e.comparison_status, e.comparison_status or "—")
        pa = f"{e.pilot_amount:.1f}" if e.pilot_amount is not None else "—"
        aa = f"{e.actual_amount:.1f}" if e.actual_amount is not None else "—"
        df = f"{e.difference:.1f}" if e.difference is not None else "—"
        er = f"{e.error_percent:.1f}%" if e.error_percent is not None else "—"
        ds = format_dt(e.event_date, "%d.%m.%Y %H:%M", user) if e.event_date else "—"
        title_attr = ""
        created_at_str = format_dt(e.created_at, "%d.%m.%Y %H:%M", user) if e.created_at else ""
        if e.source == "manual" and e.created_by:
            display = (user_names or {}).get(e.created_by, e.created_by)
            title_attr = f' title="Добавил: {display}, {created_at_str}"'
        elif e.source == "pilot_sync":
            title_attr = f' title="Синхронизировано из Pilot, {created_at_str}"'
        elif e.created_by:
            display = (user_names or {}).get(e.created_by, e.created_by)
            title_attr = f' title="Добавил: {display}, {created_at_str}"'
        elif e.created_at:
            title_attr = f' title="Создано: {created_at_str}"'
        if e.comment:
            title_attr += f' title="Примечание: {html.escape(e.comment)}"'
        actions = f'<button class="btn btn-sm btn-secondary" hx-get="/api/refuels/{e.id}/edit?page={page}&date_from={date_from}&date_to={date_to}" hx-target="#modal-container" hx-swap="innerHTML">Правка</button>'
        date_ymd = format_dt(e.event_date, "%Y-%m-%d", user) if e.event_date else ""
        note_cell = ""
        if e.exclude_from_stats:
            note_cell = '<span style="color:var(--orange);font-size:12px" title="Не учитывается в статистике">⊘</span>'
        if e.comment:
            note_cell += f'<span style="color:var(--text-dim);font-size:11px;cursor:help;border-bottom:1px dotted var(--text-dim)" title="{html.escape(e.comment)}">прим.</span>'
        h += f"<tr{rc}{title_attr}><td data-label=\"Дата\" style=\"cursor:pointer;text-decoration:underline dotted #888\" hx-get=\"/api/fuel-graph/modal?vehicle_id={vehicle_id}&imei={imei_val}&date_from={date_ymd}&date_to={date_ymd}\" hx-target=\"#modal-container\" hx-swap=\"innerHTML\">{ds}</td><td data-label=\"Pilot\">{pa}</td><td data-label=\"Чек\">{aa}</td><td data-label=\"Разница\">{df}</td><td data-label=\"Погрешность\">{er}</td><td data-label=\"Статус\"><span class=\"status-badge {sc}\">{sl}</span></td><td data-label=\"Прим.\" style=\"font-size:13px\">{note_cell}</td><td>{actions}</td></tr>"

    stats_entries = [e for e in entries if not e.is_false and not e.exclude_from_stats]
    real_entries = [e for e in entries if not e.is_false]

    total_pilot = sum(e.pilot_amount or 0 for e in stats_entries)
    total_actual = sum(e.actual_amount or 0 for e in stats_entries)
    df_total = total_actual - total_pilot
    err_pct = abs(df_total) / total_pilot * 100 if total_pilot > 0 else None

    false_count = sum(1 for e in entries if e.is_false)
    excluded_count = sum(1 for e in entries if e.exclude_from_stats and not e.is_false)
    if not real_entries:
        overall_sc = "status-false-reading"
        overall_sl = "Ложная"
    elif err_pct is None:
        overall_sc = ""
        overall_sl = "—"
    elif err_pct <= n_th:
        overall_sc = "status-normal"
        overall_sl = "Норма"
    elif err_pct <= w_th:
        overall_sc = "status-small-deviation"
        overall_sl = "Расхождение"
    else:
        overall_sc = "status-unacceptable"
        overall_sl = "Недопустимо"

    footer_label = "Итого"
    parts = []
    if false_count:
        parts.append(f"{false_count} ложн.")
    if excluded_count:
        parts.append(f"{excluded_count} искл.")
    if parts:
        footer_label += " (" + ", ".join(parts) + " не учтены)"
    h += f'<tfoot class="vehicle-group-tfoot"><tr><td data-label=""><strong>{footer_label}</strong></td><td data-label="Pilot"><strong>{total_pilot:.1f}</strong></td><td data-label="Чек"><strong>{total_actual:.1f}</strong></td><td data-label="Разница"><strong>{df_total:.1f}</strong></td><td data-label="Погрешность"><strong>{f"{err_pct:.1f}%" if err_pct is not None else "—"}</strong></td><td data-label="Статус"><span class="status-badge {overall_sc}">{overall_sl}</span></td><td></td><td></td></tr></tfoot>'
    h += "</tbody></table></div></div></div>"
    return h


def _pagination_html(page: int, total: int, qs: str) -> str:
    if total <= 1:
        return ""
    prev_d = "disabled" if page <= 1 else ""
    next_d = "disabled" if page >= total else ""
    qs_part = f"&{qs}" if qs else ""
    prev_url = f"/refuels?page={page-1}{qs_part}" if page > 1 else "#"
    next_url = f"/refuels?page={page+1}{qs_part}" if page < total else "#"
    return f'<div class="pagination"><button class="chip {prev_d}" hx-get="{prev_url}" hx-target="#refuels-list" hx-push-url="true" {prev_d}>← Назад</button><span>стр. {page} из {total}</span><button class="chip {next_d}" hx-get="{next_url}" hx-target="#refuels-list" hx-push-url="true" {next_d}>Вперед →</button></div>'


def _list_html(grouped: list, vmap: dict, page: int, total: int, qs: str, date_from: str = "", date_to: str = "", user = None, n_th: float = 3.0, w_th: float = 10.0, user_names: dict | None = None) -> str:
    if not grouped:
        return '<div class="card"><div class="empty-state"><p>Нет заправок.</p></div></div>'
    start = (page - 1) * PER_PAGE
    h = ""
    for vid, entries in grouped[start:start + PER_PAGE]:
        h += _render_vehicle_group(vid, entries, vmap, page, date_from, date_to, user, n_th, w_th, user_names)
    h += _pagination_html(page, total, qs)
    return h


@router.get("/refuels", response_class=HTMLResponse)
async def refuels_page(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1, ge=1),
):
    vehicle_id = request.query_params.get("vehicle_id")
    status_filter = request.query_params.get("status")
    date_from_str = request.query_params.get("date_from")
    date_to_str = request.query_params.get("date_to")
    company_filter = request.query_params.get("company")
    site_filter = request.query_params.get("site")
    folder_filter = request.query_params.get("folder")
    plate_search = request.query_params.get("plate_search", "").strip()
    order = request.query_params.get("order", "asc")

    user_tz = get_user_timezone(user)
    local_now = datetime.now(timezone.utc).astimezone(user_tz)
    today_str = local_now.strftime("%Y-%m-%d")
    month_start_str = local_now.replace(day=1).strftime("%Y-%m-%d")
    df_str = date_from_str or month_start_str
    dt_str = date_to_str or today_str

    # Fetch all vehicles with company/site/folder filters
    all_vehicles_query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    all_vehicles_query = apply_vehicle_filter(all_vehicles_query, user, Vehicle)
    if company_filter:
        all_vehicles_query = all_vehicles_query.where(Vehicle.client_account_id == int(company_filter))
    if site_filter:
        all_vehicles_query = all_vehicles_query.where(Vehicle.site_id == int(site_filter))
    if folder_filter:
        all_vehicles_query = all_vehicles_query.where(Vehicle.folder == folder_filter)
    all_vehicles = (await db.execute(all_vehicles_query.order_by(Vehicle.plate_number))).scalars().all()

    # Collect distinct lists for filter dropdowns
    all_companies = []
    cids = {v.client_account_id for v in all_vehicles if v.client_account_id}
    if cids:
        for c in (await db.execute(select(ClientAccount).where(ClientAccount.id.in_(cids)).order_by(ClientAccount.name))).scalars().all():
            all_companies.append((c.id, c.name))
    all_sites = []
    sids = {v.site_id for v in all_vehicles if v.site_id}
    if sids:
        for s in (await db.execute(select(Site).where(Site.id.in_(sids)).order_by(Site.name))).scalars().all():
            all_sites.append((s.id, s.name))
    all_folders = sorted({v.folder for v in all_vehicles if v.folder})

    # Build vmap
    companies = dict(all_companies)
    slookup = dict(all_sites)
    vmap = {}
    for v in all_vehicles:
        vmap[v.id] = {
            "plate_number": v.plate_number or v.name or "—",
            "name": v.name,
            "imei": v.imei or "",
            "company": companies.get(v.client_account_id) if v.client_account_id else "Без компании",
            "site": slookup.get(v.site_id) if v.site_id else "Без площадки",
            "folder": v.folder,
        }

    # Filter entries by these vehicles
    vehicle_ids = [v.id for v in all_vehicles]
    query = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    query = apply_refuel_filter(query, user, RefuelEntry, Vehicle)
    if vehicle_ids:
        query = query.where(RefuelEntry.vehicle_id.in_(vehicle_ids))
    if plate_search:
        matched = [v.id for v in all_vehicles if plate_search.lower() in (v.plate_number or "").lower() or plate_search.lower() in (v.name or "").lower()]
        if matched:
            query = query.where(RefuelEntry.vehicle_id.in_(matched))
        else:
            query = query.where(False)
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
    order_col = RefuelEntry.event_date
    if order == "desc":
        order_col = desc(order_col)
    query = query.order_by(order_col)
    entries = (await db.execute(query)).scalars().all()

    grouped = _group_by_vehicle(entries)
    grouped.sort(key=lambda x: vmap.get(x[0], {}).get("plate_number", "") or "")
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    if page > total_pages:
        page = total_pages
    start = (page - 1) * PER_PAGE
    page_groups = grouped[start:start + PER_PAGE]
    page_entries = [e for _, ve in page_groups for e in ve]

    n_th, w_th, _, _, _ = await _get_effective_thresholds(db)
    users_db = (await db.execute(select(User))).scalars().all()
    user_names = {u.username: u.full_name or u.username for u in users_db}
    nested = build_refuel_hierarchy(page_entries, vmap)
    rendered = _render_refuel_hierarchy(nested, vmap, user, page, df_str, dt_str, n_th, w_th, user_names)

    qparts = {}
    if vehicle_id:
        qparts["vehicle_id"] = vehicle_id
    if plate_search:
        qparts["plate_search"] = plate_search
    if status_filter:
        qparts["status"] = status_filter
    if company_filter:
        qparts["company"] = company_filter
    if site_filter:
        qparts["site"] = site_filter
    if folder_filter:
        qparts["folder"] = folder_filter
    if df_str != month_start_str or dt_str != today_str:
        qparts["date_from"] = df_str
        qparts["date_to"] = dt_str
    if order != "asc":
        qparts["order"] = order
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    rendered += _pagination_html(page, total_pages, qs)

    is_hx = request.headers.get("hx-request") == "true"
    is_boosted = request.headers.get("hx-boosted") == "true"
    if is_hx and not is_boosted:
        return HTMLResponse(rendered)

    days1_str = (local_now - timedelta(days=1)).strftime("%Y-%m-%d")
    days7_str = (local_now - timedelta(days=7)).strftime("%Y-%m-%d")
    days14_str = (local_now - timedelta(days=14)).strftime("%Y-%m-%d")
    days30_str = (local_now - timedelta(days=30)).strftime("%Y-%m-%d")

    return templates.TemplateResponse(request, "refuels.html", {
        "list_html": rendered,
        "page": page, "total_pages": total_pages,
        "date_from": df_str, "date_to": dt_str,
        "today_str": today_str, "month_start_str": month_start_str,
        "days1_str": days1_str, "days7_str": days7_str,
        "days14_str": days14_str, "days30_str": days30_str,
        "all_vehicles": all_vehicles,
        "all_companies": all_companies, "all_sites": all_sites, "all_folders": all_folders,
        "selected_company": company_filter or "", "selected_site": site_filter or "", "selected_folder": folder_filter or "",
        "selected_vehicle_id": vehicle_id or "",
        "selected_status": status_filter or "",
        "plate_search": plate_search,
        "order": order,
    })


@router.get("/api/refuels/sync-modal", response_class=HTMLResponse)
async def sync_modal(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    query = apply_vehicle_filter(query, user, Vehicle)
    all_vehicles = (await db.execute(query.order_by(Vehicle.plate_number))).scalars().all()
    user_tz = get_user_timezone(user)
    local_now = datetime.now(timezone.utc).astimezone(user_tz)
    today = local_now.strftime("%Y-%m-%d")
    seven_ago = (local_now - timedelta(days=7)).strftime("%Y-%m-%d")
    return templates.TemplateResponse(request, "sync_modal.html", {
        "all_vehicles": all_vehicles,
        "default_from": seven_ago,
        "default_to": today,
    })


@router.post("/api/refuels/sync", response_class=HTMLResponse)
async def sync_refuels(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=7),
    date_from: str = Form(default=None),
    date_to: str = Form(default=None),
    vehicle_id: str | None = Form(default=None),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403)

    token = user.pilot_token or request.session.get("token")
    node_id = user.pilot_node_id or request.session.get("node_id", 0)
    if not token:
        token, node_id = await _resolve_pilot_credentials(user, db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pilot = PilotService()
    now_local = utcnow()
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

    msk_tz = zoneinfo.ZoneInfo("Europe/Moscow")
    start_str = utc_to_tz(df, msk_tz).strftime("%d.%m.%Y 00:00")
    stop_str = utc_to_tz(dt, msk_tz).strftime("%d.%m.%Y 23:59")

    q = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    q = apply_vehicle_filter(q, user, Vehicle)
    if vehicle_id:
        q = q.where(Vehicle.id == int(vehicle_id))
    vehicles = (await db.execute(q)).scalars().all()
    if not vehicles:
        return HTMLResponse('<div class="card"><div class="empty-state"><p>Нет ТС для синхронизации.</p></div></div>')

    veh_ids = [v.pilot_agent_id for v in vehicles if v.pilot_agent_id]
    if not veh_ids:
        return HTMLResponse('<div class="card"><div class="empty-state"><p>Нет ТС с agent_id.</p></div></div>')

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
        return HTMLResponse(f'<div class="card"><div class="empty-state"><p>Ошибка Pilot API: {str(e)[:200]}</p></div></div>')
    raw_events = all_events

    total_new = 0
    errors = []
    thresh_cache = {}

    for ev in raw_events:
        v = _match_vehicle(ev, vehicles)
        if not v:
            errors.append(f"ТС не найден: {ev.get('name', '?')}")
            continue

        if v.id not in thresh_cache:
            thresh_cache[v.id] = await _get_effective_thresholds(db, v.id)
        n_pct, w_pct, n_abs, w_abs, enable_abs = thresh_cache[v.id]

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
                PilotRefuel.event_date >= ev_ts - timedelta(hours=1),
                PilotRefuel.event_date <= ev_ts + timedelta(hours=1),
            )
        )
        existing_pr = existing.scalar_one_or_none()
        if existing_pr:
            # Check for orphan manual entry not linked to any PilotRefuel
            orphan = await db.execute(
                select(RefuelEntry).where(
                    RefuelEntry.vehicle_id == v.id,
                    RefuelEntry.source == "manual",
                    RefuelEntry.pilot_refuel_id.is_(None),
                    RefuelEntry.is_deleted == False,
                    RefuelEntry.event_date >= ev_ts - timedelta(hours=1),
                    RefuelEntry.event_date <= ev_ts + timedelta(hours=1),
                )
            )
            orphan_entry = orphan.scalar_one_or_none()
            if orphan_entry:
                orphan_entry.pilot_refuel_id = existing_pr.id
                orphan_entry.pilot_amount = existing_pr.amount
                orphan_entry.event_date = ev_ts
                diff, err, status = _calc_comparison(existing_pr.amount, orphan_entry.actual_amount, n_pct, w_pct, n_abs, w_abs, enable_abs)
                orphan_entry.difference = diff
                orphan_entry.error_percent = err
                orphan_entry.comparison_status = status
                total_new += 1
            continue

        # Check for manual entry without PilotRefuel link
        existing_manual = await db.execute(
            select(RefuelEntry).where(
                RefuelEntry.vehicle_id == v.id,
                RefuelEntry.source == "manual",
                RefuelEntry.pilot_refuel_id.is_(None),
                RefuelEntry.is_deleted == False,
                RefuelEntry.event_date >= ev_ts - timedelta(hours=1),
                RefuelEntry.event_date <= ev_ts + timedelta(hours=1),
            )
        )
        existing_manual_entry = existing_manual.scalar_one_or_none()

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

        if existing_manual_entry:
            existing_manual_entry.pilot_refuel_id = pr.id
            existing_manual_entry.pilot_amount = amount
            existing_manual_entry.event_date = ev_ts
            diff, err, status = _calc_comparison(amount, existing_manual_entry.actual_amount, n_pct, w_pct, n_abs, w_abs, enable_abs)
            existing_manual_entry.difference = diff
            existing_manual_entry.error_percent = err
            existing_manual_entry.comparison_status = status
        else:
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
        created_by=user.username,
    )
    db.add(log)
    await db.commit()

    query = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    query = apply_refuel_filter(query, user, RefuelEntry, Vehicle)
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
    grouped.sort(key=lambda x: vmap.get(x[0], {}).get("plate_number", "") or "")
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    qf = date_from or df.strftime("%Y-%m-%d")
    qt = date_to or dt.strftime("%Y-%m-%d")
    qparts = {"date_from": qf, "date_to": qt}
    if vehicle_id:
        qparts["vehicle_id"] = vehicle_id
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    n_th, w_th, _, _, _ = await _get_effective_thresholds(db)
    users_db = (await db.execute(select(User))).scalars().all()
    user_names = {u.username: u.full_name or u.username for u in users_db}
    html = _list_html(grouped, vmap, 1, total_pages, qs, qf, qt, user, n_th, w_th, user_names)

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


@router.post("/api/refuels/sync/preview", response_class=HTMLResponse)
async def sync_refuels_preview(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    days: int = Query(default=7),
    date_from: str = Form(default=None),
    date_to: str = Form(default=None),
    vehicle_id: str | None = Form(default=None),
    conflict_actions: str = Form(default="{}"),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403)

    lock_key = 1000000 + (user.client_account_id or 0)
    await db.execute(sa_text(f"SELECT pg_advisory_xact_lock({lock_key})"))

    token = user.pilot_token or request.session.get("token")
    node_id = user.pilot_node_id or request.session.get("node_id", 0)
    if not token:
        token, node_id = await _resolve_pilot_credentials(user, db)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    pilot = PilotService()
    now_local = utcnow()
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

    msk_tz = zoneinfo.ZoneInfo("Europe/Moscow")
    start_str = utc_to_tz(df, msk_tz).strftime("%d.%m.%Y 00:00")
    stop_str = utc_to_tz(dt, msk_tz).strftime("%d.%m.%Y 23:59")

    q = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    q = apply_vehicle_filter(q, user, Vehicle)
    if vehicle_id:
        q = q.where(Vehicle.id == int(vehicle_id))
    vehicles = (await db.execute(q)).scalars().all()
    site_ids = {v.site_id for v in vehicles if v.site_id}
    sites = {}
    if site_ids:
        for s in (await db.execute(select(Site).where(Site.id.in_(site_ids)))).scalars().all():
            sites[s.id] = s.name
    if not vehicles:
        return HTMLResponse('<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Нет ТС для синхронизации.</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="this.closest(\'.modal-overlay\').remove()">Закрыть</button></div></div></div>')

    veh_ids = [v.pilot_agent_id for v in vehicles if v.pilot_agent_id]
    if not veh_ids:
        return HTMLResponse('<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Нет ТС с agent_id.</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="this.closest(\'.modal-overlay\').remove()">Закрыть</button></div></div></div>')

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
        return HTMLResponse(f'<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Ошибка Pilot API: {html.escape(str(e)[:200])}</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="this.closest(\'.modal-overlay\').remove()">Закрыть</button></div></div></div>')

    try:
        actions = json.loads(conflict_actions)
    except (json.JSONDecodeError, TypeError):
        actions = {}

    new_count = 0
    replaced_count = 0
    skipped_count = 0
    errors = []
    item_idx = 0
    new_entries = []
    unmatched = 0
    thresh_cache = {}

    for ev in all_events:
        v = _match_vehicle(ev, vehicles)
        if not v:
            unmatched += 1
            continue

        if v.id not in thresh_cache:
            thresh_cache[v.id] = await _get_effective_thresholds(db, v.id)
        n_pct, w_pct, n_abs, w_abs, enable_abs = thresh_cache[v.id]

        ev_ts = _parse_timestamp(ev.get("ts"))
        if not ev_ts:
            continue
        if ev_ts.tzinfo is not None:
            ev_ts = ev_ts.replace(tzinfo=None)

        amount = ev.get("refuel_amount")
        if not amount or amount <= 0:
            continue

        sidx = str(item_idx)
        item_idx += 1

        existing_pr = (await db.execute(
            select(PilotRefuel).where(
                PilotRefuel.vehicle_id == v.id,
                PilotRefuel.event_date >= ev_ts - timedelta(hours=1),
                PilotRefuel.event_date <= ev_ts + timedelta(hours=1),
            )
        )).scalar_one_or_none()

        if not existing_pr:
            # Check for manual entry without PilotRefuel link
            existing_manual = (await db.execute(
                select(RefuelEntry).where(
                    RefuelEntry.vehicle_id == v.id,
                    RefuelEntry.source == "manual",
                    RefuelEntry.pilot_refuel_id.is_(None),
                    RefuelEntry.is_deleted == False,
                    RefuelEntry.event_date >= ev_ts - timedelta(hours=1),
                    RefuelEntry.event_date <= ev_ts + timedelta(hours=1),
                )
            )).scalar_one_or_none()

            if existing_manual:
                if actions.get(sidx, "skip") == "skip":
                    skipped_count += 1
                    continue
                pr = PilotRefuel(
                    vehicle_id=v.id,
                    event_date=ev_ts,
                    amount=amount,
                    start_level=ev.get("start_level"),
                    end_level=ev.get("end_level"),
                    address=(ev.get("address") or "")[:500],
                    lat=ev.get("lat"),
                    lon=ev.get("lon"),
                    raw_data=ev,
                )
                db.add(pr)
                await db.flush()
                existing_manual.pilot_refuel_id = pr.id
                existing_manual.pilot_amount = amount
                existing_manual.event_date = ev_ts
                diff, err, status = _calc_comparison(amount, existing_manual.actual_amount, n_pct, w_pct, n_abs, w_abs, enable_abs)
                existing_manual.difference = diff
                existing_manual.error_percent = err
                existing_manual.comparison_status = status
                replaced_count += 1
            else:
                pr = PilotRefuel(
                    vehicle_id=v.id,
                    event_date=ev_ts,
                    amount=amount,
                    start_level=ev.get("start_level"),
                    end_level=ev.get("end_level"),
                    address=(ev.get("address") or "")[:500],
                    lat=ev.get("lat"),
                    lon=ev.get("lon"),
                    raw_data=ev,
                )
                db.add(pr)
                await db.flush()

                entry = RefuelEntry(
                    vehicle_id=v.id,
                    pilot_refuel_id=pr.id,
                    event_date=ev_ts,
                    pilot_amount=amount,
                    source="pilot_sync",
                )
                db.add(entry)
                new_entries.append({
                    "plate": v.plate_number or "—",
                    "event_date": ev_ts.strftime("%d.%m.%Y %H:%M"),
                    "amount": amount,
                    "site_name": sites.get(v.site_id) or "—",
                })
                new_count += 1
            continue

        # Existing record found
        existing_entry = (await db.execute(
            select(RefuelEntry).where(
                RefuelEntry.pilot_refuel_id == existing_pr.id,
                RefuelEntry.is_deleted == False,
            )
        )).scalar_one_or_none()

        old_amount = existing_pr.amount
        is_false = existing_entry.is_false if existing_entry else False

        # Check for orphan manual entry not linked to any PilotRefuel
        orphan_manual = (await db.execute(
            select(RefuelEntry).where(
                RefuelEntry.vehicle_id == v.id,
                RefuelEntry.source == "manual",
                RefuelEntry.pilot_refuel_id.is_(None),
                RefuelEntry.is_deleted == False,
                RefuelEntry.event_date >= ev_ts - timedelta(hours=1),
                RefuelEntry.event_date <= ev_ts + timedelta(hours=1),
            )
        )).scalar_one_or_none()

        if orphan_manual:
            if actions.get(sidx, "skip") == "skip":
                skipped_count += 1
                continue
            existing_pr.amount = amount
            existing_pr.event_date = ev_ts
            orphan_manual.pilot_refuel_id = existing_pr.id
            orphan_manual.pilot_amount = amount
            orphan_manual.event_date = ev_ts
            diff, err, status = _calc_comparison(amount, orphan_manual.actual_amount, n_pct, w_pct, n_abs, w_abs, enable_abs)
            orphan_manual.difference = diff
            orphan_manual.error_percent = err
            orphan_manual.comparison_status = status
            replaced_count += 1
            continue

        if abs((old_amount or 0) - amount) < 0.001:
            # Identical — skip
            skipped_count += 1
            continue

        # Check user's choice
        if actions.get(sidx, "skip") == "skip":
            skipped_count += 1
            continue

        # Replace
        existing_pr.amount = amount
        existing_pr.start_level = ev.get("start_level")
        existing_pr.end_level = ev.get("end_level")
        existing_pr.address = (ev.get("address") or "")[:500]
        existing_pr.lat = ev.get("lat")
        existing_pr.lon = ev.get("lon")
        existing_pr.event_date = ev_ts

        if existing_entry:
            # Update Pilot-sourced fields, preserve user fields
            existing_entry.pilot_amount = amount
            existing_entry.event_date = ev_ts

            pilot_amt = amount
            actual_amt = existing_entry.actual_amount
            if pilot_amt and actual_amt and pilot_amt > 0:
                diff, err, status = _calc_comparison(pilot_amt, actual_amt, n_pct, w_pct, n_abs, w_abs, enable_abs)
                existing_entry.difference = diff
                existing_entry.error_percent = err
                existing_entry.comparison_status = status
            elif actual_amt:
                existing_entry.difference = None
                existing_entry.error_percent = None
                existing_entry.comparison_status = "pilot_missing"

        replaced_count += 1

    log = SyncLog(
        sync_type="refuels",
        status="completed" if not errors else "partial",
        records_affected=new_count + replaced_count,
        details="; ".join(errors) if errors else None,
        created_by=user.username,
    )
    db.add(log)
    await db.commit()

    return templates.TemplateResponse(request, "sync_result.html", {
        "new_count": new_count,
        "replaced_count": replaced_count,
        "skipped_count": skipped_count,
        "errors": errors,
        "new_entries": new_entries,
        "unmatched": unmatched,
    })


async def _get_effective_thresholds(db: AsyncSession, vehicle_id: int | None = None) -> tuple[float, float, float, float, bool]:
    n_pct, w_pct = 3.0, 10.0
    n_abs, w_abs = 0.0, 0.0
    enable_abs = False

    rows = (await db.execute(select(Setting).where(Setting.key.in_(["normal_threshold", "warning_threshold"])))).scalars().all()
    for s in rows:
        if s.key == "normal_threshold":
            n_pct = float(s.value)
        elif s.key == "warning_threshold":
            w_pct = float(s.value)

    if vehicle_id is not None:
        v = (await db.execute(select(Vehicle).where(Vehicle.id == vehicle_id))).scalar_one_or_none()
        if v:
            if v.normal_threshold_pct is not None:
                n_pct = v.normal_threshold_pct
            if v.warning_threshold_pct is not None:
                w_pct = v.warning_threshold_pct
            if v.enable_abs_threshold:
                enable_abs = True
                if v.normal_threshold_abs is not None:
                    n_abs = v.normal_threshold_abs
                if v.warning_threshold_abs is not None:
                    w_abs = v.warning_threshold_abs

    return n_pct, w_pct, n_abs, w_abs, enable_abs


def _calc_comparison(pilot_amount: float | None, actual_amount: float | None, n_pct: float, w_pct: float, n_abs: float = 0.0, w_abs: float = 0.0, enable_abs: bool = False) -> tuple:
    if pilot_amount is not None and actual_amount is not None and pilot_amount > 0:
        diff = actual_amount - pilot_amount
        err = abs(diff) / pilot_amount * 100
        abs_diff = abs(diff)

        if enable_abs and abs_diff <= n_abs:
            status = "normal"
        elif enable_abs and abs_diff <= w_abs:
            status = "small_deviation"
        elif err <= n_pct:
            status = "normal"
        elif err <= w_pct:
            status = "small_deviation"
        else:
            status = "unacceptable"
    else:
        diff = None
        err = None
        status = "pilot_missing" if actual_amount else None
    return diff, err, status


@router.get("/api/refuels/add-form", response_class=HTMLResponse)
async def add_refuel_form(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    vehicle_id: int = Query(...),
    page: int = Query(default=1),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
):
    vehicle = await db.get(Vehicle, vehicle_id)
    query = select(Vehicle).where(Vehicle.is_active == True)
    query = apply_vehicle_filter(query, user, Vehicle)
    all_v = (await db.execute(query.order_by(Vehicle.plate_number))).scalars().all()
    user_tz = get_user_timezone(user)
    local_now = datetime.now(timezone.utc).astimezone(user_tz)
    return templates.TemplateResponse(request, "add_refuel_modal.html", {
        "vehicle": vehicle, "all_vehicles": all_v,
        "default_date": local_now.strftime("%Y-%m-%d"),
        "default_time": local_now.strftime("%H:%M"),
        "page": page, "date_from": date_from, "date_to": date_to,
    })


@router.post("/api/refuels/add", response_class=HTMLResponse)
async def add_refuel(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    vehicle_id: int = Form(...),
    event_date: str = Form(...),
    event_time: str = Form(...),
    actual_amount: str = Form(default=""),
    receipt_number: str = Form(default=""),
):
    actual_amount_val = float(actual_amount) if actual_amount.strip() else None
    try:
        local_dt = datetime.strptime(f"{event_date} {event_time}", "%Y-%m-%d %H:%M")
    except ValueError:
        local_dt = None

    if user.role not in ("superadmin",):
        v = await db.get(Vehicle, vehicle_id)
        if not v or (user.client_account_id and v.client_account_id != user.client_account_id):
            raise HTTPException(404, "ТС не найдено")
        if user.site_id and v.site_id != user.site_id:
            raise HTTPException(404, "ТС не найдено")

    if local_dt is not None:
        user_tz = get_user_timezone(user)
        local_dt = local_dt.replace(tzinfo=user_tz)
        dt = local_dt.astimezone(timezone.utc).replace(tzinfo=None)
    else:
        dt = utcnow()

    n_pct, w_pct, n_abs, w_abs, enable_abs = await _get_effective_thresholds(db, vehicle_id)

    nearby = await db.execute(
        select(PilotRefuel).where(
            PilotRefuel.vehicle_id == vehicle_id,
            PilotRefuel.event_date >= dt - timedelta(hours=1),
            PilotRefuel.event_date <= dt + timedelta(hours=1),
        ).order_by(PilotRefuel.event_date)
    )
    nearby_refuel = nearby.scalar_one_or_none()

    if nearby_refuel:
        diff, err, status = _calc_comparison(nearby_refuel.amount, actual_amount_val, n_pct, w_pct, n_abs, w_abs, enable_abs)
        pilot_refuel_id = nearby_refuel.id
        pilot_amount = nearby_refuel.amount
    else:
        diff = err = None
        status = "pilot_missing"
        pilot_refuel_id = None
        pilot_amount = None

    entry = RefuelEntry(
        vehicle_id=vehicle_id, pilot_refuel_id=pilot_refuel_id,
        event_date=dt, pilot_amount=pilot_amount, actual_amount=actual_amount_val,
        receipt_number=receipt_number or None, source="manual",
        difference=diff, error_percent=err, comparison_status=status,
        created_by=user.username,
    )
    db.add(entry)
    log = SyncLog(sync_type="refuel_add", status="completed", records_affected=1,
                  details=f"manual add for vehicle {vehicle_id}", created_by=user.username)
    db.add(log)
    await db.commit()

    return HTMLResponse("", headers={"HX-Trigger": "refuelsChanged"})


@router.get("/api/refuels/{entry_id}/edit", response_class=HTMLResponse)
async def edit_refuel_form(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Query(default=1),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    vehicle = await db.get(Vehicle, entry.vehicle_id)
    query = select(Vehicle).where(Vehicle.is_active == True)
    query = apply_vehicle_filter(query, user, Vehicle)
    all_v = (await db.execute(query.order_by(Vehicle.plate_number))).scalars().all()
    return templates.TemplateResponse(request, "edit_refuel_modal.html", {
        "entry": entry, "vehicle": vehicle, "all_vehicles": all_v,
        "page": page, "date_from": date_from, "date_to": date_to,
        "is_admin": user.role in ("superadmin", "company_admin"),
        "default_date": format_dt(entry.event_date, "%Y-%m-%d", user) if entry.event_date else "",
        "default_time": format_dt(entry.event_date, "%H:%M", user) if entry.event_date else "",
    })


@router.post("/api/refuels/{entry_id}/edit", response_class=HTMLResponse)
async def edit_refuel(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    actual_amount: str = Form(default=""),
    receipt_number: str = Form(default=""),
    comment: str = Form(default=""),
    exclude_from_stats: str = Form(default=""),
):
    actual_amount_val = float(actual_amount) if actual_amount.strip() else None
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    if user.role not in ("superadmin",):
        v = await db.get(Vehicle, entry.vehicle_id)
        if not v or (user.client_account_id and v.client_account_id != user.client_account_id):
            raise HTTPException(404, "Запись не найдена")
        if user.site_id and v.site_id != user.site_id:
            raise HTTPException(404, "Запись не найдена")

    n_pct, w_pct, n_abs, w_abs, enable_abs = await _get_effective_thresholds(db, entry.vehicle_id)
    entry.actual_amount = actual_amount_val
    entry.receipt_number = receipt_number or None
    entry.comment = comment.strip() or None
    entry.exclude_from_stats = exclude_from_stats == "1"

    pilot_amount = entry.pilot_amount
    if entry.pilot_refuel_id and pilot_amount is not None:
        diff, err, status = _calc_comparison(pilot_amount, actual_amount_val, n_pct, w_pct, n_abs, w_abs, enable_abs)
    else:
        diff = err = None
        status = "pilot_missing" if actual_amount_val is None else None
    entry.difference = diff
    entry.error_percent = err
    entry.comparison_status = status

    log = SyncLog(sync_type="refuel_edit", status="completed", records_affected=1,
                  details=f"edited entry {entry_id}", created_by=user.username)
    db.add(log)
    await db.commit()

    return HTMLResponse("", headers={"HX-Trigger": "refuelsChanged"})


@router.get("/api/refuels/{entry_id}/mark-false-form", response_class=HTMLResponse)
async def mark_false_form(
    request: Request,
    entry_id: int = Path(...),
    page: int = Query(default=1),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    if user.role not in ("superadmin",):
        v = await db.get(Vehicle, entry.vehicle_id)
        if not v or (user.client_account_id and v.client_account_id != user.client_account_id):
            raise HTTPException(404, "Запись не найдена")
        if user.site_id and v.site_id != user.site_id:
            raise HTTPException(404, "Запись не найдена")
    return templates.TemplateResponse(request, "mark_false_modal.html", {
        "entry_id": entry_id, "page": page, "date_from": date_from, "date_to": date_to,
    })


@router.post("/api/refuels/{entry_id}/mark-false", response_class=HTMLResponse)
async def mark_false(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    reason: str = Form(...),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    if user.role not in ("superadmin",):
        v = await db.get(Vehicle, entry.vehicle_id)
        if not v or (user.client_account_id and v.client_account_id != user.client_account_id):
            raise HTTPException(404, "Запись не найдена")
        if user.site_id and v.site_id != user.site_id:
            raise HTTPException(404, "Запись не найдена")
    entry.is_false = True
    entry.false_reason = reason
    entry.false_marked_by = user.username
    entry.false_marked_at = utcnow()
    log = SyncLog(sync_type="mark_false", status="completed", records_affected=1,
                  details=f"marked false: {reason[:200]}", created_by=user.username)
    db.add(log)
    await db.commit()
    return HTMLResponse("", headers={"HX-Trigger": "refuelsChanged"})


@router.post("/api/refuels/{entry_id}/unmark-false", response_class=HTMLResponse)
async def unmark_false(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    if user.role not in ("superadmin",):
        v = await db.get(Vehicle, entry.vehicle_id)
        if not v or (user.client_account_id and v.client_account_id != user.client_account_id):
            raise HTTPException(404, "Запись не найдена")
        if user.site_id and v.site_id != user.site_id:
            raise HTTPException(404, "Запись не найдена")
    entry.is_false = False
    entry.false_reason = None
    entry.false_marked_by = None
    entry.false_marked_at = None
    log = SyncLog(sync_type="unmark_false", status="completed", records_affected=1,
                  details="unmarked false", created_by=user.username)
    db.add(log)
    await db.commit()
    return HTMLResponse("", headers={"HX-Trigger": "refuelsChanged"})


@router.post("/api/refuels/{entry_id}/delete", response_class=HTMLResponse)
async def delete_refuel(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    if user.role not in ("superadmin",):
        v = await db.get(Vehicle, entry.vehicle_id)
        if not v or (user.client_account_id and v.client_account_id != user.client_account_id):
            raise HTTPException(404, "Запись не найдена")
        if user.site_id and v.site_id != user.site_id:
            raise HTTPException(404, "Запись не найдена")
    is_admin = user.role in ("superadmin", "company_admin")
    if not is_admin and entry.source != "manual":
        raise HTTPException(403, "Только админ может удалять синхронизированные записи")
    await db.delete(entry)
    log = SyncLog(sync_type="delete", status="completed", records_affected=1,
                  details=f"deleted entry {entry_id} by {user.username}", created_by=user.username)
    db.add(log)
    await db.commit()
    return HTMLResponse("", headers={"HX-Trigger": "refuelsChanged"})


@router.get("/api/refuels/import-checks-form", response_class=HTMLResponse)
async def import_checks_form(request: Request):
    return templates.TemplateResponse(request, "import_checks_modal.html", {})


def _parse_check_date(dt_str: str) -> datetime | None:
    if not dt_str:
        return None
    dt_str = dt_str.strip().strip('"').strip("'")
    formats = [
        "%d.%m.%Y %H:%M",
        "%H:%M %d.%m.%Y",
        "%d.%m.%Y",
    ]
    for fmt in formats:
        try:
            return datetime.strptime(dt_str, fmt)
        except ValueError:
            continue
    return None


def _parse_check_line(line: str) -> tuple | None:
    line = line.strip()
    if not line:
        return None
    parts = line.split("\t")
    if len(parts) < 2:
        return None
    plate = parts[0].strip()
    if not plate or any(kw in plate.lower() for kw in ("машина", "дата", "объём", "объем")):
        return None
    dt_raw = parts[1].strip()
    amount_raw = parts[2].strip() if len(parts) > 2 else ""
    if not amount_raw:
        return None
    dt = _parse_check_date(dt_raw)
    if not dt:
        return None
    try:
        amount = float(amount_raw.replace(",", "."))
    except (ValueError, TypeError):
        return None
    return plate, dt, amount


@router.post("/api/refuels/import-checks-preview", response_class=HTMLResponse)
async def import_checks_preview(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    data: str = Form(...),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403)

    lock_key = 2000000 + (user.client_account_id or 0)
    await db.execute(sa_text(f"SELECT pg_advisory_xact_lock({lock_key})"))

    gmt5 = zoneinfo.ZoneInfo("Asia/Yekaterinburg")
    lines = data.split("\n")

    preview_items = []
    new_count = 0
    updated_count = 0
    conflict_count = 0
    identical_count = 0
    skipped_count = 0
    item_idx = 0

    for line in lines:
        parsed = _parse_check_line(line)
        if not parsed:
            continue
        plate, local_dt, amount = parsed

        # Find vehicle
        q = select(Vehicle).where(Vehicle.is_active == True)
        q = apply_vehicle_filter(q, user, Vehicle)
        vehicles = (await db.execute(q)).scalars().all()
        v = None
        for db_v in vehicles:
            if db_v.plate_number:
                db_tokens = {t.strip() for t in db_v.plate_number.lower().replace("-", " ").replace("(", " ").replace(")", " ").split() if t.strip()}
                if plate.lower() in db_tokens:
                    v = db_v
                    break
        if not v:
            skipped_count += 1
            continue

        # Convert local GMT+5 to UTC
        local_aware = local_dt.replace(tzinfo=gmt5)
        utc_dt = local_aware.astimezone(timezone.utc).replace(tzinfo=None)

        # Look for existing RefuelEntry within ±1h
        existing = await db.execute(
            select(RefuelEntry).where(
                RefuelEntry.vehicle_id == v.id,
                RefuelEntry.is_deleted == False,
                RefuelEntry.event_date >= utc_dt - timedelta(hours=1),
                RefuelEntry.event_date <= utc_dt + timedelta(hours=1),
            ).order_by(RefuelEntry.event_date)
        )
        existing_entries = existing.scalars().all()

        # Find best match: prefer exact time match, then any
        match = None
        if existing_entries:
            best = None
            for e in existing_entries:
                diff = abs((e.event_date - utc_dt).total_seconds())
                if best is None or diff < best[1]:
                    best = (e, diff)
            match = best[0] if best else None

        item = {
            "plate": v.plate_number or "—",
            "event_date": local_dt.strftime("%d.%m.%Y %H:%M"),
            "new_amount": amount,
            "vehicle_id": v.id,
            "utc_dt": utc_dt.isoformat(),
        }

        if match:
            item["entry_id"] = match.id
            old = match.actual_amount
            item["old_amount"] = old
            if old is None:
                item["type"] = "update"
                updated_count += 1
            elif abs(old - amount) < 0.01:
                item["type"] = "identical"
                identical_count += 1
            else:
                item["type"] = "conflict"
                conflict_count += 1
        else:
            item["type"] = "new"
            item["entry_id"] = None
            item["old_amount"] = None
            new_count += 1

        item["idx"] = item_idx
        item_idx += 1
        preview_items.append(item)

    conflicts = [it for it in preview_items if it["type"] == "conflict"]

    import json
    parsed_json = json.dumps([{
        "idx": it["idx"],
        "type": it["type"],
        "plate": it["plate"],
        "event_date": it["event_date"],
        "new_amount": it["new_amount"],
        "entry_id": it.get("entry_id"),
        "vehicle_id": it["vehicle_id"],
        "utc_dt": it["utc_dt"],
    } for it in preview_items], ensure_ascii=False)

    summary = {
        "new": new_count,
        "updated": updated_count,
        "conflict": conflict_count,
        "identical": identical_count,
        "skipped": skipped_count,
    }

    return templates.TemplateResponse(request, "import_checks_preview.html", {
        "preview_items": preview_items,
        "conflicts": conflicts,
        "summary": summary,
        "parsed_json": parsed_json,
    })


@router.post("/api/refuels/import-checks-apply", response_class=HTMLResponse)
async def import_checks_apply(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    parsed_json: str = Form(...),
    conflict_actions: str = Form(default="{}"),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403)

    import json
    try:
        items = json.loads(parsed_json)
        actions = json.loads(conflict_actions)
    except (json.JSONDecodeError, TypeError):
        return HTMLResponse('<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Ошибка данных</p></div></div></div></div>')

    new_count = 0
    updated_count = 0
    skipped_count = 0
    errors = []
    thresh_cache = {}

    for item in items:
        itype = item["type"]
        sidx = str(item["idx"])
        amount = item["new_amount"]
        vehicle_id = item["vehicle_id"]
        utc_dt = datetime.fromisoformat(item["utc_dt"])

        if vehicle_id not in thresh_cache:
            thresh_cache[vehicle_id] = await _get_effective_thresholds(db, vehicle_id)
        n_pct, w_pct, n_abs, w_abs, enable_abs = thresh_cache[vehicle_id]

        if itype == "conflict":
            if actions.get(sidx, "skip") == "skip":
                skipped_count += 1
                continue
            itype = "update"  # treat as update

        if itype == "identical":
            skipped_count += 1
            continue

        if itype == "new":
            # Search for nearby PilotRefuel
            nearby = await db.execute(
                select(PilotRefuel).where(
                    PilotRefuel.vehicle_id == vehicle_id,
                    PilotRefuel.event_date >= utc_dt - timedelta(hours=1),
                    PilotRefuel.event_date <= utc_dt + timedelta(hours=1),
                ).order_by(PilotRefuel.event_date)
            )
            nearby_refuel = nearby.scalar_one_or_none()

            if nearby_refuel:
                diff, err, status = _calc_comparison(nearby_refuel.amount, amount, n_pct, w_pct, n_abs, w_abs, enable_abs)
                pilot_refuel_id = nearby_refuel.id
                pilot_amount = nearby_refuel.amount
            else:
                diff = err = None
                status = "pilot_missing"
                pilot_refuel_id = None
                pilot_amount = None

            entry = RefuelEntry(
                vehicle_id=vehicle_id,
                event_date=utc_dt,
                pilot_amount=pilot_amount,
                actual_amount=amount,
                pilot_refuel_id=pilot_refuel_id,
                source="manual",
                difference=diff,
                error_percent=err,
                comparison_status=status,
                created_by=user.username,
            )
            db.add(entry)
            new_count += 1

        elif itype == "update":
            entry_id = item.get("entry_id")
            if not entry_id:
                skipped_count += 1
                continue
            entry = await db.get(RefuelEntry, entry_id)
            if not entry or entry.is_deleted:
                skipped_count += 1
                continue
            # Get thresholds for this entry's vehicle
            if entry.vehicle_id not in thresh_cache:
                thresh_cache[entry.vehicle_id] = await _get_effective_thresholds(db, entry.vehicle_id)
            n_pct, w_pct, n_abs, w_abs, enable_abs = thresh_cache[entry.vehicle_id]
            entry.actual_amount = amount
            # Recalc comparison
            if entry.pilot_refuel_id and entry.pilot_amount is not None:
                diff, err, status = _calc_comparison(entry.pilot_amount, amount, n_pct, w_pct, n_abs, w_abs, enable_abs)
                entry.difference = diff
                entry.error_percent = err
                entry.comparison_status = status
            else:
                entry.difference = None
                entry.error_percent = None
                entry.comparison_status = "pilot_missing"
            updated_count += 1

    log = SyncLog(
        sync_type="import_checks",
        status="completed" if not errors else "partial",
        records_affected=new_count + updated_count,
        details="; ".join(errors) if errors else None,
        created_by=user.username,
    )
    db.add(log)
    await db.commit()

    parts = []
    if new_count:
        parts.append(f"Создано: {new_count}")
    if updated_count:
        parts.append(f"Обновлено: {updated_count}")
    if skipped_count:
        parts.append(f"Пропущено: {skipped_count}")
    msg = ". ".join(parts) if parts else "Нет изменений"

    hx_redirect = request.headers.get("hx-current-url", "/refuels")
    return HTMLResponse(f'<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>{msg}</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="location.reload()">OK</button></div></div></div>', headers={"HX-Redirect": hx_redirect})


def _match_vehicle(event: dict, vehicles: list) -> Vehicle | None:
    ev_name_lower = (event.get("name") or "").strip().lower()
    ev_tokens = {t.strip() for t in ev_name_lower.replace("-", " ").replace("(", " ").replace(")", " ").replace("_", " ").split() if t.strip()}
    for db_v in vehicles:
        plate = (db_v.plate_number or "").strip().lower()
        if not plate:
            continue
        if plate in ev_tokens:
            return db_v
        plate_ns = plate.replace(" ", "")
        if plate_ns in ev_tokens:
            return db_v
        if len(plate) > 2 and plate in ev_name_lower:
            return db_v
        if len(plate_ns) > 2 and plate_ns in ev_name_lower:
            return db_v
    return None


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

@router.get("/api/refuels/{entry_id}/detail", response_class=HTMLResponse)
async def refuel_detail_modal(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    vehicle = await db.get(Vehicle, entry.vehicle_id)
    if user.role not in ("superadmin",):
        if not user.client_account_id or vehicle.client_account_id != user.client_account_id:
            raise HTTPException(404, "Запись не найдена")
        if user.site_id and vehicle.site_id != user.site_id:
            raise HTTPException(404, "Запись не найдена")
    return templates.TemplateResponse(request, "refuel_detail_modal.html", {
        "entry": entry,
        "vehicle": vehicle,
        "plate": vehicle.plate_number or vehicle.name or "—",
        "is_admin": user.role in ("superadmin", "company_admin"),
        "STATUS_MAP": STATUS_MAP,
        "STATUS_LABELS": STATUS_LABELS,
    })

@router.get("/api/vehicles/{vehicle_id}/detail", response_class=HTMLResponse)
async def vehicle_detail_modal(
    request: Request,
    vehicle_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "ТС не найдено")
    if user.role not in ("superadmin",):
        if not user.client_account_id or vehicle.client_account_id != user.client_account_id:
            raise HTTPException(404, "ТС не найдено")
        if user.site_id and vehicle.site_id != user.site_id:
            raise HTTPException(404, "ТС не найдено")
    total_refuels = (await db.execute(
        select(RefuelEntry).where(RefuelEntry.vehicle_id == vehicle_id, RefuelEntry.is_deleted == False)
    )).scalars().all()
    return templates.TemplateResponse(request, "vehicle_detail_modal.html", {
        "vehicle": vehicle,
        "total_refuels": len(total_refuels),
    })
