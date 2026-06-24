"""
HaosECCBridge — Pont entre les compétences ECC et les agents HAOS.

ECC (Éléments de Compétence Critique) = compétences fondamentales
nécessaires pour certaines tâches spécialisées.

Ce bridge :
- Mappe les skills ECC aux agents HAOS qui peuvent les utiliser
- Synchronise les skills vers le contexte de l'agent
- Permet d'injecter des connaissances spécialisées dans les prompts
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ─── Mapping ECC skills → agent IDs ──────────────────────────────────────────
# Chaque skill ECC est associé à un ou plusieurs agents HAOS

ECC_SKILL_MAPPING: dict[str, list[str]] = {
    # Développement Flutter
    "flutter_ui_patterns": ["flutter-dev-01", "cto-01"],
    "flutter_state_management": ["flutter-dev-01", "lead-dev-01"],
    "flutter_navigation": ["flutter-dev-01"],
    "flutter_animations": ["flutter-dev-01"],
    "dart_advanced": ["flutter-dev-01", "lead-dev-01"],

    # Développement Backend
    "fastapi_patterns": ["backend-dev-01", "lead-dev-01", "cto-01"],
    "python_async": ["backend-dev-01", "lead-dev-01"],
    "redis_patterns": ["backend-dev-01", "devops-01"],
    "sqlite_optimization": ["backend-dev-01", "devops-01"],
    "api_design": ["backend-dev-01", "cto-01", "lead-dev-01"],

    # DevOps & Infrastructure
    "docker_compose": ["devops-01"],
    "macos_launchd": ["devops-01", "devops-01"],
    "tailscale_networking": ["devops-01", "cto-01"],
    "llama_cpp_config": ["devops-01", "cto-01"],
    "monitoring_alerting": ["devops-01", "health-monitor-01"],

    # Sécurité
    "owasp_top10": ["security-01"],
    "api_security": ["security-01", "backend-dev-01"],
    "data_privacy_gdpr": ["security-01", "cfo-01"],
    "dependency_audit": ["security-01", "qa-01"],
    "secrets_management": ["security-01", "devops-01"],

    # Qualité & Tests
    "pytest_patterns": ["qa-01", "lead-dev-01"],
    "test_automation": ["qa-01"],
    "code_coverage": ["qa-01", "code-review-01"],
    "e2e_testing": ["qa-01", "flutter-dev-01"],

    # Revue de code
    "code_review_standards": ["code-review-01", "lead-dev-01"],
    "refactoring_patterns": ["refactor-01", "lead-dev-01"],
    "design_patterns": ["refactor-01", "cto-01"],
    "clean_code": ["code-review-01", "refactor-01"],

    # Documentation
    "markdown_docs": ["doc-01"],
    "openapi_spec": ["doc-01", "backend-dev-01"],
    "technical_writing_fr": ["doc-01", "copywriter-01"],
    "readme_best_practices": ["doc-01"],

    # Produit & UX
    "user_story_mapping": ["pm-01", "ux-research-01"],
    "ux_research_methods": ["ux-research-01"],
    "figma_principles": ["ux-research-01"],
    "product_analytics": ["pm-01", "analytics-01"],
    "roadmap_planning": ["pm-01", "cpo-01"],
    "okr_framework": ["pm-01", "ceo-01", "cpo-01"],

    # Marketing & Contenu
    "seo_technical": ["seo-01"],
    "seo_content": ["seo-01", "copywriter-01", "content-strategy-01"],
    "tiktok_algorithm": ["tiktok-01", "social-media-01"],
    "youtube_seo": ["youtube-01", "seo-01"],
    "copywriting_persuasion": ["copywriter-01"],
    "content_calendar": ["content-strategy-01", "social-media-01"],
    "social_media_fr": ["social-media-01", "copywriter-01"],

    # Recherche & Analyse
    "market_analysis": ["market-research-01", "ceo-01"],
    "competitor_analysis": ["market-research-01", "cpo-01"],
    "data_analysis_python": ["analytics-01"],
    "kpi_dashboard": ["analytics-01", "cfo-01"],

    # Finance & Business
    "startup_financials": ["cfo-01", "ceo-01"],
    "saas_metrics": ["cfo-01", "analytics-01"],
    "budget_planning": ["cfo-01"],

    # IA & LLM
    "prompt_engineering": ["ceo-01", "cto-01", "lead-dev-01"],
    "langgraph_patterns": ["lead-dev-01", "cto-01", "backend-dev-01"],
    "agent_design": ["cto-01", "lead-dev-01"],
    "llm_evaluation": ["qa-01", "cto-01"],
}

# Contenu des skills ECC (résumés/guides injectables dans les prompts)
# Dans un déploiement réel, ces contenus seraient chargés depuis des fichiers
ECC_SKILL_CONTENTS: dict[str, str] = {
    "flutter_ui_patterns": (
        "Patterns Flutter UI: utiliser CustomPaint pour dessins complexes, "
        "InheritedWidget pour prop drilling, Sliver pour listes performantes, "
        "AnimatedSwitcher pour transitions fluides."
    ),
    "fastapi_patterns": (
        "Patterns FastAPI: Dependency Injection avec Depends(), "
        "lifespan context manager pour startup/shutdown, "
        "BackgroundTasks pour tâches async, Pydantic v2 pour validation."
    ),
    "redis_patterns": (
        "Patterns Redis HAOS: pub/sub pour événements inter-agents, "
        "sorted sets pour files de priorité, streams pour audit log temps réel, "
        "TTL sur les clés de session."
    ),
    "langgraph_patterns": (
        "Patterns LangGraph: StateGraph pour orchestration d'agents, "
        "checkpointing pour reprises, reducers pour fusion d'état, "
        "conditional edges pour routing dynamique."
    ),
    "prompt_engineering": (
        "Prompt Engineering HAOS: system prompt en français, "
        "chain-of-thought pour raisonnement complexe, "
        "few-shot examples pour tâches structurées, "
        "XML tags pour délimiter sections dans les prompts longs."
    ),
    "okr_framework": (
        "OKRs Startup: Objective = ambitieux mais atteignable (3 par trimestre), "
        "Key Results = mesurables, binaires ou avec seuil de 0-100%, "
        "check-in hebdomadaire, scoring 0.7 = cible idéale."
    ),
}


class HaosECCBridge:
    """
    Pont entre les compétences ECC et les agents HAOS.

    Permet à chaque agent d'accéder aux compétences critiques
    correspondant à son rôle via injection de contexte.
    """

    def __init__(self) -> None:
        self._skill_mapping = ECC_SKILL_MAPPING
        self._skill_contents = ECC_SKILL_CONTENTS

    def get_agent_skills(self, agent_id: str) -> list[str]:
        """
        Retourne la liste des skills ECC associés à un agent.

        Args:
            agent_id: ID de l'agent HAOS

        Returns:
            Liste de noms de skills ECC.
        """
        return [
            skill
            for skill, agents in self._skill_mapping.items()
            if agent_id in agents
        ]

    def get_skill_content(self, skill_name: str) -> str | None:
        """
        Retourne le contenu d'un skill ECC.

        Args:
            skill_name: Nom du skill

        Returns:
            Contenu du skill ou None si non trouvé.
        """
        return self._skill_contents.get(skill_name)

    def sync_skills(self, agent_id: str) -> str:
        """
        Génère un bloc de contexte ECC injectable dans le prompt d'un agent.

        Args:
            agent_id: ID de l'agent

        Returns:
            Texte formaté avec les compétences ECC de cet agent.
        """
        skills = self.get_agent_skills(agent_id)
        if not skills:
            return ""

        lines = [f"=== Compétences ECC ({agent_id}) ==="]
        for skill in skills:
            content = self._skill_contents.get(skill)
            if content:
                lines.append(f"\n**{skill}**\n{content}")
            else:
                lines.append(f"\n**{skill}** (référence disponible)")

        return "\n".join(lines)

    def get_agents_for_skill(self, skill_name: str) -> list[str]:
        """
        Retourne les agents qui ont une compétence ECC spécifique.

        Args:
            skill_name: Nom du skill

        Returns:
            Liste des agent IDs.
        """
        return self._skill_mapping.get(skill_name, [])

    def add_skill(
        self,
        skill_name: str,
        agent_ids: list[str],
        content: str | None = None,
    ) -> None:
        """
        Ajoute ou met à jour un skill ECC.

        Args:
            skill_name: Nom du nouveau skill
            agent_ids: Agents qui possèdent ce skill
            content: Description/guide du skill (optionnel)
        """
        self._skill_mapping[skill_name] = agent_ids
        if content:
            self._skill_contents[skill_name] = content
        logger.info(
            "ECC Bridge: skill '%s' ajouté/mis à jour → %d agents",
            skill_name,
            len(agent_ids),
        )

    def get_coverage_report(self) -> dict[str, Any]:
        """
        Génère un rapport de couverture ECC (skills par agent).

        Returns:
            Dictionnaire {agent_id: [skills], ...}
        """
        coverage: dict[str, list[str]] = {}
        for skill, agents in self._skill_mapping.items():
            for agent_id in agents:
                if agent_id not in coverage:
                    coverage[agent_id] = []
                coverage[agent_id].append(skill)

        return {
            "total_skills": len(self._skill_mapping),
            "total_agents_covered": len(coverage),
            "skills_with_content": len(self._skill_contents),
            "coverage": coverage,
        }

    def get_all_skills(self) -> list[str]:
        """Retourne la liste de tous les skills ECC définis."""
        return list(self._skill_mapping.keys())


# ─── Singleton ────────────────────────────────────────────────────────────────

_bridge_instance: HaosECCBridge | None = None


def get_ecc_bridge() -> HaosECCBridge:
    """Retourne l'instance singleton du bridge ECC."""
    global _bridge_instance
    if _bridge_instance is None:
        _bridge_instance = HaosECCBridge()
    return _bridge_instance
