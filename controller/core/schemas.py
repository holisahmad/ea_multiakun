"""
core/schemas.py — Pydantic schemas for API request/response validation.
All external messages (from EA or signal sources) must be validated here.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import List, Literal, Optional
from pydantic import BaseModel, Field, field_validator


def _parse_flexible_datetime(v: any) -> datetime:
    if isinstance(v, datetime):
        return v
    if isinstance(v, str):
        s = v.strip()
        # Only replace dots in the date part (e.g. MT5 "YYYY.MM.DD HH:MM:SS")
        if len(s) >= 10 and s[4] == "." and s[7] == ".":
            s = s[:4] + "-" + s[5:7] + "-" + s[8:]
        if " " in s and "T" not in s:
            s = s.replace(" ", "T")
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    raise ValueError(f"Invalid datetime format: {v}")


# ---------------------------------------------------------------------------
# Signal (incoming from EA/indicator)
# ---------------------------------------------------------------------------
class SignalRequest(BaseModel):
    """Inbound trading signal from an EA or indicator."""
    strategy_id: str = Field(..., min_length=1, max_length=64)
    symbol: str = Field(..., min_length=1, max_length=32)
    direction: Literal["BUY", "SELL"]
    source: str = Field(..., min_length=1, max_length=64)
    price: Optional[float] = Field(None, gt=0)
    payload: Optional[dict] = None

    @field_validator("symbol")
    @classmethod
    def symbol_uppercase(cls, v: str) -> str:
        return v.upper().strip()


class SignalResponse(BaseModel):
    signal_id: str
    status: str
    message: str


# ---------------------------------------------------------------------------
# Account Status (pushed by EA every N seconds)
# ---------------------------------------------------------------------------
class AccountStatusPush(BaseModel):
    """Status report pushed by each EA instance."""
    account_alias: str = Field(..., min_length=1, max_length=64)
    login: Optional[int] = None
    server: Optional[str] = None
    balance: Optional[float] = None
    equity: Optional[float] = None
    free_margin: Optional[float] = None
    margin_level_pct: Optional[float] = None
    floating_pnl: Optional[float] = None
    open_positions: int = 0
    connected: bool = False
    ea_version: Optional[str] = None
    timestamp_utc: datetime

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def parse_timestamp(cls, v: any) -> datetime:
        return _parse_flexible_datetime(v)


class AccountStatusResponse(BaseModel):
    ok: bool
    message: str


# ---------------------------------------------------------------------------
# Position (reported by EA)
# ---------------------------------------------------------------------------
class PositionReport(BaseModel):
    """Single position reported by EA during reconciliation/push."""
    account_alias: str
    position_ticket: int
    symbol: str
    direction: Literal["BUY", "SELL"]
    volume: float
    open_price: float
    current_price: Optional[float] = None
    tp: Optional[float] = None
    sl: Optional[float] = None
    floating_pnl: Optional[float] = None
    magic_number: Optional[int] = None
    comment: Optional[str] = None


class PositionsPushRequest(BaseModel):
    """Bulk position report from EA."""
    account_alias: str
    positions: List[PositionReport]
    timestamp_utc: datetime

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def parse_timestamp(cls, v: any) -> datetime:
        return _parse_flexible_datetime(v)


# ---------------------------------------------------------------------------
# Command (queued for EA to pick up)
# ---------------------------------------------------------------------------
class CommandPayload(BaseModel):
    """Command the EA polls and executes."""
    strategy_id: str
    cycle_id: str
    account_alias: str
    command_id: str
    action: Literal["OPEN_BUY", "OPEN_SELL", "CLOSE", "CLOSE_ALL_CYCLE", "MODIFY_SL_TP"]
    layer: int
    symbol: str
    volume: float
    tp_points: Optional[int] = None
    sl_points: Optional[int] = None
    deviation_points: int = 10
    magic_number: int
    created_at_utc: str
    expires_at_utc: str


class CommandPollResponse(BaseModel):
    """Response to EA polling for next pending command."""
    has_command: bool
    command: Optional[CommandPayload] = None


# ---------------------------------------------------------------------------
# Execution Result (EA reports back after trade attempt)
# ---------------------------------------------------------------------------
class ExecutionResultRequest(BaseModel):
    """EA posts this after attempting to execute a command."""
    command_id: str
    account_alias: str
    retcode: int
    order_ticket: Optional[int] = None
    deal_ticket: Optional[int] = None
    position_id: Optional[int] = None
    executed_volume: Optional[float] = None
    executed_price: Optional[float] = None
    status: Literal["FILLED", "PARTIAL", "REJECTED", "TIMEOUT", "UNKNOWN"]
    raw_response: Optional[dict] = None
    timestamp_utc: datetime

    @field_validator("timestamp_utc", mode="before")
    @classmethod
    def parse_timestamp(cls, v: any) -> datetime:
        return _parse_flexible_datetime(v)


class ExecutionResultResponse(BaseModel):
    ok: bool
    message: str


# ---------------------------------------------------------------------------
# Dashboard / Read endpoints
# ---------------------------------------------------------------------------
class SystemStateResponse(BaseModel):
    environment: str
    live_trading_enabled: bool
    strategy_enabled: bool
    protection_active: bool
    active_cycles: int
    last_updated: datetime


class AccountSummary(BaseModel):
    alias: str
    role: str
    connected: bool
    balance: Optional[float]
    equity: Optional[float]
    free_margin: Optional[float]
    margin_level_pct: Optional[float]
    floating_pnl: Optional[float]
    open_positions: int
    currency: str
    is_cent: bool
    last_updated: Optional[datetime]
    data_age_seconds: Optional[float]
    is_stale: bool


class CycleSummary(BaseModel):
    cycle_id: str
    strategy_id: str
    symbol: str
    direction: str
    state: str
    started_at: datetime
    total_positions: int
    floating_pnl_usd: float
    realized_pnl_usd: float


# ---------------------------------------------------------------------------
# Operator controls
# ---------------------------------------------------------------------------
class OperatorAction(BaseModel):
    action: Literal[
        "START_STRATEGY",
        "PAUSE_ENTRIES",
        "RESUME_STRATEGY",
        "EMERGENCY_PROTECTION",
        "CLOSE_CYCLE",
        "ENABLE_ACCOUNT",
        "DISABLE_ACCOUNT",
        "RESUME_AFTER_CRITICAL",
    ]
    target: Optional[str] = None    # cycle_id or account_alias where applicable
    confirm: bool = False           # Must be True for consequential actions
    reason: Optional[str] = None


class OperatorActionResponse(BaseModel):
    ok: bool
    message: str
    action: str
