"""
Routes de gestion des agents HAOS.
GET  /agents              → liste des 28 agents
GET  /agents/{id}         → détails d'un agent
POST /agents/{id}/run     → déclencher manuellement
GET  /agents/{id}/logs    → 50 derniers logs
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Body
from pydantic import BaseModel

router = APIRouter(prefix="/agents", tags=["Agents"])


class RunAgentRequest(BaseModel):
    task: str
    context: dict[str, Any] = {}


@router.get("")
async def list_agents() -> dict[str, Any]:
    """
    Liste tous les agents HAOS avec leur statut courant.
    """
    from agents.registry import get_registry
    registry = get_registry()
    agents = registry.list_all()

    return {
        "count": len(agents),
        "agents": [agent.to_dict() for agent in agents],
        "by_department": _group_by_department(agents),
        "counts_by_tier": registry.count_by_tier(),
    }


@router.get("/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, Any]:
    """
    Retourne les détails complets d'un agent (identité + statut + dernière activité).
    """
    from agents.registry import get_registry
    registry = get_registry()
    agent = registry.get_agent(agent_id)

    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' introuvable.")

    # Récupérer les derniers événements
    last_events: list[dict[str, Any]] = []
    try:
        from memory.vault import get_vault
        vault = await get_vault()
        last_events = await vault.get_events(agent_id=agent_id, limit=5)
    except Exception:
        pass

    return {
        **agent.to_dict(),
        "identity": {
            "system_prompt_length": len(agent.system_prompt),
            "tools": agent.identity.tools,
            "triggers": agent.identity.triggers,
            "ecc_skills": agent.identity.ecc_skills,
            "frequency": agent.identity.frequency,
            "redis_publishes": agent.identity.redis_publishes,
            "redis_subscribes": agent.identity.redis_subscribes,
        },
        "last_events": last_events,
    }


@router.post("/{agent_id}/run")
async def run_agent(
    agent_id: str,
    request: RunAgentRequest,
) -> dict[str, Any]:
    """
    Déclenche manuellement un agent avec une tâche spécifiée.

    Retourne le résultat de l'exécution.
    """
    from agents.registry import get_registry
    registry = get_registry()
    agent = registry.get_agent(agent_id)

    if agent is None:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' introuvable.")

    if agent.status.is_active:
        raise HTTPException(
            status_code=409,
            detail=f"Agent '{agent_id}' est déjà en cours d'exécution: {agent.status.current_task}",
        )

    result = await agent.run(
        task=request.task,
        context={**request.context, "triggered_by": "api"},
    )

    return {
        "agent_id": result.agent_id,
        "task": result.task,
        "output": result.output,
        "success": result.success,
        "error": result.error,
        "execution_time_ms": result.execution_time_ms,
        "model_tier": result.model_tier.value,
        "tokens_used": result.tokens_used,
    }


@router.get("/{agent_id}/logs")
async def get_agent_logs(
    agent_id: str,
    limit: int = 50,
) -> dict[str, Any]:
    """
    Retourne les 50 derniers logs d'un agent depuis le vault SQLite.
    """
    from agents.registry import get_registry
    registry = get_registry()

    if agent_id not in registry:
        raise HTTPException(status_code=404, detail=f"Agent '{agent_id}' introuvable.")

    try:
        from memory.vault import get_vault
        vault = await get_vault()
        events = await vault.get_events(agent_id=agent_id, limit=min(limit, 200))
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Erreur accès vault: {exc}")

    return {
        "agent_id": agent_id,
        "count": len(events),
        "events": events,
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

def _group_by_department(agents: list) -> dict[str, list[str]]:
    """Groupe les agents par département."""
    groups: dict[str, list[str]] = {}
    for agent in agents:
        dept = agent.department
        if dept not in groups:
            groups[dept] = []
        groups[dept].append(agent.agent_id)
    return groups
