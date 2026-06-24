"""
Logger Agent — Journalisation structurée de toute l'activité HAOS.
Modèle: NANO
Souscrit à tous les canaux agents.* et écrit dans SQLite.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from agents.base_agent import BaseAgent
from agents.registry import register_agent_class

logger = logging.getLogger(__name__)


@register_agent_class("logger-01")
class LoggerAgent(BaseAgent):
    """
    Agent de journalisation centralisée HAOS.

    - Souscrit à agents.* (tous les événements agents)
    - Écrit des logs structurés dans SQLite (vault.event_log)
    - Utilise NANO pour générer des résumés périodiques
    - Fournit un accès aux logs récents via l'API
    """

    def __init__(self, identity: Any) -> None:
        super().__init__(identity)
        self._listen_task: asyncio.Task | None = None
        self._log_buffer: list[dict[str, Any]] = []
        self._buffer_flush_interval: int = 10  # secondes

    async def execute(self, task: str, context: dict[str, Any]) -> str:
        """
        Commandes de gestion des logs.
        'summary': résumé IA des dernières activités
        'stats': statistiques d'utilisation
        """
        task_lower = task.strip().lower()

        if task_lower == "summary":
            return await self._generate_summary()
        elif task_lower == "stats":
            return await self._get_stats()
        else:
            return await self.generate_simple(
                f"Tu es l'agent logger HAOS. Réponds à cette requête sur les logs: {task}"
            )

    # ─── Écoute des événements ────────────────────────────────────────────────

    async def start_listening(self) -> None:
        """Démarre la surveillance de tous les canaux agents."""
        if self._listen_task and not self._listen_task.done():
            return

        self._listen_task = asyncio.create_task(
            self._listen_loop(),
            name="logger_listen_loop",
        )
        asyncio.create_task(
            self._flush_loop(),
            name="logger_flush_loop",
        )
        logger.info("LoggerAgent: écoute démarrée sur agents.*")

    async def stop_listening(self) -> None:
        """Arrête l'écoute des canaux."""
        if self._listen_task:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
        # Flush final du buffer
        await self._flush_buffer()
        logger.info("LoggerAgent: écoute arrêtée.")

    async def _listen_loop(self) -> None:
        """Boucle d'écoute des événements Redis."""
        try:
            from core.redis_bus import get_event_bus
            bus = await get_event_bus()

            async for event in bus.subscribe(["agents.*"], pattern=True):
                await self._buffer_event(event)

        except asyncio.CancelledError:
            raise
        except Exception as exc:
            logger.error("LoggerAgent: erreur écoute: %s", exc)
            # Redémarrage automatique après 5s
            await asyncio.sleep(5)
            await self.start_listening()

    async def _flush_loop(self) -> None:
        """Flush périodique du buffer vers SQLite."""
        while True:
            await asyncio.sleep(self._buffer_flush_interval)
            await self._flush_buffer()

    # ─── Buffer et persistance ────────────────────────────────────────────────

    async def _buffer_event(self, event: dict[str, Any]) -> None:
        """Ajoute un événement au buffer (en mémoire)."""
        log_entry = {
            "timestamp": event.get("timestamp", datetime.now(timezone.utc).isoformat()),
            "channel": event.get("channel", "unknown"),
            "event_type": event.get("event_type", "unknown"),
            "agent_id": event.get("agent_id") or event.get("source_agent"),
            "data": {
                k: v
                for k, v in event.items()
                if k not in ("timestamp", "channel", "event_type", "agent_id")
            },
        }
        self._log_buffer.append(log_entry)

        # Flush si le buffer devient trop grand
        if len(self._log_buffer) >= 100:
            await self._flush_buffer()

    async def _flush_buffer(self) -> None:
        """Écrit les événements du buffer dans SQLite."""
        if not self._log_buffer:
            return

        entries = self._log_buffer.copy()
        self._log_buffer.clear()

        try:
            from memory.vault import get_vault
            vault = await get_vault()
            for entry in entries:
                await vault.log_event(
                    channel=entry["channel"],
                    event_type=entry["event_type"],
                    agent_id=entry["agent_id"],
                    data=entry["data"],
                    timestamp=entry["timestamp"],
                )
            logger.debug("LoggerAgent: %d événements écrits dans vault.", len(entries))
        except Exception as exc:
            logger.error("LoggerAgent: erreur écriture vault: %s", exc)
            # Remettre les entrées dans le buffer pour réessayer
            self._log_buffer = entries + self._log_buffer

    # ─── Résumé IA ────────────────────────────────────────────────────────────

    async def _generate_summary(self, limit: int = 50) -> str:
        """Génère un résumé IA des dernières activités."""
        try:
            from memory.vault import get_vault
            vault = await get_vault()
            events = await vault.get_events(limit=limit)

            if not events:
                return "Aucune activité récente à résumer."

            # Formater les événements pour le LLM
            events_text = "\n".join(
                f"[{e.get('timestamp', '')[:19]}] "
                f"{e.get('agent_id', '?')}: {e.get('event_type', '?')} "
                f"— canal: {e.get('channel', '?')}"
                for e in events[:50]
            )

            prompt = (
                f"Voici les {len(events)} dernières activités du système HAOS.\n\n"
                f"{events_text}\n\n"
                "Génère un résumé concis (5-10 lignes) de l'activité récente: "
                "quels agents ont été actifs, quelles tâches ont été effectuées, "
                "y a-t-il eu des erreurs ou des événements notables?"
            )
            return await self.generate_simple(prompt, max_tokens=512)

        except Exception as exc:
            return f"Erreur génération résumé: {exc}"

    async def _get_stats(self) -> str:
        """Retourne des statistiques d'utilisation."""
        try:
            from memory.vault import get_vault
            vault = await get_vault()
            events = await vault.get_events(limit=1000)

            # Compter par agent
            agent_counts: dict[str, int] = {}
            event_type_counts: dict[str, int] = {}

            for e in events:
                agent_id = e.get("agent_id", "unknown")
                event_type = e.get("event_type", "unknown")
                agent_counts[agent_id] = agent_counts.get(agent_id, 0) + 1
                event_type_counts[event_type] = event_type_counts.get(event_type, 0) + 1

            lines = ["=== Statistiques HAOS ===", f"Total événements: {len(events)}", ""]
            lines.append("Par agent (top 5):")
            for agent_id, count in sorted(
                agent_counts.items(), key=lambda x: x[1], reverse=True
            )[:5]:
                lines.append(f"  {agent_id}: {count} événements")

            lines.append("\nPar type:")
            for event_type, count in sorted(
                event_type_counts.items(), key=lambda x: x[1], reverse=True
            )[:10]:
                lines.append(f"  {event_type}: {count}")

            return "\n".join(lines)

        except Exception as exc:
            return f"Erreur récupération stats: {exc}"
