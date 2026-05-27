from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from app.dependencies import get_current_username
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
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    total_vehicles = (await db.execute(select(func.count(Vehicle.id)))).scalar() or 0
    total_refuels = (await db.execute(
        select(func.count(RefuelEntry.id)).where(RefuelEntry.is_deleted == False)
    )).scalar() or 0
    critical_count = (await db.execute(
        select(func.count(RefuelEntry.id)).where(
            RefuelEntry.is_deleted == False,
            RefuelEntry.is_false == False,
            RefuelEntry.comparison_status.in_(["pilot_missing", "unacceptable"]),
        )
    )).scalar() or 0

    return templates.TemplateResponse(request, "dashboard.html", {
        "total_vehicles": total_vehicles,
        "total_refuels": total_refuels,
        "critical_count": critical_count,
    })

@router.get("/critical", response_class=HTMLResponse)
async def critical_page(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    query = select(RefuelEntry).where(
        RefuelEntry.is_deleted == False,
        RefuelEntry.comparison_status.in_(["pilot_missing", "unacceptable"]),
    ).order_by(RefuelEntry.event_date.desc()).limit(100)
    entries = (await db.execute(query)).scalars().all()

    vehicles_result = await db.execute(select(Vehicle).where(Vehicle.is_active == True))
    vehicles = {v.id: v for v in vehicles_result.scalars().all()}

    return templates.TemplateResponse(request, "critical.html", {
        "entries": entries,
        "vehicles": vehicles,
    })


@router.get("/api/critical-count")
async def critical_count(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    count = (await db.execute(
        select(func.count(RefuelEntry.id)).where(
            RefuelEntry.is_deleted == False,
            RefuelEntry.is_false == False,
            RefuelEntry.comparison_status.in_(["pilot_missing", "unacceptable"]),
        )
    )).scalar() or 0
    return HTMLResponse(str(count))
