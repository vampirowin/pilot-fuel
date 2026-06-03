import os
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.config import get_settings
from app.database import engine

settings = get_settings()

# Папка app/ (где лежит этот __init__.py)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, "static")


@asynccontextmanager
async def lifespan(app: FastAPI):
    from app.scheduler import start_scheduler
    start_scheduler()
    yield
    from app.scheduler import stop_scheduler
    await stop_scheduler()
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="UI fuel", lifespan=lifespan)
    app.add_middleware(
        SessionMiddleware,
        secret_key=settings.secret_key,
        max_age=settings.session_lifetime_hours * 3600,
    )

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    # ===== PWA: манифест и Service Worker в корне сайта =====
    @app.get("/manifest.json", include_in_schema=False)
    async def manifest():
        return FileResponse(
            os.path.join(STATIC_DIR, "manifest.json"),
            media_type="application/manifest+json",
        )

    @app.get("/sw.js", include_in_schema=False)
    async def service_worker():
        response = FileResponse(
            os.path.join(STATIC_DIR, "sw.js"),
            media_type="application/javascript",
        )
        # Чтобы браузер не кешировал SW агрессивно
        response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
        response.headers["Service-Worker-Allowed"] = "/"
        return response
    # ========================================================

    from app.api.auth import router as auth_router
    from app.api.main import router as main_router
    from app.api.vehicles import router as vehicles_router
    from app.api.refuels import router as refuels_router
    from app.api.admin import router as admin_router
    from app.api.fuel_graph import router as fuel_graph_router
    from app.api.track import router as track_router

    app.include_router(auth_router)
    app.include_router(main_router)
    app.include_router(vehicles_router)
    app.include_router(refuels_router)
    app.include_router(admin_router)
    app.include_router(fuel_graph_router)
    app.include_router(track_router)

    return app