from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.dependencies import get_current_user, apply_vehicle_filter, apply_refuel_filter
from app.database import get_db
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, func
from app.models.refuel_entry import RefuelEntry
from app.models.vehicle import Vehicle

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
):
    query = select(RefuelEntry).where(
        RefuelEntry.is_deleted == False,
        RefuelEntry.comparison_status.in_(["pilot_missing", "unacceptable"]),
    ).order_by(RefuelEntry.event_date.desc()).limit(100)
    query = apply_refuel_filter(query, user, RefuelEntry, Vehicle)
    entries = (await db.execute(query)).scalars().all()

    vq = select(Vehicle).where(Vehicle.is_active == True)
    vq = apply_vehicle_filter(vq, user, Vehicle)
    vehicles = {v.id: v for v in (await db.execute(vq)).scalars().all()}

    return templates.TemplateResponse(request, "critical.html", {
        "entries": entries,
        "vehicles": vehicles,
    })


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
