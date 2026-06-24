# HAOS — Human-AI Operating System

Système d'exploitation agentique pour startup IA mono-humain.

## Architecture

- **CEO humain** → dirige 28 agents IA spécialisés
- **Mac M4 Pro 48GB** → cerveau principal
- **3 modèles locaux** via llama.cpp server
- **Redis** → bus événementiel entre tous les agents
- **FastAPI** → API REST + WebSocket pour l'IHM Flutter
- **SQLite** → persistance mémoire / logs / tâches planifiées
- **Tailscale VPN** → communication sécurisée multi-appareils

## Modèles

| Alias    | Fichier                                        | Port | Usage                    |
|----------|------------------------------------------------|------|--------------------------|
| APEX     | Qwen3.6-35B-A3B-APEX-Balanced.gguf            | 8081 | Stratégique / Complexe   |
| QWYTHOS  | Qwythos-9B-Claude-Mythos-5-1M-MTP-BF16.gguf  | 8082 | Intermédiaire            |
| NANO     | mythos-nano-f16.gguf                           | 8083 | Rapide / Système         |

## Agents (28)

### C-Suite (APEX)
- `ceo-01` — CEO Agent (orchestrateur principal)
- `cto-01` — CTO Agent
- `cpo-01` — CPO Agent
- `cfo-01` — CFO Agent

### Développement (QWYTHOS/APEX)
- `lead-dev-01`, `flutter-dev-01`, `backend-dev-01`, `qa-01`, `security-01`, `devops-01`, `code-review-01`, `refactor-01`, `doc-01`

### Produit & Stratégie (QWYTHOS)
- `pm-01`, `ux-research-01`, `market-research-01`, `analytics-01`

### Marketing & Contenu (QWYTHOS/NANO)
- `content-strategy-01`, `copywriter-01`, `tiktok-01`, `youtube-01`, `seo-01`, `social-media-01`

### Système (NANO/QWYTHOS)
- `memory-01`, `scheduler-01`, `notification-01`, `logger-01`, `health-monitor-01`

## Démarrage rapide

```bash
# 1. Copier et configurer l'environnement
cp .env.example .env
# Éditer .env avec vos chemins

# 2. Installer les dépendances
make install

# 3. Installer les launchd agents (démarrage auto macOS)
make launchd-install

# 4. Démarrer le système
make start

# 5. Vérifier l'état
make status
```

## Structure

```
haos/
├── core/           — Config, Redis bus, LLM providers
├── agents/         — BaseAgent, Registry, 28 agents
├── api/            — FastAPI routes, WebSocket
├── orchestrator/   — LangGraph graph
├── memory/         — SQLite vault
├── ecc/            — ECC bridge
├── identities/     — 28 fichiers JSON d'identité
└── launchd/        — macOS Launch Agents
```

## Endpoints API

| Méthode | Route                    | Description                        |
|---------|--------------------------|------------------------------------|
| POST    | /chat                    | Envoyer message au CEO Agent        |
| WS      | /ws                      | WebSocket bidirectionnel CEO        |
| GET     | /events/stream           | SSE flux activité agents            |
| GET     | /agents                  | Liste 28 agents + statut            |
| GET     | /agents/{id}             | Détail agent + dernière activité    |
| POST    | /agents/{id}/run         | Déclencher manuellement un agent   |
| GET     | /agents/{id}/logs        | 50 derniers logs                   |
| GET     | /health                  | Santé système globale               |
| GET     | /health/models           | Statut + temps réponse par modèle  |

## Canaux Redis

- `human.commands` — Commandes du CEO humain
- `human.notifications` — Notifications vers l'humain
- `agents.csuite.*` — Activité C-Suite
- `agents.dev.*` — Activité développement
- `agents.marketing.*` — Activité marketing
- `agents.system.*` — Activité système
- `system.health` — Santé des services
