"""
api/routes_signal.py — Signal ingestion endpoint.
Receives trading signals from MQL5 EA or any HTTP client.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone, timedelta

from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session

from ..config import STMAConfig, get_config
from ..database.db import get_db
from ..core.schemas import SignalRequest, SignalResponse
from ..core.signal_handler import validate_and_store_signal, SignalValidationError
from ..core.cycle_manager import (
    create_cycle,
    initiate_stage1,
    initiate_manual_entry,
    get_account_layer_count,
    get_max_layers_for_account,
    is_account_full,
)
from ..database.models import Cycle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/signal", tags=["signal"])


def _verify_auth(x_auth_token: str = Header(...), cfg: STMAConfig = Depends(get_config)) -> None:
    """Verify shared auth token between EA and controller."""
    if x_auth_token != cfg.server.ea_auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing auth token",
        )


def _account_display_name(alias: str) -> str:
    return {
        "account_1": "Akun 1 (Primary)",
        "account_2": "Akun 2 (Secondary)",
        "account_3": "Akun 3 (Tertiary)",
    }.get(alias, alias)


@router.post("", response_model=SignalResponse)
def receive_signal(
    body: SignalRequest,
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    _auth: None = Depends(_verify_auth),
):
    """
    Receive a trading signal from EA/indicator or manual dashboard.

    - New signal with no active cycle → create cycle → initiate Layer 1
    - Signal with target_account → add next layer to that account (manual DCA)
    - Risk Engine approval is always required.
    """
    if not cfg.app.live_trading_enabled and cfg.app.environment == "live":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live trading is not enabled",
        )

    target_account = (body.payload or {}).get("target_account") if body.payload else None

    try:
        signal = validate_and_store_signal(
            db=db,
            cfg=cfg,
            strategy_id=body.strategy_id,
            symbol=body.symbol,
            direction=body.direction,
            source=body.source,
            price=body.price,
            payload=body.payload,
        )
    except SignalValidationError as exc:
        return SignalResponse(
            signal_id="",
            status="REJECTED",
            message=str(exc),
        )

    # Find any active cycle for this symbol/strategy
    active_cycle = (
        db.query(Cycle)
        .filter(
            Cycle.strategy_id == body.strategy_id,
            Cycle.symbol == body.symbol.upper(),
            Cycle.state.notin_(["COMPLETED", "FAILED_REQUIRES_OPERATOR", "CANCELLED", "FAILED"]),
        )
        .first()
    )

    try:
        # ── Case 1: Active cycle exists + target_account specified → add layer ──
        if active_cycle and target_account:
            layer_count = get_account_layer_count(active_cycle, target_account)
            max_layers = get_max_layers_for_account(cfg, target_account)
            acc_name = _account_display_name(target_account)

            cmd = initiate_manual_entry(
                db=db,
                cfg=cfg,
                cycle=active_cycle,
                target_alias=target_account,
            )
            new_layer = get_account_layer_count(active_cycle, target_account)
            return SignalResponse(
                signal_id=signal.id,
                status="ACCEPTED",
                message=(
                    f"Layer {new_layer}/{max_layers} berhasil dikirim ke {acc_name}. "
                    f"Perintah #{cmd.id[:8]} dikirim ke MT5."
                ),
            )

        # ── Case 2: No active cycle → create new cycle → Layer 1 on target account ──
        else:
            cycle = create_cycle(db=db, cfg=cfg, signal=signal)
            final_target = target_account or "account_1"
            cmd = initiate_manual_entry(
                db=db,
                cfg=cfg,
                cycle=cycle,
                target_alias=final_target,
            )
            acc_name = _account_display_name(final_target)
            max_layers = get_max_layers_for_account(cfg, final_target)
            return SignalResponse(
                signal_id=signal.id,
                status="ACCEPTED",
                message=(
                    f"Siklus baru dibuat. Layer 1/{max_layers} dikirim ke {acc_name}. "
                    f"Perintah #{cmd.id[:8]} dikirim ke MT5."
                ),
            )

    except RuntimeError as exc:
        logger.error("Entry initiation failed: %s", exc)
        return SignalResponse(
            signal_id=signal.id,
            status="RISK_DENIED",
            message=str(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected error in signal processing")
        raise HTTPException(status_code=500, detail=str(exc))
