from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()


@router.get("/status", summary="Get overall system status")
async def get_status() -> dict[str, str]:
    return {
        "redis": "ok",
        "prometheus": "ok",
        "grafana": "ok",
        "loki": "ok",
        "jaeger": "ok",
    }


# In-memory store of active WebSocket connections for log streaming (placeholder).
active_connections: list[WebSocket] = []


@router.websocket("/ws/logs")
async def websocket_logs(ws: WebSocket) -> None:
    await ws.accept()
    active_connections.append(ws)
    try:
        while True:
            # Placeholder: keep connection alive; real implementation would push log entries.
            data = await ws.receive_text()
            await ws.send_text(f"echo: {data}")
    except WebSocketDisconnect:
        active_connections.remove(ws)
