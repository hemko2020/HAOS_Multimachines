"""
Routes d'événements SSE (Server-Sent Events) pour l'IHM Flutter.
GET /events/stream → flux SSE en temps réel des activités des agents
Implémentation native via StreamingResponse (pas de dépendance externe).
"""

from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator

from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/events", tags=["Events"])


@router.get("/stream")
async def events_stream(request: Request) -> StreamingResponse:
    """
    Flux SSE d'activité des agents HAOS pour l'IHM Flutter.

    Envoie :
    - Événements de démarrage/fin de tâche de chaque agent
    - Événements de santé système
    - Notifications human.notifications
    - Heartbeat toutes les 30s
    """
    return StreamingResponse(
        _event_generator(request),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


def _format_sse(event: str, data: str | dict) -> str:
    """Formate un événement au format SSE standard."""
    if isinstance(data, dict):
        data = json.dumps(data, ensure_ascii=False)
    return f"event: {event}\ndata: {data}\n\n"


async def _event_generator(request: Request) -> AsyncGenerator[str, None]:
    """Génère le flux d'événements SSE depuis Redis."""
    logger.info("Client SSE connecté: %s", request.client)

    queue: asyncio.Queue = asyncio.Queue(maxsize=500)

    async def heartbeat_producer() -> None:
        while True:
            await asyncio.sleep(30)
            await queue.put({
                "event": "heartbeat",
                "data": {
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            })

    async def redis_producer() -> None:
        try:
            from core.redis_bus import get_event_bus
            bus = await get_event_bus()
            async for event in bus.subscribe(
                ["agents.*", "system.health", "human.notifications"],
                pattern=True,
            ):
                await queue.put({
                    "event": _map_event_type(event),
                    "data": event,
                })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("SSE Redis producer erreur: %s", exc)

    heartbeat_task = asyncio.create_task(heartbeat_producer())
    redis_task = asyncio.create_task(redis_producer())

    try:
        # Événement initial de connexion
        yield _format_sse("connected", {
            "message": "HAOS Event Stream connecté",
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })

        while True:
            if await request.is_disconnected():
                logger.info("Client SSE déconnecté")
                break

            try:
                item = await asyncio.wait_for(queue.get(), timeout=1.0)
                yield _format_sse(
                    item.get("event", "event"),
                    item.get("data", {}),
                )
                queue.task_done()
            except asyncio.TimeoutError:
                continue

    finally:
        heartbeat_task.cancel()
        redis_task.cancel()
        logger.info("SSE: générateur terminé")


def _map_event_type(event: dict) -> str:
    """Mappe le type d'événement Redis vers un nom SSE."""
    channel = event.get("channel", "")
    event_type = event.get("event_type", "")

    if "human.notifications" in channel:
        return "notification"
    elif "system.health" in channel:
        return "health"
    elif event_type == "task_started":
        return "agent_started"
    elif event_type == "task_completed":
        return "agent_completed"
    elif event_type == "task_failed":
        return "agent_failed"
    else:
        return "agent_event"
