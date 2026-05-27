from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
from app.dependencies import get_current_username

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin/vehicles-no-sensor", response_class=HTMLResponse)
async def vehicles_no_sensor(
    request: Request,
    _=Depends(get_current_username),
    db: AsyncSession = Depends(get_db),
):
    if not request.session.get("is_admin"):
        return HTMLResponse("Forbidden", status_code=403)

    result = await db.execute(
        select(Vehicle).where(
            Vehicle.is_active == True,
            Vehicle.has_fuel_sensor == False,
        ).order_by(Vehicle.folder, Vehicle.plate_number)
    )
    vehicles = result.scalars().all()

    out = []
    for v in vehicles:
        out.append({
            "id": v.id,
            "plate_number": v.plate_number,
            "imei": v.imei,
            "folder": v.folder,
        })

    return templates.TemplateResponse(request, "vehicles_no_sensor.html", {
        "vehicles": out,
    })
