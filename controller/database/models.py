"""database/models.py — SQLAlchemy ORM models for STMA."""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def new_uuid() -> str:
    return str(uuid.uuid4())


class Base(DeclarativeBase):
    pass


# ---------------------------------------------------------------------------
# accounts
# ---------------------------------------------------------------------------
class Account(Base):
    """Internal account registry. NO broker credentials stored here."""
    __tablename__ = "accounts"

    alias: Mapped[str] = mapped_column(String(64), primary_key=True)
    role: Mapped[str] = mapped_column(String(16))           # primary/secondary/tertiary
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    currency: Mapped[str] = mapped_column(String(8), default="USD")
    is_cent: Mapped[bool] = mapped_column(Boolean, default=False)
    cent_divisor: Mapped[int] = mapped_column(Integer, default=100)
    magic_number: Mapped[int] = mapped_column(Integer, unique=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)


# ---------------------------------------------------------------------------
# strategy_configs (versioned)
# ---------------------------------------------------------------------------
class StrategyConfig(Base):
    __tablename__ = "strategy_configs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    strategy_id: Mapped[str] = mapped_column(String(64))
    version: Mapped[int] = mapped_column(Integer, default=1)
    config_json: Mapped[str] = mapped_column(Text)           # JSON snapshot
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# signals
# ---------------------------------------------------------------------------
class Signal(Base):
    __tablename__ = "signals"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    source: Mapped[str] = mapped_column(String(64))          # EA name / manual
    direction: Mapped[str] = mapped_column(String(8))        # BUY / SELL
    symbol: Mapped[str] = mapped_column(String(32))
    strategy_id: Mapped[str] = mapped_column(String(64))
    price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), default="RECEIVED")
    # RECEIVED / ACCEPTED / REJECTED / EXPIRED / USED
    reject_reason: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    cycles: Mapped[list["Cycle"]] = relationship("Cycle", back_populates="signal")


# ---------------------------------------------------------------------------
# cycles
# ---------------------------------------------------------------------------
class Cycle(Base):
    __tablename__ = "cycles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    strategy_id: Mapped[str] = mapped_column(String(64))
    signal_id: Mapped[str] = mapped_column(String(36), ForeignKey("signals.id"))
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))        # BUY / SELL
    state: Mapped[str] = mapped_column(String(64), default="IDLE")
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ended_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    realized_pnl_usd: Mapped[float] = mapped_column(Float, default=0.0)
    notes: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    signal: Mapped["Signal"] = relationship("Signal", back_populates="cycles")
    commands: Mapped[list["Command"]] = relationship("Command", back_populates="cycle")
    state_events: Mapped[list["StateEvent"]] = relationship("StateEvent", back_populates="cycle")


# ---------------------------------------------------------------------------
# commands
# ---------------------------------------------------------------------------
class Command(Base):
    __tablename__ = "commands"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    cycle_id: Mapped[str] = mapped_column(String(36), ForeignKey("cycles.id"))
    account_alias: Mapped[str] = mapped_column(String(64))
    action: Mapped[str] = mapped_column(String(32))          # OPEN_BUY / OPEN_SELL / CLOSE / MODIFY
    layer: Mapped[int] = mapped_column(Integer, default=1)
    symbol: Mapped[str] = mapped_column(String(32))
    volume: Mapped[float] = mapped_column(Float)
    tp_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    sl_points: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(32), default="PENDING")
    # PENDING / SENT / ACKNOWLEDGED / EXECUTED / FAILED / EXPIRED / REJECTED
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    sent_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    acked_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payload_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    cycle: Mapped["Cycle"] = relationship("Cycle", back_populates="commands")
    executions: Mapped[list["Execution"]] = relationship("Execution", back_populates="command")


# ---------------------------------------------------------------------------
# executions (broker results)
# ---------------------------------------------------------------------------
class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    command_id: Mapped[str] = mapped_column(String(36), ForeignKey("commands.id"))
    account_alias: Mapped[str] = mapped_column(String(64))
    retcode: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    order_ticket: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    deal_ticket: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    position_id: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    executed_volume: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    executed_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    status: Mapped[str] = mapped_column(String(32))
    # FILLED / PARTIAL / REJECTED / TIMEOUT / UNKNOWN
    raw_response_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    command: Mapped["Command"] = relationship("Command", back_populates="executions")


# ---------------------------------------------------------------------------
# positions_snapshot
# ---------------------------------------------------------------------------
class PositionSnapshot(Base):
    __tablename__ = "positions_snapshot"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    account_alias: Mapped[str] = mapped_column(String(64))
    cycle_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    position_ticket: Mapped[int] = mapped_column(Integer)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))
    volume: Mapped[float] = mapped_column(Float)
    open_price: Mapped[float] = mapped_column(Float)
    current_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    tp: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    sl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    floating_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    magic_number: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    comment: Mapped[Optional[str]] = mapped_column(String(256), nullable=True)
    is_managed: Mapped[bool] = mapped_column(Boolean, default=True)
    last_seen: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# closed_trades (audit of every closed trade with realized P/L)
# ---------------------------------------------------------------------------
class ClosedTrade(Base):
    __tablename__ = "closed_trades"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    cycle_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    account_alias: Mapped[str] = mapped_column(String(64))
    position_ticket: Mapped[int] = mapped_column(Integer, index=True)
    symbol: Mapped[str] = mapped_column(String(32))
    direction: Mapped[str] = mapped_column(String(8))
    volume: Mapped[float] = mapped_column(Float)
    open_price: Mapped[float] = mapped_column(Float)
    close_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    realized_pnl: Mapped[float] = mapped_column(Float, default=0.0)
    close_reason: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    opened_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    closed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# risk_events
# ---------------------------------------------------------------------------
class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    cycle_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    account_alias: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    # ENTRY_BLOCKED / PROTECTION_TRIGGERED / LIMIT_REACHED / STALE_ACCOUNT / etc.
    severity: Mapped[str] = mapped_column(String(16), default="WARNING")
    # INFO / WARNING / CRITICAL
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    limit: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    resolved_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    requires_operator: Mapped[bool] = mapped_column(Boolean, default=False)
    resolved: Mapped[bool] = mapped_column(Boolean, default=False)


# ---------------------------------------------------------------------------
# state_events (cycle state transitions audit)
# ---------------------------------------------------------------------------
class StateEvent(Base):
    __tablename__ = "state_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    cycle_id: Mapped[str] = mapped_column(String(36), ForeignKey("cycles.id"))
    from_state: Mapped[str] = mapped_column(String(64))
    to_state: Mapped[str] = mapped_column(String(64))
    trigger: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    cycle: Mapped["Cycle"] = relationship("Cycle", back_populates="state_events")


# ---------------------------------------------------------------------------
# system_events (connectivity, errors, recovery)
# ---------------------------------------------------------------------------
class SystemEvent(Base):
    __tablename__ = "system_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    account_alias: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    event_type: Mapped[str] = mapped_column(String(64))
    # EA_CONNECTED / EA_DISCONNECTED / RECONCILIATION_START / RECONCILIATION_OK /
    # RECONCILIATION_MISMATCH / ORDER_FAILURE / STARTUP / SHUTDOWN
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


# ---------------------------------------------------------------------------
# account_status_cache (latest pushed status from each EA)
# ---------------------------------------------------------------------------
class AccountStatus(Base):
    __tablename__ = "account_status_cache"
    __table_args__ = (UniqueConstraint("account_alias"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_uuid)
    account_alias: Mapped[str] = mapped_column(String(64))
    login: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    server: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    balance: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    equity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    free_margin: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    margin_level_pct: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    floating_pnl: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    open_positions: Mapped[int] = mapped_column(Integer, default=0)
    connected: Mapped[bool] = mapped_column(Boolean, default=False)
    last_updated: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    ea_version: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
