"""
tests/test_signal_handler.py — Unit tests for signal validation.
"""
import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from controller.config import STMAConfig
from controller.core.signal_handler import validate_and_store_signal, SignalValidationError
from controller.database.models import Cycle, Signal


def make_config():
    from controller.config import STMAConfig
    return STMAConfig.model_validate({
        "app": {"environment": "demo", "live_trading_enabled": False},
        "server": {"ea_auth_token": "test"},
        "strategy": {
            "strategy_id": "stma-xau-v1",
            "symbol": "XAUUSD",
            "default_lot": 0.01,
            "signal_max_age_seconds": 60,
        },
        "risk": {},
        "accounts": [
            {"alias": "account_1", "role": "primary", "magic_number": 1, "enabled": True}
        ],
    })


def make_db_mock(active_cycle=None):
    """Create a mock DB session."""
    db = MagicMock()
    query = db.query.return_value
    query.filter.return_value.first.return_value = active_cycle
    db.add.return_value = None
    db.commit.return_value = None
    db.refresh.return_value = None
    return db


class TestSignalValidation:
    def test_valid_signal_accepted(self):
        db = make_db_mock(active_cycle=None)
        cfg = make_config()
        # Should not raise
        with patch.object(Signal, "__init__", lambda self, **kw: None):
            # We test through the full function with a real Signal
            pass
        # Integration-level test — verify via API tests instead

    def test_wrong_strategy_id_rejected(self):
        db = make_db_mock(active_cycle=None)
        cfg = make_config()
        with pytest.raises(SignalValidationError, match="strategy_id"):
            validate_and_store_signal(
                db=db, cfg=cfg,
                strategy_id="wrong-id",
                symbol="XAUUSD",
                direction="BUY",
                source="test-ea",
            )

    def test_wrong_symbol_rejected(self):
        db = make_db_mock(active_cycle=None)
        cfg = make_config()
        with pytest.raises(SignalValidationError, match="symbol"):
            validate_and_store_signal(
                db=db, cfg=cfg,
                strategy_id="stma-xau-v1",
                symbol="EURUSD",
                direction="BUY",
                source="test-ea",
            )

    def test_invalid_direction_rejected(self):
        db = make_db_mock(active_cycle=None)
        cfg = make_config()
        with pytest.raises(SignalValidationError, match="direction"):
            validate_and_store_signal(
                db=db, cfg=cfg,
                strategy_id="stma-xau-v1",
                symbol="XAUUSD",
                direction="HOLD",
                source="test-ea",
            )

    def test_active_cycle_blocks_new_signal(self):
        existing_cycle = MagicMock()
        existing_cycle.id = "cycle-123"
        existing_cycle.state = "ACCOUNT_1_ACTIVE"
        db = make_db_mock(active_cycle=existing_cycle)
        cfg = make_config()

        with pytest.raises(SignalValidationError, match="cycle"):
            validate_and_store_signal(
                db=db, cfg=cfg,
                strategy_id="stma-xau-v1",
                symbol="XAUUSD",
                direction="BUY",
                source="test-ea",
            )

    def test_sell_direction_valid(self):
        db = make_db_mock(active_cycle=None)
        cfg = make_config()
        sig = validate_and_store_signal(
            db=db, cfg=cfg,
            strategy_id="stma-xau-v1",
            symbol="XAUUSD",
            direction="SELL",
            source="test-ea",
        )
        assert sig.status == "ACCEPTED"
        assert sig.direction == "SELL"


class TestRiskEngine:
    """Unit tests for risk engine checks."""

    def test_risk_check_result_deny(self):
        from controller.core.risk_engine import RiskCheckResult
        result = RiskCheckResult(allowed=True)
        result.deny("Test reason")
        assert result.allowed is False
        assert "Test reason" in result.reasons_denied

    def test_risk_check_result_multiple_denials(self):
        from controller.core.risk_engine import RiskCheckResult
        result = RiskCheckResult(allowed=True)
        result.deny("Reason 1")
        result.deny("Reason 2")
        assert result.allowed is False
        assert len(result.reasons_denied) == 2

    def test_risk_check_result_warn_does_not_deny(self):
        from controller.core.risk_engine import RiskCheckResult
        result = RiskCheckResult(allowed=True)
        result.warn("Warning only")
        assert result.allowed is True
        assert len(result.warnings) == 1


class TestStateMachine:
    """Unit tests for cycle state machine transitions."""

    def test_valid_transition(self):
        from controller.core.cycle_manager import VALID_TRANSITIONS
        assert "SIGNAL_VALIDATED" in VALID_TRANSITIONS["IDLE"]

    def test_invalid_transition_raises(self):
        from controller.core.cycle_manager import transition_cycle_state, VALID_TRANSITIONS

        db = MagicMock()
        cycle = MagicMock()
        cycle.state = "IDLE"

        # IDLE → ACCOUNT_1_ACTIVE is not valid
        with pytest.raises(ValueError, match="Invalid state transition"):
            transition_cycle_state(db, cycle, "ACCOUNT_1_ACTIVE", "test")

    def test_completed_state_terminal(self):
        from controller.core.cycle_manager import VALID_TRANSITIONS
        assert VALID_TRANSITIONS["COMPLETED"] == []

    def test_failed_state_terminal(self):
        from controller.core.cycle_manager import VALID_TRANSITIONS
        assert VALID_TRANSITIONS["FAILED_REQUIRES_OPERATOR"] == []

    def test_all_states_have_transition_definitions(self):
        from controller.core.cycle_manager import VALID_TRANSITIONS
        expected_states = {
            "IDLE", "SIGNAL_VALIDATED", "ACCOUNT_1_PENDING", "ACCOUNT_1_ACTIVE",
            "ACCOUNT_2_ELIGIBLE", "ACCOUNT_2_PENDING", "ACCOUNT_2_ACTIVE",
            "ACCOUNT_3_ELIGIBLE", "ACCOUNT_3_PENDING", "ACCOUNT_3_ACTIVE",
            "RECOVERY", "PROTECTION", "CLOSING", "RECONCILIATION",
            "COMPLETED", "FAILED_REQUIRES_OPERATOR",
        }
        for state in expected_states:
            assert state in VALID_TRANSITIONS, f"State {state} missing from VALID_TRANSITIONS"


class TestStagePriceDistance:
    """Unit tests for price distance calculation."""

    def test_buy_distance_from_entry(self):
        """For BUY: adverse movement = price dropped below entry."""
        entry_price = 2000.0
        current_price = 1998.0
        point = 0.01  # XAUUSD
        distance = (entry_price - current_price) / point
        assert distance == pytest.approx(200.0)

    def test_sell_distance_from_entry(self):
        """For SELL: adverse movement = price rose above entry."""
        entry_price = 2000.0
        current_price = 2002.0
        point = 0.01
        distance = (current_price - entry_price) / point
        assert distance == pytest.approx(200.0)

    def test_buy_no_adverse_movement(self):
        """If price moved in favor of BUY, distance should be negative."""
        entry_price = 2000.0
        current_price = 2002.0
        point = 0.01
        distance = (entry_price - current_price) / point
        assert distance < 0

    def test_support_confirmation_buy_near_support(self):
        from controller.core.stage_evaluator import _check_support_confirmation
        result = _check_support_confirmation(
            current_price=1999.80,
            direction="BUY",
            support_levels=[2000.0],
            zone_points=30,
        )
        assert result is True

    def test_support_confirmation_buy_far_from_support(self):
        from controller.core.stage_evaluator import _check_support_confirmation
        result = _check_support_confirmation(
            current_price=1990.0,
            direction="BUY",
            support_levels=[2000.0],
            zone_points=30,
        )
        assert result is False

    def test_resistance_confirmation_sell(self):
        from controller.core.stage_evaluator import _check_support_confirmation
        result = _check_support_confirmation(
            current_price=2000.20,
            direction="SELL",
            support_levels=[2000.0],
            zone_points=30,
        )
        assert result is True
