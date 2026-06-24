"""
Interface abstraite LLMProvider pour HAOS.
Tous les providers (llama.cpp, futurs cloud) implémentent cette interface.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import AsyncGenerator

from core.llm.types import LLMInput, LLMOutput, ModelInfo


class LLMProvider(ABC):
    """
    Classe abstraite pour tous les providers LLM.

    Chaque provider doit implémenter :
    - generate()  → génération complète (réponse entière)
    - stream()    → génération en streaming (token par token)
    - health_check() → vérification de disponibilité
    - model_info  → propriété retournant les métadonnées du modèle
    """

    @property
    @abstractmethod
    def model_info(self) -> ModelInfo:
        """Retourne les métadonnées du modèle géré par ce provider."""
        ...

    @abstractmethod
    async def generate(self, llm_input: LLMInput) -> LLMOutput:
        """
        Génère une réponse complète pour les messages donnés.

        Args:
            llm_input: Entrée structurée (messages, température, outils, etc.)

        Returns:
            LLMOutput avec contenu, appels d'outils et métriques.

        Raises:
            LLMProviderError: Si le serveur est indisponible ou retourne une erreur.
        """
        ...

    @abstractmethod
    async def stream(
        self, llm_input: LLMInput
    ) -> AsyncGenerator[str, None]:
        """
        Génère une réponse en streaming (yield de chunks de texte).

        Args:
            llm_input: Entrée structurée (stream doit être True)

        Yields:
            Fragments de texte au fur et à mesure de la génération.
        """
        ...

    @abstractmethod
    async def health_check(self) -> bool:
        """
        Vérifie si le serveur LLM est disponible et répond.

        Returns:
            True si le serveur répond correctement, False sinon.
        """
        ...

    async def __aenter__(self) -> "LLMProvider":
        return self

    async def __aexit__(self, *args: object) -> None:
        pass


class LLMProviderError(Exception):
    """Erreur générique d'un provider LLM."""

    def __init__(
        self,
        message: str,
        provider: str = "unknown",
        status_code: int | None = None,
    ) -> None:
        super().__init__(message)
        self.provider = provider
        self.status_code = status_code

    def __repr__(self) -> str:
        return (
            f"LLMProviderError(provider={self.provider!r}, "
            f"status_code={self.status_code}, message={str(self)!r})"
        )


class LLMProviderUnavailableError(LLMProviderError):
    """Le serveur LLM n'est pas disponible (timeout, connexion refusée, etc.)."""
    pass


class LLMProviderRateLimitError(LLMProviderError):
    """Le serveur LLM est saturé (mutex APEX en cours, queue pleine, etc.)."""
    pass
