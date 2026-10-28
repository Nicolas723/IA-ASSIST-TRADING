"""Autonomous Bot & Execution Daemon API Router — v1"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query

from src.application.services.autonomous_engine import (
    AutonomousBotConfig,
    AutonomousBotStatus,
    AutonomousEngine,
    AutonomousLogEntry,
)
from src.infrastructure.persistence.repositories.engine_log_repository import (
    SqlEngineLogRepository,
)
from src.interfaces.api.v1.deps import get_autonomous_engine, get_engine_log_repository
from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

router = APIRouter()


@router.get("/status", response_model=AutonomousBotStatus)
async def get_autonomous_status(
    engine: Annotated[AutonomousEngine, Depends(get_autonomous_engine)],
) -> AutonomousBotStatus:
    """
    Get current running status, uptime, performance metrics and recent logs of the Autonomous Daemon.
    """
    return engine.get_status()


@router.post("/start", response_model=AutonomousBotStatus)
async def start_autonomous_bot(
    config: AutonomousBotConfig,
    engine: Annotated[AutonomousEngine, Depends(get_autonomous_engine)],
) -> AutonomousBotStatus:
    """
    Start the background Autonomous Trading loop with specific configuration.
    """
    return await engine.start(config)


@router.post("/stop", response_model=AutonomousBotStatus)
async def stop_autonomous_bot(
    engine: Annotated[AutonomousEngine, Depends(get_autonomous_engine)],
) -> AutonomousBotStatus:
    """
    Gracefully pause/stop the background Autonomous Trading loop.
    """
    return await engine.stop()


@router.post("/emergency-kill", response_model=AutonomousBotStatus)
async def emergency_kill_switch(
    engine: Annotated[AutonomousEngine, Depends(get_autonomous_engine)],
) -> AutonomousBotStatus:
    """
    Emergency Kill-Switch: Halt background daemon immediately and close all active paper positions.
    """
    return await engine.emergency_kill()


@router.get("/logs", response_model=list[AutonomousLogEntry])
async def get_autonomous_logs(
    engine: Annotated[AutonomousEngine, Depends(get_autonomous_engine)],
    repository: Annotated[SqlEngineLogRepository, Depends(get_engine_log_repository)],
    limit: int = Query(default=50, ge=1, le=500),
    before: datetime | None = Query(default=None, description="Page back from this time"),
) -> list[AutonomousLogEntry]:
    """Engine log, newest first.

    The in-memory list holds the latest entries (including those not yet
    flushed); the database holds the whole session history, so a past signal
    can still be explained hours later.
    """
    if before is not None and before.tzinfo is None:
        before = before.replace(tzinfo=UTC)
    entries: dict[str, AutonomousLogEntry] = {}
    try:
        for row in await repository.list_recent(limit=limit, before=before):
            entry = AutonomousLogEntry.model_validate(row)
            entries[entry.id] = entry
    except Exception as exc:  # pragma: no cover - defensive
        logger.warning("engine_logs_db_unavailable", error=str(exc))
    for entry in engine.recent_logs():
        if before is None or entry.timestamp < before:
            entries[entry.id] = entry
    ordered = sorted(entries.values(), key=lambda entry: entry.timestamp, reverse=True)
    return ordered[:limit]
