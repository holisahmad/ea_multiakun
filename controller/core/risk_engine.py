"""
core/risk_engine.py — Risk Engine for STMA.

Risk Engine approval is MANDATORY before every new entry.
All checks must pass; any single failure blocks the entry.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import List, Optional

from sqlalchemy.orm import Session

from ..config import STMAConfig, AccountConfig
from ..database.models import AccountStatus, PositionSnapshot, RiskEvent, Cycle

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Risk check result
# ---------------------------------------------------------------------------
@dataclass
class RiskCheckResult:
    allowed: bool
    reasons_denied: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def deny(self, reason: str) -> None:
        self.allowed = False
        self.reasons_denied.append(reason)

    def warn(self, message: str) -> None:
        self.warnings.append(message)


# ---------------------------------------------------------------------------
# Helper — get current account status (freshness validated here)
# ---------------------------------------------------------------------------
def _get_fresh_status(
    db: Session,
    alias: str,
    stale_threshold_s: int,
) -> Optional[AccountStatus]:
    """Return account status if fresh, else None."""
    status = db.query(AccountStatus).filter(AccountStatus.account_alias == alias).first()
    if status is None:
        return None
    age = (_utcnow() - status.last_updated.replace(tzinfo=timezone.utc)).total_seconds()
    if age > stale_threshold_s:
        return None  # stale
    if not status.connected:
        return None  # disconnected
    return status


# ---------------------------------------------------------------------------
# Per-account risk check
# ---------------------------------------------------------------------------
def check_account_risk(
    db: Session,
    cfg: STMAConfig,
    account: AccountConfig,
    result: RiskCheckResult,
) -> None:
    """Validate per-account risk conditions. Mutates `result`."""
    risk = cfg.risk
    alias = account.alias

    # 1. Account status freshness
    status = _get_fresh_status(db, alias, risk.account_stale_threshold_seconds)
    if status is None:
        if risk.block_entries_if_account_stale:
            result.deny(
                f"[{alias}] Account status is stale or disconnected. "
                f"Blocking entry per block_entries_if_account_stale=true"
            )
        else:
            result.warn(f"[{alias}] Account status stale but blocking is disabled")
        return  # Can't evaluate further

    # 2. Open layers (positions this account has in cycle)
    managed_positions = (
        db.query(PositionSnapshot)
        .filter(
            PositionSnapshot.account_alias == alias,
            PositionSnapshot.is_managed == True,
        )
        .count()
    )
    if managed_positions >= account.max_layers:
        result.deny(
            f"[{alias}] Layer limit reached: "
            f"{managed_positions}/{account.max_layers}"
        )

    # 3. Open volume
    vol_query = db.query(PositionSnapshot).filter(
        PositionSnapshot.account_alias == alias,
        PositionSnapshot.is_managed == True,
    ).all()
    open_volume = sum(p.volume for p in vol_query)
    if open_volume + account.default_lot > risk.max_volume_per_account:
        result.deny(
            f"[{alias}] Volume limit: current={open_volume:.4f}, "
            f"adding={account.default_lot:.4f}, "
            f"max={risk.max_volume_per_account:.4f}"
        )

    # 4. Floating loss per account
    if risk.max_floating_loss_per_account_usd is not None and status.floating_pnl is not None:
        norm_pnl = account.normalize_amount(status.floating_pnl)
        if norm_pnl < -abs(risk.max_floating_loss_per_account_usd):
            result.deny(
                f"[{alias}] Floating loss limit: "
                f"current={norm_pnl:.2f} USD, "
                f"limit=-{risk.max_floating_loss_per_account_usd:.2f} USD"
            )
    elif risk.max_floating_loss_per_account_usd is None:
        result.warn(f"[{alias}] max_floating_loss_per_account_usd not configured")

    # 5. Margin level
    if risk.min_margin_level_pct is not None and status.margin_level_pct is not None:
        if status.margin_level_pct < risk.min_margin_level_pct:
            result.deny(
                f"[{alias}] Margin level too low: "
                f"{status.margin_level_pct:.1f}% < min {risk.min_margin_level_pct:.1f}%"
            )
    elif risk.min_margin_level_pct is None:
        result.warn(f"[{alias}] min_margin_level_pct not configured")

    # 6. Drawdown per account (percent of balance)
    if (
        risk.max_drawdown_per_account_pct is not None
        and status.balance is not None
        and status.equity is not None
        and status.balance > 0
    ):
        norm_balance = account.normalize_amount(status.balance)
        norm_equity = account.normalize_amount(status.equity)
        drawdown_pct = (1 - norm_equity / norm_balance) * 100 if norm_balance > 0 else 0
        if drawdown_pct > risk.max_drawdown_per_account_pct:
            result.deny(
                f"[{alias}] Drawdown limit: "
                f"{drawdown_pct:.1f}% > max {risk.max_drawdown_per_account_pct:.1f}%"
            )
    elif risk.max_drawdown_per_account_pct is None:
        result.warn(f"[{alias}] max_drawdown_per_account_pct not configured")


# ---------------------------------------------------------------------------
# Aggregate risk check
# ---------------------------------------------------------------------------
def check_aggregate_risk(
    db: Session,
    cfg: STMAConfig,
    result: RiskCheckResult,
) -> None:
    """Validate aggregate (strategy-wide) risk. Mutates `result`."""
    risk = cfg.risk

    # 1. Total managed positions
    total_positions = (
        db.query(PositionSnapshot)
        .filter(PositionSnapshot.is_managed == True)
        .count()
    )
    if total_positions >= risk.max_strategy_positions:
        result.deny(
            f"Aggregate position limit reached: "
            f"{total_positions}/{risk.max_strategy_positions}"
        )

    # 2. Total volume
    all_positions = (
        db.query(PositionSnapshot)
        .filter(PositionSnapshot.is_managed == True)
        .all()
    )
    total_volume = sum(p.volume for p in all_positions)

    if risk.max_strategy_volume is not None:
        if total_volume + cfg.strategy.default_lot > risk.max_strategy_volume:
            result.deny(
                f"Aggregate volume limit: "
                f"current={total_volume:.4f}, max={risk.max_strategy_volume:.4f}"
            )
    else:
        result.warn("max_strategy_volume not configured — aggregate volume uncapped")

    # 3. Aggregate floating P/L (sum across all accounts, normalized to USD)
    if risk.max_strategy_floating_loss_usd is not None:
        # We use the last known floating_pnl from each account status
        total_floating = 0.0
        for account in cfg.accounts:
            status = db.query(AccountStatus).filter(
                AccountStatus.account_alias == account.alias
            ).first()
            if status and status.floating_pnl is not None:
                total_floating += account.normalize_amount(status.floating_pnl)

        if total_floating < -abs(risk.max_strategy_floating_loss_usd):
            result.deny(
                f"Aggregate floating loss limit: "
                f"current={total_floating:.2f} USD, "
                f"limit=-{risk.max_strategy_floating_loss_usd:.2f} USD"
            )
    else:
        result.warn("max_strategy_floating_loss_usd not configured")

    # 4. Aggregate drawdown pct (if we can compute it)
    if risk.max_strategy_drawdown_pct is not None:
        result.warn(
            "max_strategy_drawdown_pct configured but requires normalized balances "
            "across mixed-currency accounts. Implement per-account currency conversion."
        )
    else:
        result.warn("max_strategy_drawdown_pct not configured")


# ---------------------------------------------------------------------------
# Per-position (pre-trade) check
# ---------------------------------------------------------------------------
def check_position_risk(
    cfg: STMAConfig,
    account: AccountConfig,
    spread_points: Optional[float],
    result: RiskCheckResult,
) -> None:
    """Check position-level conditions (spread, volume). Mutates `result`."""
    # Spread check
    if spread_points is not None:
        if spread_points > cfg.strategy.max_spread_points:
            result.deny(
                f"Spread too wide: {spread_points} > max {cfg.strategy.max_spread_points} points"
            )

    # Volume validation (basic — broker-specific min/step not known here)
    vol = account.default_lot
    if vol <= 0:
        result.deny(f"[{account.alias}] Volume must be > 0, got {vol}")


# ---------------------------------------------------------------------------
# Main entry point: full risk gate
# ---------------------------------------------------------------------------
def approve_entry(
    db: Session,
    cfg: STMAConfig,
    account: AccountConfig,
    spread_points: Optional[float] = None,
) -> RiskCheckResult:
    """
    Full risk gate for a new entry on `account`.

    Returns RiskCheckResult. Caller must check result.allowed before placing order.
    Also persists a RiskEvent if denied.
    """
    result = RiskCheckResult(allowed=True)

    check_position_risk(cfg, account, spread_points, result)
    check_account_risk(db, cfg, account, result)
    check_aggregate_risk(db, cfg, result)

    if not result.allowed:
        # Persist risk denial event
        event = RiskEvent(
            account_alias=account.alias,
            event_type="ENTRY_BLOCKED",
            severity="WARNING",
            detail="; ".join(result.reasons_denied),
        )
        db.add(event)
        db.commit()

        logger.warning(
            "Risk engine DENIED entry | account=%s reasons=%s",
            account.alias,
            result.reasons_denied,
        )
    else:
        if result.warnings:
            logger.warning(
                "Risk engine APPROVED with warnings | account=%s warnings=%s",
                account.alias,
                result.warnings,
            )
        else:
            logger.info("Risk engine APPROVED entry | account=%s", account.alias)

    return result


# ---------------------------------------------------------------------------
# Protection trigger
# ---------------------------------------------------------------------------
def trigger_protection(
    db: Session,
    account_alias: Optional[str],
    reason: str,
    requires_operator: bool = True,
) -> RiskEvent:
    """Record a critical protection event."""
    event = RiskEvent(
        account_alias=account_alias,
        event_type="PROTECTION_TRIGGERED",
        severity="CRITICAL",
        detail=reason,
        requires_operator=requires_operator,
        resolved=False,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    logger.critical(
        "PROTECTION TRIGGERED | account=%s reason=%s requires_operator=%s",
        account_alias, reason, requires_operator,
    )
    return event
