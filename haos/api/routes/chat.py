"""
Routes de chat avec le CEO Agent.
POST /chat           → requête/réponse standard
WS   /ws             → WebSocket bidirectionnel temps réel
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from pydantic import BaseModel

from api.websocket_manager import ws_manager

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Chat"])


class ChatRequest(BaseModel):
    message: str
    context: dict[str, Any] = {}
    stream: bool = False


class ChatResponse(BaseModel):
    response: str
    agent_id: str = "ceo-01"
    success: bool = True
    error: str | None = None
    execution_time_ms: float = 0.0


@router.post("/chat")
async def chat(request: ChatRequest) -> ChatResponse:
    """
    Envoie un message au CEO Agent et retourne sa réponse.

    Point d'entrée principal pour les commandes humaines via l'IHM Flutter.
    """
    from agents.registry import get_registry
    registry = get_registry()
    ceo = registry.get_agent("ceo-01")

    if ceo is None:
        return ChatResponse(
            response="CEO Agent non disponible. Vérifiez que le système HAOS est démarré.",
            success=False,
            error="CEO agent not found",
        )

    result = await ceo.run(
        task=request.message,
        context=request.context,
    )

    # Broadcaster l'activité aux clients WebSocket connectés
    await ws_manager.broadcast_agent_event(
        event_type="chat_response",
        agent_id="ceo-01",
        data={
            "message": request.message,
            "response_length": len(result.output),
            "execution_ms": result.execution_time_ms,
        },
    )

    return ChatResponse(
        response=result.output,
        agent_id="ceo-01",
        success=result.success,
        error=result.error,
        execution_time_ms=result.execution_time_ms,
    )


@router.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket) -> None:
    """
    WebSocket bidirectionnel pour le chat temps réel avec le CEO Agent.

    Protocol de messages (JSON) :

    Client → Serveur :
      {"type": "message", "content": "...", "context": {...}}
      {"type": "clear_history"}
      {"type": "ping"}

    Serveur → Client :
      {"type": "pong"}
      {"type": "typing"}                    → CEO commence à réfléchir
      {"type": "response", "content": "..."} → réponse complète
      {"type": "agent_event", "event_type": "...", "agent_id": "...", "data": {...}}
      {"type": "error", "message": "..."}
    """
    connection_id = await ws_manager.connect(websocket)

    # Message de bienvenue
    await ws_manager.send_to(connection_id, {
        "type": "connected",
        "connection_id": connection_id,
        "message": "Connexion HAOS établie. CEO Agent prêt.",
    })

    try:
        from agents.registry import get_registry
        registry = get_registry()
        ceo = registry.get_agent("ceo-01")

        while True:
            # Recevoir le message client
            raw_data = await websocket.receive_text()

            try:
                data = json.loads(raw_data)
            except json.JSONDecodeError:
                await ws_manager.send_to(connection_id, {
                    "type": "error",
                    "message": "Format JSON invalide.",
                })
                continue

            msg_type = data.get("type", "message")

            # ── Ping/Pong ──────────────────────────────────────────
            if msg_type == "ping":
                await ws_manager.send_to(connection_id, {"type": "pong"})
                continue

            # ── Effacer l'historique ───────────────────────────────
            elif msg_type == "clear_history":
                if ceo is not None:
                    from agents.csuite.ceo_agent import CEOAgent
                    if isinstance(ceo, CEOAgent):
                        ceo.clear_conversation()
                await ws_manager.send_to(connection_id, {
                    "type": "system",
                    "message": "Historique de conversation effacé.",
                })
                continue

            # ── Message texte ──────────────────────────────────────
            elif msg_type == "message":
                content = data.get("content", "").strip()
                if not content:
                    continue

                if ceo is None:
                    await ws_manager.send_to(connection_id, {
                        "type": "error",
                        "message": "CEO Agent non disponible.",
                    })
                    continue

                # Indiquer que le CEO réfléchit
                await ws_manager.send_to(connection_id, {"type": "typing"})

                # Exécuter la demande
                result = await ceo.run(
                    task=content,
                    context=data.get("context", {}),
                )

                # Envoyer la réponse
                await ws_manager.send_to(connection_id, {
                    "type": "response",
                    "content": result.output,
                    "success": result.success,
                    "error": result.error,
                    "execution_ms": result.execution_time_ms,
                    "model_tier": result.model_tier.value,
                })

    except WebSocketDisconnect:
        logger.info("Client WebSocket déconnecté: %s", connection_id)
    except Exception as exc:
        logger.error("Erreur WebSocket %s: %s", connection_id, exc)
        try:
            await ws_manager.send_to(connection_id, {
                "type": "error",
                "message": f"Erreur serveur: {exc}",
            })
        except Exception:
            pass
    finally:
        await ws_manager.disconnect(connection_id)
