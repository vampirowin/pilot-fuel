from datetime import datetime
from sqlalchemy import Integer, String, Boolean, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class Vehicle(Base):
    __tablename__ = "vehicles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    pilot_agent_id: Mapped[int] = mapped_column(Integer, unique=True, nullable=False, index=True)
    imei: Mapped[str | None] = mapped_column(String(50), nullable=True)
    plate_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    folder: Mapped[str | None] = mapped_column(String(200), nullable=True)
    vehicle_type: Mapped[str | None] = mapped_column(String(100), nullable=True)
    sensor_count: Mapped[int] = mapped_column(Integer, default=0)
    has_fuel_sensor: Mapped[bool] = mapped_column(Boolean, default=True, server_default='t')

    client_account_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("client_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    site_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
