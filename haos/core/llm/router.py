"""
LLMRouter — Route les requêtes des agents vers le bon modèle.
Gère le mutex APEX (une seule génération APEX à la fois) avec file d'attente.
"""

from __future__ import annotations

import asyncio
import logging
from typing import AsyncGenerator

from core.llm.types import LLMInput, LLMOutput, ModelTier
from core.llm.providers.llama_cpp import LlamaCppProvider
from core.llm.providers.resolver import ModelResolver, get_model_resolver

logger = logging.getLogger(__name__)

# Timeout d'attente du mutex APEX (secondes)
APEX_QUEUE_TIMEOUT = 600.0  # 10 minutes max


class LLMRouter:
    """
    Route les requêtes d'agents vers le bon provider LLM.

    Fonctionnalités :
    - Résolution automatique du tier depuis l'agent_id ou le LLMInput
    - Mutex APEX : garantit une seule génération APEX simultanée
    - Queue d'attente pour les requêtes APEX concurrentes
    - Métriques d'utilisation par tier
    """

    def __init__(self, resolver: ModelResolver | None = None) -> None:
        self._resolver = resolver or get_model_resolver()

        # Mutex pour APEX (une seule requête à la fois)
        self._apex_lock = asyncio.Lock()
        self._apex_queue_size: int = 0

        # Compteurs d'utilisation
        self._usage_counts: dict[ModelTier, int] = {
            tier: 0 for tier in ModelTier
        }

    # ─── Génération principale ────────────────────────────────────────────────

    async def generate(self, llm_input: LLMInput) -> LLMOutput:
        """
        Génère une réponse en routant vers le bon provider.

        Si le tier est APEX, acquiert le mutex avant de générer.
        Les requêtes APEX concurrentes sont mises en file d'attente.

        Args:
            llm_input: Entrée LLM avec model_tier et messages.

        Returns:
            LLMOutput avec la réponse générée.
        """
        tier = llm_input.model_tier
        provider = self._resolver.get(tier)

        if tier == ModelTier.APEX:
            return await self._generate_apex(llm_input, provider)
        else:
            return await self._generate_direct(llm_input, provider, tier)

    async def _generate_apex(
        self,
        llm_input: LLMInput,
        provider: LlamaCppProvider,
    ) -> LLMOutput:
        """Génération APEX avec mutex (séquentialité garantie)."""
        self._apex_queue_size += 1
        agent_id = llm_input.agent_id or "unknown"

        if self._apex_queue_size > 1:
            logger.info(
                "Agent %s en attente du mutex APEX (%d en queue)",
                agent_id,
                self._apex_queue_size,
            )

        try:
            async with asyncio.timeout(APEX_QUEUE_TIMEOUT):
                async with self._apex_lock:
                    logger.info(
                        "Agent %s: génération APEX démarrée", agent_id
                    )
                    result = await self._generate_direct(
                        llm_input, provider, ModelTier.APEX
                    )
                    logger.info(
                        "Agent %s: génération APEX terminée (%.0f ms, %d tokens)",
                        agent_id,
                        result.latency_ms,
                        result.total_tokens,
                    )
                    return result

        except asyncio.TimeoutError:
            from core.llm.interface import LLMProviderRateLimitError
            raise LLMProviderRateLimitError(
                f"Timeout en attente du mutex APEX après {APEX_QUEUE_TIMEOUT}s",
                provider="APEX",
            )
        finally:
            self._apex_queue_size -= 1

    async def _generate_direct(
        self,
        llm_input: LLMInput,
        provider: LlamaCppProvider,
        tier: ModelTier,
    ) -> LLMOutput:
        """Génération directe sans gestion de mutex."""
        self._usage_counts[tier] += 1
        result = await provider.generate(llm_input)
        return result

    # ─── Streaming ────────────────────────────────────────────────────────────

    async def stream(
        self, llm_input: LLMInput
    ) -> AsyncGenerator[str, None]:
        """
        Streaming avec routing correct.
        Note: APEX mutex s'applique aussi au streaming.
        """
        tier = llm_input.model_tier
        provider = self._resolver.get(tier)

        if tier == ModelTier.APEX:
            async with self._apex_lock:
                async for chunk in provider.stream(llm_input):
                    yield chunk
        else:
            async for chunk in provider.stream(llm_input):
                yield chunk

    # ─── Diagnostics ─────────────────────────────────────────────────────────

    def get_apex_queue_size(self) -> int:
        """Nombre de requêtes APEX en attente."""
        return self._apex_queue_size

    def get_usage_stats(self) -> dict[str, int]:
        """Retourne le nombre de requêtes par tier depuis le démarrage."""
        return {tier.value: count for tier, count in self._usage_counts.items()}

    def get_provider(self, tier: ModelTier) -> LlamaCppProvider:
        """Accès direct à un provider (pour health checks)."""
        return self._resolver.get(tier)


# ─── Instance singleton globale ───────────────────────────────────────────────

_router_instance: LLMRouter | None = None


def get_llm_router() -> LLMRouter:
    """Retourne l'instance singleton du router."""
    global _router_instance
    if _router_instance is None:
        _router_instance = LLMRouter()
    return _router_instance
