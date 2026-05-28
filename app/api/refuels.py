import asyncio
import json
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
from app.models.client_account import ClientAccount
from app.models.site import Site
from app.models.setting import Setting
from app.models.sync_log import SyncLog
from app.dependencies import get_current_user, apply_refuel_filter, apply_vehicle_filter
from app.services.pilot_service import PilotService
from app.config import get_settings

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


def _render_refuel_hierarchy(nested_groups: list, vmap: dict, user_role: str, page: int = 1, date_from: str = "", date_to: str = "") -> str:
    if not nested_groups:
        return '<div class="card"><div class="empty-state"><p>Нет заправок.</p></div></div>'
    html = ""
    cidx = 0
    for cname, ctotal, sites in nested_groups:
        cidx += 1
        cid = f"rc-{cidx}"
        html += f'<div class="card level-company" style="margin-top:16px"><div class="card-header collapsible-header" onclick="toggleGroup(\'{cid}\')"><span class="arrow">&#9660;</span><div class="level-header-center"><span class="level-title">{cname}</span><span class="level-tag">Компания</span></div><span class="level-count">{ctotal}</span></div><div class="collapsible-body" id="{cid}">'
        sidx = 0
        for sname, stotal, folders in sites:
            sidx += 1
            if sname == "__flat__":
                for fname, ftotal, vehicles_list in folders:
                    for vid, plate, entries in vehicles_list:
                        html += _render_vehicle_group(vid, entries, vmap, page, date_from, date_to, user_role)
            else:
                sid = f"rs-{cidx}-{sidx}"
                html += f'<div class="card level-site" style="margin-top:8px"><div class="card-header collapsible-header" onclick="toggleGroup(\'{sid}\')"><span class="arrow">&#9660;</span><div class="level-header-center"><span class="level-title">{sname}</span><span class="level-tag">Площадка</span></div><span class="level-count">{stotal}</span></div><div class="collapsible-body" id="{sid}">'
                fidx = 0
                for fname, ftotal, vehicles_list in folders:
                    fidx += 1
                    if fname == "__flat__":
                        for vid, plate, entries in vehicles_list:
                            html += _render_vehicle_group(vid, entries, vmap, page, date_from, date_to, user_role)
                    else:
                        fid = f"rf-{cidx}-{sidx}-{fidx}"
                        html += f'<div class="level-folder" style="margin:4px 0;border:1px solid var(--border);border-radius:6px"><div class="collapsible-header" onclick="toggleGroup(\'{fid}\')"><span class="arrow">&#9660;</span><div class="level-header-center"><span class="level-title">{fname}</span><span class="level-tag">Тип ТС</span></div><span class="level-count">{ftotal}</span></div><div class="collapsible-body" id="{fid}">'
                        for vid, plate, entries in vehicles_list:
                            html += _render_vehicle_group(vid, entries, vmap, page, date_from, date_to, user_role)
                        html += '</div></div>'
                html += '</div></div>'
        html += '</div></div>'
    return html


def _render_vehicle_group(vehicle_id: int, entries: list, vmap: dict, page: int = 1, date_from: str = "", date_to: str = "", user_role: str = "user") -> str:
    plate = vmap.get(vehicle_id, {}).get("plate_number", "—")
    add_btn = f'<button class="btn btn-sm btn-secondary" hx-get="/api/refuels/add-form?vehicle_id={vehicle_id}" hx-target="#modal-container" hx-swap="innerHTML">+ Добавить</button>'
    gid = f"vg-{vehicle_id}"
    h = f'<div class="card vehicle-group"><div class="vehicle-group-title collapsible-header" onclick="toggleGroup(\'{gid}\')"><span class="arrow">&#9660;</span><span>{plate} <span class="vehicle-group-count">{len(entries)}</span></span>{add_btn}</div><div class="collapsible-body" id="{gid}"><div class="table-container"><table><thead><tr><th>Дата</th><th>Pilot (л)</th><th>Чек (л)</th><th>Разница</th><th>Погрешность</th><th>Статус</th><th>Действия</th></tr></thead><tbody>'
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
        ds = e.event_date.strftime("%d.%m.%Y %H:%M") if e.event_date else "—"
        title_attr = ""
        if e.source == "manual" and e.created_by:
            created_at_str = e.created_at.strftime("%d.%m.%Y %H:%M") if e.created_at else ""
            title_attr = f' title="Добавил: {e.created_by}, {created_at_str}"'
        actions = f'<button class="btn btn-sm btn-secondary" hx-get="/api/refuels/{e.id}/edit?page={page}&date_from={date_from}&date_to={date_to}" hx-target="#modal-container" hx-swap="innerHTML">Правка</button>'
        h += f"<tr{rc}{title_attr}><td>{ds}</td><td>{pa}</td><td>{aa}</td><td>{df}</td><td>{er}</td><td><span class=\"status-badge {sc}\">{sl}</span></td><td>{actions}</td></tr>"

    total_pilot = sum(e.pilot_amount or 0 for e in entries)
    total_actual = sum(e.actual_amount or 0 for e in entries)
    df_total = total_actual - total_pilot
    err_pct = abs(df_total) / total_pilot * 100 if total_pilot > 0 else None

    n_th = get_settings().normal_threshold
    w_th = get_settings().warning_threshold

    if any(e.is_false for e in entries):
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

    h += f'<tfoot class="vehicle-group-tfoot"><tr><td><strong>Итого</strong></td><td><strong>{total_pilot:.1f}</strong></td><td><strong>{total_actual:.1f}</strong></td><td><strong>{df_total:.1f}</strong></td><td><strong>{f"{err_pct:.1f}%" if err_pct is not None else "—"}</strong></td><td><span class="status-badge {overall_sc}">{overall_sl}</span></td><td></td></tr></tfoot>'
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


def _list_html(grouped: list, vmap: dict, page: int, total: int, qs: str, date_from: str = "", date_to: str = "", user_role: str = "user") -> str:
    if not grouped:
        return '<div class="card"><div class="empty-state"><p>Нет заправок.</p></div></div>'
    start = (page - 1) * PER_PAGE
    h = ""
    for vid, entries in grouped[start:start + PER_PAGE]:
        h += _render_vehicle_group(vid, entries, vmap, page, date_from, date_to, user_role)
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

    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    df_str = date_from_str or month_start
    dt_str = date_to_str or today

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

    grouped = _group_by_vehicle(entries)
    grouped.sort(key=lambda x: vmap.get(x[0], {}).get("plate_number", "") or "")
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    if page > total_pages:
        page = total_pages
    start = (page - 1) * PER_PAGE
    page_groups = grouped[start:start + PER_PAGE]
    page_entries = [e for _, ve in page_groups for e in ve]

    nested = build_refuel_hierarchy(page_entries, vmap)
    rendered = _render_refuel_hierarchy(nested, vmap, user.role, page, df_str, dt_str)

    qparts = {}
    if vehicle_id:
        qparts["vehicle_id"] = vehicle_id
    if status_filter:
        qparts["status"] = status_filter
    if company_filter:
        qparts["company"] = company_filter
    if site_filter:
        qparts["site"] = site_filter
    if folder_filter:
        qparts["folder"] = folder_filter
    if df_str != month_start or dt_str != today:
        qparts["date_from"] = df_str
        qparts["date_to"] = dt_str
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    rendered += _pagination_html(page, total_pages, qs)

    is_hx = request.headers.get("hx-request") == "true"
    if is_hx:
        return HTMLResponse(rendered)

    days1_str = (datetime.now() - timedelta(days=1)).strftime("%Y-%m-%d")
    days7_str = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")
    days14_str = (datetime.now() - timedelta(days=14)).strftime("%Y-%m-%d")
    days30_str = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

    return templates.TemplateResponse(request, "refuels.html", {
        "list_html": rendered,
        "all_vehicles": all_vehicles,
        "all_companies": all_companies,
        "all_sites": all_sites,
        "all_folders": all_folders,
        "selected_company": company_filter or "",
        "selected_site": site_filter or "",
        "selected_folder": folder_filter or "",
        "selected_vehicle_id": int(vehicle_id) if vehicle_id else None,
        "selected_status": status_filter or "",
        "date_from": df_str,
        "date_to": dt_str,
        "today_str": today,
        "month_start_str": month_start,
        "days1_str": days1_str,
        "days7_str": days7_str,
        "days14_str": days14_str,
        "days30_str": days30_str,
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
                PilotRefuel.event_date >= ev_ts - timedelta(hours=1),
                PilotRefuel.event_date <= ev_ts + timedelta(hours=1),
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
    html = _list_html(grouped, vmap, 1, total_pages, qs, qf, qt, user.role)

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
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403)

    token = user.pilot_token or request.session.get("token")
    node_id = user.pilot_node_id or request.session.get("node_id", 0)
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
    q = apply_vehicle_filter(q, user, Vehicle)
    if vehicle_id:
        q = q.where(Vehicle.id == int(vehicle_id))
    vehicles = (await db.execute(q)).scalars().all()
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
        return HTMLResponse(f'<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Ошибка Pilot API: {str(e)[:200]}</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="this.closest(\'.modal-overlay\').remove()">Закрыть</button></div></div></div>')

    raw_events = all_events
    if not raw_events:
        return HTMLResponse('<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Нет новых событий от Pilot за выбранный период.</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="this.closest(\'.modal-overlay\').remove()">Закрыть</button></div></div></div>')

    # Classify each event
    preview_items = []
    new_count = 0
    conflict_count = 0
    false_conflict_count = 0
    identical_count = 0
    item_idx = 0

    for ev in raw_events:
        ev_name_lower = (ev.get("name") or "").strip().lower()
        v = None
        for db_v in vehicles:
            if db_v.plate_number and db_v.plate_number.strip().lower() in ev_name_lower:
                v = db_v
                break
        if not v:
            continue

        ev_ts = _parse_timestamp(ev.get("ts"))
        if not ev_ts:
            continue
        if ev_ts.tzinfo is not None:
            ev_ts = ev_ts.replace(tzinfo=None)

        amount = ev.get("refuel_amount")
        if not amount or amount <= 0:
            continue

        # Look for existing PilotRefuel within ±1h
        existing_pr = (await db.execute(
            select(PilotRefuel).where(
                PilotRefuel.vehicle_id == v.id,
                PilotRefuel.event_date >= ev_ts - timedelta(hours=1),
                PilotRefuel.event_date <= ev_ts + timedelta(hours=1),
            )
        )).scalar_one_or_none()

        item = {
            "plate": v.plate_number or "—",
            "event_date": ev_ts.strftime("%d.%m.%Y %H:%M"),
            "new_amount": amount,
            "is_false": False,
        }

        if existing_pr:
            # Existing record found — check for RefuelEntry
            existing_entry = (await db.execute(
                select(RefuelEntry).where(
                    RefuelEntry.pilot_refuel_id == existing_pr.id,
                    RefuelEntry.is_deleted == False,
                )
            )).scalar_one_or_none()

            old_amount = existing_pr.amount
            item["old_amount"] = old_amount
            item["is_false"] = existing_entry.is_false if existing_entry else False

            if old_amount == amount:
                item["type"] = "identical"
                identical_count += 1
            elif item["is_false"]:
                item["type"] = "false_conflict"
                false_conflict_count += 1
            else:
                item["type"] = "conflict"
                conflict_count += 1

            # Store IDs for apply step
            item["existing_entry_id"] = existing_entry.id if existing_entry else None
            item["existing_pr_id"] = existing_pr.id
            item["existing_pr_date"] = existing_pr.event_date.isoformat() if existing_pr.event_date else None
            item["existing_actual_amount"] = existing_entry.actual_amount if existing_entry else None
            item["existing_receipt"] = existing_entry.receipt_number if existing_entry else None
            item["existing_comment"] = existing_entry.comment if existing_entry else None
        else:
            item["type"] = "new"
            item["old_amount"] = None
            item["existing_entry_id"] = None
            item["existing_pr_id"] = None
            new_count += 1

        # Store minimal Pilot event for apply step
        item["vehicle_id"] = v.id
        item["pilot_agent_id"] = v.pilot_agent_id
        item["ts"] = ev.get("ts")
        item["start_level"] = ev.get("start_level")
        item["end_level"] = ev.get("end_level")
        item["address"] = (ev.get("address") or "")[:500]
        item["lat"] = ev.get("lat")
        item["lon"] = ev.get("lon")

        item["idx"] = item_idx
        item_idx += 1
        preview_items.append(item)

    if not preview_items:
        return HTMLResponse('<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Нет событий, подходящих для импорта.</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="this.closest(\'.modal-overlay\').remove()">Закрыть</button></div></div></div>')

    conflicts = [it for it in preview_items if it["type"] in ("conflict", "false_conflict")]

    return templates.TemplateResponse(request, "sync_preview.html", {
        "preview_items": preview_items,
        "conflicts": conflicts,
        "new_count": new_count,
        "conflict_count": conflict_count,
        "false_conflict_count": false_conflict_count,
        "identical_count": identical_count,
        "total": len(preview_items),
        "date_from": date_from or df.strftime("%Y-%m-%d"),
        "date_to": date_to or dt.strftime("%Y-%m-%d"),
        "vehicle_id": vehicle_id or "",
    })


@router.post("/api/refuels/sync/apply", response_class=HTMLResponse)
async def sync_refuels_apply(
    request: Request,
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    date_from: str = Form(default=None),
    date_to: str = Form(default=None),
    vehicle_id: str = Form(default=""),
    conflict_actions: str = Form(default="{}"),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(status_code=403)

    token = user.pilot_token or request.session.get("token")
    node_id = user.pilot_node_id or request.session.get("node_id", 0)
    if not token:
        raise HTTPException(status_code=401, detail="Not authenticated")

    # Re-fetch Pilot data to get fresh events
    pilot = PilotService()
    now_local = datetime.now()
    try:
        df = datetime.strptime(date_from, "%Y-%m-%d") if date_from else now_local - timedelta(days=7)
        dt = datetime.strptime(date_to, "%Y-%m-%d") if date_to else now_local
    except ValueError:
        df = now_local - timedelta(days=7)
        dt = now_local

    start_str = df.strftime("%d.%m.%Y 00:00")
    stop_str = dt.strftime("%d.%m.%Y 23:59")

    q = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    q = apply_vehicle_filter(q, user, Vehicle)
    if vehicle_id:
        q = q.where(Vehicle.id == int(vehicle_id))
    vehicles = (await db.execute(q)).scalars().all()
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
        return HTMLResponse(f'<div class="modal-overlay" onclick="if(event.target===this)this.remove()"><div class="modal"><div class="modal-body"><div class="empty-state"><p>Ошибка Pilot API: {str(e)[:200]}</p></div></div><div class="modal-footer"><button type="button" class="btn btn-secondary" onclick="this.closest(\'.modal-overlay\').remove()">Закрыть</button></div></div></div>')

    try:
        actions = json.loads(conflict_actions)
    except (json.JSONDecodeError, TypeError):
        actions = {}

    n_th, w_th = await _get_thresholds(db)
    new_count = 0
    replaced_count = 0
    skipped_count = 0
    errors = []
    item_idx = 0

    for ev in all_events:
        ev_name_lower = (ev.get("name") or "").strip().lower()
        v = None
        for db_v in vehicles:
            if db_v.plate_number and db_v.plate_number.strip().lower() in ev_name_lower:
                v = db_v
                break
        if not v:
            continue

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
            # New record
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

        if old_amount == amount:
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
                diff = actual_amt - pilot_amt
                err = abs(diff) / pilot_amt * 100
                existing_entry.difference = diff
                existing_entry.error_percent = err
                existing_entry.comparison_status = "normal" if err <= n_th else ("small_deviation" if err <= w_th else "unacceptable")
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
    })


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
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    vehicle_id: int = Query(...),
):
    vehicle = await db.get(Vehicle, vehicle_id)
    query = select(Vehicle).where(Vehicle.is_active == True)
    query = apply_vehicle_filter(query, user, Vehicle)
    all_v = (await db.execute(query.order_by(Vehicle.plate_number))).scalars().all()
    return templates.TemplateResponse(request, "add_refuel_modal.html", {
        "vehicle": vehicle, "all_vehicles": all_v,
        "default_date": datetime.now().strftime("%Y-%m-%d"),
        "default_time": datetime.now().strftime("%H:%M"),
    })


@router.post("/api/refuels/add", response_class=HTMLResponse)
async def add_refuel(
    request: Request,
    user=Depends(get_current_user),
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
        vehicle_id=vehicle_id, pilot_refuel_id=pilot_refuel_id,
        event_date=dt, pilot_amount=pilot_amount, actual_amount=actual_amount,
        receipt_number=receipt_number or None, source="manual",
        difference=diff, error_percent=err, comparison_status=status,
        created_by=user.username,
    )
    db.add(entry)
    log = SyncLog(sync_type="refuel_add", status="completed", records_affected=1,
                  details=f"manual add for vehicle {vehicle_id}", created_by=user.username)
    db.add(log)
    await db.commit()

    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    q = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    q = apply_refuel_filter(q, user, RefuelEntry, Vehicle)
    try:
        df_p = datetime.strptime(month_start, "%Y-%m-%d")
        q = q.where(RefuelEntry.event_date >= df_p, RefuelEntry.event_date <= datetime.now().replace(hour=23, minute=59, second=59))
    except ValueError:
        pass
    q = q.order_by(desc(RefuelEntry.event_date))
    entries = (await db.execute(q)).scalars().all()

    all_v = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    )).scalars().all()
    vmap = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in all_v}
    grouped = _group_by_vehicle(entries)
    grouped.sort(key=lambda x: vmap.get(x[0], {}).get("plate_number", "") or "")
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    qparts = {"date_from": month_start, "date_to": today}
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    html = _list_html(grouped, vmap, 1, total_pages, qs, month_start, today, user.role)
    html += '<div class="toast toast-success">Заправка добавлена</div>'
    return HTMLResponse(html)


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
        "is_admin": user.role == "superadmin",
        "default_date": entry.event_date.strftime("%Y-%m-%d") if entry.event_date else "",
        "default_time": entry.event_date.strftime("%H:%M") if entry.event_date else "",
    })


@router.post("/api/refuels/{entry_id}/edit", response_class=HTMLResponse)
async def edit_refuel(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
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

    log = SyncLog(sync_type="refuel_edit", status="completed", records_affected=1,
                  details=f"edited entry {entry_id}", created_by=user.username)
    db.add(log)
    await db.commit()

    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    q = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    q = apply_refuel_filter(q, user, RefuelEntry, Vehicle)
    try:
        q = q.where(RefuelEntry.event_date >= datetime.strptime(month_start, "%Y-%m-%d"),
                    RefuelEntry.event_date <= datetime.now().replace(hour=23, minute=59, second=59))
    except ValueError:
        pass
    q = q.order_by(desc(RefuelEntry.event_date))
    entries = (await db.execute(q)).scalars().all()
    all_v = (await db.execute(select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True))).scalars().all()
    vmap = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in all_v}
    grouped = _group_by_vehicle(entries)
    grouped.sort(key=lambda x: vmap.get(x[0], {}).get("plate_number", "") or "")
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    qparts = {"date_from": month_start, "date_to": today}
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    html = _list_html(grouped, vmap, 1, total_pages, qs, month_start, today, user.role)
    html += '<div class="toast toast-success">Заправка обновлена</div>'
    return HTMLResponse(html)


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
    page: int = Form(default=1),
    date_from: str = Form(default=""),
    date_to: str = Form(default=""),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    entry.is_false = True
    entry.false_reason = reason
    entry.false_marked_by = user.username
    entry.false_marked_at = datetime.now()
    log = SyncLog(sync_type="mark_false", status="completed", records_affected=1,
                  details=f"marked false: {reason[:200]}", created_by=user.username)
    db.add(log)
    await db.commit()
    return await _refresh_list(request, db, user, page, date_from, date_to)


@router.post("/api/refuels/{entry_id}/unmark-false", response_class=HTMLResponse)
async def unmark_false(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Form(default=1),
    date_from: str = Form(default=""),
    date_to: str = Form(default=""),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    entry.is_false = False
    entry.false_reason = None
    entry.false_marked_by = None
    entry.false_marked_at = None
    log = SyncLog(sync_type="unmark_false", status="completed", records_affected=1,
                  details="unmarked false", created_by=user.username)
    db.add(log)
    await db.commit()
    return await _refresh_list(request, db, user, page, date_from, date_to)


@router.post("/api/refuels/{entry_id}/delete", response_class=HTMLResponse)
async def delete_refuel(
    request: Request,
    entry_id: int = Path(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    page: int = Form(default=1),
    date_from: str = Form(default=""),
    date_to: str = Form(default=""),
):
    entry = await db.get(RefuelEntry, entry_id)
    if not entry or entry.is_deleted:
        raise HTTPException(404, "Запись не найдена")
    is_admin = user.role == "superadmin"
    if not is_admin and entry.source != "manual":
        raise HTTPException(403, "Только админ может удалять синхронизированные записи")
    await db.delete(entry)
    log = SyncLog(sync_type="delete", status="completed", records_affected=1,
                  details=f"deleted entry {entry_id} by {user.username}", created_by=user.username)
    db.add(log)
    await db.commit()
    return await _refresh_list(request, db, user, page, date_from, date_to)


async def _refresh_list(request: Request, db: AsyncSession, user, page: int, date_from: str, date_to: str) -> HTMLResponse:
    today = datetime.now().strftime("%Y-%m-%d")
    month_start = datetime.now().replace(day=1).strftime("%Y-%m-%d")
    df_str = date_from or month_start
    dt_str = date_to or today

    q = select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    q = apply_refuel_filter(q, user, RefuelEntry, Vehicle)
    try:
        df = datetime.strptime(df_str, "%Y-%m-%d")
        q = q.where(RefuelEntry.event_date >= df)
    except ValueError:
        pass
    try:
        dt = datetime.strptime(dt_str, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
        q = q.where(RefuelEntry.event_date <= dt)
    except ValueError:
        pass
    q = q.order_by(desc(RefuelEntry.event_date))
    entries = (await db.execute(q)).scalars().all()

    all_v = (await db.execute(
        select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    )).scalars().all()
    vmap = {v.id: {"plate_number": v.plate_number, "name": v.name} for v in all_v}
    grouped = _group_by_vehicle(entries)
    grouped.sort(key=lambda x: vmap.get(x[0], {}).get("plate_number", "") or "")
    total_pages = max(1, math.ceil(len(grouped) / PER_PAGE))
    if page > total_pages:
        page = total_pages

    qparts = {}
    if df_str != month_start or dt_str != today:
        qparts["date_from"] = df_str
        qparts["date_to"] = dt_str
    qs = "&".join(f"{k}={v}" for k, v in qparts.items())
    html = _list_html(grouped, vmap, page, total_pages, qs, df_str, dt_str, user.role)
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
