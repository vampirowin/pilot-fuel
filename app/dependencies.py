from fastapi import Request, HTTPException, Depends
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import User
from app.models.vehicle import Vehicle


def get_current_username(request: Request) -> str:
    username = request.session.get("username")
    if not username:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    return username


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db),
) -> User:
    username = get_current_username(request)
    result = await db.execute(select(User).where(User.username == username))
    user = result.scalar_one_or_none()
    if not user:
        raise HTTPException(status_code=302, headers={"Location": "/login"})
    if not user.is_active:
        request.session.clear()
        raise HTTPException(status_code=302, headers={"Location": "/login?error=blocked"})
    return user


async def require_superadmin(user: User = Depends(get_current_user)) -> User:
    if user.role != "superadmin":
        raise HTTPException(status_code=302, headers={"Location": "/"})
    return user


def apply_vehicle_filter(query, user: User, model_vehicle=Vehicle):
    if user.role == "superadmin":
        return query
    if user.client_account_id:
        query = query.where(model_vehicle.client_account_id == user.client_account_id)
        if user.site_id:
            query = query.where(model_vehicle.site_id == user.site_id)
    else:
        query = query.where(False)
    return query


def apply_refuel_filter(query, user: User, refuel_model, vehicle_model=Vehicle):
    if user.role == "superadmin":
        return query
    from sqlalchemy import select as sel
    sq = sel(vehicle_model.id).where(vehicle_model.client_account_id == user.client_account_id)
    if user.site_id:
        sq = sq.where(vehicle_model.site_id == user.site_id)
    elif user.client_account_id is None:
        sq = sq.where(False)
    return query.where(refuel_model.vehicle_id.in_(sq))
