from datetime import datetime
from typing import Any
from sqlalchemy import Integer, String, DateTime, Text, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class SyncLog(Base):
    __tablename__ = "sync_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    vehicle_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("vehicles.id", ondelete="SET NULL"), nullable=True)
    sync_type: Mapped[str] = mapped_column(String(30), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False)
    records_affected: Mapped[int | None] = mapped_column(Integer, nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    details_json: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str | None] = mapped_column(String(100), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    completed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
