"""
config.py — Configuration loader and validator for STMA Controller.

Loads config.yaml, validates mandatory risk fields, and exposes typed
dataclasses. Live trading will be refused if mandatory risk values are null.
"""
from __future__ import annotations

import os
from decimal import Decimal
from pathlib import Path
from typing import List, Literal, Optional

import yaml
from pydantic import BaseModel, Field, field_validator, model_validator

CONFIG_PATH = Path(__file__).parent / "config.yaml"

# ---------------------------------------------------------------------------
# Sub-models
# ---------------------------------------------------------------------------

class AppConfig(BaseModel):
    name: str = "Smart Trade Multi Account"
    version: str = "0.1"
    environment: Literal["demo", "live"] = "demo"
    live_trading_enabled: bool = False
    log_level: str = "INFO"
    timezone: str = "Asia/Jakarta"


class ServerConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 8000
    ea_auth_token: str = "CHANGE_ME_BEFORE_USE"
    command_poll_interval_seconds: int = 1
    status_push_interval_seconds: int = 5
    command_timeout_seconds: int = 10
    max_command_age_seconds: int = 30

    @field_validator("ea_auth_token")
    @classmethod
    def token_must_be_changed(cls, v: str) -> str:
        if v == "CHANGE_ME_BEFORE_USE":
            import warnings
            warnings.warn(
                "ea_auth_token is still the default placeholder. "
                "Change it in config.yaml before connecting real EA instances.",
                stacklevel=2,
            )
        return v


class StrategyConfig(BaseModel):
    strategy_id: str = "stma-xau-v1"
    symbol: str = "XAUUSD"
    direction_mode: Literal["signal_based"] = "signal_based"
    default_lot: float = 0.01
    max_active_cycles_per_symbol: int = 1
    entry_model: Literal["hybrid", "distance_only"] = "hybrid"

    # Exit mode: individual TP per posisi, atau basket TP gabungan
    exit_mode: Literal["individual", "basket"] = "individual"
    basket_target_usd: float = 10.00
    tp_mode: Literal["individual", "basket"] = "individual"  # sync with exit_mode
    individual_tp_points: int = 300
    individual_sl_enabled: bool = False  # Default: NO SL
    individual_sl_points: int = 0

    # Account 1 multi-layer (M1 timeframe)
    account1_max_layers: int = 3
    account1_layer_distance_points: int = 200  # Adverse move antar layer (XAUUSD: 200 pts = ~$2)
    account1_signal_type: str = "fractal_sr"   # fractal_sr | ema_cross | distance_only
    account1_timeframe: str = "M1"

    # Account 2 multi-layer (M15 timeframe)
    account2_max_layers: int = 3
    account2_activate_distance_points: int = 400  # Adverse dari last layer Akun1
    account2_layer_distance_points: int = 300
    account2_signal_type: str = "ema_cross"
    account2_timeframe: str = "M15"

    # Account 3 multi-layer (M15 timeframe)
    account3_max_layers: int = 3
    account3_activate_distance_points: int = 500  # Adverse dari last layer Akun2
    account3_layer_distance_points: int = 400
    account3_signal_type: str = "ema_cross"
    account3_timeframe: str = "M15"

    # Legacy/compat - backward compatible
    stage2_distance_points: int = 200
    stage3_distance_points: int = 200
    confirmation_zone_points: int = 30

    trailing_enabled: bool = False
    trailing_start_points: int = 150
    trailing_distance_points: int = 100
    trailing_step_points: int = 10

    max_spread_points: int = 50
    signal_max_age_seconds: int = 60
    deviation_points: int = 10

    @property
    def max_layers_per_account(self) -> int:
        """Backward compat — returns max of all account max_layers."""
        return max(self.account1_max_layers, self.account2_max_layers, self.account3_max_layers)


class RiskConfig(BaseModel):
    aggregate_protection_enabled: bool = True
    require_operator_resume_after_critical_trigger: bool = True
    block_entries_if_account_stale: bool = True
    account_stale_threshold_seconds: int = 30

    max_layers_per_account: int = 3
    max_volume_per_account: float = 0.03
    max_floating_loss_per_account_usd: Optional[float] = None
    max_drawdown_per_account_pct: Optional[float] = None
    min_margin_level_pct: Optional[float] = None
    max_order_failures_per_window: int = 5
    order_failure_window_seconds: int = 300

    max_strategy_positions: int = 9
    max_strategy_volume: Optional[float] = None
    max_strategy_floating_loss_usd: Optional[float] = None
    max_strategy_drawdown_pct: Optional[float] = None

    def mandatory_live_fields_ok(self) -> tuple[bool, list[str]]:
        """Check mandatory risk fields before live trading. Returns (ok, missing_fields)."""
        missing: list[str] = []
        checks = {
            "max_floating_loss_per_account_usd": self.max_floating_loss_per_account_usd,
            "max_drawdown_per_account_pct": self.max_drawdown_per_account_pct,
            "min_margin_level_pct": self.min_margin_level_pct,
            "max_strategy_volume": self.max_strategy_volume,
            "max_strategy_floating_loss_usd": self.max_strategy_floating_loss_usd,
            "max_strategy_drawdown_pct": self.max_strategy_drawdown_pct,
        }
        for name, val in checks.items():
            if val is None:
                missing.append(f"risk.{name}")
        return len(missing) == 0, missing


class AccountConfig(BaseModel):
    alias: str
    role: Literal["primary", "secondary", "tertiary"]
    enabled: bool = True
    symbol: Optional[str] = None  # Broker specific symbol override (e.g. XAUUSDm)
    login: Optional[int] = None
    server: Optional[str] = None
    currency: str = "USD"
    is_cent: bool = False
    cent_divisor: int = 100
    magic_number: int
    max_layers: int = 3
    default_lot: float = 0.01

    def normalize_amount(self, raw_amount: float) -> float:
        """Convert cent-account displayed amount to USD-equivalent."""
        if self.is_cent:
            return raw_amount / self.cent_divisor
        return raw_amount


# ---------------------------------------------------------------------------
# Root config
# ---------------------------------------------------------------------------

class STMAConfig(BaseModel):
    app: AppConfig = Field(default_factory=AppConfig)
    server: ServerConfig = Field(default_factory=ServerConfig)
    strategy: StrategyConfig = Field(default_factory=StrategyConfig)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    accounts: List[AccountConfig] = []

    @model_validator(mode="after")
    def validate_live_trading(self) -> "STMAConfig":
        if self.app.live_trading_enabled:
            if self.app.environment != "live":
                raise ValueError(
                    "live_trading_enabled=true requires environment='live'"
                )
            ok, missing = self.risk.mandatory_live_fields_ok()
            if not ok:
                raise ValueError(
                    f"Cannot enable live trading. These risk fields are not configured: "
                    f"{', '.join(missing)}. "
                    f"Set values in config.yaml before enabling live trading."
                )
        return self

    @model_validator(mode="after")
    def validate_accounts(self) -> "STMAConfig":
        roles = [a.role for a in self.accounts if a.enabled]
        if "primary" not in roles:
            raise ValueError("At least one account with role='primary' must be enabled.")
        aliases = [a.alias for a in self.accounts]
        if len(aliases) != len(set(aliases)):
            raise ValueError("Account aliases must be unique.")
        return self

    def get_account(self, alias: str) -> Optional[AccountConfig]:
        for a in self.accounts:
            if a.alias == alias:
                return a
        return None

    def primary_account(self) -> Optional[AccountConfig]:
        for a in self.accounts:
            if a.role == "primary" and a.enabled:
                return a
        return None

    def secondary_account(self) -> Optional[AccountConfig]:
        for a in self.accounts:
            if a.role == "secondary" and a.enabled:
                return a
        return None

    def tertiary_account(self) -> Optional[AccountConfig]:
        for a in self.accounts:
            if a.role == "tertiary" and a.enabled:
                return a
        return None


# ---------------------------------------------------------------------------
# Loader
# ---------------------------------------------------------------------------

_config_cache: Optional[STMAConfig] = None


def load_config(path: Path = CONFIG_PATH, force_reload: bool = False) -> STMAConfig:
    """Load and validate config from YAML file. Cached after first load."""
    global _config_cache
    if _config_cache is not None and not force_reload:
        return _config_cache

    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {path}")

    with open(path, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if raw is None:
        raise ValueError("Config file is empty.")

    # Environment variable overrides for cloud & server deployment
    import os
    if "STMA_SERVER_HOST" in os.environ:
        raw.setdefault("server", {})["host"] = os.environ["STMA_SERVER_HOST"]
    if "STMA_SERVER_PORT" in os.environ:
        raw.setdefault("server", {})["port"] = int(os.environ["STMA_SERVER_PORT"])
    elif "PORT" in os.environ:
        raw.setdefault("server", {})["port"] = int(os.environ["PORT"])
    if "STMA_AUTH_TOKEN" in os.environ:
        raw.setdefault("server", {})["ea_auth_token"] = os.environ["STMA_AUTH_TOKEN"]

    cfg = STMAConfig.model_validate(raw)
    _config_cache = cfg
    return cfg


def get_config() -> STMAConfig:
    """FastAPI dependency — returns the loaded config."""
    return load_config()
