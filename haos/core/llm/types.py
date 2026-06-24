"""
Types de données pour le système LLM HAOS.
Définit les structures de messages, entrées/sorties et métadonnées de modèles.
"""

from __future__ import annotations

from enum import Enum
from dataclasses import dataclass, field
from typing import Any


# ─── Niveaux de modèles ───────────────────────────────────────────────────────

class ModelTier(str, Enum):
    """
    Niveau de modèle LLM disponible dans HAOS.

    APEX     → Qwen3.6-35B (~22GB) — Stratégique, complexe, lent
    QWYTHOS  → Qwythos-9B (~10GB) — Intermédiaire, polyvalent
    NANO     → mythos-nano (~3GB)  — Rapide, système, tâches simples
    """
    APEX = "APEX"
    QWYTHOS = "QWYTHOS"
    NANO = "NANO"


# ─── Messages ─────────────────────────────────────────────────────────────────

@dataclass
class Message:
    """Un message dans la conversation (rôle + contenu)."""
    role: str  # "system" | "user" | "assistant" | "tool"
    content: str
    name: str | None = None  # Utilisé pour les réponses d'outils
    tool_call_id: str | None = None  # ID de l'appel d'outil correspondant

    def to_dict(self) -> dict[str, Any]:
        """Convertit en format OpenAI API."""
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.name:
            d["name"] = self.name
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        return d


# ─── Appels d'outils ─────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """Représente un appel d'outil demandé par le modèle."""
    id: str
    name: str
    arguments: dict[str, Any]

    @classmethod
    def from_openai_dict(cls, data: dict[str, Any]) -> "ToolCall":
        """Construit depuis le format API OpenAI."""
        import json
        args = data["function"]["arguments"]
        if isinstance(args, str):
            args = json.loads(args)
        return cls(
            id=data["id"],
            name=data["function"]["name"],
            arguments=args,
        )


@dataclass
class ToolDefinition:
    """Définit un outil disponible pour le modèle (format OpenAI function calling)."""
    name: str
    description: str
    parameters: dict[str, Any]  # JSON Schema

    def to_openai_dict(self) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


# ─── Entrée/Sortie LLM ────────────────────────────────────────────────────────

@dataclass
class LLMInput:
    """Entrée pour un provider LLM."""
    messages: list[Message]
    model_tier: ModelTier = ModelTier.NANO
    temperature: float = 0.7
    max_tokens: int = 2048
    tools: list[ToolDefinition] = field(default_factory=list)
    tool_choice: str = "auto"  # "auto" | "none" | "required"
    stream: bool = False
    agent_id: str | None = None  # Pour le logging et routing

    def to_openai_payload(self, model_name: str) -> dict[str, Any]:
        """Convertit en payload API OpenAI."""
        payload: dict[str, Any] = {
            "model": model_name,
            "messages": [m.to_dict() for m in self.messages],
            "temperature": self.temperature,
            "max_tokens": self.max_tokens,
            "stream": self.stream,
        }
        if self.tools:
            payload["tools"] = [t.to_openai_dict() for t in self.tools]
            payload["tool_choice"] = self.tool_choice
        return payload


@dataclass
class LLMOutput:
    """Sortie d'un provider LLM."""
    content: str
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str = "stop"  # "stop" | "tool_calls" | "length"
    prompt_tokens: int = 0
    completion_tokens: int = 0
    model_tier: ModelTier = ModelTier.NANO
    latency_ms: float = 0.0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    @property
    def has_tool_calls(self) -> bool:
        return len(self.tool_calls) > 0


# ─── Métadonnées de modèle ────────────────────────────────────────────────────

@dataclass
class ModelInfo:
    """Informations sur un modèle disponible."""
    tier: ModelTier
    model_file: str
    port: int
    base_url: str
    alias: str
    description: str
    context_size: int = 32768
    is_available: bool = False
    last_response_ms: float = 0.0

    @property
    def model_id(self) -> str:
        """Identifiant utilisé dans les appels API (nom du fichier sans extension)."""
        return self.model_file.replace(".gguf", "")


# ─── Association agent / modèle ───────────────────────────────────────────────

@dataclass
class AgentModel:
    """Lie un agent_id à un ModelTier spécifique."""
    agent_id: str
    model_tier: ModelTier
    model_file: str
    model_port: int


# ─── Résultat d'exécution d'agent ─────────────────────────────────────────────

@dataclass
class AgentResult:
    """Résultat de l'exécution d'un agent sur une tâche."""
    agent_id: str
    task: str
    output: str
    success: bool = True
    error: str | None = None
    tool_calls_made: list[ToolCall] = field(default_factory=list)
    tokens_used: int = 0
    execution_time_ms: float = 0.0
    model_tier: ModelTier = ModelTier.NANO
