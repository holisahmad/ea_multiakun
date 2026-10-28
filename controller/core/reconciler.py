"""
core/reconciler.py — Position reconciliation between controller state and MT5 reality.

The reconciler treats MT5 position data as execution truth.
Controller state is adjusted to match actual broker state.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import List, Optional

from sqlalchemy.orm import Session

from ..config import STMAConfig
from ..database.models import (
    PositionSnapshot,
    Cycle,
    Command,
    SystemEvent,
    RiskEvent,
    ClosedTrade,
)
from .risk_engine import trigger_protection

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Update position snapshot from EA report
# ---------------------------------------------------------------------------
def upsert_positions(
    db: Session,
    cfg: STMAConfig,
    account_alias: str,
    reported_positions: List[dict],
) -> dict:
    """
    Reconcile the DB position snapshot for an account with what the EA reports.

    Returns a summary dict: {new: int, updated: int, removed: int, mismatches: list}
    """
    account = cfg.get_account(account_alias)
    if account is None:
        raise ValueError(f"Unknown account alias: {account_alias}")

    managed_magic_numbers = {a.magic_number for a in cfg.accounts}
    reported_tickets = {int(p["position_ticket"]) for p in reported_positions}

    existing = (
        db.query(PositionSnapshot)
        .filter(PositionSnapshot.account_alias == account_alias)
        .all()
    )
    existing_by_ticket = {p.position_ticket: p for p in existing}

    new_count = updated_count = removed_count = 0
    mismatches: List[str] = []

    # Upsert reported positions
    for pos_data in reported_positions:
        ticket = int(pos_data["position_ticket"])
        magic = pos_data.get("magic_number")
        is_managed = magic in managed_magic_numbers if magic else False

        if ticket in existing_by_ticket:
            snap = existing_by_ticket[ticket]
            snap.current_price = pos_data.get("current_price")
            snap.floating_pnl = pos_data.get("floating_pnl")
            snap.tp = pos_data.get("tp")
            snap.sl = pos_data.get("sl")
            snap.last_seen = _utcnow()
            db.add(snap)
            updated_count += 1
        else:
            snap = PositionSnapshot(
                account_alias=account_alias,
                position_ticket=ticket,
                symbol=pos_data.get("symbol", cfg.strategy.symbol),
                direction=pos_data.get("direction", "BUY"),
                volume=float(pos_data.get("volume", 0)),
                open_price=float(pos_data.get("open_price", 0)),
                current_price=pos_data.get("current_price"),
                tp=pos_data.get("tp"),
                sl=pos_data.get("sl"),
                floating_pnl=pos_data.get("floating_pnl"),
                magic_number=magic,
                comment=pos_data.get("comment"),
                is_managed=is_managed,
                last_seen=_utcnow(),
            )
            db.add(snap)
            new_count += 1

            if is_managed:
                # Attempt to link to open cycle
                active_cycle = (
                    db.query(Cycle)
                    .filter(
                        Cycle.symbol == cfg.strategy.symbol.upper(),
                        Cycle.state.notin_(["COMPLETED", "FAILED_REQUIRES_OPERATOR"]),
                    )
                    .first()
                )
                if active_cycle:
                    snap.cycle_id = active_cycle.id

    affected_cycle_ids = set()
    # Remove positions no longer reported by EA (they were closed)
    for ticket, snap in existing_by_ticket.items():
        if ticket not in reported_tickets:
            if snap.is_managed:
                logger.info(
                    "Position %d on %s no longer reported — assumed closed",
                    ticket, account_alias,
                )
                closed_pnl = snap.floating_pnl or 0.0
                if snap.cycle_id:
                    affected_cycle_ids.add(snap.cycle_id)
                    cy = db.query(Cycle).filter(Cycle.id == snap.cycle_id).first()
                    if cy:
                        cy.realized_pnl_usd = round((cy.realized_pnl_usd or 0.0) + closed_pnl, 2)
                        db.add(cy)

                # Simpan log permanen tiap trade yang selesai (ClosedTrade)
                closed_record = ClosedTrade(
                    cycle_id=snap.cycle_id,
                    account_alias=account_alias,
                    position_ticket=ticket,
                    symbol=snap.symbol,
                    direction=snap.direction,
                    volume=snap.volume,
                    open_price=snap.open_price,
                    close_price=snap.current_price or snap.open_price,
                    realized_pnl=round(closed_pnl, 2),
                    close_reason="CLOSED_BY_BROKER",
                    opened_at=snap.last_seen,
                    closed_at=_utcnow(),
                )
                db.add(closed_record)

                # Catat SystemEvent log agar muncul di tab log aktivitas
                acc_name = "Akun 1 (Primary)" if account_alias == "account_1" else ("Akun 2 (Secondary)" if account_alias == "account_2" else "Akun 3 (Tertiary)")
                pnl_str = f"+${closed_pnl:.2f}" if closed_pnl >= 0 else f"-${abs(closed_pnl):.2f}"
                sys_evt = SystemEvent(
                    account_alias=account_alias,
                    event_type="TRADE_CLOSED",
                    detail=f"{acc_name}: Posisi #{ticket} ({snap.direction} {snap.volume} lot) telah DITUTUP | Realized PnL: {pnl_str} USD",
                    created_at=_utcnow(),
                )
                db.add(sys_evt)

            db.delete(snap)
            removed_count += 1

    db.commit()

    # Auto-complete cycles if all managed positions are closed
    from .cycle_manager import transition_cycle_state
    for cid in affected_cycle_ids:
        remaining = (
            db.query(PositionSnapshot)
            .filter(PositionSnapshot.cycle_id == cid, PositionSnapshot.is_managed == True)
            .count()
        )
        if remaining == 0:
            cycle = db.query(Cycle).filter(Cycle.id == cid).first()
            if cycle and cycle.state in ("ACCOUNT_1_ACTIVE", "ACCOUNT_2_ACTIVE", "ACCOUNT_3_ACTIVE", "CLOSING"):
                logger.info("All positions closed for cycle %s — marking COMPLETED", cid)
                transition_cycle_state(db, cycle, "COMPLETED", trigger="all_positions_closed", detail="All managed positions closed")

    # Log mismatch if managed positions were found on accounts not associated with a cycle
    summary = {
        "new": new_count,
        "updated": updated_count,
        "removed": removed_count,
        "mismatches": mismatches,
    }

    _record_reconciliation_event(db, account_alias, summary)
    return summary


# ---------------------------------------------------------------------------
# Reconcile command state with execution results
# ---------------------------------------------------------------------------
def reconcile_command_state(
    db: Session,
    command_id: str,
    position_ticket: Optional[int],
    status: str,
) -> None:
    """
    Update command state based on execution outcome.
    Only transitions from SENT or ACKNOWLEDGED are valid.
    """
    cmd = db.query(Command).filter(Command.id == command_id).first()
    if cmd is None:
        logger.error("reconcile_command_state: unknown command_id=%s", command_id)
        return

    if cmd.state in ("EXECUTED", "FAILED", "EXPIRED", "REJECTED"):
        logger.warning(
            "Command %s already in terminal state %s; ignoring reconcile",
            command_id, cmd.state,
        )
        return

    if status == "FILLED":
        cmd.state = "EXECUTED"
    elif status in ("REJECTED",):
        cmd.state = "REJECTED"
    elif status in ("TIMEOUT", "UNKNOWN"):
        cmd.state = "FAILED"
        logger.warning(
            "Command %s result is %s — uncertain outcome. "
            "Reconcile actual positions before retrying.",
            command_id, status,
        )
    elif status == "PARTIAL":
        cmd.state = "EXECUTED"
        logger.warning("Command %s partially filled", command_id)

    db.add(cmd)
    db.commit()


# ---------------------------------------------------------------------------
# Verify no managed positions remain for a cycle (for COMPLETED transition)
# ---------------------------------------------------------------------------
def cycle_has_open_positions(db: Session, cycle_id: str) -> bool:
    """Return True if managed positions still exist for this cycle."""
    count = (
        db.query(PositionSnapshot)
        .filter(
            PositionSnapshot.cycle_id == cycle_id,
            PositionSnapshot.is_managed == True,
        )
        .count()
    )
    return count > 0


# ---------------------------------------------------------------------------
# Internal: record reconciliation system event
# ---------------------------------------------------------------------------
def _record_reconciliation_event(
    db: Session,
    account_alias: str,
    summary: dict,
) -> None:
    import json
    event = SystemEvent(
        account_alias=account_alias,
        event_type="RECONCILIATION_OK",
        detail=json.dumps(summary),
    )
    db.add(event)
    db.commit()
