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
from ..core.cycle_manager import create_cycle, initiate_stage1

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/signal", tags=["signal"])


def _verify_auth(x_auth_token: str = Header(...), cfg: STMAConfig = Depends(get_config)) -> None:
    """Verify shared auth token between EA and controller."""
    if x_auth_token != cfg.server.ea_auth_token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or missing auth token",
        )


@router.post("", response_model=SignalResponse)
def receive_signal(
    body: SignalRequest,
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    _auth: None = Depends(_verify_auth),
):
    """
    Receive a trading signal from EA/indicator.

    Signal is validated, persisted, and if accepted, triggers stage 1 entry evaluation.
    A valid signal is NOT an unconditional instruction to trade.
    Risk Engine approval is still required.
    """
    if not cfg.app.live_trading_enabled and cfg.app.environment == "live":
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Live trading is not enabled",
        )

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

    # Create cycle and attempt stage 1
    try:
        cycle = create_cycle(db=db, cfg=cfg, signal=signal)
        cmd = initiate_stage1(db=db, cfg=cfg, cycle=cycle)
        return SignalResponse(
            signal_id=signal.id,
            status="ACCEPTED",
            message=f"Cycle {cycle.id} created. Stage 1 command {cmd.id} queued.",
        )
    except RuntimeError as exc:
        logger.error("Stage 1 initiation failed: %s", exc)
        return SignalResponse(
            signal_id=signal.id,
            status="RISK_DENIED",
            message=str(exc),
        )
    except Exception as exc:
        logger.exception("Unexpected error in signal processing")
        raise HTTPException(status_code=500, detail=str(exc))
