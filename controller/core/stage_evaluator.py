"""
core/stage_evaluator.py — Stage 2/3 eligibility evaluator.

Runs periodically to check if active cycles qualify for stage escalation.
Decoupled from HTTP request cycle for clean separation.
"""
from __future__ import annotations

import logging
from datetime import timezone
from typing import Optional

from sqlalchemy.orm import Session

from ..config import STMAConfig
from ..database.models import Cycle, PositionSnapshot, AccountStatus
from .cycle_manager import evaluate_stage2, evaluate_stage3

logger = logging.getLogger(__name__)


def _utcnow():
    from datetime import datetime
    return datetime.now(timezone.utc)


def run_stage_evaluation(
    db: Session,
    cfg: STMAConfig,
    current_prices: dict[str, float],  # {"XAUUSD": 1985.50}
    spread_points: Optional[float] = None,
    support_levels: Optional[list[float]] = None,
) -> dict:
    """
    Evaluate all active cycles for stage 2/3 progression.

    Args:
        current_prices: Latest price per symbol from EA market data push.
        spread_points: Current spread in points for the symbol.
        support_levels: Known support price levels (from EA indicator).

    Returns a summary of actions taken.
    """
    symbol = cfg.strategy.symbol.upper()
    current_price = current_prices.get(symbol)
    # Auto-complete cycles that have 0 open positions and no pending commands
    from ..database.models import Command
    from .cycle_manager import transition_cycle_state
    for active_c in db.query(Cycle).filter(Cycle.state.notin_(["COMPLETED", "FAILED_REQUIRES_OPERATOR", "CANCELLED"])).all():
        pos_cnt = db.query(PositionSnapshot).filter(PositionSnapshot.cycle_id == active_c.id, PositionSnapshot.is_managed == True).count()
        cmd_cnt = db.query(Command).filter(Command.cycle_id == active_c.id, Command.state.in_(["PENDING", "SENT"])).count()
        
        started_at = active_c.started_at
        if started_at and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        age_seconds = (_utcnow() - started_at).total_seconds() if started_at else 999.0

        # Only auto-complete if in CLOSING state or after 15s grace period
        if pos_cnt == 0 and cmd_cnt == 0 and (active_c.state == "CLOSING" or age_seconds > 15.0):
            logger.info("Cycle %s has 0 open positions (age=%.1fs) — marking COMPLETED", active_c.id, age_seconds)
            transition_cycle_state(db, active_c, "COMPLETED", trigger="all_positions_closed", detail="All managed positions closed")

    if current_price is None:
        return {"skipped": True, "reason": "no_price"}

    actions = []

    # Get all cycles eligible for stage evaluation
    active_cycles = (
        db.query(Cycle)
        .filter(
            Cycle.symbol == symbol,
            Cycle.state.in_([
                "ACCOUNT_1_ACTIVE",
                "ACCOUNT_2_ELIGIBLE",
                "ACCOUNT_2_ACTIVE",
                "ACCOUNT_3_ELIGIBLE",
            ]),
        )
        .all()
    )

    for cycle in active_cycles:
        action = _evaluate_cycle(
            db, cfg, cycle, current_price, spread_points, support_levels
        )
        if action:
            actions.append(action)

    return {"evaluated_cycles": len(active_cycles), "actions": actions}


def _evaluate_cycle(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    current_price: float,
    spread_points: Optional[float],
    support_levels: Optional[list[float]],
) -> Optional[dict]:
    # Check if cycle has finished (all positions closed via TP/SL)
    if cycle.state in ("ACCOUNT_1_ACTIVE", "ACCOUNT_2_ACTIVE", "ACCOUNT_3_ACTIVE"):
        from ..database.models import Command
        pos_count = (
            db.query(PositionSnapshot)
            .filter(PositionSnapshot.cycle_id == cycle.id, PositionSnapshot.is_managed == True)
            .count()
        )
        pending_cmd_count = (
            db.query(Command)
            .filter(Command.cycle_id == cycle.id, Command.state.in_(["PENDING", "SENT"]))
            .count()
        )
        started_at = cycle.started_at
        if started_at and started_at.tzinfo is None:
            started_at = started_at.replace(tzinfo=timezone.utc)
        age_seconds = (_utcnow() - started_at).total_seconds() if started_at else 999.0

        if pos_count == 0 and pending_cmd_count == 0 and (cycle.state == "CLOSING" or age_seconds > 15.0):
            logger.info("Cycle %s has 0 open positions (age=%.1fs) — marking COMPLETED", cycle.id, age_seconds)
            from .cycle_manager import transition_cycle_state
            transition_cycle_state(db, cycle, "COMPLETED", trigger="all_positions_closed", detail="All managed positions closed")
            return {"cycle_id": cycle.id, "action": "cycle_completed"}

    # Find stage 1 entry price from position snapshot
    stage1_price = _get_entry_price(db, cycle.id, cfg.primary_account().alias if cfg.primary_account() else None)
    stage2_price = _get_entry_price(db, cycle.id, cfg.secondary_account().alias if cfg.secondary_account() else None)

    # Check support confirmation
    support_confirmed = False
    if support_levels and cfg.strategy.entry_model == "hybrid":
        support_confirmed = _check_support_confirmation(
            current_price=current_price,
            direction=cycle.direction,
            support_levels=support_levels,
            zone_points=cfg.strategy.confirmation_zone_points,
        )

    if cycle.state in ("ACCOUNT_1_ACTIVE", "ACCOUNT_2_ELIGIBLE") and stage1_price:
        cmd = evaluate_stage2(
            db=db,
            cfg=cfg,
            cycle=cycle,
            current_price=current_price,
            stage1_entry_price=stage1_price,
            spread_points=spread_points,
            support_confirmed=support_confirmed,
        )
        if cmd:
            return {"cycle_id": cycle.id, "action": "stage2_command", "cmd_id": cmd.id}

    elif cycle.state in ("ACCOUNT_2_ACTIVE", "ACCOUNT_3_ELIGIBLE") and stage2_price:
        cmd = evaluate_stage3(
            db=db,
            cfg=cfg,
            cycle=cycle,
            current_price=current_price,
            stage2_entry_price=stage2_price,
            spread_points=spread_points,
            support_confirmed=support_confirmed,
        )
        if cmd:
            return {"cycle_id": cycle.id, "action": "stage3_command", "cmd_id": cmd.id}

    return None


def _get_entry_price(
    db: Session,
    cycle_id: str,
    account_alias: Optional[str],
) -> Optional[float]:
    """Get the first open entry price for an account in a cycle."""
    if account_alias is None:
        return None
    pos = (
        db.query(PositionSnapshot)
        .filter(
            PositionSnapshot.cycle_id == cycle_id,
            PositionSnapshot.account_alias == account_alias,
            PositionSnapshot.is_managed == True,
        )
        .first()
    )
    return pos.open_price if pos else None


def _check_support_confirmation(
    current_price: float,
    direction: str,
    support_levels: list[float],
    zone_points: int,
) -> bool:
    """
    Check if current price is near a support/resistance level.

    For BUY: price should be near a support level (price ≤ level + zone).
    For SELL: price should be near a resistance level (price ≥ level - zone).

    NOTE: This is a basic proximity check. Real support detection requires
    proper technical analysis and must be tested independently.
    Point value for XAUUSD = 0.01 USD/point (verify with broker).
    """
    zone_price = zone_points * 0.01  # Convert points to price units (XAUUSD)

    for level in support_levels:
        if direction == "BUY":
            if abs(current_price - level) <= zone_price and current_price <= level + zone_price:
                logger.info(
                    "Support confirmation: price=%.2f near support=%.2f (zone=%.2f)",
                    current_price, level, zone_price,
                )
                return True
        else:  # SELL
            if abs(current_price - level) <= zone_price and current_price >= level - zone_price:
                logger.info(
                    "Resistance confirmation: price=%.2f near resistance=%.2f (zone=%.2f)",
                    current_price, level, zone_price,
                )
                return True

    return False
