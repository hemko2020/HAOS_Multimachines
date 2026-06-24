"""
FastAPI Application HAOS — Point d'entrée principal.

Démarre tous les services au lifespan :
- Connexion Redis
- Initialisation SQLite vault
- Chargement des 28 agents
- Démarrage des agents système (health monitor, scheduler, logger, notification)
"""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import AsyncGenerator

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from core.config import settings

# Configuration du logging
logging.basicConfig(
    level=getattr(logging, settings.log_level.upper(), logging.INFO),
    format="%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


# ─── Lifespan (démarrage / arrêt) ────────────────────────────────────────────

@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None, None]:
    """
    Gère le cycle de vie de l'application.
    Startup: connecte Redis, initialise vault, charge agents, démarre daemons.
    Shutdown: arrête les agents système, ferme les connexions.
    """
    logger.info("=" * 60)
    logger.info("HAOS démarrage — Human-AI Operating System v1.0")
    logger.info("=" * 60)

    # ── 1. Connexion Redis ─────────────────────────────────────────
    logger.info("[1/5] Connexion Redis...")
    try:
        from core.redis_bus import get_event_bus
        bus = await get_event_bus()
        info = await bus.get_redis_info()
        logger.info(
            "✅ Redis connecté (v%s, %s RAM)",
            info.get("version"),
            info.get("used_memory_human"),
        )
    except Exception as exc:
        logger.error("❌ Redis indisponible: %s", exc)
        logger.warning("  → Continuez sans Redis (fonctionnalités réduites)")

    # ── 2. Initialisation SQLite vault ────────────────────────────
    logger.info("[2/5] Initialisation SQLite vault...")
    try:
        from memory.vault import get_vault
        vault = await get_vault()
        logger.info("✅ Vault initialisé: %s", vault._db_path)
    except Exception as exc:
        logger.error("❌ Vault SQLite erreur: %s", exc)

    # ── 3. Chargement des 28 agents ───────────────────────────────
    logger.info("[3/5] Chargement des agents...")
    try:
        from agents.registry import get_registry
        registry = get_registry()
        logger.info(
            "✅ %d agents chargés — tiers: %s",
            len(registry),
            registry.count_by_tier(),
        )
    except Exception as exc:
        logger.error("❌ Erreur chargement agents: %s", exc)

    # ── 4. Démarrage agents système ───────────────────────────────
    logger.info("[4/5] Démarrage agents système...")
    try:
        from agents.registry import get_registry
        registry = get_registry()

        # Health Monitor
        health_agent = registry.get_agent("health-monitor-01")
        if health_agent:
            from agents.system.health_monitor import HealthMonitorAgent
            if isinstance(health_agent, HealthMonitorAgent):
                await health_agent.start_monitoring()
                logger.info("✅ Health Monitor démarré")

        # Scheduler
        scheduler_agent = registry.get_agent("scheduler-01")
        if scheduler_agent:
            from agents.system.scheduler_agent import SchedulerAgent
            if isinstance(scheduler_agent, SchedulerAgent):
                await scheduler_agent.start()
                logger.info("✅ Scheduler démarré")

        # Logger Agent (écoute tous les canaux)
        logger_agent = registry.get_agent("logger-01")
        if logger_agent:
            from agents.system.logger_agent import LoggerAgent
            if isinstance(logger_agent, LoggerAgent):
                await logger_agent.start_listening()
                logger.info("✅ Logger Agent démarré")

        # Notification Agent
        notif_agent = registry.get_agent("notification-01")
        if notif_agent:
            from agents.system.notification_agent import NotificationAgent
            if isinstance(notif_agent, NotificationAgent):
                await notif_agent.start()
                logger.info("✅ Notification Agent démarré")

    except Exception as exc:
        logger.error("❌ Erreur démarrage agents système: %s", exc)

    # ── 5. Annonce système ────────────────────────────────────────
    logger.info("[5/5] HAOS opérationnel!")
    logger.info(
        "API disponible sur http://0.0.0.0:%d",
        settings.api_port,
    )
    logger.info("=" * 60)

    # Publication de l'événement de démarrage
    try:
        from core.redis_bus import get_event_bus
        bus = await get_event_bus()
        await bus.publish_notification(
            message="🚀 HAOS démarré et opérationnel. 28 agents prêts.",
            priority="INFO",
        )
    except Exception:
        pass

    # ─── Yield : application active ──────────────────────────────
    yield

    # ─── SHUTDOWN ────────────────────────────────────────────────
    logger.info("HAOS arrêt en cours...")

    # Arrêter les agents système
    try:
        from agents.registry import get_registry
        registry = get_registry()

        for agent_id, cls_name, stop_method in [
            ("health-monitor-01", "HealthMonitorAgent", "stop_monitoring"),
            ("scheduler-01", "SchedulerAgent", "stop"),
            ("logger-01", "LoggerAgent", "stop_listening"),
            ("notification-01", "NotificationAgent", "stop"),
        ]:
            agent = registry.get_agent(agent_id)
            if agent and hasattr(agent, stop_method):
                await getattr(agent, stop_method)()
    except Exception as exc:
        logger.error("Erreur arrêt agents: %s", exc)

    # Fermer les providers LLM
    try:
        from core.llm.providers.resolver import get_model_resolver
        resolver = get_model_resolver()
        await resolver.close_all()
    except Exception:
        pass

    # Fermer Redis
    try:
        from core.redis_bus import shutdown_event_bus
        await shutdown_event_bus()
    except Exception:
        pass

    logger.info("✅ HAOS arrêté proprement.")


# ─── Création de l'application ────────────────────────────────────────────────

app = FastAPI(
    title="HAOS — Human-AI Operating System",
    description=(
        "Système d'exploitation agentique pour startup IA mono-humain. "
        "28 agents IA spécialisés orchestrés par un CEO humain."
    ),
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)

# ─── CORS (pour Flutter IHM) ─────────────────────────────────────────────────

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # En production: limiter aux IPs Tailscale
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─── Routes ───────────────────────────────────────────────────────────────────

from api.routes.health import router as health_router
from api.routes.agents import router as agents_router
from api.routes.chat import router as chat_router
from api.routes.events import router as events_router

app.include_router(health_router)
app.include_router(agents_router)
app.include_router(chat_router)
app.include_router(events_router)


@app.get("/", tags=["Root"])
async def root() -> dict:
    """Point d'entrée racine — informations système."""
    from agents.registry import get_registry
    try:
        registry = get_registry()
        agent_count = len(registry)
        active_count = len(registry.get_active_agents())
    except Exception:
        agent_count = 0
        active_count = 0

    return {
        "system": "HAOS — Human-AI Operating System",
        "version": "1.0.0",
        "status": "operational",
        "agents_total": agent_count,
        "agents_active": active_count,
        "endpoints": {
            "chat": "POST /chat",
            "websocket": "WS /ws",
            "events_stream": "GET /events/stream",
            "agents": "GET /agents",
            "health": "GET /health",
            "docs": "GET /docs",
        },
    }
