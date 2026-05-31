from datetime import datetime, timezone
import zoneinfo
from fastapi import APIRouter, Request, Depends
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import User
from app.services.pilot_service import PilotService
from app.dependencies import get_current_user, get_current_username
from app.timezone_utils import utcnow, get_timezone_choices

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("username"):
        return RedirectResponse(url="/", status_code=302)
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(request, "login.html", {"error": error})


@router.post("/login")
async def login(request: Request, db: AsyncSession = Depends(get_db)):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if not username or not password:
        return templates.TemplateResponse(request, "login.html", {"error": "Логин и пароль обязательны"})

    service = PilotService()
    try:
        result = await service.login(username, password)
    except Exception as e:
        return templates.TemplateResponse(request, "login.html", {"error": f"Ошибка: {e}"})

    token = result.get("token")
    node_id = result.get("node_id", 0)

    request.session["username"] = username
    request.session["token"] = token
    request.session["node_id"] = node_id

    stmt = select(User).where(User.username == username)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user:
        if not user.is_active:
            return templates.TemplateResponse(request, "login.html", {"error": "Пользователь заблокирован. Обратитесь к администратору."})
        user.pilot_token = token
        user.pilot_node_id = node_id
        if password:
            user.pilot_password = password
        user.last_login = utcnow()
        request.session["role"] = user.role
        request.session["client_account_id"] = user.client_account_id
        request.session["site_id"] = user.site_id
    else:
        user = User(
            username=username,
            pilot_token=token,
            pilot_node_id=node_id,
            role="user",
            last_login=utcnow(),
        )
        db.add(user)
        await db.flush()
        request.session["role"] = "user"
        request.session["client_account_id"] = None
        request.session["site_id"] = None
    await db.commit()

    return RedirectResponse(url="/", status_code=302)


@router.get("/admin/login", response_class=HTMLResponse)
async def superadmin_login_page(request: Request):
    if request.session.get("username") and request.session.get("role") == "superadmin":
        return RedirectResponse(url="/admin/users", status_code=302)
    from app.config import get_settings
    error = request.query_params.get("error", "")
    return templates.TemplateResponse(request, "admin_login.html", {"error": error})


@router.post("/admin/login")
async def superadmin_login(request: Request, db: AsyncSession = Depends(get_db)):
    from app.config import get_settings
    settings = get_settings()

    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if not username or not password:
        return templates.TemplateResponse(request, "admin_login.html", {"error": "Логин и пароль обязательны"})

    if username != settings.superadmin_username or password != settings.superadmin_password:
        return templates.TemplateResponse(request, "admin_login.html", {"error": "Неверный логин или пароль"})

    stmt = select(User).where(User.username == username)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user:
        user.last_login = utcnow()
    else:
        user = User(
            username=username,
            role="superadmin",
            last_login=utcnow(),
        )
        db.add(user)
    await db.commit()

    request.session["username"] = username
    request.session["role"] = "superadmin"
    request.session["token"] = None
    request.session["node_id"] = None
    request.session["client_account_id"] = None
    request.session["site_id"] = None

    return RedirectResponse(url="/admin/users", status_code=302)


@router.get("/profile", response_class=HTMLResponse)
async def profile_page(
    request: Request,
    user: User = Depends(get_current_user),
):
    ru_zones, other_zones = get_timezone_choices()
    return templates.TemplateResponse(request, "profile.html", {
        "user": user, "ru_zones": ru_zones, "other_zones": other_zones, "saved": False,
    })


@router.post("/profile")
async def profile_save(
    request: Request,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    tz = form.get("timezone", "").strip()
    if tz:
        try:
            zoneinfo.ZoneInfo(tz)
            user.timezone = tz
        except Exception:
            pass
    else:
        user.timezone = None
    full_name = form.get("full_name", "").strip()
    user.full_name = full_name or None
    await db.commit()
    ru_zones, other_zones = get_timezone_choices()
    return templates.TemplateResponse(request, "profile.html", {
        "user": user, "ru_zones": ru_zones, "other_zones": other_zones, "saved": True,
    })


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)
