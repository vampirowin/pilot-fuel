from datetime import datetime
from typing import Optional
from sqlalchemy import Integer, String, Boolean, DateTime, Text, ForeignKey, func
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base
from app.services.crypto import EncryptedText


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    full_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="user")
    timezone: Mapped[Optional[str]] = mapped_column(String(50), nullable=True, default=None)

    pilot_token: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)
    pilot_node_id: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pilot_password: Mapped[str | None] = mapped_column(EncryptedText, nullable=True)

    password_hash: Mapped[str | None] = mapped_column(Text, nullable=True)

    client_account_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("client_accounts.id", ondelete="SET NULL"), nullable=True, index=True)
    site_id: Mapped[int | None] = mapped_column(Integer, ForeignKey("sites.id", ondelete="SET NULL"), nullable=True, index=True)

    is_active: Mapped[bool] = mapped_column(Boolean, default=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    last_login: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
