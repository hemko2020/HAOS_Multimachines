"""
Memory Agent — Gestionnaire de mémoire persistante HAOS.
Modèle: QWYTHOS (intermédiaire, bon pour la synthèse)
Gère les sessions et contextes de tous les agents dans SQLite.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from agents.base_agent import BaseAgent
from agents.registry import register_agent_class

logger = logging.getLogger(__name__)

# Nombre de sessions à récupérer pour le contexte
DEFAULT_CONTEXT_SESSIONS = 5


@register_agent_class("memory-01")
class MemoryAgent(BaseAgent):
    """
    Agent de mémoire centralisée HAOS.

    Fonctionnalités :
    - Sauvegarde des sessions (résumés par agent)
    - Récupération du contexte historique
    - Synthèse IA des mémoires longue durée
    - Triggered by: fin de session d'un agent
    """

    async def execute(self, task: str, context: dict[str, Any]) -> str:
        """
        Opérations de mémoire :
        - 'save <agent_id>: <contenu>'  → sauvegarde une mémoire
        - 'get <agent_id>'              → récupère le contexte
        - 'summarize <agent_id>'        → résumé IA des mémoires
        - 'clear <agent_id>'            → efface les mémoires d'un agent
        """
        task_stripped = task.strip()

        if task_stripped.startswith("save "):
            return await self._handle_save(task_stripped[5:])
        elif task_stripped.startswith("get "):
            agent_id = task_stripped[4:].strip()
            return await self._handle_get(agent_id)
        elif task_stripped.startswith("summarize "):
            agent_id = task_stripped[10:].strip()
            return await self._handle_summarize(agent_id)
        elif task_stripped.startswith("clear "):
            agent_id = task_stripped[6:].strip()
            return await self._handle_clear(agent_id)
        else:
            # Utiliser le LLM pour répondre à des questions sur la mémoire
            return await self.generate_simple(
                f"Tu es l'agent mémoire HAOS. Réponds à cette requête sur la mémoire des agents: {task}"
            )

    # ─── Sauvegarde de session ────────────────────────────────────────────────

    async def save_session(
        self,
        agent_id: str,
        task: str,
        output: str,
        success: bool = True,
    ) -> None:
        """
        Sauvegarde une session d'agent après sa complétion.
        Génère automatiquement un résumé via QWYTHOS.

        Args:
            agent_id: ID de l'agent dont on sauvegarde la session
            task: Tâche exécutée
            output: Résultat produit
            success: Si la tâche a réussi
        """
        try:
            # Générer un résumé concis avec le LLM
            summary = await self._generate_summary(agent_id, task, output)

            from memory.vault import get_vault
            vault = await get_vault()
            await vault.save_memory(
                agent_id=agent_id,
                memory_type="session",
                content=summary,
                metadata={
                    "task": task[:200],
                    "success": success,
                    "raw_output_length": len(output),
                },
            )
            logger.info(
                "MemoryAgent: session sauvegardée pour agent %s", agent_id
            )

        except Exception as exc:
            logger.error(
                "MemoryAgent: erreur sauvegarde session %s: %s", agent_id, exc
            )

    async def get_context(
        self,
        agent_id: str,
        n_sessions: int = DEFAULT_CONTEXT_SESSIONS,
    ) -> str:
        """
        Récupère le contexte récent d'un agent (N dernières sessions).

        Args:
            agent_id: ID de l'agent
            n_sessions: Nombre de sessions à récupérer

        Returns:
            Contexte formaté pour injection dans un prompt.
        """
        try:
            from memory.vault import get_vault
            vault = await get_vault()
            memories = await vault.get_memories(
                agent_id=agent_id,
                limit=n_sessions,
            )

            if not memories:
                return "Aucun contexte précédent disponible."

            lines = [
                f"=== Contexte précédent de {agent_id} ({len(memories)} sessions) ==="
            ]
            for m in memories:
                ts = m.get("created_at", "")[:10]
                content = m.get("content", "")
                lines.append(f"\n[{ts}] {content}")

            return "\n".join(lines)

        except Exception as exc:
            logger.error(
                "MemoryAgent: erreur récupération contexte %s: %s", agent_id, exc
            )
            return f"Erreur récupération contexte: {exc}"

    # ─── Handlers de commandes ────────────────────────────────────────────────

    async def _handle_save(self, args: str) -> str:
        """Parse et sauvegarde: 'agent_id: contenu'."""
        if ":" not in args:
            return "Format invalide. Utiliser: save <agent_id>: <contenu>"
        agent_id, content = args.split(":", 1)
        agent_id = agent_id.strip()
        content = content.strip()

        try:
            from memory.vault import get_vault
            vault = await get_vault()
            await vault.save_memory(
                agent_id=agent_id,
                memory_type="manual",
                content=content,
            )
            return f"Mémoire sauvegardée pour {agent_id}."
        except Exception as exc:
            return f"Erreur sauvegarde: {exc}"

    async def _handle_get(self, agent_id: str) -> str:
        """Récupère le contexte d'un agent."""
        return await self.get_context(agent_id)

    async def _handle_summarize(self, agent_id: str) -> str:
        """Génère une synthèse IA des mémoires d'un agent."""
        try:
            from memory.vault import get_vault
            vault = await get_vault()
            memories = await vault.get_memories(agent_id=agent_id, limit=20)

            if not memories:
                return f"Aucune mémoire trouvée pour {agent_id}."

            all_content = "\n---\n".join(
                m.get("content", "") for m in memories
            )

            prompt = (
                f"Voici les {len(memories)} dernières mémoires de l'agent '{agent_id}':\n\n"
                f"{all_content[:3000]}\n\n"
                "Génère une synthèse concise (3-5 phrases) des connaissances et du contexte "
                f"de cet agent: son rôle, ses dernières activités, ses points importants."
            )
            return await self.generate_simple(prompt, max_tokens=512)

        except Exception as exc:
            return f"Erreur synthèse: {exc}"

    async def _handle_clear(self, agent_id: str) -> str:
        """Efface les mémoires d'un agent."""
        try:
            from memory.vault import get_vault
            vault = await get_vault()
            count = await vault.clear_memories(agent_id)
            return f"{count} mémoire(s) effacée(s) pour {agent_id}."
        except Exception as exc:
            return f"Erreur suppression: {exc}"

    # ─── Génération de résumé ─────────────────────────────────────────────────

    async def _generate_summary(
        self,
        agent_id: str,
        task: str,
        output: str,
    ) -> str:
        """Génère un résumé concis d'une session via QWYTHOS."""
        # Tronquer l'output si trop long
        output_preview = output[:1500] if len(output) > 1500 else output

        prompt = (
            f"Agent: {agent_id}\n"
            f"Tâche: {task}\n"
            f"Résultat: {output_preview}\n\n"
            "Résume cette session en 2-3 phrases (ce qui a été fait, le résultat clé). "
            "Sois factuel et concis."
        )
        return await self.generate_simple(prompt, max_tokens=256, temperature=0.3)

    # ─── Gestion des événements Redis ─────────────────────────────────────────

    async def handle_event(self, event: dict[str, Any]) -> None:
        """
        Écoute les événements task_completed et sauvegarde automatiquement.
        """
        if event.get("event_type") != "task_completed":
            return

        agent_id = event.get("agent_id")
        if not agent_id or agent_id == self.agent_id:
            return

        # Récupérer les détails de la tâche si disponibles
        task = event.get("task", "")
        output_length = event.get("output_length", 0)

        if task:
            await self.save_session(
                agent_id=agent_id,
                task=task,
                output=f"[Session auto-sauvegardée. Longueur output: {output_length} chars]",
                success=True,
            )
