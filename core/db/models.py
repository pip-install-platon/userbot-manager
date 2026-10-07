import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    LargeBinary,
    String,
    UniqueConstraint,
    Uuid,
    false,
    func,
    true,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from core.constants import ClientState, Direction, MediaKind
from core.db.base import Base


class Operator(Base):
    __tablename__ = "operators"

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    display_name: Mapped[str] = mapped_column(String(128))
    is_superadmin: Mapped[bool] = mapped_column(Boolean, default=False, server_default=false())
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, server_default=true())
    profile_ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )

    profile_media: Mapped[list["ProfileMedia"]] = relationship(
        back_populates="operator",
        cascade="all, delete-orphan",
        lazy="selectin",
        order_by="ProfileMedia.position",
    )


class ProfileMedia(Base):
    __tablename__ = "profile_media"
    __table_args__ = (
        CheckConstraint(
            f"kind IN ('{MediaKind.PHOTO.value}', '{MediaKind.VIDEO.value}')",
            name="kind",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="CASCADE"),
        index=True,
    )
    kind: Mapped[str] = mapped_column(String(16))
    file_id: Mapped[str] = mapped_column(String(512))
    caption_ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    position: Mapped[int] = mapped_column()

    operator: Mapped[Operator] = relationship(back_populates="profile_media")


class Client(Base):
    __tablename__ = "clients"
    __table_args__ = (
        CheckConstraint(
            "state IN ('"
            + "', '".join(state.value for state in ClientState)
            + "')",
            name="state",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True, index=True)
    username: Mapped[str | None] = mapped_column(String(32), nullable=True)
    assigned_operator_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    state: Mapped[str] = mapped_column(String(32), default=ClientState.NEW.value)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class Message(Base):
    """Persisted dialog line.

    ``telegram_message_id`` is not part of the public domain sketch, but a unique
    incoming Telegram update must not create a second row.
    """

    __tablename__ = "messages"
    __table_args__ = (
        UniqueConstraint(
            "client_id",
            "direction",
            "telegram_message_id",
            name="uq_messages_source",
        ),
        CheckConstraint(
            f"direction IN ('{Direction.INCOMING.value}', '{Direction.OUTGOING.value}')",
            name="direction",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid(as_uuid=True), primary_key=True)
    client_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("clients.id", ondelete="CASCADE"),
        index=True,
    )
    operator_id: Mapped[uuid.UUID] = mapped_column(
        Uuid(as_uuid=True),
        ForeignKey("operators.id", ondelete="RESTRICT"),
        index=True,
    )
    direction: Mapped[str] = mapped_column(String(8))
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    media_file_id: Mapped[str | None] = mapped_column(String(512), nullable=True)
    telegram_message_id: Mapped[int | None] = mapped_column(BigInteger, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
