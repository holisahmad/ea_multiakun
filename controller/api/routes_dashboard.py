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
    Execution,
    Signal,
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
        .filter(Cycle.state.notin_(["COMPLETED", "FAILED_REQUIRES_OPERATOR", "CANCELLED", "FAILED"]))
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
            
            # If account heartbeat is stale, mark as disconnected in DB cache
            if stale and status.connected:
                status.connected = False
                db.add(status)
                db.commit()

        is_connected = bool(status and status.connected and not stale)

        bal = account.normalize_amount(status.balance) if status and status.balance is not None else None
        acc_realized = round(bal - 1000.0, 2) if bal is not None else None

        summaries.append(AccountSummary(
            alias=account.alias,
            role=account.role,
            connected=is_connected,
            balance=bal,
            equity=account.normalize_amount(status.equity) if status and status.equity is not None else None,
            free_margin=account.normalize_amount(status.free_margin) if status and status.free_margin is not None else None,
            margin_level_pct=status.margin_level_pct if status else None,
            floating_pnl=account.normalize_amount(status.floating_pnl) if status and status.floating_pnl is not None else None,
            realized_pnl=acc_realized,
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
        query = query.filter(Cycle.state.notin_(["COMPLETED", "FAILED_REQUIRES_OPERATOR", "CANCELLED", "FAILED"]))

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
    cfg: STMAConfig = Depends(get_config),
    limit: int = 50,
):
    """Get recent commands for audit with clear origin and role attribution."""
    cmds = (
        db.query(Command)
        .order_by(Command.created_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for c in cmds:
        cy = c.cycle
        sig = cy.signal if cy else None
        source_str = (sig.source if sig else "") or (c.payload_json or "")
        is_manual = "manual" in source_str.lower() or "dashboard" in source_str.lower()
        
        acc = cfg.get_account(c.account_alias)
        role = acc.role if acc else "unknown"

        result.append({
            "id": c.id,
            "cycle_id": c.cycle_id,
            "account_alias": c.account_alias,
            "account_name": "Akun 1 (Primary)" if c.account_alias == "account_1" else ("Akun 2 (Secondary)" if c.account_alias == "account_2" else "Akun 3 (Tertiary)"),
            "account_role": role,
            "action": c.action,
            "state": c.state,
            "volume": c.volume,
            "tp_points": c.tp_points,
            "sl_points": c.sl_points,
            "layer": c.layer,
            "source_type": "MANUAL" if is_manual else "BOT",
            "created_at": c.created_at.isoformat() if c.created_at else None,
        })
    return result


@router.get("/trades-history")
def get_trades_history(
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    limit: int = 50,
):
    """Get list of executed trades from broker with account and origin attribution."""
    execs = (
        db.query(Execution)
        .order_by(Execution.recorded_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for e in execs:
        cmd = e.command
        cy = cmd.cycle if cmd else None
        sig = cy.signal if cy else None
        
        acc = cfg.get_account(e.account_alias)
        role = acc.role if acc else "unknown"
        
        source = (sig.source if sig else "") or (cmd.payload_json if cmd else "")
        is_manual = "manual" in source.lower() or "dashboard" in source.lower()
        source_type = "MANUAL" if is_manual else "BOT"

        action = cmd.action if cmd else "UNKNOWN"
        direction = "BUY" if "BUY" in action else ("SELL" if "SELL" in action else "CLOSE")

        result.append({
            "id": e.id,
            "account_alias": e.account_alias,
            "account_name": "Akun 1 (Primary)" if e.account_alias == "account_1" else ("Akun 2 (Secondary)" if e.account_alias == "account_2" else "Akun 3 (Tertiary)"),
            "account_role": role,
            "order_ticket": e.order_ticket,
            "deal_ticket": e.deal_ticket,
            "action": action,
            "direction": direction,
            "layer": cmd.layer if cmd else 1,
            "volume": e.executed_volume or (cmd.volume if cmd else 0.01),
            "executed_price": e.executed_price,
            "tp_points": cmd.tp_points if cmd else None,
            "sl_points": cmd.sl_points if cmd else None,
            "status": e.status,
            "retcode": e.retcode,
            "retcode_desc": "Berhasil (Done)" if e.retcode == 10009 else f"Kode {e.retcode}",
            "source_type": source_type,
            "source_raw": sig.source if sig else "manual",
            "recorded_at": e.recorded_at.isoformat() if e.recorded_at else None,
        })
    return result


@router.get("/signals")
def get_signals_log(
    db: Session = Depends(get_db),
    limit: int = 50,
):
    """Get recent trading signals log with acceptance and rejection details."""
    signals = (
        db.query(Signal)
        .order_by(Signal.received_at.desc())
        .limit(limit)
        .all()
    )
    result = []
    for s in signals:
        is_manual = "manual" in (s.source or "").lower() or "dashboard" in (s.source or "").lower()
        result.append({
            "id": s.id,
            "symbol": s.symbol,
            "direction": s.direction,
            "source": s.source,
            "source_type": "MANUAL" if is_manual else "BOT",
            "status": s.status,
            "reject_reason": s.reject_reason,
            "received_at": s.received_at.isoformat() if s.received_at else None,
        })
    return result


@router.get("/performance")
def get_trading_performance(
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
):
    """Get today's trading summary and per-account performance matrix."""
    today_utc = _utcnow().replace(hour=0, minute=0, second=0, microsecond=0)
    
    execs_today = (
        db.query(Execution)
        .filter(Execution.recorded_at >= today_utc)
        .all()
    )
    # If today's list is empty, also count last 24h
    if not execs_today:
        execs_today = db.query(Execution).order_by(Execution.recorded_at.desc()).limit(30).all()

    total_trades_today = len(execs_today)
    total_vol_today = sum(e.executed_volume or 0 for e in execs_today)
    buy_trades = sum(1 for e in execs_today if e.command and "BUY" in e.command.action)
    sell_trades = sum(1 for e in execs_today if e.command and "SELL" in e.command.action)

    completed_cycles_today = (
        db.query(Cycle)
        .filter(Cycle.state == "COMPLETED")
        .order_by(Cycle.ended_at.desc())
        .limit(20)
        .all()
    )
    cycle_realized = sum(c.realized_pnl_usd or 0.0 for c in completed_cycles_today)

    acc_matrix = []
    total_floating_pnl = 0.0
    total_balance_delta = 0.0
    for account in cfg.accounts:
        st = db.query(AccountStatus).filter(AccountStatus.account_alias == account.alias).first()
        float_pnl = account.normalize_amount(st.floating_pnl) if st and st.floating_pnl is not None else 0.0
        total_floating_pnl += float_pnl

        bal = account.normalize_amount(st.balance) if st and st.balance is not None else 1000.0
        acc_realized = round(bal - 1000.0, 2)
        total_balance_delta += acc_realized
        
        acc_trades_cnt = sum(1 for e in execs_today if e.account_alias == account.alias)
        
        acc_matrix.append({
            "alias": account.alias,
            "name": "Akun 1 (Primary)" if account.alias == "account_1" else ("Akun 2 (Secondary)" if account.alias == "account_2" else "Akun 3 (Tertiary)"),
            "role": account.role,
            "connected": bool(st and st.connected),
            "balance": bal,
            "equity": account.normalize_amount(st.equity) if st and st.equity is not None else bal,
            "floating_pnl": round(float_pnl, 2),
            "realized_pnl": acc_realized,
            "margin_level_pct": st.margin_level_pct if st else 0.0,
            "open_positions": st.open_positions if st else 0,
            "trades_count": acc_trades_cnt,
        })

    realized_pnl_today = cycle_realized if abs(cycle_realized) > 0.001 else total_balance_delta

    active_cycles_count = (
        db.query(Cycle)
        .filter(Cycle.state.notin_(["COMPLETED", "FAILED_REQUIRES_OPERATOR", "CANCELLED", "FAILED"]))
        .count()
    )

    return {
        "total_trades_today": total_trades_today,
        "buy_trades_today": buy_trades,
        "sell_trades_today": sell_trades,
        "total_volume_today": round(total_vol_today, 4),
        "total_floating_pnl": round(total_floating_pnl, 2),
        "total_realized_pnl": round(realized_pnl_today, 2),
        "net_pnl_today": round(total_floating_pnl + realized_pnl_today, 2),
        "accounts": acc_matrix,
        "active_cycles_count": active_cycles_count,
        "timestamp_utc": _utcnow().isoformat(),
    }


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
