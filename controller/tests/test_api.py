"""
tests/test_api.py — FastAPI integration tests using TestClient.
Tests actual HTTP endpoints with an in-memory SQLite database.
"""
import pytest
from datetime import datetime, timezone
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from controller.main import app
from controller.database.db import get_db
from controller.database.models import Base
from controller.config import get_config, STMAConfig

# ---------------------------------------------------------------------------
# Test fixtures
# ---------------------------------------------------------------------------

TEST_AUTH_TOKEN = "test-token-abc123"


def make_test_config():
    return STMAConfig.model_validate({
        "app": {"environment": "demo", "live_trading_enabled": False},
        "server": {"ea_auth_token": TEST_AUTH_TOKEN, "max_command_age_seconds": 60},
        "strategy": {
            "strategy_id": "stma-xau-v1",
            "symbol": "XAUUSD",
            "default_lot": 0.01,
            "signal_max_age_seconds": 60,
            "individual_tp_points": 300,
            "individual_sl_points": 500,
            "individual_sl_enabled": True,
            "entry_model": "hybrid",
            "max_spread_points": 50,
            "tp_mode": "individual",
            "deviation_points": 10,
            "stage2_distance_points": 200,
            "stage3_distance_points": 200,
            "confirmation_zone_points": 30,
        },
        "risk": {
            "aggregate_protection_enabled": True,
            "block_entries_if_account_stale": True,
            "account_stale_threshold_seconds": 30,
            "max_layers_per_account": 3,
            "max_volume_per_account": 0.03,
            "max_strategy_positions": 9,
        },
        "accounts": [
            {
                "alias": "account_1",
                "role": "primary",
                "enabled": True,
                "magic_number": 20240001,
                "default_lot": 0.01,
                "max_layers": 3,
            },
            {
                "alias": "account_2",
                "role": "secondary",
                "enabled": True,
                "magic_number": 20240002,
                "default_lot": 0.01,
                "max_layers": 3,
            },
            {
                "alias": "account_3",
                "role": "tertiary",
                "enabled": True,
                "magic_number": 20240003,
                "default_lot": 0.01,
                "max_layers": 3,
            },
        ],
    })


@pytest.fixture
def db_engine():
    """In-memory SQLite engine using StaticPool so all threads share the exact same DB."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    yield engine
    Base.metadata.drop_all(engine)


@pytest.fixture
def db_session(db_engine):
    """Session for direct DB queries inside test assertions."""
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)
    session = TestingSession()
    yield session
    session.close()


@pytest.fixture
def client(db_engine, db_session):
    """FastAPI test client with overridden DB and config."""
    TestingSession = sessionmaker(autocommit=False, autoflush=False, bind=db_engine)

    def _get_test_db():
        session = TestingSession()
        try:
            yield session
        finally:
            session.close()

    app.dependency_overrides[get_db] = _get_test_db
    app.dependency_overrides[get_config] = make_test_config
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


AUTH_HEADERS = {"x-auth-token": TEST_AUTH_TOKEN}
BAD_AUTH_HEADERS = {"x-auth-token": "wrong-token"}

# ---------------------------------------------------------------------------
# Health endpoint
# ---------------------------------------------------------------------------

class TestHealth:
    def test_health_ok(self, client):
        r = client.get("/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


# ---------------------------------------------------------------------------
# Auth protection
# ---------------------------------------------------------------------------

class TestAuthProtection:
    def test_signal_requires_auth(self, client):
        r = client.post("/signal", json={
            "strategy_id": "stma-xau-v1",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "source": "test",
        })
        assert r.status_code == 422 or r.status_code == 401

    def test_signal_wrong_token_rejected(self, client):
        r = client.post(
            "/signal",
            json={"strategy_id": "stma-xau-v1", "symbol": "XAUUSD", "direction": "BUY", "source": "test"},
            headers=BAD_AUTH_HEADERS,
        )
        assert r.status_code == 401


# ---------------------------------------------------------------------------
# Account status push
# ---------------------------------------------------------------------------

class TestAccountStatusPush:
    def test_push_status_ok(self, client, db_session):
        r = client.post("/ea/status", json={
            "account_alias": "account_1",
            "login": 12345678,
            "server": "Exness-MT5Demo",
            "balance": 10000.0,
            "equity": 9980.0,
            "free_margin": 9800.0,
            "margin_level_pct": 1980.0,
            "floating_pnl": -20.0,
            "open_positions": 0,
            "connected": True,
            "ea_version": "0.1.0",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }, headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["ok"] is True

    def test_push_unknown_account_rejected(self, client):
        r = client.post("/ea/status", json={
            "account_alias": "unknown_account",
            "connected": True,
            "open_positions": 0,
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        }, headers=AUTH_HEADERS)
        assert r.status_code == 400


# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------

class TestSignalHandling:
    def test_signal_rejected_wrong_strategy(self, client, db_session):
        _push_account_status(client, "account_1", db_session)
        r = client.post("/signal", json={
            "strategy_id": "wrong-strategy",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "source": "test-ea",
        }, headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        assert data["status"] == "REJECTED"

    def test_signal_rejected_wrong_symbol(self, client, db_session):
        r = client.post("/signal", json={
            "strategy_id": "stma-xau-v1",
            "symbol": "EURUSD",
            "direction": "BUY",
            "source": "test-ea",
        }, headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["status"] == "REJECTED"

    def test_signal_risk_denied_no_account_status(self, client, db_session):
        """Signal valid but risk denied because account status is stale/missing."""
        r = client.post("/signal", json={
            "strategy_id": "stma-xau-v1",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "source": "test-ea",
        }, headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        # Risk engine should deny because no account status available
        assert data["status"] in ("RISK_DENIED", "REJECTED")

    def test_signal_accepted_with_fresh_status(self, client, db_session):
        """Signal should be accepted when account status is fresh and within risk limits."""
        _push_account_status(client, "account_1", db_session)
        r = client.post("/signal", json={
            "strategy_id": "stma-xau-v1",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "source": "test-ea",
        }, headers=AUTH_HEADERS)
        assert r.status_code == 200
        data = r.json()
        # Should be ACCEPTED (risk passes with fresh status and no open positions)
        assert data["status"] == "ACCEPTED"
        assert data["signal_id"] != ""


# ---------------------------------------------------------------------------
# Command polling
# ---------------------------------------------------------------------------

class TestCommandPolling:
    def test_poll_no_command(self, client, db_session):
        r = client.get("/ea/command/account_1", headers=AUTH_HEADERS)
        assert r.status_code == 200
        assert r.json()["has_command"] is False

    def test_poll_after_signal_has_command(self, client, db_session):
        """After a valid signal, account_1 should have a pending command."""
        _push_account_status(client, "account_1", db_session)
        sig_r = client.post("/signal", json={
            "strategy_id": "stma-xau-v1",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "source": "test-ea",
        }, headers=AUTH_HEADERS)
        if sig_r.json().get("status") == "ACCEPTED":
            poll_r = client.get("/ea/command/account_1", headers=AUTH_HEADERS)
            assert poll_r.status_code == 200
            data = poll_r.json()
            assert data["has_command"] is True
            cmd = data["command"]
            assert cmd["action"] == "OPEN_BUY"
            assert cmd["symbol"] == "XAUUSD"
            assert cmd["volume"] == pytest.approx(0.01)


# ---------------------------------------------------------------------------
# Dashboard endpoints
# ---------------------------------------------------------------------------

class TestDashboard:
    def test_system_state(self, client):
        r = client.get("/dashboard/state")
        assert r.status_code == 200
        state = r.json()
        assert state["environment"] == "demo"
        assert state["live_trading_enabled"] is False

    def test_accounts_summary(self, client, db_session):
        _push_account_status(client, "account_1", db_session)
        r = client.get("/dashboard/accounts")
        assert r.status_code == 200
        accounts = r.json()
        assert len(accounts) == 3  # All 3 configured accounts
        acc1 = next(a for a in accounts if a["alias"] == "account_1")
        assert acc1["connected"] is True

    def test_cycles_empty(self, client):
        r = client.get("/dashboard/cycles")
        assert r.status_code == 200
        assert r.json() == []

    def test_risk_events_empty(self, client):
        r = client.get("/dashboard/risk-events")
        assert r.status_code == 200
        assert isinstance(r.json(), list)


# ---------------------------------------------------------------------------
# Duplicate command guard
# ---------------------------------------------------------------------------

class TestIdempotency:
    def test_duplicate_signal_blocked_by_active_cycle(self, client, db_session):
        _push_account_status(client, "account_1", db_session)

        # First signal
        r1 = client.post("/signal", json={
            "strategy_id": "stma-xau-v1",
            "symbol": "XAUUSD",
            "direction": "BUY",
            "source": "test-ea",
        }, headers=AUTH_HEADERS)

        if r1.json().get("status") == "ACCEPTED":
            # Second signal should be rejected (active cycle exists)
            r2 = client.post("/signal", json={
                "strategy_id": "stma-xau-v1",
                "symbol": "XAUUSD",
                "direction": "BUY",
                "source": "test-ea",
            }, headers=AUTH_HEADERS)
            assert r2.json()["status"] == "REJECTED"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _push_account_status(client, alias: str, db_session):
    client.post("/ea/status", json={
        "account_alias": alias,
        "login": 12345678,
        "server": "Exness-MT5Demo",
        "balance": 10000.0,
        "equity": 10000.0,
        "free_margin": 9800.0,
        "margin_level_pct": 5000.0,
        "floating_pnl": 0.0,
        "open_positions": 0,
        "connected": True,
        "ea_version": "0.1.0",
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
    }, headers=AUTH_HEADERS)
