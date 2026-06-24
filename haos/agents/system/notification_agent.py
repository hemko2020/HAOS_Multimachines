"""
Notification Agent — Envoi de notifications vers l'humain via Tailscale.
Modèle: NANO (très rapide, tâche simple)
Souscrit à human.notifications.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from agents.base_agent import BaseAgent
from agents.registry import register_agent_class

logger = logging.getLogger(__name__)

# Priorités des notifications
PRIORITY_INFO = "INFO"
PRIORITY_WARNING = "WARNING"
PRIORITY_CRITICAL = "CRITICAL"

# Emojis par priorité
PRIORITY_ICONS = {
    PRIORITY_INFO: "ℹ️",
    PRIORITY_WARNING: "⚠️",
    PRIORITY_CRITICAL: "🔴",
}


@register_agent_class("notification-01")
class NotificationAgent(BaseAgent):
    """
    Agent de notifications HAOS.

    - Souscrit à human.notifications
    - Formate les notifications selon leur priorité
    - Envoie via HTTP Tailscale vers iPhone/iPad
    - File les notifications (pas de spam)
    - Garde un historique des notifications envoyées
    """

    def __init__(self, identity: Any) -> None:
        super().__init__(identity)
        self._listen_task: asyncio.Task | None = None
        self._notification_queue: asyncio.Queue = asyncio.Queue(maxsize=100)
        self._send_task: asyncio.Task | None = None
        self._notification_history: list[dict[str, Any]] = []
        self._max_history: int = 100

    async def execute(self, task: str, context: dict[str, Any]) -> str:
        """
        Commandes de gestion des notifications.
        'send <message>'  → envoie une notification manuelle
        'history'         → affiche l'historique
        'test'            → envoie une notification de test
        """
        task_stripped = task.strip()

        if task_stripped.lower() == "test":
            await self.send_notification(
                message="🧪 Test de notification HAOS — Système opérationnel.",
                priority=PRIORITY_INFO,
            )
            return "Notification de test envoyée."
        elif task_stripped.lower() == "history":
            return self._format_history()
        elif task_stripped.lower().startswith("send "):
            msg = task_stripped[5:].strip()
            await self.send_notification(msg, PRIORITY_INFO)
            return f"Notification envoyée: {msg[:50]}"
        else:
            return await self.generate_simple(
                f"Tu gères les notifications HAOS. Réponds à: {task}"
            )

    # ─── Démarrage ────────────────────────────────────────────────────────────

    async def start(self) -> None:
        """Démarre l'écoute Redis et le worker d'envoi."""
        if self._listen_task and not self._listen_task.done():
            return

        self._listen_task = asyncio.create_task(
            self._listen_loop(),
            name="notification_listen",
        )
        self._send_task = asyncio.create_task(
            self._send_worker(),
            name="notification_sender",
        )
        logger.info("NotificationAgent: démarré.")

    async def stop(self) -> None:
        """Arrête l'agent proprement."""
        for task in (self._listen_task, self._send_task):
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
        logger.info("NotificationAgent: arrêté.")

    # ─── Écoute Redis ─────────────────────────────────────────────────────────

    async def _listen_loop(self) -> None:
        """Écoute le canal human.notifications."""
        try:
            from core.redis_bus import get_event_bus, CHANNEL_HUMAN_NOTIFICATIONS
            bus = await get_event_bus()

            async for event in bus.subscribe([CHANNEL_HUMAN_NOTIFICATIONS]):
                message = event.get("message", "")
                priority = event.get("priority", PRIORITY_INFO)
                source_agent = event.get("source_agent")

                if message:
                    await self._notification_queue.put({
                        "message": message,
                        "priority": priority,
                        "source_agent": source_agent,
                        "received_at": datetime.now(timezone.utc).isoformat(),
                    })
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("NotificationAgent: erreur écoute Redis: %s", exc)
            await asyncio.sleep(5)
            await self.start()

    # ─── Worker d'envoi ───────────────────────────────────────────────────────

    async def _send_worker(self) -> None:
        """Worker qui consomme la queue et envoie les notifications."""
        while True:
            try:
                notification = await self._notification_queue.get()
                await self.send_notification(
                    message=notification["message"],
                    priority=notification.get("priority", PRIORITY_INFO),
                    source_agent=notification.get("source_agent"),
                )
                self._notification_queue.task_done()

                # Petite pause anti-spam entre les envois
                await asyncio.sleep(0.5)

            except asyncio.CancelledError:
                raise
            except Exception as exc:
                logger.error(
                    "NotificationAgent: erreur envoi notification: %s", exc
                )

    # ─── Envoi de notification ────────────────────────────────────────────────

    async def send_notification(
        self,
        message: str,
        priority: str = PRIORITY_INFO,
        source_agent: str | None = None,
    ) -> bool:
        """
        Formate et envoie une notification vers l'iPhone/iPad via Tailscale.

        Args:
            message: Texte de la notification
            priority: INFO, WARNING ou CRITICAL
            source_agent: Agent émetteur (optionnel)

        Returns:
            True si envoyé avec succès.
        """
        # Formater le message
        icon = PRIORITY_ICONS.get(priority, "•")
        formatted = self._format_notification(message, priority, source_agent, icon)

        # Enregistrer dans l'historique
        self._add_to_history(message, priority, source_agent)

        # Envoyer via Tailscale HTTP
        success = await self._send_via_tailscale(formatted, priority)

        if success:
            logger.info(
                "Notification envoyée [%s]: %s", priority, message[:80]
            )
        else:
            logger.warning(
                "Notification non-envoyée (Tailscale indisponible) [%s]: %s",
                priority,
                message[:80],
            )

        return success

    async def _send_via_tailscale(
        self,
        message: str,
        priority: str,
    ) -> bool:
        """Envoie la notification HTTP vers l'endpoint Tailscale."""
        from core.config import settings
        import httpx

        if not settings.tailscale_notify_url:
            return False

        try:
            async with httpx.AsyncClient(timeout=5.0) as client:
                response = await client.post(
                    settings.tailscale_notify_url,
                    json={
                        "message": message,
                        "priority": priority,
                        "source": "HAOS",
                        "timestamp": datetime.now(timezone.utc).isoformat(),
                    },
                )
                return response.status_code in (200, 201, 204)
        except Exception as exc:
            logger.debug(
                "Tailscale indisponible pour notifications: %s", exc
            )
            return False

    # ─── Formatage et historique ──────────────────────────────────────────────

    def _format_notification(
        self,
        message: str,
        priority: str,
        source_agent: str | None,
        icon: str,
    ) -> str:
        """Formate une notification pour l'affichage."""
        ts = datetime.now(timezone.utc).strftime("%H:%M")
        agent_str = f" [{source_agent}]" if source_agent else ""
        return f"{icon} HAOS{agent_str} {ts}\n{message}"

    def _add_to_history(
        self,
        message: str,
        priority: str,
        source_agent: str | None,
    ) -> None:
        """Ajoute au journal d'historique (taille limitée)."""
        self._notification_history.append({
            "message": message,
            "priority": priority,
            "source_agent": source_agent,
            "sent_at": datetime.now(timezone.utc).isoformat(),
        })
        # Limiter la taille
        if len(self._notification_history) > self._max_history:
            self._notification_history = self._notification_history[-self._max_history:]

    def _format_history(self) -> str:
        """Formate l'historique des notifications."""
        if not self._notification_history:
            return "Aucune notification dans l'historique."

        lines = [f"=== Historique notifications ({len(self._notification_history)}) ==="]
        for n in self._notification_history[-20:]:  # 20 dernières
            ts = n.get("sent_at", "")[:16].replace("T", " ")
            icon = PRIORITY_ICONS.get(n.get("priority", "INFO"), "•")
            lines.append(f"[{ts}] {icon} {n['message'][:80]}")
        return "\n".join(lines)
