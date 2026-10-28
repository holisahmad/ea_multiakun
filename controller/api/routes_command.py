"""
api/routes_command.py — EA command polling and execution result endpoints.

EA polls GET /command/{alias} to receive pending commands.
EA posts to POST /execution-result to report what happened.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Header, HTTPException, status
from sqlalchemy.orm import Session

from ..config import STMAConfig, get_config
from ..database.db import get_db
from ..database.models import Command, Execution, AccountStatus
from ..core.schemas import (
    AccountStatusPush,
    AccountStatusResponse,
    CommandPollResponse,
    CommandPayload,
    ExecutionResultRequest,
    ExecutionResultResponse,
    PositionsPushRequest,
)
from ..core.reconciler import upsert_positions, reconcile_command_state
from ..core.cycle_manager import mark_account_active, transition_cycle_state
from ..database.models import Cycle

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/ea", tags=["ea"])


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _verify_auth(x_auth_token: str = Header(...), cfg: STMAConfig = Depends(get_config)) -> None:
    if x_auth_token != cfg.server.ea_auth_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token")


# ---------------------------------------------------------------------------
# EA polls for pending command
# ---------------------------------------------------------------------------
@router.get("/command/{account_alias}", response_model=CommandPollResponse)
def poll_command(
    account_alias: str,
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    _auth: None = Depends(_verify_auth),
):
    """
    EA polls this endpoint every N seconds to receive a pending command.

    Only one PENDING command is returned at a time.
    Expired commands are automatically skipped and marked EXPIRED.
    """
    now = _utcnow()

    # Find oldest PENDING command for this account
    cmd = (
        db.query(Command)
        .filter(
            Command.account_alias == account_alias,
            Command.state == "PENDING",
        )
        .order_by(Command.created_at)
        .first()
    )

    if cmd is None:
        return CommandPollResponse(has_command=False)

    # Check expiry
    expires_at = cmd.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)

    if now > expires_at:
        cmd.state = "EXPIRED"
        db.add(cmd)
        db.commit()
        logger.warning("Command %s expired before EA picked it up", cmd.id)
        return CommandPollResponse(has_command=False)

    # Mark as SENT
    cmd.state = "SENT"
    cmd.sent_at = now
    db.add(cmd)
    db.commit()

    # Parse payload
    payload_dict = json.loads(cmd.payload_json) if cmd.payload_json else {}

    payload = CommandPayload(
        strategy_id=payload_dict.get("strategy_id", cfg.strategy.strategy_id),
        cycle_id=str(cmd.cycle_id),
        account_alias=account_alias,
        command_id=str(cmd.id),
        action=cmd.action,
        layer=cmd.layer,
        symbol=cmd.symbol,
        volume=cmd.volume,
        tp_points=cmd.tp_points,
        sl_points=cmd.sl_points,
        deviation_points=payload_dict.get("deviation_points", cfg.strategy.deviation_points),
        magic_number=payload_dict.get("magic_number", 0),
        created_at_utc=cmd.created_at.isoformat() if cmd.created_at else now.isoformat(),
        expires_at_utc=expires_at.isoformat(),
    )

    logger.info(
        "Command dispatched to EA | cmd_id=%s account=%s action=%s",
        cmd.id, account_alias, cmd.action,
    )
    return CommandPollResponse(has_command=True, command=payload)


# ---------------------------------------------------------------------------
# EA reports execution result
# ---------------------------------------------------------------------------
@router.post("/execution-result", response_model=ExecutionResultResponse)
def receive_execution_result(
    body: ExecutionResultRequest,
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    _auth: None = Depends(_verify_auth),
):
    """
    EA posts the result of a command execution.

    A successful HTTP response here is NOT proof of a filled order.
    Actual position state is verified via the reconciliation endpoint.
    """
    # Validate command exists and is in expected state
    cmd = db.query(Command).filter(Command.id == body.command_id).first()
    if cmd is None:
        logger.error("Execution result for unknown command_id=%s", body.command_id)
        raise HTTPException(status_code=404, detail=f"Command {body.command_id} not found")

    if cmd.account_alias != body.account_alias:
        raise HTTPException(
            status_code=400,
            detail=f"account_alias mismatch: command={cmd.account_alias}, reported={body.account_alias}",
        )

    # Reject duplicate execution results
    existing_exec = (
        db.query(Execution)
        .filter(Execution.command_id == body.command_id)
        .first()
    )
    if existing_exec:
        logger.warning("Duplicate execution result for cmd=%s — ignoring", body.command_id)
        return ExecutionResultResponse(ok=True, message="Already recorded")

    # Acknowledge
    cmd.state = "ACKNOWLEDGED"
    cmd.acked_at = _utcnow()
    db.add(cmd)

    # Persist execution record
    exec_record = Execution(
        command_id=body.command_id,
        account_alias=body.account_alias,
        retcode=body.retcode,
        order_ticket=body.order_ticket,
        deal_ticket=body.deal_ticket,
        position_id=body.position_id,
        executed_volume=body.executed_volume,
        executed_price=body.executed_price,
        status=body.status,
        raw_response_json=json.dumps(body.raw_response) if body.raw_response else None,
    )
    db.add(exec_record)
    db.commit()

    # Update command state from execution status
    reconcile_command_state(db, body.command_id, body.position_id, body.status)

    # Update cycle state if FILLED
    if body.status == "FILLED" and cmd.action in ("OPEN_BUY", "OPEN_SELL"):
        cycle = db.query(Cycle).filter(Cycle.id == cmd.cycle_id).first()
        if cycle:
            stage_map = {
                "ACCOUNT_1_PENDING": 1,
                "ACCOUNT_2_PENDING": 2,
                "ACCOUNT_3_PENDING": 3,
            }
            stage = stage_map.get(cycle.state)
            if stage:
                try:
                    mark_account_active(db, cycle, stage)
                except ValueError as e:
                    logger.warning("Could not advance cycle state: %s", e)
    elif body.status == "REJECTED" and cmd.action in ("OPEN_BUY", "OPEN_SELL"):
        cycle = db.query(Cycle).filter(Cycle.id == cmd.cycle_id).first()
        if cycle:
            if cycle.state == "ACCOUNT_1_PENDING":
                transition_cycle_state(db, cycle, "FAILED_REQUIRES_OPERATOR", trigger="broker_rejected_stage1")
            elif cycle.state == "ACCOUNT_2_PENDING":
                transition_cycle_state(db, cycle, "ACCOUNT_1_ACTIVE", trigger="broker_rejected_stage2", detail="Reverted to Stage 1")
            elif cycle.state == "ACCOUNT_3_PENDING":
                transition_cycle_state(db, cycle, "ACCOUNT_2_ACTIVE", trigger="broker_rejected_stage3", detail="Reverted to Stage 2")

    logger.info(
        "Execution result recorded | cmd=%s status=%s retcode=%s",
        body.command_id, body.status, body.retcode,
    )
    return ExecutionResultResponse(ok=True, message=f"Execution {body.status} recorded")


# ---------------------------------------------------------------------------
# EA pushes account status
# ---------------------------------------------------------------------------
@router.post("/status", response_model=AccountStatusResponse)
def receive_account_status(
    body: AccountStatusPush,
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    _auth: None = Depends(_verify_auth),
):
    """EA pushes its current account status (balance, equity, margin, etc.)."""
    account = cfg.get_account(body.account_alias)
    if account is None:
        raise HTTPException(status_code=400, detail=f"Unknown account alias: {body.account_alias}")

    # Upsert status
    existing = (
        db.query(AccountStatus)
        .filter(AccountStatus.account_alias == body.account_alias)
        .first()
    )
    if existing:
        existing.login = body.login
        existing.server = body.server
        existing.balance = body.balance
        existing.equity = body.equity
        existing.free_margin = body.free_margin
        existing.margin_level_pct = body.margin_level_pct
        existing.floating_pnl = body.floating_pnl
        existing.open_positions = body.open_positions
        existing.connected = body.connected
        existing.last_updated = _utcnow()
        existing.ea_version = body.ea_version
        db.add(existing)
    else:
        new_status = AccountStatus(
            account_alias=body.account_alias,
            login=body.login,
            server=body.server,
            balance=body.balance,
            equity=body.equity,
            free_margin=body.free_margin,
            margin_level_pct=body.margin_level_pct,
            floating_pnl=body.floating_pnl,
            open_positions=body.open_positions,
            connected=body.connected,
            last_updated=_utcnow(),
            ea_version=body.ea_version,
        )
        db.add(new_status)

    db.commit()
    return AccountStatusResponse(ok=True, message="Status updated")


# ---------------------------------------------------------------------------
# EA pushes positions for reconciliation
# ---------------------------------------------------------------------------
@router.post("/positions", response_model=dict)
def receive_positions(
    body: PositionsPushRequest,
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    _auth: None = Depends(_verify_auth),
):
    """EA pushes all open positions for reconciliation."""
    account = cfg.get_account(body.account_alias)
    if account is None:
        raise HTTPException(status_code=400, detail=f"Unknown account: {body.account_alias}")

    positions_raw = [p.model_dump() for p in body.positions]
    summary = upsert_positions(db, cfg, body.account_alias, positions_raw)

    logger.info(
        "Positions reconciled | account=%s new=%d updated=%d removed=%d",
        body.account_alias, summary["new"], summary["updated"], summary["removed"],
    )
    return {"ok": True, **summary}
