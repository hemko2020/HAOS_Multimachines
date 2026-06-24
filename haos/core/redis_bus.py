"""
Bus événementiel Redis pour HAOS.
Permet la communication asynchrone entre tous les 28 agents via pub/sub.

Canaux principaux :
  - human.commands        → commandes du CEO humain
  - human.notifications   → notifications vers l'humain
  - agents.csuite.*       → activité C-Suite
  - agents.dev.*          → activité développement
  - agents.marketing.*    → activité marketing
  - agents.system.*       → activité système
  - system.health         → santé des services
"""

from __future__ import annotations

import json
import asyncio
import logging
from datetime import datetime, timezone
from typing import AsyncGenerator, Any

import redis.asyncio as aioredis
from redis.asyncio.client import PubSub

from core.config import settings

logger = logging.getLogger(__name__)

# ─── Canaux standards ─────────────────────────────────────────────────────────

CHANNEL_HUMAN_COMMANDS = "human.commands"
CHANNEL_HUMAN_NOTIFICATIONS = "human.notifications"
CHANNEL_SYSTEM_HEALTH = "system.health"
CHANNEL_AGENTS_ALL = "agents.*"


class RedisEventBus:
    """
    Bus d'événements Redis asynchrone.
    Fournit publish/subscribe pour la communication inter-agents.
    """

    def __init__(self, redis_url: str | None = None) -> None:
        self._url = redis_url or settings.redis_url
        self._client: aioredis.Redis | None = None
        self._connected: bool = False

    # ─── Connexion ────────────────────────────────────────────────────────────

    async def connect(self) -> None:
        """Établit la connexion au serveur Redis."""
        if self._connected:
            return
        self._client = aioredis.from_url(
            self._url,
            encoding="utf-8",
            decode_responses=True,
        )
        # Vérification de la connexion
        await self._client.ping()
        self._connected = True
        logger.info("RedisEventBus connecté à %s", self._url)

    async def disconnect(self) -> None:
        """Ferme la connexion Redis proprement."""
        if self._client and self._connected:
            await self._client.aclose()
            self._connected = False
            logger.info("RedisEventBus déconnecté.")

    def _ensure_connected(self) -> aioredis.Redis:
        """Garantit que le client est connecté."""
        if not self._connected or self._client is None:
            raise RuntimeError(
                "RedisEventBus non connecté. Appelez await connect() d'abord."
            )
        return self._client

    # ─── Publication ──────────────────────────────────────────────────────────

    async def publish(
        self,
        channel: str,
        event: dict[str, Any],
        source_agent: str | None = None,
    ) -> int:
        """
        Publie un événement JSON sur un canal Redis.

        Args:
            channel: Canal Redis (ex: "agents.csuite.ceo")
            event: Dictionnaire Python (sera sérialisé en JSON)
            source_agent: ID de l'agent émetteur (optionnel)

        Returns:
            Nombre d'abonnés ayant reçu le message.
        """
        client = self._ensure_connected()

        # Enrichissement automatique de l'événement
        payload: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "channel": channel,
            **event,
        }
        if source_agent:
            payload["source_agent"] = source_agent

        message = json.dumps(payload, ensure_ascii=False)
        receivers = await client.publish(channel, message)

        logger.debug(
            "Événement publié sur '%s' → %d récepteur(s): %s",
            channel,
            receivers,
            str(payload)[:200],
        )
        return receivers

    # ─── Souscription ─────────────────────────────────────────────────────────

    async def subscribe(
        self,
        channels: list[str],
        pattern: bool = False,
    ) -> AsyncGenerator[dict[str, Any], None]:
        """
        Souscrit à un ou plusieurs canaux Redis et génère les événements reçus.

        Args:
            channels: Liste de canaux (ou patterns si pattern=True)
            pattern:  Si True, utilise psubscribe (supporte wildcards *)

        Yields:
            Dictionnaires d'événements désérialisés depuis JSON.

        Example:
            async for event in bus.subscribe(["agents.*"], pattern=True):
                print(event)
        """
        client = self._ensure_connected()
        pubsub: PubSub = client.pubsub()

        if pattern:
            await pubsub.psubscribe(*channels)
        else:
            await pubsub.subscribe(*channels)

        logger.info(
            "Abonné aux canaux %s (pattern=%s)", channels, pattern
        )

        try:
            async for raw_message in pubsub.listen():
                # Filtrer les messages de contrôle (type subscribe/psubscribe)
                if raw_message["type"] not in ("message", "pmessage"):
                    continue

                data = raw_message.get("data")
                if not data:
                    continue

                try:
                    event = json.loads(data)
                except json.JSONDecodeError:
                    logger.warning(
                        "Message non-JSON reçu sur %s: %s",
                        raw_message.get("channel"),
                        data,
                    )
                    continue

                yield event

        finally:
            if pattern:
                await pubsub.punsubscribe(*channels)
            else:
                await pubsub.unsubscribe(*channels)
            await pubsub.aclose()

    # ─── Utilitaires ──────────────────────────────────────────────────────────

    async def publish_agent_event(
        self,
        agent_id: str,
        event_type: str,
        data: dict[str, Any],
        department: str = "system",
    ) -> int:
        """
        Raccourci pour publier un événement d'agent sur le bon canal.
        Canal: agents.<department>.<agent_id>
        """
        channel = f"agents.{department}.{agent_id}"
        return await self.publish(
            channel=channel,
            event={"event_type": event_type, "agent_id": agent_id, **data},
            source_agent=agent_id,
        )

    async def publish_notification(
        self,
        message: str,
        priority: str = "INFO",
        agent_id: str | None = None,
    ) -> int:
        """
        Publie une notification destinée à l'humain.
        Priorities: INFO, WARNING, CRITICAL
        """
        return await self.publish(
            channel=CHANNEL_HUMAN_NOTIFICATIONS,
            event={
                "message": message,
                "priority": priority,
                "source_agent": agent_id,
            },
        )

    async def publish_health(
        self,
        service: str,
        status: str,
        details: dict[str, Any] | None = None,
    ) -> int:
        """Publie un événement de santé système."""
        return await self.publish(
            channel=CHANNEL_SYSTEM_HEALTH,
            event={
                "service": service,
                "status": status,
                "details": details or {},
            },
        )

    async def get_redis_info(self) -> dict[str, Any]:
        """Retourne les informations de diagnostic Redis."""
        client = self._ensure_connected()
        info = await client.info()
        return {
            "version": info.get("redis_version"),
            "connected_clients": info.get("connected_clients"),
            "used_memory_human": info.get("used_memory_human"),
            "uptime_in_seconds": info.get("uptime_in_seconds"),
        }


# ─── Instance singleton globale ───────────────────────────────────────────────

_bus_instance: RedisEventBus | None = None


async def get_event_bus() -> RedisEventBus:
    """Retourne l'instance singleton du bus (connectée)."""
    global _bus_instance
    if _bus_instance is None:
        _bus_instance = RedisEventBus()
    if not _bus_instance._connected:
        await _bus_instance.connect()
    return _bus_instance


async def shutdown_event_bus() -> None:
    """Ferme proprement le bus événementiel."""
    global _bus_instance
    if _bus_instance and _bus_instance._connected:
        await _bus_instance.disconnect()
        _bus_instance = None
