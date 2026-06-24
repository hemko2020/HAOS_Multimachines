"""
LangGraph Orchestration Graph pour HAOS.

Définit le graphe de décision du CEO Agent pour les tâches complexes
nécessitant plusieurs agents en séquence ou en parallèle.

État du graphe :
  - human_input       : demande originale du CEO humain
  - analysis          : analyse du CEO Agent (plan d'action)
  - delegated_tasks   : tâches déléguées aux agents
  - results           : résultats collectés
  - final_response    : réponse consolidée au CEO humain
"""

from __future__ import annotations

import logging
from typing import Any, TypedDict, Annotated
import operator

logger = logging.getLogger(__name__)


# ─── État du graphe ───────────────────────────────────────────────────────────

class HAOSState(TypedDict):
    """État partagé dans le graphe LangGraph."""
    human_input: str
    context: dict[str, Any]
    analysis: str
    delegated_tasks: Annotated[list[dict[str, Any]], operator.add]
    task_results: Annotated[list[dict[str, Any]], operator.add]
    final_response: str
    error: str


# ─── Nœuds du graphe ──────────────────────────────────────────────────────────

async def analyze_request(state: HAOSState) -> HAOSState:
    """
    Nœud CEO Analysis: analyse la demande et détermine le plan d'action.
    Utilise APEX pour une réflexion profonde.
    """
    from agents.registry import get_registry
    from core.llm.types import Message, LLMInput, ModelTier
    from core.llm.router import get_llm_router

    logger.info("Graph: analyse de la demande '%s'", state["human_input"][:60])

    router = get_llm_router()

    analysis_prompt = f"""Tu es le CEO d'une startup IA avec 27 agents spécialisés.

Demande : {state["human_input"]}

Analyse cette demande et détermine:
1. Quels agents doivent être impliqués
2. Dans quel ordre les activer
3. Quelle information chaque agent a besoin

Réponds en JSON structuré:
{{
  "summary": "résumé de la demande",
  "agents_needed": ["agent-id-1", "agent-id-2"],
  "execution_plan": "description du plan",
  "parallel_possible": true/false,
  "priority": "low/normal/high/urgent"
}}"""

    output = await router.generate(LLMInput(
        messages=[Message(role="user", content=analysis_prompt)],
        model_tier=ModelTier.APEX,
        temperature=0.3,
        max_tokens=1024,
    ))

    return {**state, "analysis": output.content}


async def delegate_tasks(state: HAOSState) -> HAOSState:
    """
    Nœud Delegation: parse le plan et délègue aux agents.
    """
    import json
    from agents.registry import get_registry

    logger.info("Graph: délégation des tâches")

    registry = get_registry()
    tasks: list[dict[str, Any]] = []

    # Parser l'analyse JSON
    try:
        analysis_data = json.loads(state["analysis"])
        agents_needed = analysis_data.get("agents_needed", [])
    except (json.JSONDecodeError, KeyError):
        # Fallback: déléguer directement au CEO agent
        agents_needed = ["ceo-01"]

    for agent_id in agents_needed:
        agent = registry.get_agent(agent_id)
        if agent:
            tasks.append({
                "agent_id": agent_id,
                "task": state["human_input"],
                "status": "pending",
            })

    return {**state, "delegated_tasks": tasks}


async def execute_tasks(state: HAOSState) -> HAOSState:
    """
    Nœud Execution: exécute les tâches déléguées.
    Les tâches parallèles s'exécutent en parallèle via asyncio.gather.
    """
    import asyncio
    from agents.registry import get_registry

    logger.info("Graph: exécution de %d tâche(s)", len(state["delegated_tasks"]))
    registry = get_registry()
    results: list[dict[str, Any]] = []

    async def run_task(task_data: dict[str, Any]) -> dict[str, Any]:
        agent_id = task_data["agent_id"]
        agent = registry.get_agent(agent_id)
        if not agent:
            return {
                "agent_id": agent_id,
                "output": f"Agent {agent_id} introuvable",
                "success": False,
            }
        result = await agent.run(
            task=task_data["task"],
            context=state.get("context", {}),
        )
        return {
            "agent_id": agent_id,
            "output": result.output,
            "success": result.success,
            "error": result.error,
        }

    # Exécuter en parallèle si plusieurs tâches
    if len(state["delegated_tasks"]) > 1:
        results = list(await asyncio.gather(
            *[run_task(t) for t in state["delegated_tasks"]],
            return_exceptions=False,
        ))
    elif state["delegated_tasks"]:
        results = [await run_task(state["delegated_tasks"][0])]

    return {**state, "task_results": results}


async def synthesize_response(state: HAOSState) -> HAOSState:
    """
    Nœud Synthesis: consolide tous les résultats en une réponse finale.
    Utilise APEX pour une synthèse de qualité.
    """
    from core.llm.types import Message, LLMInput, ModelTier
    from core.llm.router import get_llm_router

    logger.info("Graph: synthèse des résultats")

    # Si un seul résultat et réussi, retourner directement
    if len(state["task_results"]) == 1 and state["task_results"][0].get("success"):
        return {**state, "final_response": state["task_results"][0]["output"]}

    # Sinon, synthétiser avec APEX
    router = get_llm_router()

    results_text = "\n\n".join(
        f"**{r['agent_id']}** ({'succès' if r.get('success') else 'échec'}):\n{r.get('output', r.get('error', 'Aucun résultat'))}"
        for r in state["task_results"]
    )

    synthesis_prompt = f"""Demande originale du CEO : {state["human_input"]}

Résultats des agents :
{results_text}

Synthétise ces résultats en une réponse claire, structurée et actionnable pour le CEO humain.
Mets en avant les points importants, les décisions à prendre et les prochaines étapes."""

    output = await router.generate(LLMInput(
        messages=[Message(role="user", content=synthesis_prompt)],
        model_tier=ModelTier.APEX,
        temperature=0.5,
        max_tokens=2048,
    ))

    return {**state, "final_response": output.content}


# ─── Conditions de routing ────────────────────────────────────────────────────

def should_delegate(state: HAOSState) -> str:
    """Détermine si on doit déléguer ou répondre directement."""
    analysis = state.get("analysis", "")
    # Si l'analyse est vide ou l'input est simple → réponse directe
    if not analysis or len(state["human_input"]) < 50:
        return "simple"
    return "delegate"


# ─── Construction du graphe ───────────────────────────────────────────────────

def build_haos_graph():
    """
    Construit et retourne le graphe LangGraph HAOS.

    Flux :
    analyze_request → [delegate_tasks → execute_tasks] → synthesize_response → END
    """
    try:
        from langgraph.graph import StateGraph, END

        graph = StateGraph(HAOSState)

        # Ajouter les nœuds
        graph.add_node("analyze", analyze_request)
        graph.add_node("delegate", delegate_tasks)
        graph.add_node("execute", execute_tasks)
        graph.add_node("synthesize", synthesize_response)

        # Définir le flux
        graph.set_entry_point("analyze")
        graph.add_conditional_edges(
            "analyze",
            should_delegate,
            {
                "delegate": "delegate",
                "simple": "synthesize",
            },
        )
        graph.add_edge("delegate", "execute")
        graph.add_edge("execute", "synthesize")
        graph.add_edge("synthesize", END)

        return graph.compile()

    except ImportError:
        logger.warning(
            "LangGraph non disponible. Utiliser le CEO agent directement."
        )
        return None


async def run_orchestration(
    human_input: str,
    context: dict[str, Any] | None = None,
) -> str:
    """
    Point d'entrée pour lancer l'orchestration LangGraph.

    Args:
        human_input: Demande du CEO humain
        context: Contexte additionnel

    Returns:
        Réponse finale synthétisée.
    """
    graph = build_haos_graph()

    if graph is None:
        # Fallback: CEO agent direct
        from agents.registry import get_registry
        registry = get_registry()
        ceo = registry.get_agent("ceo-01")
        if ceo:
            result = await ceo.run(human_input, context or {})
            return result.output
        return "Système HAOS non disponible."

    initial_state: HAOSState = {
        "human_input": human_input,
        "context": context or {},
        "analysis": "",
        "delegated_tasks": [],
        "task_results": [],
        "final_response": "",
        "error": "",
    }

    final_state = await graph.ainvoke(initial_state)
    return final_state.get("final_response", "Aucune réponse générée.")
