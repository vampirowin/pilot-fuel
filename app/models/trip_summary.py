from datetime import datetime
from sqlalchemy import Integer, Float, DateTime, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column, relationship
from app.database import Base


class TripSummary(Base):
    __tablename__ = "trip_summaries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(Integer, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True)
    date: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    duration_seconds: Mapped[int] = mapped_column(Integer, default=0)
    motion_seconds: Mapped[int] = mapped_column(Integer, default=0)
    gps_km: Mapped[float] = mapped_column(Float, default=0)
    can_km: Mapped[float] = mapped_column(Float, default=0)
    max_speed: Mapped[float] = mapped_column(Float, default=0)
    avg_speed: Mapped[float] = mapped_column(Float, default=0)
    parking_count: Mapped[int] = mapped_column(Integer, default=0)
    segment_count: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    vehicle = relationship("Vehicle", backref="trip_summaries")
