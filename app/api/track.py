import zoneinfo
from datetime import datetime, timedelta, timezone
from fastapi import APIRouter, Request, Depends, Query, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.vehicle import Vehicle
from app.models.user import User
from app.dependencies import get_current_user
from app.services.pilot_service import PilotService
from app.timezone_utils import get_user_timezone

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/api/vehicles/{vehicle_id}/track-modal", response_class=HTMLResponse)
async def track_modal(
    request: Request,
    vehicle_id: int,
    imei: str = Query(default=""),
    date_from: str = Query(default=""),
    date_to: str = Query(default=""),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    if user.role not in ("superadmin",):
        if not user.client_account_id or vehicle.client_account_id != user.client_account_id:
            raise HTTPException(404, "Vehicle not found")
        if user.site_id and vehicle.site_id != user.site_id:
            raise HTTPException(404, "Vehicle not found")

    user_tz = get_user_timezone(user)
    local_now = datetime.now(timezone.utc).astimezone(user_tz)
    today = local_now.strftime("%Y-%m-%d")
    days7 = (local_now - timedelta(days=7)).strftime("%Y-%m-%d")

    actual_imei = imei or vehicle.imei or ""

    return templates.TemplateResponse(request, "track_modal.html", {
        "vehicle_id": vehicle_id,
        "imei": actual_imei,
        "plate": vehicle.plate_number or vehicle.name or "—",
        "date_from": date_from or days7,
        "date_to": date_to or today,
        "today_str": today,
        "days7_str": days7,
        "user_timezone": str(user_tz),
    })


@router.get("/api/vehicles/{vehicle_id}/track-data")
async def track_data(
    request: Request,
    vehicle_id: int,
    date_from: str = Query(...),
    date_to: str = Query(...),
    user=Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    vehicle = await db.get(Vehicle, vehicle_id)
    if not vehicle:
        raise HTTPException(404, "Vehicle not found")
    if user.role not in ("superadmin",):
        if not user.client_account_id or vehicle.client_account_id != user.client_account_id:
            raise HTTPException(404, "Vehicle not found")
        if user.site_id and vehicle.site_id != user.site_id:
            raise HTTPException(404, "Vehicle not found")

    token = user.pilot_token or request.session.get("token")
    node_id = user.pilot_node_id or request.session.get("node_id", 0)
    if not token:
        if vehicle.client_account_id:
            admin = (
                await db.execute(
                    select(User).where(
                        User.client_account_id == vehicle.client_account_id,
                        User.role == "company_admin",
                        User.pilot_token.isnot(None),
                    )
                )
            ).scalar_one_or_none()
            if admin and admin.pilot_token:
                token = admin.pilot_token
                node_id = admin.pilot_node_id or node_id
    if not token:
        return JSONResponse(status_code=401, content={"error": "Not authenticated"})

    try:
        df = datetime.strptime(date_from, "%Y-%m-%d")
        dt = datetime.strptime(date_to, "%Y-%m-%d").replace(hour=23, minute=59, second=59)
    except ValueError:
        raise HTTPException(400, "Invalid date format, use YYYY-MM-DD")

    ts_from = int(df.replace(tzinfo=timezone.utc).timestamp())
    ts_to = int(dt.replace(tzinfo=timezone.utc).timestamp())

    pilot = PilotService()
    segments = []
    if vehicle.imei:
        try:
            segments = await pilot.get_track(token, node_id, vehicle.imei, vehicle.pilot_agent_id, ts_from, ts_to)
        except Exception:
            segments = []

    return {"segments": segments, "plate": vehicle.plate_number or vehicle.name or "—"}
