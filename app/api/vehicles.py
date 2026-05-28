from fastapi import APIRouter, Request, Depends, HTTPException, Form
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
from app.models.client_account import ClientAccount
from app.models.site import Site
from app.services.pilot_service import PilotService
from app.dependencies import get_current_user, apply_vehicle_filter

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


def group_by_folder(vehicles: list) -> list:
    groups = {}
    for v in vehicles:
        folder = v.get("folder") or "Без папки"
        groups.setdefault(folder, []).append(v)
    sorted_folders = sorted(groups.keys(), key=lambda x: (x == "Без папки", x))
    return [(f, groups[f]) for f in sorted_folders]


def build_vehicle_dict(v: Vehicle, company_name: str = "", site_name: str = "") -> dict:
    return {
        "id": v.id,
        "plate_number": v.plate_number,
        "imei": v.imei,
        "folder": v.folder,
        "sensor_count": v.sensor_count,
        "company_name": company_name,
        "site_name": site_name,
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
            # Flatten site level — collect all vehicles across all placeholder sites
            flat_vehicles = []
            for sname in site_names:
                for folder_vehicles in tree[cname][sname].values():
                    flat_vehicles.extend(folder_vehicles)
            ctotal = len(flat_vehicles)
            sites.append(("__flat__", 0, [flat_vehicles]))
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


def render_nested_partial(nested_groups: list, can_act: bool) -> str:
    if not nested_groups:
        return '<div class="card"><div class="empty-state"><h3>Нет транспортных средств</h3><p>Нажмите «Синхронизировать», чтобы загрузить список ТС из Pilot.</p></div></div>'

    cidx = 0
    sidx = 0
    fidx = 0
    html = ""
    for cname, ctotal, sites in nested_groups:
        cidx += 1
        cid = f"c-{cidx}"
        html += f'<div class="card level-company" style="margin-top: 16px;"><div class="card-header collapsible-header" onclick="toggleGroup(\'{cid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-company">{cname}</span><span class="level-count">({ctotal})</span></div><div class="collapsible-body" id="{cid}">'
        for sname, stotal, folders in sites:
            if sname == "__flat__":
                # Flat mode — render vehicles directly under company
                all_vehicles = folders[0]
                html += '<div class="table-container" style="margin-top:12px"><table><thead><tr>'
                if can_act:
                    html += '<th style="width:32px;"><input type="checkbox" onchange="var e=this;document.querySelectorAll(\'#bulk-vehicle-form input[name=vehicle_ids]\').forEach(function(c){c.checked=e.checked})"></th>'
                html += '<th>#</th><th>Госномер</th><th>IMEI</th><th>Датчик</th><th>Заправки</th><th>Компания</th>'
                if can_act:
                    html += '<th style="width:100px;">Действия</th>'
                html += '</tr></thead><tbody>'
                for idx, v in enumerate(all_vehicles, 1):
                    badge = "status-normal" if v.get("sensor_count", 0) > 0 else "status-false-reading"
                    html += f'<tr id="v-{v["id"]}">'
                    if can_act:
                        html += f'<td><input type="checkbox" name="vehicle_ids" value="{v["id"]}"></td>'
                    html += f'<td style="color:var(--text-dim)">{idx}</td><td><strong>{v.get("plate_number") or "—"}</strong></td><td style="font-family:monospace;font-size:12px">{v.get("imei") or "—"}</td><td><span class="status-badge {badge}">{v.get("sensor_count", 0)} датч.</span></td><td><a href="/refuels?vehicle_id={v["id"]}" class="btn btn-sm btn-secondary">Заправки</a></td><td>{v.get("company_name") or "—"}</td>'
                    if can_act:
                        html += f'<td><button class="btn btn-sm btn-danger" hx-post="/api/vehicles/{v["id"]}/toggle-sensor" hx-target="#v-{v["id"]}" hx-swap="outerHTML" hx-confirm="Пометить «{v.get("plate_number") or v["id"]}» как ТС без датчика?">Нет датчика</button></td>'
                    html += '</tr>'
                html += '</tbody></table></div>'
            else:
                sidx += 1
                sid = f"s-{cidx}-{sidx}"
                html += f'<div class="card level-site" style="margin: 8px 0;"><div class="card-header collapsible-header" onclick="toggleGroup(\'{sid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-site">{sname}</span><span class="level-count">({stotal})</span></div><div class="collapsible-body" id="{sid}">'
                for fname, vehicles in folders:
                    if fname == "__flat__":
                        # Flat folder — render table directly
                        html += '<div class="table-container"><table><thead><tr>'
                        if can_act:
                            html += '<th style="width:32px;"><input type="checkbox" onchange="var e=this;document.querySelectorAll(\'#bulk-vehicle-form input[name=vehicle_ids]\').forEach(function(c){c.checked=e.checked})"></th>'
                        html += '<th>#</th><th>Госномер</th><th>IMEI</th><th>Датчик</th><th>Заправки</th><th>Компания</th>'
                        if can_act:
                            html += '<th style="width:100px;">Действия</th>'
                        html += '</tr></thead><tbody>'
                        for idx, v in enumerate(vehicles, 1):
                            badge = "status-normal" if v.get("sensor_count", 0) > 0 else "status-false-reading"
                            html += f'<tr id="v-{v["id"]}">'
                            if can_act:
                                html += f'<td><input type="checkbox" name="vehicle_ids" value="{v["id"]}"></td>'
                            html += f'<td style="color:var(--text-dim)">{idx}</td><td><strong>{v.get("plate_number") or "—"}</strong></td><td style="font-family:monospace;font-size:12px">{v.get("imei") or "—"}</td><td><span class="status-badge {badge}">{v.get("sensor_count", 0)} датч.</span></td><td><a href="/refuels?vehicle_id={v["id"]}" class="btn btn-sm btn-secondary">Заправки</a></td><td>{v.get("company_name") or "—"}</td>'
                            if can_act:
                                html += f'<td><button class="btn btn-sm btn-danger" hx-post="/api/vehicles/{v["id"]}/toggle-sensor" hx-target="#v-{v["id"]}" hx-swap="outerHTML" hx-confirm="Пометить «{v.get("plate_number") or v["id"]}» как ТС без датчика?">Нет датчика</button></td>'
                            html += '</tr>'
                        html += '</tbody></table></div>'
                    else:
                        fidx += 1
                        fid = f"f-{cidx}-{sidx}-{fidx}"
                        html += f'<div class="level-folder" style="margin:4px 0;border:1px solid var(--border);border-radius:6px"><div class="collapsible-header" onclick="toggleGroup(\'{fid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-folder">{fname}</span><span class="level-count">({len(vehicles)})</span></div><div class="collapsible-body" id="{fid}"><div class="table-container"><table><thead><tr>'
                    if can_act:
                        html += '<th style="width:32px;"><input type="checkbox" onchange="var e=this;document.querySelectorAll(\'#bulk-vehicle-form input[name=vehicle_ids]\').forEach(function(c){c.checked=e.checked})"></th>'
                    html += '<th>#</th><th>Госномер</th><th>IMEI</th><th>Датчик</th><th>Заправки</th><th>Компания</th>'
                    if can_act:
                        html += '<th style="width:100px;">Действия</th>'
                    html += '</tr></thead><tbody>'
                    for idx, v in enumerate(vehicles, 1):
                        badge = "status-normal" if v.get("sensor_count", 0) > 0 else "status-false-reading"
                        html += f'<tr id="v-{v["id"]}">'
                        if can_act:
                            html += f'<td><input type="checkbox" name="vehicle_ids" value="{v["id"]}"></td>'
                        html += f'<td style="color:var(--text-dim)">{idx}</td><td><strong>{v.get("plate_number") or "—"}</strong></td><td style="font-family:monospace;font-size:12px">{v.get("imei") or "—"}</td><td><span class="status-badge {badge}">{v.get("sensor_count", 0)} датч.</span></td><td><a href="/refuels?vehicle_id={v["id"]}" class="btn btn-sm btn-secondary">Заправки</a></td><td>{v.get("company_name") or "—"}</td>'
                        if can_act:
                            html += f'<td><button class="btn btn-sm btn-danger" hx-post="/api/vehicles/{v["id"]}/toggle-sensor" hx-target="#v-{v["id"]}" hx-swap="outerHTML" hx-confirm="Пометить «{v.get("plate_number") or v["id"]}» как ТС без датчика?">Нет датчика</button></td>'
                        html += '</tr>'
                    html += '</tbody></table></div></div></div>'
                html += '</div></div>'
        html += '</div></div>'
    return html


def render_table_partial(vehicles: list, is_superadmin: bool = False, is_company_admin: bool = False) -> str:
    can_act = is_superadmin or is_company_admin
    if not vehicles:
        return '<div class="card"><div class="empty-state"><h3>Нет транспортных средств</h3><p>Нажмите «Синхронизировать», чтобы загрузить список ТС из Pilot.</p></div></div>'
    return render_nested_partial(build_nested_groups(vehicles), can_act)


@router.get("/vehicles", response_class=HTMLResponse)
async def vehicles_page(
    request: Request,
    plate: str = "",
    imei: str = "",
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    query = apply_vehicle_filter(query, user, Vehicle)
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

    return templates.TemplateResponse(request, "vehicles.html", {
        "plate": plate,
        "imei": imei,
        "is_superadmin": is_su,
        "is_company_admin": is_ca,
        "nested_groups": build_nested_groups(out) if out else [],
    })


@router.get("/api/vehicles/search", response_class=HTMLResponse)
async def search_vehicles(
    request: Request,
    plate: str = "",
    imei: str = "",
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == True)
    query = apply_vehicle_filter(query, user, Vehicle)
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


@router.post("/api/vehicles/{vehicle_id}/toggle-sensor", response_class=HTMLResponse)
async def toggle_fuel_sensor(
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
    v.has_fuel_sensor = not v.has_fuel_sensor
    await db.commit()
    return HTMLResponse("")


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
        sensors = pv.get("sensors", {})
        sensor_count = len(sensors) if isinstance(sensors, dict) else 0

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

        out.append({"id": vid, "plate_number": plate, "imei": imei, "folder": folder, "sensor_count": sensor_count, "company_name": ""})

    await db.commit()
    is_su = user.role == "superadmin"
    return HTMLResponse(render_table_partial(out, is_su))
