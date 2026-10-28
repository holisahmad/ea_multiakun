"""
main.py — STMA Central Controller entry point.

Starts FastAPI app, initializes DB, and mounts all route modules.
"""
from __future__ import annotations

import logging
import logging.config
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

import asyncio
from .config import load_config, get_config
from .database.db import init_db, SessionLocal
from .database.models import PositionSnapshot
from .core.stage_evaluator import run_stage_evaluation
from fastapi.staticfiles import StaticFiles
from .api.routes_signal import router as signal_router
from .api.routes_command import router as command_router
from .api.routes_dashboard import router as dashboard_router
from .api.routes_control import router as control_router

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)-30s | %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S",
)
logger = logging.getLogger(__name__)


async def _stage_evaluator_loop() -> None:
    """Background periodic loop to evaluate active cycles for Stage 2/3 progression."""
    while True:
        try:
            await asyncio.sleep(3)
            cfg = get_config()
            db = SessionLocal()
            try:
                recent_pos = (
                    db.query(PositionSnapshot)
                    .filter(PositionSnapshot.symbol.startswith(cfg.strategy.symbol.upper()))
                    .order_by(PositionSnapshot.last_seen.desc())
                    .first()
                )
                prices = {}
                if recent_pos and recent_pos.current_price:
                    prices = {cfg.strategy.symbol.upper(): recent_pos.current_price}
                run_stage_evaluation(db, cfg, current_prices=prices)
            finally:
                db.close()
        except asyncio.CancelledError:
            break
        except Exception as exc:
            logger.debug("Stage evaluation loop error: %s", exc)


# ---------------------------------------------------------------------------
# Startup/shutdown lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator:
    """Initialize resources on startup, clean up on shutdown."""
    logger.info("=" * 60)
    logger.info("STMA Controller starting up")

    # Load and validate configuration
    try:
        cfg = load_config()
        logger.info(
            "Config loaded | env=%s live_trading=%s symbol=%s",
            cfg.app.environment,
            cfg.app.live_trading_enabled,
            cfg.strategy.symbol,
        )

        if cfg.app.live_trading_enabled:
            logger.warning("⚠ LIVE TRADING IS ENABLED — ensure all tests have passed")
        else:
            logger.info("✓ Demo mode — live trading is disabled")

        # Warn about unconfigured risk limits
        ok, missing = cfg.risk.mandatory_live_fields_ok()
        if not ok:
            logger.warning(
                "Risk limits not fully configured (OK for demo): %s",
                ", ".join(missing),
            )

    except Exception as exc:
        logger.critical("Failed to load config: %s", exc)
        raise

    # Initialize database
    init_db()
    logger.info("Database initialized: %s", "stma.db")

    # Start background tasks
    eval_task = asyncio.create_task(_stage_evaluator_loop())

    logger.info("=" * 60)
    logger.info("STMA Controller ready. API on http://127.0.0.1:8000")
    logger.info("Docs: http://127.0.0.1:8000/docs")
    logger.info("=" * 60)

    yield  # App is running

    eval_task.cancel()
    try:
        await eval_task
    except asyncio.CancelledError:
        pass

    logger.info("STMA Controller shutting down")


# ---------------------------------------------------------------------------
# FastAPI application
# ---------------------------------------------------------------------------
app = FastAPI(
    title="Smart Trade Multi Account Controller",
    description=(
        "Central coordination controller for STMA multi-account MT5 trading system. "
        "DEMO MODE by default. Not financial advice."
    ),
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# Allow dashboard to connect from file:// or localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Restrict in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---------------------------------------------------------------------------
# Mount routers
# ---------------------------------------------------------------------------
app.include_router(signal_router)
app.include_router(command_router)
app.include_router(dashboard_router)
app.include_router(control_router)

# Mount dashboard static files
import os
dashboard_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "dashboard")
if os.path.exists(dashboard_dir):
    app.mount("/dashboard-ui", StaticFiles(directory=dashboard_dir, html=True), name="dashboard-ui")


# ---------------------------------------------------------------------------
# Health check & Favicon
# ---------------------------------------------------------------------------
@app.get("/favicon.ico", include_in_schema=False)
def favicon():
    """Return empty 204 for favicon to keep browser console clean."""
    from fastapi.responses import Response
    return Response(status_code=204)


@app.get("/health", tags=["system"])
def health_check():
    """Basic health check endpoint."""
    return {"status": "ok", "service": "stma-controller", "version": "0.1.0"}


from fastapi.responses import JSONResponse, RedirectResponse

@app.get("/dashboard", include_in_schema=False)
@app.get("/dashboard/", include_in_schema=False)
def dashboard_redirect():
    return RedirectResponse(url="/dashboard-ui/")


@app.get("/", tags=["system"])
def root():
    return {
        "service": "Smart Trade Multi Account Controller",
        "version": "0.1.0",
        "dashboard": "/dashboard-ui/",
        "docs": "/docs",
        "health": "/health",
        "warning": "DEMO MODE. Not financial advice.",
    }


# ---------------------------------------------------------------------------
# Exception handlers
# ---------------------------------------------------------------------------
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logger.exception("Unhandled exception: %s", exc)
    return JSONResponse(
        status_code=500,
        content={"detail": "Internal server error. Check logs for details."},
    )
