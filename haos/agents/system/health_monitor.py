"""
Health Monitor Agent — Surveillance de l'état du système HAOS.
Modèle: NANO (léger, rapide)
Fréquence: toutes les 60 secondes
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone
from typing import Any

from agents.base_agent import BaseAgent
from agents.registry import register_agent_class
from core.llm.types import ModelTier

logger = logging.getLogger(__name__)

# Services surveillés
MONITORED_SERVICES = ["redis", "apex", "qwythos", "nano", "api"]


@register_agent_class("health-monitor-01")
class HealthMonitorAgent(BaseAgent):
    """
    Agent de surveillance de la santé des services HAOS.

    Vérifie toutes les 60 secondes :
    - Serveurs llama.cpp (APEX, QWYTHOS, NANO)
    - Serveur Redis
    - API FastAPI elle-même

    Publie les résultats sur system.health
    Envoie une alerte sur human.notifications si un service tombe.
    """

    def __init__(self, identity: Any) -> None:
        super().__init__(identity)
        self._check_interval: int = 60  # secondes
        self._running: bool = False
        self._last_statuses: dict[str, bool] = {}
        self._monitor_task: asyncio.Task | None = None

    async def execute(self, task: str, context: dict[str, Any]) -> str:
        """Exécution manuelle d'un health check immédiat."""
        results = await self._check_all_services()
        report = self._format_report(results)
        return report

    # ─── Démarrage du monitoring continu ──────────────────────────────────────

    async def start_monitoring(self) -> None:
        """Démarre la boucle de surveillance en arrière-plan."""
        if self._running:
            return
        self._running = True
        self._monitor_task = asyncio.create_task(
            self._monitoring_loop(),
            name=f"health_monitor_{self.agent_id}",
        )
        logger.info(
            "HealthMonitorAgent: surveillance démarrée (intervalle=%ds)",
            self._check_interval,
        )

    async def stop_monitoring(self) -> None:
        """Arrête la boucle de surveillance."""
        self._running = False
        if self._monitor_task and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
        logger.info("HealthMonitorAgent: surveillance arrêtée.")

    async def _monitoring_loop(self) -> None:
        """Boucle principale de surveillance."""
        while self._running:
            try:
                results = await self._check_all_services()
                await self._publish_health_results(results)
                await self._check_for_alerts(results)
            except Exception as exc:
                logger.error("Erreur dans la boucle de surveillance: %s", exc)

            await asyncio.sleep(self._check_interval)

    # ─── Vérifications des services ───────────────────────────────────────────

    async def _check_all_services(self) -> dict[str, dict[str, Any]]:
        """Vérifie tous les services et retourne leurs statuts."""
        import httpx

        results: dict[str, dict[str, Any]] = {}

        # Vérifier Redis
        results["redis"] = await self._check_redis()

        # Vérifier les serveurs llama.cpp
        from core.config import settings
        llm_services = {
            "apex": settings.apex_base_url,
            "qwythos": settings.qwythos_base_url,
            "nano": settings.nano_base_url,
        }
        for name, base_url in llm_services.items():
            results[name] = await self._check_llm_server(name, base_url)

        # Vérifier l'API elle-même (toujours OK si on est ici)
        results["api"] = {
            "service": "api",
            "status": "up",
            "response_ms": 0.0,
            "checked_at": datetime.now(timezone.utc).isoformat(),
        }

        return results

    async def _check_redis(self) -> dict[str, Any]:
        """Vérifie la disponibilité de Redis."""
        import redis.asyncio as aioredis
        from core.config import settings

        try:
            client = aioredis.from_url(settings.redis_url, socket_timeout=2.0)
            start = asyncio.get_event_loop().time()
            await client.ping()
            ms = (asyncio.get_event_loop().time() - start) * 1000
            await client.aclose()
            return {
                "service": "redis",
                "status": "up",
                "response_ms": round(ms, 2),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as exc:
            return {
                "service": "redis",
                "status": "down",
                "error": str(exc),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    async def _check_llm_server(
        self,
        name: str,
        base_url: str,
    ) -> dict[str, Any]:
        """Vérifie la disponibilité d'un serveur llama.cpp."""
        import httpx
        import time

        try:
            start = time.monotonic()
            async with httpx.AsyncClient(timeout=3.0) as client:
                response = await client.get(f"{base_url}/health")
            ms = (time.monotonic() - start) * 1000

            if response.status_code == 200:
                return {
                    "service": name,
                    "status": "up",
                    "response_ms": round(ms, 2),
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
            else:
                return {
                    "service": name,
                    "status": "degraded",
                    "http_status": response.status_code,
                    "checked_at": datetime.now(timezone.utc).isoformat(),
                }
        except Exception as exc:
            return {
                "service": name,
                "status": "down",
                "error": str(exc),
                "checked_at": datetime.now(timezone.utc).isoformat(),
            }

    # ─── Publication et alertes ───────────────────────────────────────────────

    async def _publish_health_results(
        self,
        results: dict[str, dict[str, Any]],
    ) -> None:
        """Publie les résultats sur le canal system.health."""
        try:
            from core.redis_bus import get_event_bus
            bus = await get_event_bus()

            overall_status = (
                "up"
                if all(r.get("status") == "up" for r in results.values())
                else "degraded"
            )

            await bus.publish_health(
                service="haos",
                status=overall_status,
                details={
                    "services": results,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
            )
        except Exception as exc:
            logger.warning(
                "HealthMonitor: impossible de publier sur Redis: %s", exc
            )

    async def _check_for_alerts(
        self,
        results: dict[str, dict[str, Any]],
    ) -> None:
        """
        Compare les statuts actuels avec les précédents.
        Envoie une alerte si un service vient de tomber ou de revenir.
        """
        try:
            from core.redis_bus import get_event_bus
            bus = await get_event_bus()

            for service, data in results.items():
                current_up = data.get("status") == "up"
                previous_up = self._last_statuses.get(service)

                # Nouveau service ou changement d'état
                if previous_up is None:
                    self._last_statuses[service] = current_up
                    continue

                if previous_up and not current_up:
                    # Service vient de tomber
                    await bus.publish_notification(
                        message=f"🔴 SERVICE DOWN: {service.upper()} est indisponible! Erreur: {data.get('error', 'inconnu')}",
                        priority="CRITICAL",
                        agent_id=self.agent_id,
                    )
                    logger.critical(
                        "SERVICE DOWN: %s — %s", service, data.get("error")
                    )

                elif not previous_up and current_up:
                    # Service vient de revenir
                    await bus.publish_notification(
                        message=f"🟢 SERVICE UP: {service.upper()} est de nouveau disponible (réponse: {data.get('response_ms', '?')}ms)",
                        priority="INFO",
                        agent_id=self.agent_id,
                    )
                    logger.info("SERVICE RECOVERED: %s", service)

                self._last_statuses[service] = current_up

        except Exception as exc:
            logger.warning(
                "HealthMonitor: erreur lors de la vérification des alertes: %s", exc
            )

    # ─── Rapport ──────────────────────────────────────────────────────────────

    def _format_report(self, results: dict[str, dict[str, Any]]) -> str:
        """Formate un rapport de santé lisible."""
        lines = ["=== Rapport de Santé HAOS ==="]
        for service, data in results.items():
            status = data.get("status", "unknown")
            icon = "✅" if status == "up" else "❌"
            ms = data.get("response_ms")
            ms_str = f" ({ms}ms)" if ms is not None else ""
            error = data.get("error", "")
            error_str = f" — {error}" if error else ""
            lines.append(f"{icon} {service.upper()}: {status}{ms_str}{error_str}")

        all_up = all(r.get("status") == "up" for r in results.values())
        lines.append("")
        lines.append(
            "🟢 Tous les services opérationnels."
            if all_up
            else "🔴 Certains services sont défaillants!"
        )
        return "\n".join(lines)
