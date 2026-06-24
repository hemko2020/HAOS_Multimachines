"""
Routes de santé système HAOS.
GET /health          → santé globale
GET /health/models   → statut et temps de réponse par modèle
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter

router = APIRouter(prefix="/health", tags=["Health"])


@router.get("")
async def get_health() -> dict[str, Any]:
    """
    Vérifie l'état de santé global du système HAOS.

    Retourne le statut de : Redis, APEX, QWYTHOS, NANO, API.
    """
    checks = await asyncio.gather(
        _check_redis(),
        _check_llm("apex"),
        _check_llm("qwythos"),
        _check_llm("nano"),
        return_exceptions=True,
    )

    services: dict[str, Any] = {}
    service_names = ["redis", "apex", "qwythos", "nano"]
    all_up = True

    for name, result in zip(service_names, checks):
        if isinstance(result, Exception):
            services[name] = {"status": "error", "error": str(result)}
            all_up = False
        else:
            services[name] = result
            if result.get("status") != "up":
                all_up = False

    # API toujours UP si on répond
    services["api"] = {"status": "up", "version": "1.0.0"}

    return {
        "status": "healthy" if all_up else "degraded",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "services": services,
    }


@router.get("/models")
async def get_models_health() -> dict[str, Any]:
    """
    Statut détaillé des 3 serveurs LLM avec temps de réponse.
    """
    from core.llm.providers.resolver import get_model_resolver
    from core.llm.types import ModelTier

    resolver = get_model_resolver()
    response_times = await resolver.get_response_times()
    health_checks = await resolver.health_check_all()

    models: dict[str, Any] = {}
    for tier in ModelTier:
        alias = tier.value
        provider = resolver.get(tier)
        info = provider.model_info
        models[alias] = {
            "alias": alias,
            "model_file": info.model_file,
            "port": info.port,
            "status": "up" if health_checks.get(alias) else "down",
            "response_ms": response_times.get(alias, -1),
            "context_size": info.context_size,
            "description": info.description,
        }

    return {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "models": models,
        "all_available": all(health_checks.values()),
    }


# ─── Helpers ──────────────────────────────────────────────────────────────────

async def _check_redis() -> dict[str, Any]:
    """Vérifie Redis."""
    import time
    try:
        import redis.asyncio as aioredis
        from core.config import settings
        start = time.monotonic()
        client = aioredis.from_url(settings.redis_url, socket_timeout=2.0)
        await client.ping()
        ms = (time.monotonic() - start) * 1000
        await client.aclose()
        return {"status": "up", "response_ms": round(ms, 2)}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}


async def _check_llm(tier_name: str) -> dict[str, Any]:
    """Vérifie un serveur llama.cpp."""
    import httpx
    import time
    from core.config import settings

    url_map = {
        "apex": settings.apex_base_url,
        "qwythos": settings.qwythos_base_url,
        "nano": settings.nano_base_url,
    }
    base_url = url_map.get(tier_name, "")
    if not base_url:
        return {"status": "unknown"}

    try:
        start = time.monotonic()
        async with httpx.AsyncClient(timeout=3.0) as client:
            resp = await client.get(f"{base_url}/health")
        ms = (time.monotonic() - start) * 1000
        if resp.status_code == 200:
            return {"status": "up", "response_ms": round(ms, 2)}
        return {"status": "degraded", "http_status": resp.status_code}
    except Exception as exc:
        return {"status": "down", "error": str(exc)}
