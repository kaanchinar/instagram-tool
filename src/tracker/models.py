import enum
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from tracker.db import Base

BigIntPK = BigInteger().with_variant(Integer, "sqlite")


class SnapshotStatus(str, enum.Enum):
    ok = "ok"
    failed = "failed"


class Direction(str, enum.Enum):
    follower = "follower"
    following = "following"


class EventType(str, enum.Enum):
    new_follower = "new_follower"
    unfollowed = "unfollowed"
    i_followed = "i_followed"
    i_unfollowed = "i_unfollowed"


class Snapshot(Base):
    __tablename__ = "snapshots"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    taken_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    follower_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    following_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    status: Mapped[SnapshotStatus] = mapped_column(
        Enum(SnapshotStatus, name="snapshot_status")
    )
    error: Mapped[str | None] = mapped_column(Text, nullable=True)

    entries: Mapped[list["SnapshotEntry"]] = relationship(
        back_populates="snapshot", cascade="all, delete-orphan"
    )


class SnapshotEntry(Base):
    __tablename__ = "snapshot_entries"
    __table_args__ = (
        UniqueConstraint(
            "snapshot_id", "ig_user_id", "direction", name="uq_snapshot_entry"
        ),
    )

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("snapshots.id"), index=True
    )
    ig_user_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str] = mapped_column(Text)
    direction: Mapped[Direction] = mapped_column(Enum(Direction, name="direction"))

    snapshot: Mapped[Snapshot] = relationship(back_populates="entries")


class Person(Base):
    __tablename__ = "people"

    ig_user_id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    username: Mapped[str] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_follower: Mapped[bool] = mapped_column(Boolean)
    is_following: Mapped[bool] = mapped_column(Boolean)
    whitelisted: Mapped[bool] = mapped_column(Boolean, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    last_changed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(BigIntPK, primary_key=True)
    detected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    ig_user_id: Mapped[int] = mapped_column(BigInteger, index=True)
    username: Mapped[str] = mapped_column(Text)
    type: Mapped[EventType] = mapped_column(Enum(EventType, name="event_type"))
    snapshot_id: Mapped[int] = mapped_column(ForeignKey("snapshots.id"))
