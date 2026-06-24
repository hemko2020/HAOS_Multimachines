"""
Provider LLM pour llama.cpp server (API compatible OpenAI).
Gère la génération complète et le streaming via httpx async.
"""

from __future__ import annotations

import json
import time
import logging
from typing import AsyncGenerator

import httpx

from core.llm.interface import (
    LLMProvider,
    LLMProviderError,
    LLMProviderUnavailableError,
)
from core.llm.types import LLMInput, LLMOutput, ModelInfo, ToolCall

logger = logging.getLogger(__name__)

# Timeout par défaut pour les requêtes longues (APEX peut être lent)
DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 300.0  # 5 minutes pour les générations longues


class LlamaCppProvider(LLMProvider):
    """
    Provider pour llama.cpp server.
    Supporte: génération complète, streaming, tool calling (format OpenAI).

    Le serveur doit être démarré avec :
      llama-server -m <model.gguf> --port <port> --ctx-size <n> -ngl 99
    """

    def __init__(
        self,
        model_info: ModelInfo,
        connect_timeout: float = DEFAULT_CONNECT_TIMEOUT,
        read_timeout: float = DEFAULT_READ_TIMEOUT,
    ) -> None:
        self._model_info = model_info
        self._base_url = model_info.base_url
        self._timeout = httpx.Timeout(
            connect=connect_timeout,
            read=read_timeout,
            write=10.0,
            pool=10.0,
        )
        # Client HTTP réutilisable (connexion persistante)
        self._client: httpx.AsyncClient | None = None

    @property
    def model_info(self) -> ModelInfo:
        return self._model_info

    # ─── Gestion du client HTTP ───────────────────────────────────────────────

    async def _get_client(self) -> httpx.AsyncClient:
        """Retourne le client HTTP (crée si nécessaire)."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={"Content-Type": "application/json"},
            )
        return self._client

    async def close(self) -> None:
        """Ferme proprement le client HTTP."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ─── Génération complète ──────────────────────────────────────────────────

    async def generate(self, llm_input: LLMInput) -> LLMOutput:
        """
        Génère une réponse complète via l'API /v1/chat/completions.
        Supporte le tool calling si des outils sont définis dans llm_input.
        """
        client = await self._get_client()
        payload = llm_input.to_openai_payload(
            model_name=self._model_info.model_id
        )
        payload["stream"] = False

        start_time = time.monotonic()
        try:
            response = await client.post("/chat/completions", json=payload)
            response.raise_for_status()
        except httpx.ConnectError as exc:
            raise LLMProviderUnavailableError(
                f"llama.cpp server indisponible à {self._base_url}: {exc}",
                provider=self._model_info.alias,
            ) from exc
        except httpx.TimeoutException as exc:
            raise LLMProviderUnavailableError(
                f"Timeout lors de la requête à {self._base_url}: {exc}",
                provider=self._model_info.alias,
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise LLMProviderError(
                f"Erreur HTTP {exc.response.status_code}: {exc.response.text}",
                provider=self._model_info.alias,
                status_code=exc.response.status_code,
            ) from exc

        latency_ms = (time.monotonic() - start_time) * 1000
        data = response.json()

        return self._parse_completion_response(data, latency_ms)

    # ─── Streaming ────────────────────────────────────────────────────────────

    async def stream(
        self, llm_input: LLMInput
    ) -> AsyncGenerator[str, None]:
        """
        Génère une réponse en streaming (Server-Sent Events).
        Yields des fragments de texte au fur et à mesure.
        """
        client = await self._get_client()
        payload = llm_input.to_openai_payload(
            model_name=self._model_info.model_id
        )
        payload["stream"] = True

        try:
            async with client.stream("POST", "/chat/completions", json=payload) as response:
                response.raise_for_status()
                async for line in response.aiter_lines():
                    if not line or not line.startswith("data: "):
                        continue
                    chunk_str = line[6:]  # Supprimer "data: "
                    if chunk_str == "[DONE]":
                        break
                    try:
                        chunk = json.loads(chunk_str)
                        delta = chunk["choices"][0].get("delta", {})
                        content = delta.get("content", "")
                        if content:
                            yield content
                    except (json.JSONDecodeError, KeyError, IndexError):
                        continue

        except httpx.ConnectError as exc:
            raise LLMProviderUnavailableError(
                f"llama.cpp server indisponible pour streaming: {exc}",
                provider=self._model_info.alias,
            ) from exc

    # ─── Health check ─────────────────────────────────────────────────────────

    async def health_check(self) -> bool:
        """
        Vérifie la disponibilité du serveur llama.cpp.
        Utilise l'endpoint /health (retourne {"status": "ok"}).
        """
        try:
            client = await self._get_client()
            # Utiliser un client séparé avec timeout court pour le health check
            async with httpx.AsyncClient(timeout=3.0) as check_client:
                response = await check_client.get(f"{self._base_url}/health")
                if response.status_code == 200:
                    self._model_info.is_available = True
                    return True
                self._model_info.is_available = False
                return False
        except Exception:
            self._model_info.is_available = False
            return False

    async def get_response_time(self) -> float:
        """Mesure le temps de réponse du serveur (ms). Retourne -1 si indisponible."""
        start = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=5.0) as check_client:
                response = await check_client.get(
                    f"{self._base_url}/health"
                )
                if response.status_code == 200:
                    ms = (time.monotonic() - start) * 1000
                    self._model_info.last_response_ms = ms
                    return ms
        except Exception:
            pass
        return -1.0

    # ─── Parsing des réponses ─────────────────────────────────────────────────

    def _parse_completion_response(
        self,
        data: dict,
        latency_ms: float,
    ) -> LLMOutput:
        """Convertit la réponse JSON OpenAI en LLMOutput."""
        choice = data["choices"][0]
        message = choice["message"]
        usage = data.get("usage", {})

        # Extraction du contenu texte
        content: str = message.get("content") or ""

        # Extraction des tool calls si présents
        tool_calls: list[ToolCall] = []
        raw_tool_calls = message.get("tool_calls") or []
        for tc in raw_tool_calls:
            try:
                tool_calls.append(ToolCall.from_openai_dict(tc))
            except (KeyError, json.JSONDecodeError) as exc:
                logger.warning("Impossible de parser tool_call: %s — %s", tc, exc)

        return LLMOutput(
            content=content,
            tool_calls=tool_calls,
            finish_reason=choice.get("finish_reason", "stop"),
            prompt_tokens=usage.get("prompt_tokens", 0),
            completion_tokens=usage.get("completion_tokens", 0),
            model_tier=self._model_info.tier,
            latency_ms=latency_ms,
        )
