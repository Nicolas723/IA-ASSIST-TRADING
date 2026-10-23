"""Autonomous Bot & Execution Daemon API Router — v1"""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from src.application.services.autonomous_engine import (
    AutonomousBotConfig,
    AutonomousBotStatus,
    AutonomousEngine,
    AutonomousLogEntry,
)
from src.interfaces.api.v1.deps import get_autonomous_engine

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
) -> list[AutonomousLogEntry]:
    """
    Retrieve real-time event & decision logs from the autonomous bot.
    """
    return engine.get_status().logs
