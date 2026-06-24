"""
ModelResolver — Factory pattern résolvant ModelTier → LlamaCppProvider.
Fournit l'accès centralisé aux 3 providers (APEX, QWYTHOS, NANO).
"""

from __future__ import annotations

import logging
from functools import lru_cache

from core.config import settings
from core.llm.types import ModelInfo, ModelTier
from core.llm.providers.llama_cpp import LlamaCppProvider

logger = logging.getLogger(__name__)


def _build_model_info(tier: ModelTier) -> ModelInfo:
    """Construit le ModelInfo pour un tier donné depuis la configuration."""
    if tier == ModelTier.APEX:
        return ModelInfo(
            tier=ModelTier.APEX,
            model_file=settings.apex_model_file,
            port=settings.apex_port,
            base_url=settings.apex_base_url,
            alias="APEX",
            description="Qwen3.6-35B — Stratégique, complexe, lent (~22GB)",
            context_size=32768,
        )
    elif tier == ModelTier.QWYTHOS:
        return ModelInfo(
            tier=ModelTier.QWYTHOS,
            model_file=settings.qwythos_model_file,
            port=settings.qwythos_port,
            base_url=settings.qwythos_base_url,
            alias="QWYTHOS",
            description="Qwythos-9B — Intermédiaire, polyvalent (~10GB)",
            context_size=32768,
        )
    elif tier == ModelTier.NANO:
        return ModelInfo(
            tier=ModelTier.NANO,
            model_file=settings.nano_model_file,
            port=settings.nano_port,
            base_url=settings.nano_base_url,
            alias="NANO",
            description="mythos-nano — Rapide, système (~3GB)",
            context_size=8192,
        )
    else:
        raise ValueError(f"ModelTier inconnu: {tier}")


class ModelResolver:
    """
    Résout les ModelTier en instances LlamaCppProvider.
    Maintient une instance unique par tier (singleton pattern).
    """

    def __init__(self) -> None:
        # Création des 3 providers au démarrage
        self._providers: dict[ModelTier, LlamaCppProvider] = {
            tier: LlamaCppProvider(_build_model_info(tier))
            for tier in ModelTier
        }
        logger.info(
            "ModelResolver initialisé avec %d providers: %s",
            len(self._providers),
            [t.value for t in self._providers],
        )

    def get(self, tier: ModelTier) -> LlamaCppProvider:
        """
        Retourne le provider pour un niveau de modèle donné.

        Args:
            tier: APEX, QWYTHOS ou NANO

        Returns:
            Instance LlamaCppProvider correspondante.

        Raises:
            ValueError: Si le tier est inconnu.
        """
        provider = self._providers.get(tier)
        if provider is None:
            raise ValueError(f"Aucun provider pour ModelTier: {tier}")
        return provider

    def get_all(self) -> dict[ModelTier, LlamaCppProvider]:
        """Retourne tous les providers."""
        return dict(self._providers)

    async def health_check_all(self) -> dict[str, bool]:
        """
        Vérifie la disponibilité de tous les serveurs.

        Returns:
            Dictionnaire {alias: is_available}
        """
        results: dict[str, bool] = {}
        for tier, provider in self._providers.items():
            available = await provider.health_check()
            results[tier.value] = available
            logger.debug(
                "Health check %s: %s",
                tier.value,
                "OK" if available else "INDISPONIBLE",
            )
        return results

    async def get_response_times(self) -> dict[str, float]:
        """
        Mesure les temps de réponse de tous les serveurs.

        Returns:
            Dictionnaire {alias: latency_ms} (-1 si indisponible)
        """
        results: dict[str, float] = {}
        for tier, provider in self._providers.items():
            ms = await provider.get_response_time()
            results[tier.value] = ms
        return results

    async def close_all(self) -> None:
        """Ferme tous les clients HTTP proprement."""
        for provider in self._providers.values():
            await provider.close()
        logger.info("Tous les providers LLM ont été fermés.")


# ─── Instance singleton globale ───────────────────────────────────────────────

_resolver_instance: ModelResolver | None = None


def get_model_resolver() -> ModelResolver:
    """Retourne l'instance singleton du resolver."""
    global _resolver_instance
    if _resolver_instance is None:
        _resolver_instance = ModelResolver()
    return _resolver_instance
