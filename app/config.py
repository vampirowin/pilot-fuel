from functools import lru_cache
from pydantic_settings import BaseSettings


class Settings(BaseSettings):
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/pilot_fuel"
    pilot_api_base_url: str = "https://blade.pilot-gps.com"
    secret_key: str = "change-me"
    session_lifetime_hours: int = 48
    app_port: int = 9001
    app_host: str = "0.0.0.0"
    timezone: str = "Europe/Moscow"

    # Default fuel sensor semantic IDs (Pilot system)
    fuel_sensor_semantic_ids: list[int] = [1, 2, 3, 4]
    # 1 = Fuel level (analog), 2 = Fuel level (digital), 3 = Fuel consumption, 4 = Fuel level (percent)

    normal_threshold: float = 3.0
    warning_threshold: float = 10.0

    superadmin_username: str = ""
    superadmin_password: str = ""

    class Config:
        env_file = ".env"


@lru_cache
def get_settings() -> Settings:
    return Settings()
