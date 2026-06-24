"""
Scheduler Agent — Planificateur de tâches HAOS.
Modèle: NANO (léger)
Gère les tâches récurrentes via APScheduler + SQLite vault.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from agents.registry import register_agent_class

logger = logging.getLogger(__name__)

# Tâches planifiées par défaut
DEFAULT_SCHEDULES = [
    {
        "id": "daily_briefing",
        "name": "Briefing quotidien",
        "agent_id": "ceo-01",
        "task": "Prépare le briefing quotidien de la startup: résume les activités d'hier, les priorités d'aujourd'hui, et les alertes importantes.",
        "trigger": "cron",
        "hour": 8,
        "minute": 0,
    },
    {
        "id": "market_research_update",
        "name": "Mise à jour veille marché",
        "agent_id": "market-research-01",
        "task": "Effectue une veille marché rapide: nouvelles tendances IA, actualités concurrents, opportunités.",
        "trigger": "interval",
        "hours": 4,
    },
    {
        "id": "health_check",
        "name": "Vérification santé système",
        "agent_id": "health-monitor-01",
        "task": "Vérifier l'état de tous les services HAOS.",
        "trigger": "interval",
        "hours": 1,
    },
    {
        "id": "weekly_report",
        "name": "Rapport hebdomadaire",
        "agent_id": "analytics-01",
        "task": "Génère le rapport hebdomadaire de la startup: KPIs, avancement, blocages, recommandations.",
        "trigger": "cron",
        "day_of_week": "mon",
        "hour": 9,
        "minute": 0,
    },
]


@register_agent_class("scheduler-01")
class SchedulerAgent(BaseAgent):
    """
    Agent planificateur de tâches HAOS.

    Fonctionnalités :
    - Démarre/arrête APScheduler
    - Charge les tâches planifiées depuis le vault SQLite
    - Déclenche d'autres agents à intervalles configurés
    - Supporte: cron, interval, one-shot (date)
    """

    def __init__(self, identity: Any) -> None:
        super().__init__(identity)
        self._scheduler: Any = None  # APScheduler AsyncIOScheduler
        self._running: bool = False

    async def execute(self, task: str, context: dict[str, Any]) -> str:
        """
        Gestion manuelle du planificateur.
        Commandes: 'list', 'pause <id>', 'resume <id>', 'run <id>'
        """
        task_lower = task.strip().lower()

        if task_lower == "list":
            return self._list_jobs()
        elif task_lower.startswith("run "):
            job_id = task_lower[4:].strip()
            return await self._run_job_now(job_id)
        elif task_lower == "status":
            return self._get_status()
        else:
            # Utiliser le LLM pour interpréter la commande
            return await self.generate_simple(
                f"Tu es l'agent planificateur HAOS. Interprète cette commande et réponds de manière concise: {task}"
            )

    # ─── Démarrage APScheduler ────────────────────────────────────────────────

    async def start(self) -> None:
        """Démarre le planificateur APScheduler."""
        if self._running:
            return

        try:
            from apscheduler.schedulers.asyncio import AsyncIOScheduler
            from apscheduler.triggers.cron import CronTrigger
            from apscheduler.triggers.interval import IntervalTrigger

            self._scheduler = AsyncIOScheduler(timezone="Europe/Paris")

            # Charger les tâches depuis le vault
            await self._load_tasks_from_vault()

            # Ajouter les tâches par défaut si vault vide
            await self._ensure_default_tasks()

            self._scheduler.start()
            self._running = True
            logger.info(
                "SchedulerAgent: planificateur démarré avec %d tâches.",
                len(self._scheduler.get_jobs()),
            )

        except Exception as exc:
            logger.error("SchedulerAgent: erreur démarrage: %s", exc)

    async def stop(self) -> None:
        """Arrête le planificateur proprement."""
        if self._scheduler and self._running:
            self._scheduler.shutdown(wait=False)
            self._running = False
            logger.info("SchedulerAgent: planificateur arrêté.")

    # ─── Chargement des tâches ────────────────────────────────────────────────

    async def _load_tasks_from_vault(self) -> None:
        """Charge les tâches planifiées depuis le vault SQLite."""
        try:
            from memory.vault import get_vault
            vault = await get_vault()
            tasks = await vault.get_scheduled_tasks()
            for task_data in tasks:
                self._add_job_from_data(task_data)
        except Exception as exc:
            logger.warning(
                "SchedulerAgent: impossible de charger depuis vault: %s", exc
            )

    async def _ensure_default_tasks(self) -> None:
        """Ajoute les tâches par défaut si elles n'existent pas."""
        if not self._scheduler:
            return

        existing_ids = {job.id for job in self._scheduler.get_jobs()}

        for task_data in DEFAULT_SCHEDULES:
            if task_data["id"] not in existing_ids:
                self._add_job_from_data(task_data)

    def _add_job_from_data(self, task_data: dict[str, Any]) -> None:
        """Ajoute un job APScheduler depuis un dictionnaire de tâche."""
        if not self._scheduler:
            return

        job_id = task_data["id"]
        agent_id = task_data["agent_id"]
        task_text = task_data["task"]
        trigger_type = task_data.get("trigger", "interval")

        try:
            if trigger_type == "cron":
                from apscheduler.triggers.cron import CronTrigger
                trigger = CronTrigger(
                    hour=task_data.get("hour", 8),
                    minute=task_data.get("minute", 0),
                    day_of_week=task_data.get("day_of_week"),
                    timezone="Europe/Paris",
                )
            elif trigger_type == "interval":
                from apscheduler.triggers.interval import IntervalTrigger
                trigger = IntervalTrigger(
                    hours=task_data.get("hours", 1),
                    minutes=task_data.get("minutes", 0),
                )
            else:
                logger.warning(
                    "SchedulerAgent: trigger type inconnu: %s", trigger_type
                )
                return

            self._scheduler.add_job(
                func=self._trigger_agent,
                trigger=trigger,
                id=job_id,
                name=task_data.get("name", job_id),
                args=[agent_id, task_text],
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info(
                "Job planifié: %s → agent %s (%s)",
                job_id, agent_id, trigger_type,
            )
        except Exception as exc:
            logger.error(
                "Impossible d'ajouter le job %s: %s", job_id, exc
            )

    # ─── Exécution des tâches ─────────────────────────────────────────────────

    async def _trigger_agent(self, agent_id: str, task: str) -> None:
        """Déclenche un agent avec une tâche planifiée."""
        logger.info(
            "SchedulerAgent: déclenchement de l'agent %s — '%s'",
            agent_id,
            task[:60],
        )
        try:
            from agents.registry import get_registry
            registry = get_registry()
            agent = registry.get_agent(agent_id)

            if agent is None:
                logger.warning(
                    "SchedulerAgent: agent %s introuvable.", agent_id
                )
                return

            await agent.run(task=task, context={"triggered_by": "scheduler"})

        except Exception as exc:
            logger.error(
                "SchedulerAgent: erreur déclenchement agent %s: %s",
                agent_id, exc
            )

    async def _run_job_now(self, job_id: str) -> str:
        """Exécute immédiatement un job par son ID."""
        if not self._scheduler:
            return "Planificateur non démarré."

        job = self._scheduler.get_job(job_id)
        if not job:
            return f"Job '{job_id}' introuvable."

        job.modify(next_run_time=__import__("datetime").datetime.now())
        return f"Job '{job_id}' déclenché immédiatement."

    def _list_jobs(self) -> str:
        """Retourne la liste des jobs planifiés."""
        if not self._scheduler:
            return "Planificateur non démarré."

        jobs = self._scheduler.get_jobs()
        if not jobs:
            return "Aucun job planifié."

        lines = ["=== Tâches planifiées ==="]
        for job in jobs:
            next_run = job.next_run_time
            next_str = next_run.strftime("%d/%m/%Y %H:%M") if next_run else "N/A"
            lines.append(f"• {job.id}: {job.name} — Prochain: {next_str}")
        return "\n".join(lines)

    def _get_status(self) -> str:
        """Retourne l'état du planificateur."""
        if not self._scheduler:
            return "Planificateur: ARRÊTÉ"
        state = "DÉMARRÉ" if self._running else "ARRÊTÉ"
        count = len(self._scheduler.get_jobs()) if self._running else 0
        return f"Planificateur: {state} — {count} tâche(s) active(s)"
