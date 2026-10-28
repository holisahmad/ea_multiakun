"""
core/signal_handler.py — Signal validation and ingestion.

Validates incoming trading signals before they trigger a cycle.
A valid signal is not an unconditional instruction to trade.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from ..config import STMAConfig
from ..database.models import Signal, Cycle

logger = logging.getLogger(__name__)


class SignalValidationError(Exception):
    """Raised when a signal fails validation."""
    pass


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def validate_and_store_signal(
    *,
    db: Session,
    cfg: STMAConfig,
    strategy_id: str,
    symbol: str,
    direction: str,
    source: str,
    price: Optional[float] = None,
    payload: Optional[dict] = None,
) -> Signal:
    """
    Validate an incoming signal and persist it.

    Returns the stored Signal object (status=ACCEPTED or raises on failure).
    Rejected signals are also stored for auditability.
    """
    now = _utcnow()
    expires_at = now + timedelta(seconds=cfg.strategy.signal_max_age_seconds)

    signal = Signal(
        source=source,
        direction=direction,
        symbol=symbol,
        strategy_id=strategy_id,
        price=price,
        payload_json=json.dumps(payload) if payload else None,
        received_at=now,
        expires_at=expires_at,
        status="RECEIVED",
    )

    reject_reason: Optional[str] = None

    try:
        # 1. Strategy ID match
        if strategy_id != cfg.strategy.strategy_id:
            reject_reason = (
                f"strategy_id mismatch: got '{strategy_id}', "
                f"expected '{cfg.strategy.strategy_id}'"
            )
            raise SignalValidationError(reject_reason)

        # 2. Symbol match
        if symbol.upper() != cfg.strategy.symbol.upper():
            reject_reason = (
                f"symbol mismatch: got '{symbol}', expected '{cfg.strategy.symbol}'"
            )
            raise SignalValidationError(reject_reason)

        # 3. Direction valid
        if direction not in ("BUY", "SELL"):
            reject_reason = f"invalid direction '{direction}': must be BUY or SELL"
            raise SignalValidationError(reject_reason)

        # 4. No overlapping active cycle
        active_cycle = (
            db.query(Cycle)
            .filter(
                Cycle.strategy_id == strategy_id,
                Cycle.symbol == symbol.upper(),
                Cycle.state.notin_([
                    "COMPLETED",
                    "FAILED_REQUIRES_OPERATOR",
                    "CANCELLED",
                    "FAILED",
                ]),
            )
            .first()
        )
        if active_cycle:
            is_manual = "manual" in (source or "").lower() or "dashboard" in (source or "").lower()
            target_account = (payload or {}).get("target_account") if payload else None
            if is_manual and target_account:
                from ..database.models import PositionSnapshot
                from .cycle_manager import get_account_layer_count, get_max_layers_for_account
                from ..config import load_config
                _cfg = load_config()

                current_layer_count = get_account_layer_count(active_cycle, target_account)
                max_layers = get_max_layers_for_account(_cfg, target_account)

                if current_layer_count < max_layers:
                    signal.status = "ACCEPTED"
                    logger.info(
                        "Manual signal accepted for layer %d/%d | target=%s cycle=%s",
                        current_layer_count + 1, max_layers, target_account, active_cycle.id,
                    )
                    db.add(signal)
                    db.commit()
                    db.refresh(signal)
                    return signal
                else:
                    # Account is full, check if other accounts are available
                    reject_reason = (
                        f"Akun {target_account} sudah penuh ({current_layer_count}/{max_layers} layers). "
                        f"Pilih akun lain atau tunggu posisi ditutup."
                    )
                    raise SignalValidationError(reject_reason)

            reject_reason = (
                f"Siklus aktif sedang berjalan ({active_cycle.state}). "
                f"Akun {target_account or 'Primary'} sudah memiliki posisi di siklus ini."
            )
            raise SignalValidationError(reject_reason)

        # 5. Strategy must be enabled (controlled via system state — checked by caller)

        signal.status = "ACCEPTED"
        logger.info(
            "Signal accepted | source=%s direction=%s symbol=%s signal_id=%s",
            source, direction, symbol, signal.id,
        )

    except SignalValidationError as exc:
        signal.status = "REJECTED"
        signal.reject_reason = str(exc)
        logger.warning(
            "Signal rejected | source=%s reason=%s",
            source, exc,
        )

    finally:
        db.add(signal)
        db.commit()
        db.refresh(signal)

    if signal.status == "REJECTED":
        raise SignalValidationError(signal.reject_reason or "Unknown rejection")

    return signal
