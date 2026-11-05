"""SQLAlchemy models. One database holds every pack (rows carry the pack slug)."""

from __future__ import annotations

import os
from datetime import date, datetime, timezone

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, sessionmaker


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Account(Base):
    __tablename__ = "accounts"
    __table_args__ = (UniqueConstraint("pack", "domain"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    pack: Mapped[str] = mapped_column(String(100), index=True)
    domain: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255))
    source: Mapped[str] = mapped_column(String(100))
    parent_domain: Mapped[str | None] = mapped_column(String(255), nullable=True)
    suppressed: Mapped[bool] = mapped_column(Boolean, default=False)
    attrs: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    signals: Mapped[list["SignalRow"]] = relationship(back_populates="account")


class SignalRow(Base):
    __tablename__ = "signals"
    # Idempotency: re-running never stores the same signal twice
    __table_args__ = (UniqueConstraint("account_id", "type", "source_url", "summary"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    type: Mapped[str] = mapped_column(String(100))
    summary: Mapped[str] = mapped_column(Text)
    source_url: Mapped[str] = mapped_column(String(1000))
    observed_at: Mapped[date] = mapped_column(Date)
    confidence: Mapped[float] = mapped_column(Float, default=1.0)
    raw: Mapped[dict] = mapped_column(JSON, default=dict)

    account: Mapped[Account] = relationship(back_populates="signals")


class Run(Base):
    __tablename__ = "runs"

    id: Mapped[int] = mapped_column(primary_key=True)
    pack: Mapped[str] = mapped_column(String(100), index=True)
    offline: Mapped[bool] = mapped_column(Boolean, default=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    accounts: Mapped[int] = mapped_column(Integer, default=0)
    shortlisted: Mapped[int] = mapped_column(Integer, default=0)
    pages_fetched: Mapped[int] = mapped_column(Integer, default=0)
    llm_calls: Mapped[int] = mapped_column(Integer, default=0)
    errors: Mapped[list] = mapped_column(JSON, default=list)


class ScoreRow(Base):
    __tablename__ = "scores"

    id: Mapped[int] = mapped_column(primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("runs.id"), index=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    fit: Mapped[float] = mapped_column(Float)
    timing: Mapped[float] = mapped_column(Float)
    total: Mapped[float] = mapped_column(Float)
    excluded: Mapped[bool] = mapped_column(Boolean, default=False)
    shortlisted: Mapped[bool] = mapped_column(Boolean, default=False)
    explanation: Mapped[str] = mapped_column(Text)
    fit_detail: Mapped[dict] = mapped_column(JSON, default=dict)
    play: Mapped[str | None] = mapped_column(String(100), nullable=True)

    account: Mapped[Account] = relationship()


class Action(Base):
    """Outcome log: what you did with an account and what happened."""

    __tablename__ = "actions"

    id: Mapped[int] = mapped_column(primary_key=True)
    account_id: Mapped[int] = mapped_column(ForeignKey("accounts.id"), index=True)
    type: Mapped[str] = mapped_column(String(50))  # play_run, replied, meeting, referral, no_response, skip
    play: Mapped[str | None] = mapped_column(String(100), nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class PageCache(Base):
    __tablename__ = "page_cache"

    url: Mapped[str] = mapped_column(String(1000), primary_key=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    content: Mapped[str] = mapped_column(Text)


def database_url() -> str:
    return os.getenv("SE_DATABASE_URL", "sqlite:///signal_engine.db")


def make_session_factory(db_url: str | None = None):
    engine = create_engine(db_url or database_url(), future=True)
    Base.metadata.create_all(engine)
    return sessionmaker(engine, expire_on_commit=False, future=True)
