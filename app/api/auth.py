from datetime import datetime
from fastapi import APIRouter, Request, Depends, HTTPException
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from app.database import get_db
from app.models.user import User
from app.services.pilot_service import PilotService
from app.dependencies import get_current_username

router = APIRouter()


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    if request.session.get("username"):
        return RedirectResponse(url="/", status_code=302)
    return HTMLResponse(LOGIN_PAGE)


@router.post("/login")
async def login(
    request: Request,
    db: AsyncSession = Depends(get_db),
):
    form = await request.form()
    username = form.get("username", "")
    password = form.get("password", "")

    if not username or not password:
        return HTMLResponse(LOGIN_ERROR_HTML, status_code=400)

    service = PilotService()
    try:
        result = await service.login(username, password)
    except Exception as e:
        return HTMLResponse(
            LOGIN_ERROR_HTML.replace("Ошибка входа", f"Ошибка: {e}"),
            status_code=401,
        )

    token = result.get("token")
    node_id = result.get("node_id", 0)

    request.session["username"] = username
    request.session["token"] = token
    request.session["node_id"] = node_id
    request.session["is_admin"] = False

    stmt = select(User).where(User.username == username)
    user = (await db.execute(stmt)).scalar_one_or_none()
    if user:
        user.pilot_token = token
        user.pilot_node_id = node_id
        user.last_login = datetime.now()
    else:
        user = User(
            username=username,
            pilot_token=token,
            pilot_node_id=node_id,
            last_login=datetime.now(),
        )
        db.add(user)
    await db.commit()

    return RedirectResponse(url="/", status_code=302)


@router.get("/logout")
async def logout(request: Request):
    request.session.clear()
    return RedirectResponse(url="/login", status_code=302)


LOGIN_PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pilot-fuel — вход</title>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<link rel="stylesheet" href="/static/css/style.css">
</head>
<body class="login-page">
<div class="login-container">
  <div class="login-card">
    <div class="login-icon">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M3 22V8l7-5v14l-7 5zM10 3l7 5v14l-7-5V3zM17 8l4 2v12l-4-2V8z"/>
      </svg>
    </div>
    <h1 class="login-title">pilot-fuel</h1>
    <p class="login-subtitle">Мониторинг топлива</p>
    <form action="/login" method="POST" hx-post="/login" hx-target="body" hx-push-url="true">
      <div class="form-group">
        <label for="username">Логин Pilot</label>
        <input type="text" id="username" name="username" required autocomplete="username">
      </div>
      <div class="form-group">
        <label for="password">Пароль</label>
        <input type="password" id="password" name="password" required autocomplete="current-password">
      </div>
      <button type="submit" class="btn btn-primary btn-full">Войти</button>
    </form>
  </div>
</div>
</body>
</html>"""

LOGIN_ERROR_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>pilot-fuel — вход</title>
<script src="https://unpkg.com/htmx.org@2.0.4"></script>
<link rel="stylesheet" href="/static/css/style.css">
</head>
<body class="login-page">
<div class="login-container">
  <div class="login-card">
    <div class="login-icon">
      <svg width="48" height="48" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5">
        <path d="M3 22V8l7-5v14l-7 5zM10 3l7 5v14l-7-5V3zM17 8l4 2v12l-4-2V8z"/>
      </svg>
    </div>
    <h1 class="login-title">pilot-fuel</h1>
    <p class="login-subtitle">Мониторинг топлива</p>
    <div class="alert alert-error">Ошибка входа. Проверьте логин и пароль.</div>
    <form action="/login" method="POST" hx-post="/login" hx-target="body" hx-push-url="true">
      <div class="form-group">
        <label for="username">Логин Pilot</label>
        <input type="text" id="username" name="username" required autocomplete="username">
      </div>
      <div class="form-group">
        <label for="password">Пароль</label>
        <input type="password" id="password" name="password" required autocomplete="current-password">
      </div>
      <button type="submit" class="btn btn-primary btn-full">Войти</button>
    </form>
  </div>
</div>
</body>
</html>"""
