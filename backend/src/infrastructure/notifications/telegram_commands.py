"""Telegram chat commands through Bot API long polling.

Long polling needs no public URL, so it works on a laptop as well as on a VPS.
Only messages from the configured chat are answered; anything else is ignored.
Credentials are read on every poll, so changing them from the UI takes effect
without a restart.
"""

from __future__ import annotations

import asyncio
import contextlib
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from src.shared.logging.setup import get_logger

logger = get_logger(__name__)

CommandHandler = Callable[[str], Awaitable[str]]
Credentials = Callable[[], tuple[str, str, bool]]

POLL_TIMEOUT_SECONDS = 25
IDLE_SECONDS = 30

COMMANDS = [
    {"command": "estado", "description": "Saldo, posiciones y estado del asistente"},
    {"command": "resumen", "description": "Resultado de hoy"},
    {"command": "pausar", "description": "Detener el asistente"},
    {"command": "emergencia", "description": "Cerrar todas las posiciones"},
    {"command": "ayuda", "description": "Lista de comandos"},
]


class TelegramCommandListener:
    """Poll ``getUpdates`` and answer commands from the authorised chat."""

    def __init__(
        self,
        *,
        credentials: Credentials,
        handler: CommandHandler,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._credentials = credentials
        self._handler = handler
        self._transport = transport
        self._task: asyncio.Task[None] | None = None
        self._offset: int | None = None
        self._registered_token = ""

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._run(), name="telegram-commands")

    async def stop(self) -> None:
        task, self._task = self._task, None
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    async def _run(self) -> None:
        async with httpx.AsyncClient(
            timeout=POLL_TIMEOUT_SECONDS + 10, transport=self._transport
        ) as client:
            while True:
                token, chat_id, enabled = self._credentials()
                if not (enabled and token and chat_id):
                    await asyncio.sleep(IDLE_SECONDS)
                    continue
                try:
                    await self.poll_once(client, token, chat_id)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    logger.warning("telegram_commands_poll_failed", error=str(exc))
                    await asyncio.sleep(IDLE_SECONDS)

    async def poll_once(self, client: httpx.AsyncClient, token: str, chat_id: str) -> int:
        """One long poll; returns how many commands were answered."""
        base = f"https://api.telegram.org/bot{token}"
        if self._registered_token != token:
            await self._register(client, base)
            self._registered_token = token
            # Commands sent while the backend was down are stale (a /pausar
            # from yesterday must not fire now), so start after the backlog.
            self._offset = await self._latest_offset(client, base)

        params: dict[str, Any] = {"timeout": POLL_TIMEOUT_SECONDS, "allowed_updates": '["message"]'}
        if self._offset is not None:
            params["offset"] = self._offset
        response = await client.get(f"{base}/getUpdates", params=params)
        if response.status_code == 409:
            # Another process polls this bot (e.g. a VPS and a laptop at once).
            logger.warning("telegram_commands_conflict")
            await asyncio.sleep(IDLE_SECONDS)
            return 0
        response.raise_for_status()
        answered = 0
        for update in response.json().get("result", []):
            self._offset = int(update["update_id"]) + 1
            message = update.get("message") or {}
            text = str(message.get("text") or "")
            if str((message.get("chat") or {}).get("id")) != chat_id.strip():
                continue
            if not text.startswith("/"):
                continue
            try:
                reply = await self._handler(text)
            except Exception as exc:
                logger.warning("telegram_command_failed", command=text[:32], error=str(exc))
                reply = "⚠️ No se pudo completar el comando. Revisa el backend."
            await client.post(
                f"{base}/sendMessage",
                json={
                    "chat_id": chat_id,
                    "text": reply,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": True,
                },
            )
            answered += 1
        return answered

    async def _latest_offset(self, client: httpx.AsyncClient, base: str) -> int | None:
        response = await client.get(f"{base}/getUpdates", params={"offset": -1, "timeout": 0})
        response.raise_for_status()
        updates = response.json().get("result", [])
        return int(updates[-1]["update_id"]) + 1 if updates else None

    async def _register(self, client: httpx.AsyncClient, base: str) -> None:
        """Show the command menu in the Telegram client (best-effort)."""
        try:
            await client.post(f"{base}/setMyCommands", json={"commands": COMMANDS})
        except Exception as exc:
            logger.info("telegram_set_commands_failed", error=str(exc))
