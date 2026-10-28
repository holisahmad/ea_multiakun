"""
tests/test_config.py — Unit tests for config loading and validation.
"""
import pytest
from pathlib import Path
from unittest.mock import patch
import yaml
import tempfile
import os

from controller.config import STMAConfig, load_config, AccountConfig


def make_minimal_config(**overrides) -> dict:
    """Return a minimal valid config dict for testing."""
    base = {
        "app": {
            "name": "Test STMA",
            "environment": "demo",
            "live_trading_enabled": False,
        },
        "server": {
            "ea_auth_token": "test-token-123",
            "port": 8000,
        },
        "strategy": {
            "strategy_id": "stma-xau-v1",
            "symbol": "XAUUSD",
            "default_lot": 0.01,
        },
        "risk": {
            "aggregate_protection_enabled": True,
        },
        "accounts": [
            {
                "alias": "account_1",
                "role": "primary",
                "enabled": True,
                "magic_number": 20240001,
                "default_lot": 0.01,
            },
            {
                "alias": "account_2",
                "role": "secondary",
                "enabled": True,
                "magic_number": 20240002,
                "default_lot": 0.01,
            },
        ],
    }
    base.update(overrides)
    return base


def load_config_from_dict(d: dict) -> STMAConfig:
    return STMAConfig.model_validate(d)


class TestConfigLoading:
    def test_minimal_config_loads(self):
        cfg = load_config_from_dict(make_minimal_config())
        assert cfg.strategy.symbol == "XAUUSD"
        assert cfg.app.live_trading_enabled is False

    def test_primary_account_found(self):
        cfg = load_config_from_dict(make_minimal_config())
        primary = cfg.primary_account()
        assert primary is not None
        assert primary.alias == "account_1"

    def test_secondary_account_found(self):
        cfg = load_config_from_dict(make_minimal_config())
        secondary = cfg.secondary_account()
        assert secondary is not None
        assert secondary.alias == "account_2"

    def test_no_primary_account_raises(self):
        cfg_dict = make_minimal_config()
        cfg_dict["accounts"][0]["role"] = "secondary"
        cfg_dict["accounts"][1]["role"] = "secondary"
        with pytest.raises(Exception):
            load_config_from_dict(cfg_dict)

    def test_duplicate_aliases_raises(self):
        cfg_dict = make_minimal_config()
        cfg_dict["accounts"][1]["alias"] = "account_1"  # Duplicate
        with pytest.raises(Exception):
            load_config_from_dict(cfg_dict)


class TestLiveTradingValidation:
    def test_live_trading_blocked_with_null_risk(self):
        """Live trading must be blocked if mandatory risk fields are null."""
        cfg_dict = make_minimal_config()
        cfg_dict["app"]["environment"] = "live"
        cfg_dict["app"]["live_trading_enabled"] = True
        # Mandatory risk fields are null by default
        with pytest.raises(Exception, match="risk"):
            load_config_from_dict(cfg_dict)

    def test_live_trading_blocked_wrong_environment(self):
        cfg_dict = make_minimal_config()
        cfg_dict["app"]["environment"] = "demo"
        cfg_dict["app"]["live_trading_enabled"] = True
        with pytest.raises(Exception, match="environment"):
            load_config_from_dict(cfg_dict)

    def test_demo_mode_with_null_risk_allowed(self):
        cfg_dict = make_minimal_config()
        cfg = load_config_from_dict(cfg_dict)
        ok, missing = cfg.risk.mandatory_live_fields_ok()
        assert ok is False  # Still shows missing, but doesn't block demo
        assert len(missing) > 0

    def test_mandatory_risk_fields_identified(self):
        cfg = load_config_from_dict(make_minimal_config())
        ok, missing = cfg.risk.mandatory_live_fields_ok()
        assert "risk.max_floating_loss_per_account_usd" in missing
        assert "risk.max_drawdown_per_account_pct" in missing
        assert "risk.min_margin_level_pct" in missing


class TestCentAccountNormalization:
    def test_standard_account_passthrough(self):
        acct = AccountConfig(
            alias="acc1", role="primary", magic_number=1,
            is_cent=False, cent_divisor=100,
        )
        assert acct.normalize_amount(1000.0) == 1000.0

    def test_cent_account_divides_by_100(self):
        acct = AccountConfig(
            alias="acc1", role="primary", magic_number=1,
            is_cent=True, cent_divisor=100,
        )
        assert acct.normalize_amount(100000.0) == 1000.0

    def test_cent_account_custom_divisor(self):
        acct = AccountConfig(
            alias="acc1", role="primary", magic_number=1,
            is_cent=True, cent_divisor=1000,
        )
        assert acct.normalize_amount(1000000.0) == 1000.0
