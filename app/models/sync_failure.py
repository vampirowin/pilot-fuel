from datetime import datetime, date
from sqlalchemy import Integer, String, Text, DateTime, Date, Boolean, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base


class SyncFailure(Base):
    __tablename__ = "sync_failures"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    client_account_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    sync_date: Mapped[date] = mapped_column(Date, nullable=False)
    attempt: Mapped[int] = mapped_column(Integer, default=1)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    dismissed: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())
