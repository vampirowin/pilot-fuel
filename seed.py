import asyncio
from app.config import get_settings
from app.database import async_session
from app.models.user import User
from app.models.setting import Setting
from sqlalchemy import select


async def seed():
    settings = get_settings()
    if not settings.superadmin_username or not settings.superadmin_password:
        print("SUPERADMIN_USERNAME or SUPERADMIN_PASSWORD not set in .env")
        return

    async with async_session() as db:
        result = await db.execute(select(User).where(User.username == settings.superadmin_username))
        existing = result.scalar_one_or_none()
        if existing:
            print(f"Superadmin '{settings.superadmin_username}' already exists, updating password")
            existing.password_hash = settings.superadmin_password
            existing.role = "superadmin"
        else:
            user = User(
                username=settings.superadmin_username,
                role="superadmin",
                password_hash=settings.superadmin_password,
            )
            db.add(user)
            print(f"Superadmin '{settings.superadmin_username}' created")

        result = await db.execute(select(Setting).where(Setting.key == "normal_threshold"))
        if not result.scalar_one_or_none():
            db.add(Setting(key="normal_threshold", value="3.0", description="Норма (процентов)"))
        result = await db.execute(select(Setting).where(Setting.key == "warning_threshold"))
        if not result.scalar_one_or_none():
            db.add(Setting(key="warning_threshold", value="10.0", description="Предупреждение (процентов)"))

        await db.commit()
        print("Default settings seeded")


if __name__ == "__main__":
    asyncio.run(seed())
