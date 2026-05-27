from datetime import datetime
from sqlalchemy import Integer, String, DateTime, Float, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class PilotRefuel(Base):
    __tablename__ = "pilot_refuels"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(Integer, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True)
    sensor_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("fuel_sensors.id", ondelete="SET NULL"), nullable=True)
    event_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    start_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    end_level: Mapped[float | None] = mapped_column(Float, nullable=True)
    odometer: Mapped[float | None] = mapped_column(Float, nullable=True)
    address: Mapped[str | None] = mapped_column(String(500), nullable=True)
    lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    lon: Mapped[float | None] = mapped_column(Float, nullable=True)
    raw_data: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    synced_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
