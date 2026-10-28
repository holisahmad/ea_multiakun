"""
api/routes_dashboard.py — Dashboard read endpoints for operator monitoring.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from ..config import STMAConfig, get_config
from ..database.db import get_db
from ..database.models import (
    AccountStatus,
    Cycle,
    PositionSnapshot,
    RiskEvent,
    Command,
    SystemEvent,
)
from ..core.schemas import (
    AccountSummary,
    CycleSummary,
    SystemStateResponse,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


@router.get("/state", response_model=SystemStateResponse)
def get_system_state(
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
):
    """Overall system state."""
    active_cycles = (
        db.query(Cycle)
        .filter(Cycle.state.notin_(["COMPLETED", "FAILED_REQUIRES_OPERATOR"]))
        .count()
    )

    unresolved_protection = (
        db.query(RiskEvent)
        .filter(
            RiskEvent.event_type == "PROTECTION_TRIGGERED",
            RiskEvent.resolved == False,
        )
        .count()
    )

    return SystemStateResponse(
        environment=cfg.app.environment,
        live_trading_enabled=cfg.app.live_trading_enabled,
        strategy_enabled=True,  # Will be controlled via system_state table in future
        protection_active=unresolved_protection > 0,
        active_cycles=active_cycles,
        last_updated=_utcnow(),
    )


@router.get("/accounts", response_model=List[AccountSummary])
def get_accounts(
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
):
    """Account summaries with connection status and risk metrics."""
    summaries = []
    for account in cfg.accounts:
        status: Optional[AccountStatus] = (
            db.query(AccountStatus)
            .filter(AccountStatus.account_alias == account.alias)
            .first()
        )

        stale = True
        data_age = None
        last_updated = None

        if status:
            last_updated = status.last_updated
            if last_updated.tzinfo is None:
                last_updated = last_updated.replace(tzinfo=timezone.utc)
            data_age = (_utcnow() - last_updated).total_seconds()
            stale = data_age > cfg.risk.account_stale_threshold_seconds or not status.connected

        summaries.append(AccountSummary(
            alias=account.alias,
            role=account.role,
            connected=status.connected if status else False,
            balance=account.normalize_amount(status.balance) if status and status.balance is not None else None,
            equity=account.normalize_amount(status.equity) if status and status.equity is not None else None,
            free_margin=account.normalize_amount(status.free_margin) if status and status.free_margin is not None else None,
            margin_level_pct=status.margin_level_pct if status else None,
            floating_pnl=account.normalize_amount(status.floating_pnl) if status and status.floating_pnl is not None else None,
            open_positions=status.open_positions if status else 0,
            currency=account.currency,
            is_cent=account.is_cent,
            last_updated=last_updated,
            data_age_seconds=data_age,
            is_stale=stale,
        ))

    return summaries


@router.get("/cycles", response_model=List[CycleSummary])
def get_active_cycles(
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    include_completed: bool = False,
):
    """List active (or all) cycles."""
    query = db.query(Cycle)
    if not include_completed:
        query = query.filter(Cycle.state.notin_(["COMPLETED", "FAILED_REQUIRES_OPERATOR"]))

    cycles = query.order_by(Cycle.started_at.desc()).limit(50).all()

    results = []
    for cycle in cycles:
        total_positions = (
            db.query(PositionSnapshot)
            .filter(
                PositionSnapshot.cycle_id == cycle.id,
                PositionSnapshot.is_managed == True,
            )
            .count()
        )

        floating_pnl = (
            db.query(PositionSnapshot)
            .filter(
                PositionSnapshot.cycle_id == cycle.id,
                PositionSnapshot.is_managed == True,
            )
            .all()
        )
        total_floating = sum(
            (p.floating_pnl or 0) for p in floating_pnl
        )

        started_at = cycle.started_at
        if started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)

        results.append(CycleSummary(
            cycle_id=cycle.id,
            strategy_id=cycle.strategy_id,
            symbol=cycle.symbol,
            direction=cycle.direction,
            state=cycle.state,
            started_at=started_at,
            total_positions=total_positions,
            floating_pnl_usd=total_floating,
            realized_pnl_usd=cycle.realized_pnl_usd or 0.0,
        ))

    return results


@router.get("/positions")
def get_positions(
    db: Session = Depends(get_db),
    account_alias: Optional[str] = None,
):
    """Get current managed position snapshots."""
    query = db.query(PositionSnapshot).filter(PositionSnapshot.is_managed == True)
    if account_alias:
        query = query.filter(PositionSnapshot.account_alias == account_alias)
    positions = query.all()
    return [
        {
            "account_alias": p.account_alias,
            "position_ticket": p.position_ticket,
            "symbol": p.symbol,
            "direction": p.direction,
            "volume": p.volume,
            "open_price": p.open_price,
            "current_price": p.current_price,
            "tp": p.tp,
            "sl": p.sl,
            "floating_pnl": p.floating_pnl,
            "cycle_id": p.cycle_id,
            "last_seen": p.last_seen.isoformat() if p.last_seen else None,
        }
        for p in positions
    ]


@router.get("/risk-events")
def get_risk_events(
    db: Session = Depends(get_db),
    unresolved_only: bool = True,
):
    """Get recent risk events."""
    query = db.query(RiskEvent)
    if unresolved_only:
        query = query.filter(RiskEvent.resolved == False)
    events = query.order_by(RiskEvent.created_at.desc()).limit(100).all()
    return [
        {
            "id": e.id,
            "account_alias": e.account_alias,
            "event_type": e.event_type,
            "severity": e.severity,
            "detail": e.detail,
            "requires_operator": e.requires_operator,
            "resolved": e.resolved,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]


@router.get("/commands")
def get_recent_commands(
    db: Session = Depends(get_db),
    limit: int = 50,
):
    """Get recent commands for audit."""
    cmds = (
        db.query(Command)
        .order_by(Command.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": c.id,
            "cycle_id": c.cycle_id,
            "account_alias": c.account_alias,
            "action": c.action,
            "state": c.state,
            "volume": c.volume,
            "layer": c.layer,
            "created_at": c.created_at.isoformat() if c.created_at else None,
        }
        for c in cmds
    ]


@router.get("/system-events")
def get_system_events(db: Session = Depends(get_db), limit: int = 50):
    """Get recent system events (connectivity, errors, reconciliation)."""
    events = (
        db.query(SystemEvent)
        .order_by(SystemEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id": e.id,
            "account_alias": e.account_alias,
            "event_type": e.event_type,
            "detail": e.detail,
            "created_at": e.created_at.isoformat() if e.created_at else None,
        }
        for e in events
    ]
