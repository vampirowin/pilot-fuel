from fastapi import APIRouter, Request, Depends, HTTPException, Path, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
from app.models.user import User
from app.models.client_account import ClientAccount
from app.models.site import Site
from app.models.setting import Setting
from app.models.refuel_entry import RefuelEntry
from app.dependencies import get_current_username, get_current_user, require_superadmin
from app.services.pilot_service import PilotService
from app.api.refuels import _get_effective_thresholds, _calc_comparison

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


# ─── Companies ────────────────────────────────────────────────────


@router.get("/admin/companies", response_class=HTMLResponse)
async def companies_page(
    request: Request,
    _=Depends(get_current_user),
    user: User = Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    companies = (await db.execute(select(ClientAccount).order_by(ClientAccount.name))).scalars().all()
    return templates.TemplateResponse(request, "admin/companies.html", {
        "companies": companies,
    })


@router.get("/admin/companies/add", response_class=HTMLResponse)
async def add_company_form(
    request: Request,
    _=Depends(require_superadmin),
):
    return templates.TemplateResponse(request, "admin/company_edit.html", {
        "company": None,
    })


@router.post("/admin/companies/add")
async def add_company(
    request: Request,
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
):
    company = ClientAccount(name=name)
    db.add(company)
    await db.commit()
    return RedirectResponse(url="/admin/companies", status_code=302)


@router.get("/admin/companies/{company_id}/edit", response_class=HTMLResponse)
async def edit_company_form(
    request: Request,
    company_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    company = await db.get(ClientAccount, company_id)
    if not company:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "admin/company_edit.html", {
        "company": company,
    })


@router.post("/admin/companies/{company_id}/edit")
async def edit_company(
    request: Request,
    company_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
):
    company = await db.get(ClientAccount, company_id)
    if not company:
        raise HTTPException(404)
    company.name = name
    await db.commit()
    return RedirectResponse(url="/admin/companies", status_code=302)


@router.post("/admin/companies/{company_id}/delete")
async def delete_company(
    request: Request,
    company_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    company = await db.get(ClientAccount, company_id)
    if not company:
        raise HTTPException(404)
    vehicles_count = (await db.execute(select(Vehicle.id).where(Vehicle.client_account_id == company_id).limit(1))).scalar_one_or_none()
    if vehicles_count:
        return HTMLResponse("Сначала переместите ТС из этой компании", status_code=400)
    users_count = (await db.execute(select(User.id).where(User.client_account_id == company_id).limit(1))).scalar_one_or_none()
    if users_count:
        return HTMLResponse("Сначала переместите пользователей из этой компании", status_code=400)
    await db.delete(company)
    await db.commit()
    return RedirectResponse(url="/admin/companies", status_code=302)


# ─── Sites ────────────────────────────────────────────────────────


@router.get("/admin/companies/{company_id}/sites", response_class=HTMLResponse)
async def sites_page(
    request: Request,
    company_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    company = await db.get(ClientAccount, company_id)
    if not company:
        raise HTTPException(404)
    sites = (await db.execute(select(Site).where(Site.client_account_id == company_id).order_by(Site.name))).scalars().all()
    return templates.TemplateResponse(request, "admin/sites.html", {
        "company": company,
        "sites": sites,
    })


@router.get("/admin/companies/{company_id}/sites/add", response_class=HTMLResponse)
async def add_site_form(
    request: Request,
    company_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    company = await db.get(ClientAccount, company_id)
    if not company:
        raise HTTPException(404)
    return templates.TemplateResponse(request, "admin/site_edit.html", {
        "company": company,
        "site": None,
    })


@router.post("/admin/companies/{company_id}/sites/add")
async def add_site(
    request: Request,
    company_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
):
    site = Site(client_account_id=company_id, name=name)
    db.add(site)
    await db.commit()
    return RedirectResponse(url=f"/admin/companies/{company_id}/sites", status_code=302)


@router.get("/admin/companies/{company_id}/sites/{site_id}/edit", response_class=HTMLResponse)
async def edit_site_form(
    request: Request,
    company_id: int = Path(...),
    site_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    site = await db.get(Site, site_id)
    if not site or site.client_account_id != company_id:
        raise HTTPException(404)
    company = await db.get(ClientAccount, company_id)
    return templates.TemplateResponse(request, "admin/site_edit.html", {
        "company": company,
        "site": site,
    })


@router.post("/admin/companies/{company_id}/sites/{site_id}/edit")
async def edit_site(
    request: Request,
    company_id: int = Path(...),
    site_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
    name: str = Form(...),
):
    site = await db.get(Site, site_id)
    if not site or site.client_account_id != company_id:
        raise HTTPException(404)
    site.name = name
    await db.commit()
    return RedirectResponse(url=f"/admin/companies/{company_id}/sites", status_code=302)


@router.post("/admin/companies/{company_id}/sites/{site_id}/delete")
async def delete_site(
    request: Request,
    company_id: int = Path(...),
    site_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    site = await db.get(Site, site_id)
    if not site or site.client_account_id != company_id:
        raise HTTPException(404)
    stmt = select(Vehicle).where(Vehicle.site_id == site_id)
    vehicles = (await db.execute(stmt)).scalars().all()
    for v in vehicles:
        v.site_id = None
    await db.delete(site)
    await db.commit()
    return RedirectResponse(url=f"/admin/companies/{company_id}/sites", status_code=302)


# ─── Site-Vehicle assignment ──────────────────────────────────────


@router.get("/admin/companies/{company_id}/sites/{site_id}/vehicles", response_class=HTMLResponse)
async def site_vehicles_page(
    request: Request,
    company_id: int = Path(...),
    site_id: int = Path(...),
    plate: str = "",
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    company = await db.get(ClientAccount, company_id)
    site = await db.get(Site, site_id)
    if not company or not site or site.client_account_id != company_id:
        raise HTTPException(404)

    free_q = select(Vehicle).where(
        Vehicle.client_account_id == company_id,
        Vehicle.site_id.is_(None),
        Vehicle.is_active == True,
        Vehicle.has_fuel_sensor == True,
    )
    if plate:
        free_q = free_q.where(Vehicle.plate_number.ilike(f"%{plate}%"))
    free_q = free_q.order_by(Vehicle.plate_number)
    free_vehicles = (await db.execute(free_q)).scalars().all()

    assigned_vehicles = (await db.execute(
        select(Vehicle).where(
            Vehicle.client_account_id == company_id,
            Vehicle.site_id == site_id,
            Vehicle.is_active == True,
        ).order_by(Vehicle.plate_number)
    )).scalars().all()

    return templates.TemplateResponse(request, "admin/site_vehicles.html", {
        "company": company,
        "site": site,
        "plate": plate,
        "free_vehicles": free_vehicles,
        "assigned_vehicles": assigned_vehicles,
    })


@router.get("/api/admin/site-vehicles-search/{company_id}/{site_id}", response_class=HTMLResponse)
async def site_vehicles_search(
    request: Request,
    company_id: int = Path(...),
    site_id: int = Path(...),
    plate: str = "",
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    q = select(Vehicle).where(
        Vehicle.client_account_id == company_id,
        Vehicle.site_id.is_(None),
        Vehicle.is_active == True,
        Vehicle.has_fuel_sensor == True,
    )
    if plate:
        q = q.where(Vehicle.plate_number.ilike(f"%{plate}%"))
    q = q.order_by(Vehicle.plate_number)
    vehicles = (await db.execute(q)).scalars().all()

    if not vehicles:
        return '<p style="color:var(--text-dim);margin:0">Нет свободных ТС.</p>'

    rows = ""
    for v in vehicles:
        rows += f'<tr><td><input type="checkbox" name="vehicle_ids" value="{v.id}"></td><td><strong>{v.plate_number or "—"}</strong></td><td style="font-family:monospace;font-size:12px">{v.imei or "—"}</td></tr>'
    return f'<div class="table-container"><table><thead><tr><th style="width:40px"><input type="checkbox" id="select-all"></th><th>Госномер</th><th>IMEI</th></tr></thead><tbody>{rows}</tbody></table></div>'


@router.post("/admin/companies/{company_id}/sites/{site_id}/vehicles/assign")
async def assign_vehicles_to_site(
    request: Request,
    company_id: int = Path(...),
    site_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
    vehicle_ids: list[int] = Form(default=[]),
):
    site = await db.get(Site, site_id)
    if not site or site.client_account_id != company_id:
        raise HTTPException(404)
    if vehicle_ids:
        vehicles = (await db.execute(
            select(Vehicle).where(
                Vehicle.id.in_(vehicle_ids),
                Vehicle.client_account_id == company_id,
            )
        )).scalars().all()
        for v in vehicles:
            v.site_id = site_id
        await db.commit()
    return RedirectResponse(url=f"/admin/companies/{company_id}/sites/{site_id}/vehicles", status_code=302)


@router.post("/admin/companies/{company_id}/sites/{site_id}/vehicles/unassign")
async def unassign_vehicle_from_site(
    request: Request,
    company_id: int = Path(...),
    site_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
    vehicle_id: int = Form(...),
):
    v = await db.get(Vehicle, vehicle_id)
    if v and v.client_account_id == company_id and v.site_id == site_id:
        v.site_id = None
        await db.commit()
    return RedirectResponse(url=f"/admin/companies/{company_id}/sites/{site_id}/vehicles", status_code=302)


# ─── Users ────────────────────────────────────────────────────────


@router.get("/admin/users", response_class=HTMLResponse)
async def users_page(
    request: Request,
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    users = (await db.execute(
        select(User).order_by(User.created_at.desc())
    )).scalars().all()
    companies = {c.id: c.name for c in (await db.execute(select(ClientAccount))).scalars().all()}
    return templates.TemplateResponse(request, "admin/users.html", {
        "users": users,
        "companies": companies,
    })


@router.get("/admin/users/{user_id}/edit", response_class=HTMLResponse)
async def edit_user_form(
    request: Request,
    user_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(404)
    companies = (await db.execute(select(ClientAccount).order_by(ClientAccount.name))).scalars().all()
    sites = []
    if target.client_account_id:
        sites = (await db.execute(
            select(Site).where(Site.client_account_id == target.client_account_id).order_by(Site.name)
        )).scalars().all()
    return templates.TemplateResponse(request, "admin/user_edit.html", {
        "target": target,
        "companies": companies,
        "sites": sites,
    })


@router.post("/admin/users/{user_id}/edit")
async def edit_user(
    request: Request,
    user_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
    full_name: str = Form(default=""),
    role: str = Form(...),
    client_account_id: int = Form(default=0),
    site_id: int = Form(default=0),
    is_active: str = Form(default=""),
):
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(404)
    target.full_name = full_name.strip() or None
    target.role = role
    target.client_account_id = client_account_id if client_account_id > 0 else None
    target.site_id = site_id if site_id > 0 else None
    target.is_active = is_active == "1"
    await db.commit()
    return RedirectResponse(url="/admin/users", status_code=302)


@router.post("/admin/users/{user_id}/delete")
async def delete_user(
    request: Request,
    user_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(404)
    if target.role == "superadmin":
        return HTMLResponse("Нельзя удалить суперадмина", status_code=400)
    await db.delete(target)
    await db.commit()
    return RedirectResponse(url="/admin/users", status_code=302)


# ─── Vehicles without sensor ──────────────────────────────────────


def group_by_folder(vehicles: list) -> list:
    groups = {}
    for v in vehicles:
        folder = v.get("folder") or "Без папки"
        groups.setdefault(folder, []).append(v)
    sorted_folders = sorted(groups.keys(), key=lambda x: (x == "Без папки", x))
    return [(f, groups[f]) for f in sorted_folders]


def render_no_sensor_partial(vehicles: list) -> str:
    if not vehicles:
        return '<div class="card"><div class="empty-state"><h3>Нет исключённых ТС</h3><p>Все ТС имеют датчики топлива.</p></div></div>'
    groups = group_by_folder(vehicles)
    html = ""
    gidx = 0
    for group_name, group_vehicles in groups:
        gidx += 1
        gid = f"ns-{gidx}"
        select_all_js = "var e=this;document.querySelectorAll('#no-sensor-form input[name=vehicle_ids]').forEach(function(c){c.checked=e.checked})"
        html += f'<div class="card level-folder" style="margin-top: 16px;"><div class="card-header collapsible-header" onclick="toggleGroup(\'{gid}\')"><span class="arrow">&#9660;</span><span class="level-badge level-badge-folder">{group_name}</span><span class="level-count">{len(group_vehicles)}</span></div><div class="collapsible-body" id="{gid}"><div class="table-container"><table><thead><tr><th style="width:32px;"><input type="checkbox" onchange="{select_all_js}"></th><th>#</th><th>Госномер</th><th>IMEI</th><th>Действия</th></tr></thead><tbody>'
        for idx, v in enumerate(group_vehicles, 1):
            html += f'<tr id="v-{v["id"]}"><td><input type="checkbox" name="vehicle_ids" value="{v["id"]}"></td><td style="color: var(--text-dim);">{idx}</td><td><strong>{v.get("plate_number") or "—"}</strong></td><td style="font-family:monospace;font-size:12px">{v.get("imei") or "—"}</td><td><button class="btn btn-sm btn-primary" hx-post="/api/vehicles/{v["id"]}/toggle-sensor" hx-target="#v-{v["id"]}" hx-swap="outerHTML" hx-confirm="Вернуть «{v.get("plate_number") or v["id"]}» в список?">Вернуть</button></td></tr>'
        html += '</tbody></table></div></div></div>'
    return html


@router.get("/admin/vehicles-no-sensor", response_class=HTMLResponse)
async def vehicles_no_sensor(
    request: Request,
    plate: str = "",
    imei: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in ("superadmin", "company_admin"):
        raise HTTPException(302, headers={"Location": "/"})

    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == False)
    if user.role == "company_admin" and user.client_account_id:
        query = query.where(Vehicle.client_account_id == user.client_account_id)
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

    out = [{"id": v.id, "plate_number": v.plate_number, "imei": v.imei, "folder": v.folder} for v in db_vehicles]
    return templates.TemplateResponse(request, "admin/vehicles_no_sensor.html", {
        "plate": plate,
        "imei": imei,
        "total_count": len(out),
        "groups": group_by_folder(out) if out else [],
        "search_url": "/api/admin/vehicles-no-sensor/search",
        "search_target": "#vehicles-list",
    })


@router.get("/api/admin/vehicles-no-sensor/search", response_class=HTMLResponse)
async def search_no_sensor(
    request: Request,
    plate: str = "",
    imei: str = "",
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if user.role not in ("superadmin", "company_admin"):
        return HTMLResponse("Forbidden", status_code=403)

    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == False)
    if user.role == "company_admin" and user.client_account_id:
        query = query.where(Vehicle.client_account_id == user.client_account_id)
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
    out = [{"id": v.id, "plate_number": v.plate_number, "imei": v.imei, "folder": v.folder} for v in db_vehicles]
    return HTMLResponse(render_no_sensor_partial(out))


@router.post("/api/admin/vehicles-no-sensor/bulk-restore", response_class=HTMLResponse)
async def bulk_restore_no_sensor(
    request: Request,
    user: User = Depends(get_current_user),
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
            v.has_fuel_sensor = True
        await db.commit()

    query = select(Vehicle).where(Vehicle.is_active == True, Vehicle.has_fuel_sensor == False)
    if user.role == "company_admin" and user.client_account_id:
        query = query.where(Vehicle.client_account_id == user.client_account_id)
    query = query.order_by(Vehicle.folder, Vehicle.plate_number)
    result = await db.execute(query)
    db_vehicles = result.scalars().all()
    out = [{"id": v.id, "plate_number": v.plate_number, "imei": v.imei, "folder": v.folder} for v in db_vehicles]
    return HTMLResponse(render_no_sensor_partial(out))


# ─── Settings ──────────────────────────────────────────────────────


@router.get("/admin/settings", response_class=HTMLResponse)
async def settings_page(
    request: Request,
    _=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if request.session.get("role") != "superadmin":
        raise HTTPException(302, headers={"Location": "/"})
    rows = (await db.execute(select(Setting))).scalars().all()
    return templates.TemplateResponse(request, "admin/settings.html", {
        "settings": {s.key: s for s in rows},
    })


@router.post("/admin/settings")
async def update_settings(
    request: Request,
    _=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
    normal_threshold: float = Form(...),
    warning_threshold: float = Form(...),
):
    if request.session.get("role") != "superadmin":
        raise HTTPException(302, headers={"Location": "/"})
    for key, val in [("normal_threshold", str(normal_threshold)), ("warning_threshold", str(warning_threshold))]:
        stmt = select(Setting).where(Setting.key == key)
        s = (await db.execute(stmt)).scalar_one_or_none()
        if s:
            s.value = val
        else:
            db.add(Setting(key=key, value=val))
    await db.commit()

    await _recalculate_all(db)

    return RedirectResponse(url="/admin/settings", status_code=302)


async def _recalculate_all(db: AsyncSession):
    entries = (await db.execute(
        select(RefuelEntry).where(RefuelEntry.is_deleted == False)
    )).scalars().all()
    thresh_cache = {}
    for e in entries:
        if e.is_false:
            continue
        vid = e.vehicle_id
        if vid not in thresh_cache:
            thresh_cache[vid] = await _get_effective_thresholds(db, vid)
        n_pct, w_pct, n_abs, w_abs, enable_abs = thresh_cache[vid]
        if e.pilot_amount and e.actual_amount and e.pilot_amount > 0:
            diff, err, status = _calc_comparison(e.pilot_amount, e.actual_amount, n_pct, w_pct, n_abs, w_abs, enable_abs)
            e.difference = diff
            e.error_percent = err
            e.comparison_status = status
        elif e.actual_amount is not None:
            e.difference = None
            e.error_percent = None
            e.comparison_status = "pilot_missing"
    await db.commit()


@router.get("/admin/settings/recalculate")
async def recalculate_all(
    request: Request,
    _=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    if request.session.get("role") != "superadmin":
        raise HTTPException(302, headers={"Location": "/"})
    await _recalculate_all(db)
    return RedirectResponse(url="/admin/settings?recalculated=1", status_code=302)


# ─── API: Get sites for a company (for edit user form) ────────────


@router.get("/api/admin/companies/{company_id}/sites")
async def get_company_sites(
    company_id: int = Path(...),
    _=Depends(require_superadmin),
    db: AsyncSession = Depends(get_db),
):
    sites = (await db.execute(
        select(Site).where(Site.client_account_id == company_id).order_by(Site.name)
    )).scalars().all()
    return [{"id": s.id, "name": s.name} for s in sites]
