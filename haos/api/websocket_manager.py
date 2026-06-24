"""
WebSocket Connection Manager pour HAOS.
Gère les connexions WebSocket actives pour le chat temps réel avec le CEO Agent.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import WebSocket

logger = logging.getLogger(__name__)


class WebSocketManager:
    """
    Gestionnaire de connexions WebSocket.

    Maintient les connexions actives et permet le broadcast
    de messages vers tous les clients connectés ou vers
    un client spécifique.
    """

    def __init__(self) -> None:
        # {connection_id: WebSocket}
        self._connections: dict[str, WebSocket] = {}
        self._connection_counter: int = 0

    async def connect(self, websocket: WebSocket) -> str:
        """
        Accepte une nouvelle connexion WebSocket.

        Args:
            websocket: Instance WebSocket FastAPI

        Returns:
            ID unique de la connexion.
        """
        await websocket.accept()
        self._connection_counter += 1
        connection_id = f"ws_{self._connection_counter}"
        self._connections[connection_id] = websocket

        logger.info(
            "WebSocket connecté: %s (total: %d)",
            connection_id,
            len(self._connections),
        )
        return connection_id

    async def disconnect(self, connection_id: str) -> None:
        """Ferme et supprime une connexion."""
        if connection_id in self._connections:
            del self._connections[connection_id]
            logger.info(
                "WebSocket déconnecté: %s (restant: %d)",
                connection_id,
                len(self._connections),
            )

    async def send_to(
        self,
        connection_id: str,
        message: dict[str, Any],
    ) -> bool:
        """
        Envoie un message JSON à une connexion spécifique.

        Returns:
            True si envoyé avec succès.
        """
        websocket = self._connections.get(connection_id)
        if not websocket:
            return False
        try:
            await websocket.send_json(message)
            return True
        except Exception as exc:
            logger.warning(
                "Erreur envoi WebSocket %s: %s", connection_id, exc
            )
            await self.disconnect(connection_id)
            return False

    async def broadcast(self, message: dict[str, Any]) -> int:
        """
        Envoie un message JSON à toutes les connexions actives.

        Returns:
            Nombre de connexions ayant reçu le message.
        """
        if not self._connections:
            return 0

        disconnected: list[str] = []
        sent_count: int = 0

        for connection_id, websocket in list(self._connections.items()):
            try:
                await websocket.send_json(message)
                sent_count += 1
            except Exception:
                disconnected.append(connection_id)

        # Nettoyer les connexions mortes
        for cid in disconnected:
            await self.disconnect(cid)

        return sent_count

    async def broadcast_agent_event(
        self,
        event_type: str,
        agent_id: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """Raccourci pour broadcaster un événement d'agent."""
        await self.broadcast({
            "type": "agent_event",
            "event_type": event_type,
            "agent_id": agent_id,
            "data": data or {},
        })

    @property
    def active_connections_count(self) -> int:
        return len(self._connections)

    @property
    def connection_ids(self) -> list[str]:
        return list(self._connections.keys())


# Singleton global
ws_manager = WebSocketManager()
