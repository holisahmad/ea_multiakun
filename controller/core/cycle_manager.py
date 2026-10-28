"""
core/cycle_manager.py — Cycle state machine for STMA.

Manages the lifecycle of a trading cycle from IDLE to COMPLETED.
Supports hierarchical multi-layer strategy: each account can have up to
max_layers positions, with account escalation when current account is full.

State flow (multi-layer):
  IDLE → SIGNAL_VALIDATED
  → ACCOUNT_1_PENDING → ACCOUNT_1_ACTIVE   (layer 1,2,3 on same account)
  → ACCOUNT_2_PENDING → ACCOUNT_2_ACTIVE   (activates when acct1 is full)
  → ACCOUNT_3_PENDING → ACCOUNT_3_ACTIVE   (activates when acct2 is full)
  → CLOSING → COMPLETED
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
        "ACCOUNT_1_ACTIVE",    # additional layer on same account
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
        "ACCOUNT_2_ACTIVE",    # additional layer on same account
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
    "ACCOUNT_3_ACTIVE": [
        "ACCOUNT_3_ACTIVE",    # additional layer on same account
        "CLOSING",
        "PROTECTION",
        "RECOVERY",
        "FAILED_REQUIRES_OPERATOR",
        "COMPLETED",
    ],
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
# Layer count helpers
# ---------------------------------------------------------------------------
def get_layer_counts(cycle: Cycle) -> dict[str, int]:
    """Parse layer_counts_json from cycle. Returns {account_alias: count}."""
    if cycle.layer_counts_json:
        try:
            return json.loads(cycle.layer_counts_json)
        except Exception:
            pass
    return {"account_1": 0, "account_2": 0, "account_3": 0}


def set_layer_counts(db: Session, cycle: Cycle, counts: dict[str, int]) -> None:
    """Persist layer counts to cycle."""
    cycle.layer_counts_json = json.dumps(counts)
    db.add(cycle)
    db.commit()


def increment_layer(db: Session, cycle: Cycle, account_alias: str) -> int:
    """Increment layer count for an account. Returns the NEW layer number."""
    counts = get_layer_counts(cycle)
    counts[account_alias] = counts.get(account_alias, 0) + 1
    set_layer_counts(db, cycle, counts)
    return counts[account_alias]


def get_account_layer_count(cycle: Cycle, account_alias: str) -> int:
    """Get current layer count for a specific account in this cycle."""
    return get_layer_counts(cycle).get(account_alias, 0)


def get_max_layers_for_account(cfg: STMAConfig, account_alias: str) -> int:
    """Get configured max layers for an account."""
    if account_alias == "account_1":
        return cfg.strategy.account1_max_layers
    elif account_alias == "account_2":
        return cfg.strategy.account2_max_layers
    elif account_alias == "account_3":
        return cfg.strategy.account3_max_layers
    return 3


def is_account_full(cycle: Cycle, cfg: STMAConfig, account_alias: str) -> bool:
    """Return True if the account has reached its max layers in this cycle."""
    current = get_account_layer_count(cycle, account_alias)
    max_layers = get_max_layers_for_account(cfg, account_alias)
    return current >= max_layers


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
        layer_counts_json=json.dumps({"account_1": 0, "account_2": 0, "account_3": 0}),
        exit_mode=cfg.strategy.exit_mode,
        basket_target_usd=cfg.strategy.basket_target_usd if cfg.strategy.exit_mode == "basket" else None,
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
    explicit_tp = False
    explicit_sl = False

    if cycle and cycle.signal and cycle.signal.payload_json:
        try:
            sig_payload = json.loads(cycle.signal.payload_json)
            if isinstance(sig_payload, dict):
                if "tp_points" in sig_payload:
                    explicit_tp = True
                    val = sig_payload["tp_points"]
                    custom_tp = int(val) if val is not None and int(val) > 0 else 0
                if "sl_points" in sig_payload:
                    explicit_sl = True
                    val = sig_payload["sl_points"]
                    custom_sl = int(val) if val is not None and int(val) > 0 else 0
                if "volume" in sig_payload and sig_payload["volume"] is not None:
                    custom_vol = float(sig_payload["volume"])
        except Exception:
            pass

    # Jika dispesifikasikan (termasuk 0 untuk tanpa TP/SL), hormati nilai tersebut
    if explicit_tp:
        final_tp = custom_tp
    else:
        # Default: TP individual dari config; 0 jika basket mode
        if cfg.strategy.exit_mode == "basket":
            final_tp = None  # Basket mode: tidak set TP per posisi
        else:
            final_tp = cfg.strategy.individual_tp_points

    if explicit_sl:
        final_sl = custom_sl
    else:
        # Default: NO SL (individual_sl_enabled=False by default)
        final_sl = cfg.strategy.individual_sl_points if cfg.strategy.individual_sl_enabled else None

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
# Stage 1: submit entry for primary account (Layer 1)
# ---------------------------------------------------------------------------
def initiate_stage1(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    spread_points: Optional[float] = None,
) -> Command:
    """
    Evaluate risk and create OPEN command for primary account Layer 1.
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

    # Increment layer counter
    layer_num = increment_layer(db, cycle, primary.alias)

    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, primary, action, layer=layer_num)

    transition_cycle_state(
        db, cycle, "ACCOUNT_1_PENDING",
        trigger="stage1_command_created",
        detail=f"cmd_id={cmd.id} layer={layer_num}",
    )
    return cmd


# ---------------------------------------------------------------------------
# initiate_next_layer: add next layer to SAME account (intra-account DCA)
# ---------------------------------------------------------------------------
def initiate_next_layer(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    account: AccountConfig,
    spread_points: Optional[float] = None,
) -> Optional[Command]:
    """
    Add another layer to the SAME account. Called by stage_evaluator when
    adverse price movement threshold is met and signal confirmation is OK.

    Returns Command if approved, None if account is full or risk denied.
    """
    if is_account_full(cycle, cfg, account.alias):
        logger.info(
            "Account %s is at max layers (%d) in cycle %s",
            account.alias, get_max_layers_for_account(cfg, account.alias), cycle.id
        )
        return None

    risk_result = approve_entry(db, cfg, account, spread_points)
    if not risk_result.allowed:
        logger.warning("Next-layer risk denied for %s: %s", account.alias, risk_result.reasons_denied)
        return None

    layer_num = increment_layer(db, cycle, account.alias)
    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, account, action, layer=layer_num)

    # State stays ACCOUNT_X_ACTIVE — just add layer, no state change needed
    # (ACCOUNT_1_ACTIVE → ACCOUNT_1_ACTIVE transition is allowed)
    account_num = "1" if account.role == "primary" else ("2" if account.role == "secondary" else "3")
    current_state = f"ACCOUNT_{account_num}_ACTIVE"
    if cycle.state == current_state:
        # Re-transition to same state for audit trail
        try:
            transition_cycle_state(
                db, cycle, current_state,
                trigger=f"layer{layer_num}_added",
                detail=f"cmd_id={cmd.id} account={account.alias}",
            )
        except ValueError:
            # Same state transition may not be in valid list — just log
            logger.info("Layer %d added to %s (no state change)", layer_num, account.alias)
            db.commit()

    logger.info(
        "Layer %d added to %s in cycle %s | cmd_id=%s",
        layer_num, account.alias, cycle.id, cmd.id
    )
    return cmd


# ---------------------------------------------------------------------------
# Manual entry: user sends order to chosen account
# ---------------------------------------------------------------------------
def initiate_manual_entry(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
    target_alias: str = "account_1",
    spread_points: Optional[float] = None,
) -> Command:
    """
    Evaluate risk and create OPEN command for the chosen account.
    Supports multi-layer: if account already has positions, adds next layer.
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

    # Increment layer for this account
    layer_num = increment_layer(db, cycle, account.alias)

    pending_state = "ACCOUNT_1_PENDING"
    active_state = "ACCOUNT_1_ACTIVE"
    if account.alias == "account_2" or account.role == "secondary":
        pending_state = "ACCOUNT_2_PENDING"
        active_state = "ACCOUNT_2_ACTIVE"
    elif account.alias == "account_3" or account.role == "tertiary":
        pending_state = "ACCOUNT_3_PENDING"
        active_state = "ACCOUNT_3_ACTIVE"

    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, account, action, layer=layer_num)

    # If cycle is already in ACTIVE state for this account, stay in ACTIVE
    if cycle.state == active_state:
        try:
            transition_cycle_state(
                db, cycle, active_state,
                trigger=f"manual_layer{layer_num}_{account.alias}",
                detail=f"cmd_id={cmd.id}",
            )
        except ValueError:
            logger.info("Staying in %s after manual layer add", active_state)
            db.commit()
    else:
        transition_cycle_state(
            db, cycle, pending_state,
            trigger=f"manual_{account.alias}_command_created",
            detail=f"cmd_id={cmd.id} layer={layer_num}",
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
    First try to add another layer to Account 1 (if not full).
    Then escalate to Account 2 if Account 1 is full.

    Returns a Command if eligible and approved, or None if not eligible yet.
    """
    if cycle.state not in ("ACCOUNT_1_ACTIVE", "ACCOUNT_2_ELIGIBLE"):
        logger.debug("Stage 2 not evaluable in state %s", cycle.state)
        return None

    primary = cfg.primary_account()
    secondary = cfg.secondary_account()

    if cycle.direction == "BUY":
        distance = (stage1_entry_price - current_price) / _get_point_value()
    else:  # SELL
        distance = (current_price - stage1_entry_price) / _get_point_value()

    # ── Attempt: add next layer to Account 1 first ──────────────────────────
    if primary and not is_account_full(cycle, cfg, primary.alias):
        layer_dist = cfg.strategy.account1_layer_distance_points
        if distance >= layer_dist:
            # Hybrid confirmation check
            if cfg.strategy.entry_model == "hybrid" and not support_confirmed:
                logger.info(
                    "Account 1 layer distance reached (%.1f pts) but confirmation not met",
                    distance,
                )
                return None
            cmd = initiate_next_layer(db, cfg, cycle, primary, spread_points)
            if cmd:
                return cmd

    # ── Account 1 is full or layer distance not met → try Account 2 escalation ──
    if secondary is None or not secondary.enabled:
        logger.info("No secondary account configured; skipping stage 2 escalation")
        return None

    acct2_dist = cfg.strategy.account2_activate_distance_points
    if distance < acct2_dist:
        logger.debug(
            "Account 2 activation distance not reached: %.1f < %d points",
            distance, acct2_dist,
        )
        if cycle.state == "ACCOUNT_1_ACTIVE":
            pass  # Wait
        return None

    # Mark eligible
    if cycle.state == "ACCOUNT_1_ACTIVE":
        transition_cycle_state(
            db, cycle, "ACCOUNT_2_ELIGIBLE",
            trigger="account2_distance_reached",
            detail=f"distance={distance:.1f} pts price={current_price}",
        )

    # Hybrid confirmation for account 2
    if cfg.strategy.entry_model == "hybrid" and not support_confirmed:
        logger.info("Account 2 eligible but support confirmation not yet met")
        return None

    # Risk check
    risk_result = approve_entry(db, cfg, secondary, spread_points)
    if not risk_result.allowed:
        logger.warning("Account 2 risk denied: %s", risk_result.reasons_denied)
        return None

    layer_num = increment_layer(db, cycle, secondary.alias)
    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, secondary, action, layer=layer_num)

    transition_cycle_state(
        db, cycle, "ACCOUNT_2_PENDING",
        trigger="account2_command_created",
        detail=f"cmd_id={cmd.id} layer={layer_num}",
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
    """
    Evaluate stage 3 eligibility.
    First try to add another layer to Account 2 (if not full).
    Then escalate to Account 3 if Account 2 is full.
    """
    if cycle.state not in ("ACCOUNT_2_ACTIVE", "ACCOUNT_3_ELIGIBLE"):
        return None

    secondary = cfg.secondary_account()
    tertiary = cfg.tertiary_account()

    if cycle.direction == "BUY":
        distance = (stage2_entry_price - current_price) / _get_point_value()
    else:
        distance = (current_price - stage2_entry_price) / _get_point_value()

    # ── Attempt: add next layer to Account 2 first ──────────────────────────
    if secondary and not is_account_full(cycle, cfg, secondary.alias):
        layer_dist = cfg.strategy.account2_layer_distance_points
        if distance >= layer_dist:
            if cfg.strategy.entry_model == "hybrid" and not support_confirmed:
                logger.info(
                    "Account 2 layer distance reached (%.1f pts) but confirmation not met",
                    distance,
                )
                return None
            cmd = initiate_next_layer(db, cfg, cycle, secondary, spread_points)
            if cmd:
                return cmd

    # ── Account 2 is full → try Account 3 escalation ────────────────────────
    if tertiary is None or not tertiary.enabled:
        return None

    acct3_dist = cfg.strategy.account3_activate_distance_points
    if distance < acct3_dist:
        return None

    if cycle.state == "ACCOUNT_2_ACTIVE":
        transition_cycle_state(
            db, cycle, "ACCOUNT_3_ELIGIBLE",
            trigger="account3_distance_reached",
            detail=f"distance={distance:.1f} pts price={current_price}",
        )

    if cfg.strategy.entry_model == "hybrid" and not support_confirmed:
        return None

    risk_result = approve_entry(db, cfg, tertiary, spread_points)
    if not risk_result.allowed:
        logger.warning("Account 3 risk denied: %s", risk_result.reasons_denied)
        return None

    layer_num = increment_layer(db, cycle, tertiary.alias)
    action = f"OPEN_{cycle.direction}"
    cmd = _make_command(db, cfg, cycle, tertiary, action, layer=layer_num)

    transition_cycle_state(
        db, cycle, "ACCOUNT_3_PENDING",
        trigger="account3_command_created",
        detail=f"cmd_id={cmd.id} layer={layer_num}",
    )
    return cmd


# ---------------------------------------------------------------------------
# Acknowledge account layers active
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
# Basket exit check: close all if total P/L >= basket_target_usd
# ---------------------------------------------------------------------------
def check_basket_exit(
    db: Session,
    cfg: STMAConfig,
    cycle: Cycle,
) -> bool:
    """
    Check if basket exit condition is met.
    Returns True if close was initiated, False otherwise.
    """
    if cycle.exit_mode != "basket":
        return False

    target = cycle.basket_target_usd or cfg.strategy.basket_target_usd
    if not target:
        return False

    # Sum all floating PnL from managed positions in this cycle
    positions = (
        db.query(PositionSnapshot)
        .filter(
            PositionSnapshot.cycle_id == cycle.id,
            PositionSnapshot.is_managed == True,
        )
        .all()
    )
    total_floating = sum(p.floating_pnl or 0 for p in positions)

    if total_floating >= target:
        logger.info(
            "Basket target reached: %.2f >= %.2f | initiating close | cycle=%s",
            total_floating, target, cycle.id
        )
        initiate_close(db, cfg, cycle, reason="basket_target_reached")
        return True

    return False


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
