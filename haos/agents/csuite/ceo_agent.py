"""
CEO Agent — Orchestrateur principal HAOS.
Modèle: APEX (Qwen3.6-35B — le plus puissant)
Point d'entrée unique pour les commandes humaines.
"""

from __future__ import annotations

import logging
from typing import Any

from agents.base_agent import BaseAgent
from agents.registry import register_agent_class
from core.llm.types import Message, ToolDefinition

logger = logging.getLogger(__name__)

# Outils disponibles pour le CEO Agent
CEO_TOOLS = [
    ToolDefinition(
        name="delegate_to_agent",
        description="Délègue une tâche à un agent spécifique du système HAOS.",
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID de l'agent cible (ex: 'cto-01', 'flutter-dev-01')",
                },
                "task": {
                    "type": "string",
                    "description": "Description complète et précise de la tâche à effectuer.",
                },
                "context": {
                    "type": "object",
                    "description": "Contexte additionnel pour l'agent (données, contraintes, etc.)",
                },
                "priority": {
                    "type": "string",
                    "enum": ["low", "normal", "high", "urgent"],
                    "description": "Niveau de priorité de la tâche.",
                },
            },
            "required": ["agent_id", "task"],
        },
    ),
    ToolDefinition(
        name="get_agent_status",
        description="Obtient l'état actuel d'un ou plusieurs agents.",
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID de l'agent (ou 'all' pour tous les agents).",
                },
            },
            "required": ["agent_id"],
        },
    ),
    ToolDefinition(
        name="get_agent_memory",
        description="Récupère le contexte et les mémoires récentes d'un agent.",
        parameters={
            "type": "object",
            "properties": {
                "agent_id": {
                    "type": "string",
                    "description": "ID de l'agent dont on veut le contexte.",
                },
                "n_sessions": {
                    "type": "integer",
                    "description": "Nombre de sessions à récupérer (défaut: 5).",
                },
            },
            "required": ["agent_id"],
        },
    ),
    ToolDefinition(
        name="send_notification",
        description="Envoie une notification push vers l'iPhone/iPad du CEO humain.",
        parameters={
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "description": "Texte de la notification.",
                },
                "priority": {
                    "type": "string",
                    "enum": ["INFO", "WARNING", "CRITICAL"],
                    "description": "Priorité de la notification.",
                },
            },
            "required": ["message"],
        },
    ),
    ToolDefinition(
        name="get_system_health",
        description="Obtient l'état de santé complet du système HAOS (Redis, LLM servers, API).",
        parameters={
            "type": "object",
            "properties": {},
        },
    ),
]


@register_agent_class("ceo-01")
class CEOAgent(BaseAgent):
    """
    Agent CEO — Orchestrateur principal du système HAOS.

    Responsabilités :
    - Analyser les demandes de l'humain CEO
    - Router vers le(s) agent(s) approprié(s)
    - Coordonner les tâches multi-agents
    - Maintenir l'état global de la startup
    - Communiquer les décisions stratégiques
    """

    def __init__(self, identity: Any) -> None:
        super().__init__(identity)
        self._tool_definitions = CEO_TOOLS
        self._conversation_history: list[Message] = []
        self._max_history_length: int = 20  # Garder 20 tours de conversation

    async def execute(self, task: str, context: dict[str, Any]) -> str:
        """
        Traitement principal d'une demande.

        1. Analyse la demande avec APEX
        2. Si tool_calls → exécute les outils (délégation, status, etc.)
        3. Retourne la réponse finale au CEO humain
        """
        # Construire le message utilisateur
        user_message = Message(role="user", content=task)

        # Ajouter le contexte si présent
        if context:
            context_lines = [f"**Contexte:**"]
            for k, v in context.items():
                context_lines.append(f"- {k}: {v}")
            user_message = Message(
                role="user",
                content=f"{chr(10).join(context_lines)}\n\n**Demande:**\n{task}",
            )

        # Ajouter à l'historique de conversation
        self._conversation_history.append(user_message)
        self._trim_conversation_history()

        # Première génération avec APEX
        output = await self.generate(
            messages=self._conversation_history,
            temperature=0.7,
            max_tokens=4096,
            tools=self._tool_definitions,
        )

        # Traiter les appels d'outils si nécessaires
        if output.has_tool_calls:
            tool_results = await self._execute_tool_calls(output)
            # Deuxième génération avec les résultats d'outils
            tool_messages = self._build_tool_result_messages(output, tool_results)
            final_output = await self.generate(
                messages=self._conversation_history + tool_messages,
                temperature=0.7,
                max_tokens=4096,
                tools=[],  # Pas de nouveaux tool calls
            )
            response = final_output.content
        else:
            response = output.content

        # Ajouter la réponse à l'historique
        self._conversation_history.append(
            Message(role="assistant", content=response)
        )

        return response

    # ─── Exécution des outils ─────────────────────────────────────────────────

    async def _execute_tool_calls(
        self,
        output: Any,
    ) -> dict[str, str]:
        """Exécute tous les tool_calls retournés par le LLM."""
        results: dict[str, str] = {}

        for tool_call in output.tool_calls:
            tool_name = tool_call.name
            args = tool_call.arguments

            logger.info(
                "CEO: outil appelé '%s' avec args: %s",
                tool_name,
                str(args)[:100],
            )

            try:
                if tool_name == "delegate_to_agent":
                    result = await self._tool_delegate(
                        agent_id=args["agent_id"],
                        task=args["task"],
                        context=args.get("context", {}),
                        priority=args.get("priority", "normal"),
                    )
                elif tool_name == "get_agent_status":
                    result = await self._tool_get_status(args["agent_id"])
                elif tool_name == "get_agent_memory":
                    result = await self._tool_get_memory(
                        agent_id=args["agent_id"],
                        n_sessions=args.get("n_sessions", 5),
                    )
                elif tool_name == "send_notification":
                    result = await self._tool_send_notification(
                        message=args["message"],
                        priority=args.get("priority", "INFO"),
                    )
                elif tool_name == "get_system_health":
                    result = await self._tool_get_health()
                else:
                    result = f"Outil inconnu: {tool_name}"

            except Exception as exc:
                result = f"Erreur outil {tool_name}: {exc}"
                logger.error("CEO tool error %s: %s", tool_name, exc)

            results[tool_call.id] = result

        return results

    def _build_tool_result_messages(
        self,
        output: Any,
        tool_results: dict[str, str],
    ) -> list[Message]:
        """Construit les messages de résultats d'outils pour le second appel LLM."""
        messages = []

        # Message assistant avec les tool_calls
        # (Format attendu par l'API OpenAI)
        assistant_msg = Message(
            role="assistant",
            content=output.content or "",
        )
        messages.append(assistant_msg)

        # Messages de résultats d'outils
        for tool_call in output.tool_calls:
            result = tool_results.get(tool_call.id, "Pas de résultat")
            messages.append(
                Message(
                    role="tool",
                    content=result,
                    name=tool_call.name,
                    tool_call_id=tool_call.id,
                )
            )
        return messages

    # ─── Implémentations des outils ───────────────────────────────────────────

    async def _tool_delegate(
        self,
        agent_id: str,
        task: str,
        context: dict[str, Any],
        priority: str,
    ) -> str:
        """Délègue une tâche à un agent spécifique."""
        from agents.registry import get_registry
        registry = get_registry()
        agent = registry.get_agent(agent_id)

        if agent is None:
            available = ", ".join(registry.list_ids()[:10])
            return (
                f"Agent '{agent_id}' introuvable. "
                f"Agents disponibles (premiers 10): {available}"
            )

        logger.info("CEO: délégation → %s — '%s'", agent_id, task[:60])
        result = await agent.run(
            task=task,
            context={**context, "delegated_by": "ceo-01", "priority": priority},
        )

        if result.success:
            return f"✅ Agent {agent_id} a terminé:\n{result.output}"
        else:
            return f"❌ Agent {agent_id} a échoué: {result.error}"

    async def _tool_get_status(self, agent_id: str) -> str:
        """Obtient le statut d'un ou de tous les agents."""
        from agents.registry import get_registry
        registry = get_registry()

        if agent_id == "all":
            status = registry.get_status()
            lines = []
            for aid, data in status.items():
                icon = "🟢" if not data.get("is_active") else "🔵"
                lines.append(
                    f"{icon} {data.get('avatar', '')} {aid}: "
                    f"runs={data.get('total_runs', 0)}, "
                    f"errors={data.get('total_errors', 0)}"
                )
            return "\n".join(lines) or "Aucun agent chargé."
        else:
            agent = registry.get_agent(agent_id)
            if not agent:
                return f"Agent '{agent_id}' introuvable."
            return str(agent.to_dict())

    async def _tool_get_memory(self, agent_id: str, n_sessions: int) -> str:
        """Récupère la mémoire d'un agent via le MemoryAgent."""
        from agents.registry import get_registry
        registry = get_registry()
        memory_agent = registry.get_agent("memory-01")

        if memory_agent is None:
            return "Agent mémoire (memory-01) non disponible."

        # Appel direct à la méthode get_context
        from agents.system.memory_agent import MemoryAgent
        if isinstance(memory_agent, MemoryAgent):
            return await memory_agent.get_context(agent_id, n_sessions)
        return "Agent mémoire incompatible."

    async def _tool_send_notification(
        self,
        message: str,
        priority: str,
    ) -> str:
        """Envoie une notification via le NotificationAgent."""
        try:
            from core.redis_bus import get_event_bus
            bus = await get_event_bus()
            await bus.publish_notification(
                message=message,
                priority=priority,
                agent_id=self.agent_id,
            )
            return f"Notification envoyée avec priorité {priority}."
        except Exception as exc:
            return f"Erreur envoi notification: {exc}"

    async def _tool_get_health(self) -> str:
        """Obtient la santé complète du système."""
        from agents.registry import get_registry
        registry = get_registry()
        health_agent = registry.get_agent("health-monitor-01")

        if health_agent is None:
            return "Agent health-monitor non disponible."

        result = await health_agent.run("Vérifier l'état de tous les services.")
        return result.output

    # ─── Gestion de l'historique ──────────────────────────────────────────────

    def _trim_conversation_history(self) -> None:
        """Limite la longueur de l'historique de conversation."""
        if len(self._conversation_history) > self._max_history_length * 2:
            # Garder les N derniers échanges (user + assistant)
            self._conversation_history = self._conversation_history[
                -self._max_history_length * 2:
            ]

    def clear_conversation(self) -> None:
        """Efface l'historique de conversation (nouvelle session)."""
        self._conversation_history.clear()
        logger.info("CEO: historique de conversation effacé.")
