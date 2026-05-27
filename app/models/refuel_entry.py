from datetime import datetime
from sqlalchemy import Integer, String, DateTime, Float, Boolean, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class RefuelEntry(Base):
    __tablename__ = "refuel_entries"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int] = mapped_column(Integer, ForeignKey("vehicles.id", ondelete="CASCADE"), nullable=False, index=True)
    pilot_refuel_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("pilot_refuels.id", ondelete="SET NULL"), nullable=True)

    event_date: Mapped[datetime] = mapped_column(DateTime, nullable=False, index=True)
    pilot_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    actual_amount: Mapped[float | None] = mapped_column(Float, nullable=True)
    receipt_number: Mapped[str | None] = mapped_column(String(100), nullable=True)

    source: Mapped[str] = mapped_column(String(20), nullable=False, default="pilot_sync")

    difference: Mapped[float | None] = mapped_column(Float, nullable=True)
    error_percent: Mapped[float | None] = mapped_column(Float, nullable=True)
    comparison_status: Mapped[str | None] = mapped_column(String(30), nullable=True)

    is_false: Mapped[bool] = mapped_column(Boolean, default=False)
    false_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    false_marked_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    false_marked_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    is_deleted: Mapped[bool] = mapped_column(Boolean, default=False)
    deleted_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
