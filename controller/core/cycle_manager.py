"""
core/cycle_manager.py — Cycle state machine for STMA.

Manages the lifecycle of a trading cycle from IDLE to COMPLETED.
State transitions are explicit, persisted, and auditable.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..config import STMAConfig, AccountConfig
from ..database.models import Cycle, Command, Signal, StateEvent, SystemEvent, PositionSnapshot
from .risk_engine import approve_entry

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Valid state transitions
# ---------------------------------------------------------------------------
VALID_TRANSITIONS: dict[str, list[str]] = {
    "IDLE": ["SIGNAL_VALIDATED"],
    "SIGNAL_VALIDATED": ["ACCOUNT_1_PENDING", "ACCOUNT_2_PENDING", "ACCOUNT_3_PENDING", "FAILED_REQUIRES_OPERATOR"],
    "ACCOUNT_1_PENDING": ["ACCOUNT_1_ACTIVE", "FAILED_REQUIRES_OPERATOR", "RECOVERY", "CLOSING", "COMPLETED"],
    "ACCOUNT_1_ACTIVE": [
        "ACCOUNT_2_ELIGIBLE",
        "ACCOUNT_2_PENDING",
        "ACCOUNT_3_PENDING",
        "CLOSING",
        "PROTECTION",
        "RECOVERY",
        "FAILED_REQUIRES_OPERATOR",
        "COMPLETED",
    ],
    "ACCOUNT_2_ELIGIBLE": ["ACCOUNT_2_PENDING", "ACCOUNT_1_ACTIVE", "CLOSING", "PROTECTION", "COMPLETED"],
    "ACCOUNT_2_PENDING": ["ACCOUNT_2_ACTIVE", "ACCOUNT_1_ACTIVE", "FAILED_REQUIRES_OPERATOR", "RECOVERY", "CLOSING", "COMPLETED"],
    "ACCOUNT_2_ACTIVE": [
        "ACCOUNT_3_ELIGIBLE",
        "ACCOUNT_3_PENDING",
        "CLOSING",
        "PROTECTION",
        "RECOVERY",
        "FAILED_REQUIRES_OPERATOR",
        "COMPLETED",
    ],
    "ACCOUNT_3_ELIGIBLE": ["ACCOUNT_3_PENDING", "ACCOUNT_2_ACTIVE", "CLOSING", "PROTECTION", "COMPLETED"],
    "ACCOUNT_3_PENDING": ["ACCOUNT_3_ACTIVE", "ACCOUNT_2_ACTIVE", "FAILED_REQUIRES_OPERATOR", "RECOVERY", "CLOSING", "COMPLETED"],
    "ACCOUNT_3_ACTIVE": ["CLOSING", "PROTECTION", "RECOVERY", "FAILED_REQUIRES_OPERATOR", "COMPLETED"],
    "CLOSING": ["RECONCILIATION", "COMPLETED", "FAILED_REQUIRES_OPERATOR"],
    "RECONCILIATION": ["COMPLETED", "FAILED_REQUIRES_OPERATOR"],
    "RECOVERY": ["ACCOUNT_1_ACTIVE", "ACCOUNT_2_ACTIVE", "ACCOUNT_3_ACTIVE", "FAILED_REQUIRES_OPERATOR"],
    "PROTECTION": ["CLOSING", "FAILED_REQUIRES_OPERATOR"],
    "COMPLETED": [],
    "FAILED_REQUIRES_OPERATOR": [],
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# State transition helper
# ---------------------------------------------------------------------------
def transition_cycle_state(
    db: Session,
    cycle: Cycle,
    new_state: str,
    trigger: Optional[str] = None,
    detail: Optional[str] = None,
) -> None:
    """
    Transition cycle to new_state with full validation and audit trail.
    Raises ValueError if transition is not valid.
    """
    valid_next = VALID_TRANSITIONS.get(cycle.state, [])
    if new_state not in valid_next:
        raise ValueError(
            f"Invalid state transition: {cycle.state} → {new_state}. "
            f"Allowed: {valid_next}"
        )

    old_state = cycle.state

    # Persist state event BEFORE changing cycle state
    state_event = StateEvent(
        cycle_id=cycle.id,
        from_state=old_state,
        to_state=new_state,
        trigger=trigger,
        detail=detail,
    )
    db.add(state_event)

    # Update cycle
    cycle.state = new_state
    if new_state in ("COMPLETED", "FAILED_REQUIRES_OPERATOR"):
        cycle.ended_at = _utcnow()

    db.add(cycle)
    db.commit()

    logger.info(
        "Cycle state transition | cycle_id=%s %s → %s trigger=%s",
        cycle.id, old_state, new_state, trigger,
    )


# ---------------------------------------------------------------------------
# Create new cycle from a validated signal
# ---------------------------------------------------------------------------
def create_cycle(
    db: Session,
    cfg: STMAConfig,
    signal: Signal,
) -> Cycle:
    """Create a new cycle and transition it from IDLE → SIGNAL_VALIDATED."""
    cycle = Cycle(
        strategy_id=cfg.strategy.strategy_id,
        signal_id=signal.id,
        symbol=cfg.strategy.symbol.upper(),
        direction=signal.direction,
        state="IDLE",
    )
    db.add(cycle)
    db.commit()
    db.refresh(cycle)

    # Mark signal as USED
    signal.status = "USED"
    db.add(signal)
    db.commit()

    transition_cycle_state(
        db, cycle,
        new_state="SIGNAL_VALIDATED",
        trigger="signal_accepted",
        detail=f"signal_id={signal.id} source={signal.source}",
    )
    return cycle


# ---------------------------------------------------------------------------
# Command factory helpers
# ---------------------------------------------------------------------------
def _make_command(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    account: AccountConfig,
    action: str,
    layer: int,
) -> Command:
    """Create and persist a pending command. Does NOT dispatch it."""
    now = _utcnow()
    cmd_symbol = account.symbol or cycle.symbol

    # Check for custom tp/sl/volume overrides from signal payload
    custom_tp = None
    custom_sl = None
    custom_vol = None
    if cycle and cycle.signal and cycle.signal.payload_json:
        try:
            sig_payload = json.loads(cycle.signal.payload_json)
            if isinstance(sig_payload, dict):
                if "tp_points" in sig_payload and sig_payload["tp_points"] is not None:
                    custom_tp = int(sig_payload["tp_points"])
                if "sl_points" in sig_payload and sig_payload["sl_points"] is not None:
                    custom_sl = int(sig_payload["sl_points"])
                if "volume" in sig_payload and sig_payload["volume"] is not None:
                    custom_vol = float(sig_payload["volume"])
        except Exception:
            pass

    final_tp = custom_tp if custom_tp is not None else (cfg.strategy.individual_tp_points if cfg.strategy.tp_mode == "individual" else None)
    final_sl = custom_sl if custom_sl is not None else (cfg.strategy.individual_sl_points if cfg.strategy.individual_sl_enabled else None)
    final_vol = custom_vol if custom_vol is not None and custom_vol > 0 else account.default_lot

    cmd = Command(
        cycle_id=cycle.id,
        account_alias=account.alias,
        action=action,
        layer=layer,
        symbol=cmd_symbol,
        volume=final_vol,
        tp_points=final_tp,
        sl_points=final_sl,
        state="PENDING",
        created_at=now,
        expires_at=now + timedelta(seconds=cfg.server.max_command_age_seconds),
        payload_json=json.dumps({
            "strategy_id": cfg.strategy.strategy_id,
            "cycle_id": cycle.id,
            "account_alias": account.alias,
            "action": action,
            "layer": layer,
            "symbol": cmd_symbol,
            "volume": final_vol,
            "tp_points": final_tp or 0,
            "sl_points": final_sl or 0,
            "deviation_points": cfg.strategy.deviation_points,
            "magic_number": account.magic_number,
            "created_at_utc": now.isoformat(),
            "expires_at_utc": (now + timedelta(seconds=cfg.server.max_command_age_seconds)).isoformat(),
        }),
    )
    db.add(cmd)
    db.commit()
    db.refresh(cmd)
    logger.info(
        "Command created | cmd_id=%s account=%s action=%s cycle=%s layer=%d",
        cmd.id, account.alias, action, cycle.id, layer,
    )
    return cmd


# ---------------------------------------------------------------------------
# Stage 1: submit entry for primary account
# ---------------------------------------------------------------------------
def initiate_stage1(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    spread_points: Optional[float] = None,
) -> Command:
    """
    Evaluate risk and create OPEN command for primary account.
    Transitions cycle: SIGNAL_VALIDATED → ACCOUNT_1_PENDING.
    """
    primary = cfg.primary_account()
    if primary is None:
        raise RuntimeError("No primary account configured")

    risk_result = approve_entry(db, cfg, primary, spread_points)
    if not risk_result.allowed:
        transition_cycle_state(
            db, cycle, "FAILED_REQUIRES_OPERATOR",
            trigger="risk_denied_stage1",
            detail="; ".join(risk_result.reasons_denied),
        )
        raise RuntimeError(
            f"Risk engine denied stage 1 entry: {risk_result.reasons_denied}"
        )

    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, primary, action, layer=1)

    transition_cycle_state(
        db, cycle, "ACCOUNT_1_PENDING",
        trigger="stage1_command_created",
        detail=f"cmd_id={cmd.id}",
    )
    return cmd


def initiate_manual_entry(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    target_alias: str = "account_1",
    spread_points: Optional[float] = None,
) -> Command:
    """
    Evaluate risk and create OPEN command for the chosen account (account_1, account_2, or account_3).
    """
    account = cfg.get_account(target_alias)
    if account is None:
        account = cfg.primary_account()
    if account is None:
        raise RuntimeError(f"Target account {target_alias} not found or configured")

    risk_result = approve_entry(db, cfg, account, spread_points)
    if not risk_result.allowed:
        if cycle.state == "SIGNAL_VALIDATED":
            transition_cycle_state(
                db, cycle, "FAILED_REQUIRES_OPERATOR",
                trigger=f"risk_denied_{account.alias}",
                detail="; ".join(risk_result.reasons_denied),
            )
        raise RuntimeError(
            f"Risk engine menolak entry di {account.alias}: {'; '.join(risk_result.reasons_denied)}"
        )

    layer = 1
    pending_state = "ACCOUNT_1_PENDING"
    if account.alias == "account_2" or account.role == "secondary":
        layer = 2
        pending_state = "ACCOUNT_2_PENDING"
    elif account.alias == "account_3" or account.role == "tertiary":
        layer = 3
        pending_state = "ACCOUNT_3_PENDING"

    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, account, action, layer=layer)

    transition_cycle_state(
        db, cycle, pending_state,
        trigger=f"manual_{account.alias}_command_created",
        detail=f"cmd_id={cmd.id}",
    )
    return cmd


# ---------------------------------------------------------------------------
# Stage 2: evaluate and submit entry for secondary account
# ---------------------------------------------------------------------------
def evaluate_stage2(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    current_price: float,
    stage1_entry_price: float,
    spread_points: Optional[float] = None,
    support_confirmed: bool = False,
) -> Optional[Command]:
    """
    Evaluate stage 2 eligibility.

    Returns a Command if eligible and approved, or None if not eligible yet.
    """
    if cycle.state not in ("ACCOUNT_1_ACTIVE", "ACCOUNT_2_ELIGIBLE"):
        logger.debug("Stage 2 not evaluable in state %s", cycle.state)
        return None

    secondary = cfg.secondary_account()
    if secondary is None or not secondary.enabled:
        logger.info("No secondary account configured; skipping stage 2")
        return None

    # Price distance check
    if cycle.direction == "BUY":
        distance = (stage1_entry_price - current_price) / _get_point_value()
    else:  # SELL
        distance = (current_price - stage1_entry_price) / _get_point_value()

    if distance < cfg.strategy.stage2_distance_points:
        logger.debug(
            "Stage 2 distance not reached: %.1f < %d points",
            distance, cfg.strategy.stage2_distance_points,
        )
        # Transition to ELIGIBLE marker if not already
        if cycle.state == "ACCOUNT_1_ACTIVE":
            # Not yet eligible — wait
            pass
        return None

    # Mark eligible
    if cycle.state == "ACCOUNT_1_ACTIVE":
        transition_cycle_state(
            db, cycle, "ACCOUNT_2_ELIGIBLE",
            trigger="stage2_distance_reached",
            detail=f"distance={distance:.1f} pts price={current_price}",
        )

    # Hybrid confirmation check
    if cfg.strategy.entry_model == "hybrid" and not support_confirmed:
        logger.info("Stage 2 distance reached but support confirmation not yet met")
        return None

    # Risk check
    risk_result = approve_entry(db, cfg, secondary, spread_points)
    if not risk_result.allowed:
        logger.warning("Stage 2 risk denied: %s", risk_result.reasons_denied)
        return None

    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, secondary, action, layer=1)

    transition_cycle_state(
        db, cycle, "ACCOUNT_2_PENDING",
        trigger="stage2_command_created",
        detail=f"cmd_id={cmd.id}",
    )
    return cmd


# ---------------------------------------------------------------------------
# Stage 3: evaluate and submit entry for tertiary account
# ---------------------------------------------------------------------------
def evaluate_stage3(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    current_price: float,
    stage2_entry_price: float,
    spread_points: Optional[float] = None,
    support_confirmed: bool = False,
) -> Optional[Command]:
    """Evaluate stage 3 eligibility. Mirror of evaluate_stage2."""
    if cycle.state not in ("ACCOUNT_2_ACTIVE", "ACCOUNT_3_ELIGIBLE"):
        return None

    tertiary = cfg.tertiary_account()
    if tertiary is None or not tertiary.enabled:
        return None

    if cycle.direction == "BUY":
        distance = (stage2_entry_price - current_price) / _get_point_value()
    else:
        distance = (current_price - stage2_entry_price) / _get_point_value()

    if distance < cfg.strategy.stage3_distance_points:
        return None

    if cycle.state == "ACCOUNT_2_ACTIVE":
        transition_cycle_state(
            db, cycle, "ACCOUNT_3_ELIGIBLE",
            trigger="stage3_distance_reached",
            detail=f"distance={distance:.1f} pts price={current_price}",
        )

    if cfg.strategy.entry_model == "hybrid" and not support_confirmed:
        return None

    risk_result = approve_entry(db, cfg, tertiary, spread_points)
    if not risk_result.allowed:
        logger.warning("Stage 3 risk denied: %s", risk_result.reasons_denied)
        return None

    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, tertiary, action, layer=1)

    transition_cycle_state(
        db, cycle, "ACCOUNT_3_PENDING",
        trigger="stage3_command_created",
        detail=f"cmd_id={cmd.id}",
    )
    return cmd


# ---------------------------------------------------------------------------
# Acknowledge stage n active
# ---------------------------------------------------------------------------
def mark_account_active(db: Session, cycle: Cycle, stage: int) -> None:
    state_map = {1: "ACCOUNT_1_ACTIVE", 2: "ACCOUNT_2_ACTIVE", 3: "ACCOUNT_3_ACTIVE"}
    new_state = state_map.get(stage)
    if new_state is None:
        raise ValueError(f"Invalid stage number: {stage}")
    transition_cycle_state(
        db, cycle, new_state,
        trigger=f"stage{stage}_execution_confirmed",
    )


# ---------------------------------------------------------------------------
# Initiate close of all managed positions in this cycle
# ---------------------------------------------------------------------------
def initiate_close(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    reason: str = "operator_request",
) -> list[Command]:
    """Create CLOSE commands for all accounts that have positions in this cycle."""
    if cycle.state in ("COMPLETED", "FAILED_REQUIRES_OPERATOR", "CLOSING"):
        logger.info("Cycle %s already in %s; skipping close", cycle.id, cycle.state)
        return []

    transition_cycle_state(
        db, cycle, "CLOSING",
        trigger=reason,
    )

    cmds: list[Command] = []
    for account in cfg.accounts:
        if not account.enabled:
            continue
        # Check if account has managed positions in this cycle
        pos = (
            db.query(PositionSnapshot)
            .filter(
                PositionSnapshot.account_alias == account.alias,
                PositionSnapshot.cycle_id == cycle.id,
                PositionSnapshot.is_managed == True,
            )
            .first()
        )
        if pos is None:
            continue

        cmd = _make_command(db, cfg, cycle, account, "CLOSE_ALL_CYCLE", layer=0)
        cmds.append(cmd)

    return cmds


# ---------------------------------------------------------------------------
# Utility: get point value for XAUUSD
# ---------------------------------------------------------------------------
def _get_point_value() -> float:
    """
    Return the point size (minimum price move) for XAUUSD.
    For Exness/standard brokers: XAUUSD point = 0.01 USD
    This MUST be verified against the actual broker's symbol specifications.

    NOTE: Do NOT hardcode this for production. Fetch from EA's SymbolInfoDouble.
    """
    return 0.01  # XAUUSD: 1 point = 0.01 USD (verify with your broker)
