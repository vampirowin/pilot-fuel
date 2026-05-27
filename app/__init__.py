from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from starlette.middleware.sessions import SessionMiddleware
from app.config import get_settings
from app.database import engine

settings = get_settings()


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    app = FastAPI(title="pilot-fuel", lifespan=lifespan)
    app.add_middleware(SessionMiddleware, secret_key=settings.secret_key, max_age=settings.session_lifetime_hours * 3600)

    app.mount("/static", StaticFiles(directory="app/static"), name="static")

    from app.api.auth import router as auth_router
    from app.api.main import router as main_router
    from app.api.vehicles import router as vehicles_router
    from app.api.refuels import router as refuels_router

    app.include_router(auth_router)
    app.include_router(main_router)
    app.include_router(vehicles_router)
    app.include_router(refuels_router)

    return app
