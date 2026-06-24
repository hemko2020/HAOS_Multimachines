"""
AgentRegistry — Registre singleton de tous les 28 agents HAOS.
Charge les identités, instancie les agents, suit leur santé.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Type

from agents.base_agent import AgentIdentity, BaseAgent
from core.config import settings

logger = logging.getLogger(__name__)

# Mapping agent_id → classe Python concrète
# Rempli progressivement lors de l'import des modules agents
_AGENT_CLASS_REGISTRY: dict[str, Type[BaseAgent]] = {}


def register_agent_class(agent_id: str):
    """
    Décorateur pour enregistrer une classe d'agent dans le registre global.

    Usage :
        @register_agent_class("ceo-01")
        class CEOAgent(BaseAgent):
            ...
    """
    def decorator(cls: Type[BaseAgent]) -> Type[BaseAgent]:
        _AGENT_CLASS_REGISTRY[agent_id] = cls
        logger.debug("Classe agent enregistrée: %s → %s", agent_id, cls.__name__)
        return cls
    return decorator


class AgentRegistry:
    """
    Registre centralisé de tous les agents HAOS.

    Responsabilités :
    - Charger les 28 fichiers identity.json
    - Instancier les classes agents correspondantes
    - Fournir accès par ID ou département
    - Suivre l'état de santé de chaque agent
    """

    def __init__(self) -> None:
        self._agents: dict[str, BaseAgent] = {}
        self._identities: dict[str, AgentIdentity] = {}
        self._loaded: bool = False

    # ─── Chargement ───────────────────────────────────────────────────────────

    def load_all(self) -> None:
        """
        Charge toutes les identités depuis identities/ et instancie les agents.
        Importe automatiquement tous les modules agents pour déclencher les enregistrements.
        """
        if self._loaded:
            return

        # Importer tous les modules agents pour déclencher @register_agent_class
        self._import_agent_modules()

        identities_dir = settings.identities_dir
        if not identities_dir.exists():
            # Fallback: chercher dans le répertoire courant
            identities_dir = Path(__file__).parent.parent / "identities"

        if not identities_dir.exists():
            logger.warning("Répertoire identities/ introuvable: %s", identities_dir)
            return

        json_files = sorted(identities_dir.glob("*.json"))
        logger.info(
            "Chargement de %d fichiers d'identité depuis %s",
            len(json_files),
            identities_dir,
        )

        for json_path in json_files:
            try:
                identity = AgentIdentity.from_json(json_path)
                self._identities[identity.id] = identity

                agent = self._instantiate_agent(identity)
                if agent:
                    self._agents[identity.id] = agent
                    logger.info(
                        "Agent chargé: %s %s (%s / %s)",
                        identity.avatar,
                        identity.name,
                        identity.id,
                        identity.model_tier.value,
                    )
            except Exception as exc:
                logger.error(
                    "Erreur chargement agent depuis %s: %s",
                    json_path.name,
                    exc,
                )

        self._loaded = True
        logger.info(
            "AgentRegistry: %d/%d agents chargés avec succès.",
            len(self._agents),
            len(json_files),
        )

    def _import_agent_modules(self) -> None:
        """Importe tous les modules agents pour enregistrer les classes."""
        modules_to_import = [
            "agents.csuite.ceo_agent",
            "agents.system.memory_agent",
            "agents.system.scheduler_agent",
            "agents.system.notification_agent",
            "agents.system.logger_agent",
            "agents.system.health_monitor",
        ]
        for module_path in modules_to_import:
            try:
                import importlib
                importlib.import_module(module_path)
            except ImportError as exc:
                logger.warning("Impossible d'importer %s: %s", module_path, exc)

    def _instantiate_agent(self, identity: AgentIdentity) -> BaseAgent | None:
        """Crée une instance de la classe agent appropriée."""
        agent_class = _AGENT_CLASS_REGISTRY.get(identity.id)

        if agent_class is None:
            # Utiliser un agent générique si aucune classe spécifique n'est enregistrée
            agent_class = _AGENT_CLASS_REGISTRY.get("__generic__")

        if agent_class is None:
            # Créer un agent générique inline
            return _GenericAgent(identity)

        return agent_class(identity)

    # ─── Accès aux agents ─────────────────────────────────────────────────────

    def get_agent(self, agent_id: str) -> BaseAgent | None:
        """Retourne l'instance d'un agent par son ID."""
        return self._agents.get(agent_id)

    def get_identity(self, agent_id: str) -> AgentIdentity | None:
        """Retourne l'identité d'un agent par son ID."""
        return self._identities.get(agent_id)

    def get_by_department(self, department: str) -> list[BaseAgent]:
        """Retourne tous les agents d'un département."""
        return [
            agent
            for agent in self._agents.values()
            if agent.department == department
        ]

    def list_all(self) -> list[BaseAgent]:
        """Retourne tous les agents chargés."""
        return list(self._agents.values())

    def list_ids(self) -> list[str]:
        """Retourne tous les IDs d'agents."""
        return list(self._agents.keys())

    # ─── Statut et santé ──────────────────────────────────────────────────────

    def get_status(self) -> dict[str, Any]:
        """
        Retourne l'état de santé de tous les agents.
        Format : {agent_id: {is_active, current_task, last_run, ...}}
        """
        return {
            agent_id: agent.to_dict()
            for agent_id, agent in self._agents.items()
        }

    def get_active_agents(self) -> list[BaseAgent]:
        """Retourne les agents actuellement en cours d'exécution."""
        return [
            agent
            for agent in self._agents.values()
            if agent.status.is_active
        ]

    def count_by_tier(self) -> dict[str, int]:
        """Compte les agents par tier de modèle."""
        from core.llm.types import ModelTier
        counts: dict[str, int] = {tier.value: 0 for tier in ModelTier}
        for agent in self._agents.values():
            counts[agent.model_tier.value] += 1
        return counts

    def __len__(self) -> int:
        return len(self._agents)

    def __contains__(self, agent_id: str) -> bool:
        return agent_id in self._agents


# ─── Agent générique (fallback) ───────────────────────────────────────────────

class _GenericAgent(BaseAgent):
    """
    Agent générique utilisé pour les agents sans classe spécifique implémentée.
    Exécute des tâches via le LLM configuré, sans logique métier supplémentaire.
    """

    async def execute(self, task: str, context: dict[str, Any]) -> str:
        context_str = ""
        if context:
            context_str = "\n\nContexte :\n" + "\n".join(
                f"- {k}: {v}" for k, v in context.items()
            )

        prompt = f"{task}{context_str}"
        return await self.generate_simple(prompt)


# ─── Instance singleton globale ───────────────────────────────────────────────

_registry_instance: AgentRegistry | None = None


def get_registry() -> AgentRegistry:
    """Retourne l'instance singleton du registre (chargée)."""
    global _registry_instance
    if _registry_instance is None:
        _registry_instance = AgentRegistry()
    if not _registry_instance._loaded:
        _registry_instance.load_all()
    return _registry_instance
