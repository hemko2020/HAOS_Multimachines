"""
BaseAgent — Classe abstraite de base pour les 28 agents HAOS.

Chaque agent :
  - Possède une identité chargée depuis identities/<id>.json
  - Utilise le bon modèle LLM selon son model_tier
  - Communique via Redis (publish/subscribe)
  - Enregistre ses actions dans le vault SQLite
"""

from __future__ import annotations

import json
import time
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.config import settings
from core.llm.types import (
    AgentResult,
    LLMInput,
    LLMOutput,
    Message,
    ModelTier,
    ToolDefinition,
)
from core.llm.router import get_llm_router

logger = logging.getLogger(__name__)


@dataclass
class AgentIdentity:
    """
    Identité complète d'un agent HAOS,
    chargée depuis identities/<agent_id>.json
    """
    id: str
    name: str
    name_fr: str
    department: str
    avatar: str
    model_tier: ModelTier
    model_file: str
    model_port: int
    system_prompt: str
    tools: list[str]
    triggers: list[str]
    redis_publishes: list[str]
    redis_subscribes: list[str]
    ecc_skills: list[str]
    frequency: str

    @classmethod
    def from_json(cls, path: Path) -> "AgentIdentity":
        """Charge une identité depuis un fichier JSON."""
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return cls(
            id=data["id"],
            name=data["name"],
            name_fr=data["name_fr"],
            department=data["department"],
            avatar=data["avatar"],
            model_tier=ModelTier(data["model_tier"]),
            model_file=data["model_file"],
            model_port=data["model_port"],
            system_prompt=data["system_prompt"],
            tools=data.get("tools", []),
            triggers=data.get("triggers", []),
            redis_publishes=data.get("redis_publishes", []),
            redis_subscribes=data.get("redis_subscribes", []),
            ecc_skills=data.get("ecc_skills", []),
            frequency=data.get("frequency", "on-demand"),
        )


@dataclass
class AgentStatus:
    """État courant d'un agent."""
    agent_id: str
    is_active: bool = False
    current_task: str | None = None
    last_run: datetime | None = None
    last_error: str | None = None
    total_runs: int = 0
    total_errors: int = 0


class BaseAgent(ABC):
    """
    Classe abstraite de base pour tous les agents HAOS.

    Les sous-classes doivent implémenter :
    - execute(task: str, context: dict) -> str
    """

    def __init__(self, identity: AgentIdentity) -> None:
        self._identity = identity
        self._status = AgentStatus(agent_id=identity.id)
        self._router = get_llm_router()
        self._tool_definitions: list[ToolDefinition] = []

        # Import lazy pour éviter les dépendances circulaires
        self._bus: Any | None = None
        self._vault: Any | None = None

    # ─── Propriétés d'identité ────────────────────────────────────────────────

    @property
    def agent_id(self) -> str:
        return self._identity.id

    @property
    def name(self) -> str:
        return self._identity.name

    @property
    def name_fr(self) -> str:
        return self._identity.name_fr

    @property
    def model_tier(self) -> ModelTier:
        return self._identity.model_tier

    @property
    def department(self) -> str:
        return self._identity.department

    @property
    def avatar(self) -> str:
        return self._identity.avatar

    @property
    def system_prompt(self) -> str:
        return self._identity.system_prompt

    @property
    def identity(self) -> AgentIdentity:
        return self._identity

    @property
    def status(self) -> AgentStatus:
        return self._status

    # ─── Exécution principale ─────────────────────────────────────────────────

    async def run(
        self,
        task: str,
        context: dict[str, Any] | None = None,
        conversation_history: list[Message] | None = None,
    ) -> AgentResult:
        """
        Point d'entrée principal pour exécuter une tâche.
        Gère l'état, les événements Redis et la persistance.

        Args:
            task: Description de la tâche à effectuer
            context: Contexte additionnel (données, instructions)
            conversation_history: Messages précédents pour la continuité

        Returns:
            AgentResult avec le résultat et les métriques.
        """
        start_time = time.monotonic()
        self._status.is_active = True
        self._status.current_task = task
        self._status.total_runs += 1

        # Publication de l'événement de démarrage
        await self.publish_event(
            "task_started",
            {"task": task, "context_keys": list((context or {}).keys())},
        )

        try:
            output = await self.execute(task, context or {})

            result = AgentResult(
                agent_id=self.agent_id,
                task=task,
                output=output,
                success=True,
                execution_time_ms=(time.monotonic() - start_time) * 1000,
                model_tier=self.model_tier,
            )

            self._status.last_run = datetime.now(timezone.utc)
            await self.publish_event(
                "task_completed",
                {
                    "task": task,
                    "output_length": len(output),
                    "execution_ms": result.execution_time_ms,
                },
            )
            return result

        except Exception as exc:
            self._status.total_errors += 1
            self._status.last_error = str(exc)
            logger.exception(
                "Erreur agent %s sur tâche '%s': %s",
                self.agent_id, task, exc
            )
            await self.publish_event(
                "task_failed",
                {"task": task, "error": str(exc)},
            )
            return AgentResult(
                agent_id=self.agent_id,
                task=task,
                output="",
                success=False,
                error=str(exc),
                execution_time_ms=(time.monotonic() - start_time) * 1000,
                model_tier=self.model_tier,
            )
        finally:
            self._status.is_active = False
            self._status.current_task = None

    @abstractmethod
    async def execute(self, task: str, context: dict[str, Any]) -> str:
        """
        Implémentation spécifique de la tâche par l'agent.
        Doit être surchargée par chaque agent concret.

        Args:
            task: Description de la tâche
            context: Contexte additionnel

        Returns:
            Réponse textuelle de l'agent.
        """
        ...

    # ─── Génération LLM ───────────────────────────────────────────────────────

    async def generate(
        self,
        messages: list[Message],
        temperature: float = 0.7,
        max_tokens: int = 2048,
        tools: list[ToolDefinition] | None = None,
    ) -> LLMOutput:
        """
        Génère une réponse LLM en utilisant le modèle configuré pour cet agent.
        Injecte automatiquement le system prompt.
        """
        # Construire la liste complète des messages avec system prompt
        full_messages = [
            Message(role="system", content=self.system_prompt),
            *messages,
        ]

        llm_input = LLMInput(
            messages=full_messages,
            model_tier=self.model_tier,
            temperature=temperature,
            max_tokens=max_tokens,
            tools=tools or self._tool_definitions,
            agent_id=self.agent_id,
        )

        return await self._router.generate(llm_input)

    async def generate_simple(
        self,
        prompt: str,
        temperature: float = 0.7,
        max_tokens: int = 1024,
    ) -> str:
        """
        Génération simple (texte uniquement, pas de tool calling).
        Raccourci pratique pour les agents système.
        """
        output = await self.generate(
            messages=[Message(role="user", content=prompt)],
            temperature=temperature,
            max_tokens=max_tokens,
            tools=[],
        )
        return output.content

    # ─── Événements Redis ─────────────────────────────────────────────────────

    async def publish_event(
        self,
        event_type: str,
        data: dict[str, Any] | None = None,
    ) -> None:
        """
        Publie un événement sur le canal Redis de cet agent.
        Ne lève pas d'exception si Redis est indisponible (fail silently).
        """
        try:
            from core.redis_bus import get_event_bus
            bus = await get_event_bus()
            await bus.publish_agent_event(
                agent_id=self.agent_id,
                event_type=event_type,
                data=data or {},
                department=self.department,
            )
        except Exception as exc:
            logger.warning(
                "Impossible de publier événement Redis (agent %s): %s",
                self.agent_id,
                exc,
            )

    async def listen(self) -> None:
        """
        Souscrit aux canaux Redis configurés dans l'identité.
        Appelle handle_event() pour chaque message reçu.
        Override pour personnaliser le comportement.
        """
        channels = self._identity.redis_subscribes
        if not channels:
            return

        try:
            from core.redis_bus import get_event_bus
            bus = await get_event_bus()

            # Déterminer si on utilise des patterns (contient *)
            has_patterns = any("*" in ch for ch in channels)

            async for event in bus.subscribe(channels, pattern=has_patterns):
                await self.handle_event(event)

        except Exception as exc:
            logger.error(
                "Erreur écoute Redis (agent %s): %s",
                self.agent_id,
                exc,
            )

    async def handle_event(self, event: dict[str, Any]) -> None:
        """
        Gestionnaire d'événements Redis.
        Override dans les sous-classes pour réagir aux événements.
        """
        pass

    # ─── Représentation ───────────────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        """Représentation sérialisable de l'agent."""
        return {
            "id": self.agent_id,
            "name": self.name,
            "name_fr": self.name_fr,
            "department": self.department,
            "avatar": self.avatar,
            "model_tier": self.model_tier.value,
            "is_active": self._status.is_active,
            "current_task": self._status.current_task,
            "last_run": (
                self._status.last_run.isoformat()
                if self._status.last_run
                else None
            ),
            "total_runs": self._status.total_runs,
            "total_errors": self._status.total_errors,
        }

    def __repr__(self) -> str:
        return (
            f"<{self.__class__.__name__} "
            f"id={self.agent_id!r} "
            f"tier={self.model_tier.value}>"
        )
