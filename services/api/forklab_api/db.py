"""Persistence.

SQLite by default so the demo runs with one command. Set DATABASE_URL to a
Postgres URL for a team deployment. Job state lives in the database rather than
in a process, so a worker crash or a browser refresh leaves a resumable record
rather than a lost run.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime

from sqlalchemy import (
    JSON,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DEFAULT_URL = "sqlite:///./forklab.db"


def database_url() -> str:
    return os.environ.get("DATABASE_URL", DEFAULT_URL)


class Base(DeclarativeBase):
    pass


def utcnow() -> datetime:
    return datetime.now(UTC)


class ScenarioRow(Base):
    __tablename__ = "scenarios"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    label: Mapped[str] = mapped_column(String(200))
    parent_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    input_digest: Mapped[str] = mapped_column(String(32))
    policy_digest: Mapped[str] = mapped_column(String(32))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class ExperimentRow(Base):
    """A durable job. `lease_expires_at` is what makes recovery possible."""

    __tablename__ = "experiments"

    id: Mapped[str] = mapped_column(String(64), primary_key=True)
    organization_id: Mapped[str] = mapped_column(String(64), index=True)
    baseline_scenario_id: Mapped[str] = mapped_column(ForeignKey("scenarios.id"))
    candidate_scenario_id: Mapped[str | None] = mapped_column(
        ForeignKey("scenarios.id"), nullable=True
    )
    state: Mapped[str] = mapped_column(String(32), index=True, default="queued")
    replications: Mapped[int] = mapped_column(Integer, default=30)
    completed_replications: Mapped[int] = mapped_column(Integer, default=0)
    stage: Mapped[str] = mapped_column(String(64), default="queued")
    engine_version: Mapped[str] = mapped_column(String(64), default="")
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True, index=True)
    worker_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    cancel_requested: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    report: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )


class ReplicationRow(Base):
    """One replication checkpoint. Written as the worker progresses."""

    __tablename__ = "replications"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    arm: Mapped[str] = mapped_column(String(16))
    seed: Mapped[int] = mapped_column(Integer)
    metrics: Mapped[dict] = mapped_column(JSON)
    invariant_failures: Mapped[list] = mapped_column(JSON, default=list)
    duration_seconds: Mapped[float] = mapped_column(Float, default=0.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class EventLogRow(Base):
    """The event log of a single stored replication, kept for order inspection."""

    __tablename__ = "event_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    experiment_id: Mapped[str] = mapped_column(ForeignKey("experiments.id"), index=True)
    arm: Mapped[str] = mapped_column(String(16))
    seed: Mapped[int] = mapped_column(Integer)
    events: Mapped[list] = mapped_column(JSON)


_engine = None
_SessionLocal = None


def get_engine():
    global _engine, _SessionLocal
    if _engine is None:
        url = database_url()
        connect_args = {"check_same_thread": False} if url.startswith("sqlite") else {}
        _engine = create_engine(url, connect_args=connect_args, future=True)
        _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def init_db() -> None:
    Base.metadata.create_all(get_engine())


@contextmanager
def session_scope() -> Iterator[Session]:
    get_engine()
    assert _SessionLocal is not None
    session = _SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
