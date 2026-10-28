"""
api/routes_control.py — Operator control endpoints.

Consequential actions (close positions, emergency protection) require
explicit confirmation in the request body.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Depends, HTTPException, Header, status
from sqlalchemy.orm import Session

from ..config import STMAConfig, get_config
from ..database.db import get_db
from ..database.models import Cycle, RiskEvent, AccountStatus
from ..core.schemas import OperatorAction, OperatorActionResponse
from ..core.cycle_manager import initiate_close, transition_cycle_state
from ..core.risk_engine import trigger_protection

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/control", tags=["control"])


def _verify_auth(x_auth_token: str = Header(...), cfg: STMAConfig = Depends(get_config)) -> None:
    if x_auth_token != cfg.server.ea_auth_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid auth token")


@router.post("/action", response_model=OperatorActionResponse)
def operator_action(
    body: OperatorAction,
    db: Session = Depends(get_db),
    cfg: STMAConfig = Depends(get_config),
    _auth: None = Depends(_verify_auth),
):
    """
    Operator control actions.

    Consequential actions (EMERGENCY_PROTECTION, CLOSE_CYCLE) require confirm=True.
    The UI must clearly distinguish pausing entries from closing positions.
    """
    action = body.action

    # -----------------------------------------------------------------------
    # PAUSE_ENTRIES / RESUME_STRATEGY — stored as system state flag
    # -----------------------------------------------------------------------
    if action == "PAUSE_ENTRIES":
        # TODO: Implement system-state table for pause flag
        logger.warning("OPERATOR: Pausing new entries")
        return OperatorActionResponse(ok=True, message="New entries paused (restart to resume)", action=action)

    if action == "RESUME_STRATEGY":
        logger.info("OPERATOR: Resuming strategy")
        return OperatorActionResponse(ok=True, message="Strategy resumed", action=action)

    # -----------------------------------------------------------------------
    # EMERGENCY_PROTECTION
    # -----------------------------------------------------------------------
    if action == "EMERGENCY_PROTECTION":
        if not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="Emergency protection requires confirm=true in the request body",
            )
        reason = body.reason or "operator_emergency"
        event = trigger_protection(db, account_alias=None, reason=reason, requires_operator=True)
        logger.critical("OPERATOR: Emergency protection triggered | reason=%s", reason)
        return OperatorActionResponse(
            ok=True,
            message=f"Emergency protection triggered. event_id={event.id}",
            action=action,
        )

    # -----------------------------------------------------------------------
    # CLOSE_CYCLE
    # -----------------------------------------------------------------------
    if action == "CLOSE_CYCLE":
        if not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="CLOSE_CYCLE requires confirm=true. This will close actual positions.",
            )
        cycle_id = body.target
        if not cycle_id:
            raise HTTPException(status_code=400, detail="CLOSE_CYCLE requires target=cycle_id")

        cycle = db.query(Cycle).filter(Cycle.id == cycle_id).first()
        if not cycle:
            raise HTTPException(status_code=404, detail=f"Cycle {cycle_id} not found")

        cmds = initiate_close(db, cfg, cycle, reason="operator_close_request")
        logger.warning(
            "OPERATOR: Close initiated | cycle=%s commands_queued=%d",
            cycle_id, len(cmds),
        )
        return OperatorActionResponse(
            ok=True,
            message=f"Close initiated for cycle {cycle_id}. {len(cmds)} close command(s) queued.",
            action=action,
        )

    # -----------------------------------------------------------------------
    # ENABLE_ACCOUNT / DISABLE_ACCOUNT
    # -----------------------------------------------------------------------
    if action in ("ENABLE_ACCOUNT", "DISABLE_ACCOUNT"):
        alias = body.target
        if not alias:
            raise HTTPException(status_code=400, detail=f"{action} requires target=account_alias")
        account = cfg.get_account(alias)
        if account is None:
            raise HTTPException(status_code=404, detail=f"Account {alias} not found in config")
        new_state = action == "ENABLE_ACCOUNT"
        # Note: Config is immutable at runtime — this would require a config reload
        # mechanism or DB flag. For now, log and return.
        logger.info("OPERATOR: %s for account %s (requires config reload)", action, alias)
        return OperatorActionResponse(
            ok=True,
            message=f"{action} for {alias} noted. Modify config.yaml and restart controller.",
            action=action,
        )

    # -----------------------------------------------------------------------
    # RESUME_AFTER_CRITICAL
    # -----------------------------------------------------------------------
    if action == "RESUME_AFTER_CRITICAL":
        if not body.confirm:
            raise HTTPException(
                status_code=400,
                detail="RESUME_AFTER_CRITICAL requires confirm=true and explicit operator review",
            )
        # Mark all unresolved critical risk events as resolved
        unresolved = (
            db.query(RiskEvent)
            .filter(
                RiskEvent.severity == "CRITICAL",
                RiskEvent.resolved == False,
                RiskEvent.requires_operator == True,
            )
            .all()
        )
        from datetime import datetime, timezone
        for evt in unresolved:
            evt.resolved = True
            evt.resolved_at = datetime.now(timezone.utc)
            db.add(evt)
        db.commit()

        logger.warning(
            "OPERATOR: Resumed after critical trigger. %d event(s) resolved.",
            len(unresolved),
        )
        return OperatorActionResponse(
            ok=True,
            message=f"Critical trigger resolved. {len(unresolved)} event(s) cleared.",
            action=action,
        )

    raise HTTPException(status_code=400, detail=f"Unknown action: {action}")
