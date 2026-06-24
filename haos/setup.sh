#!/bin/bash
set -e

echo "🚀 HAOS — Setup complet"
HAOS_ROOT="/Users/Nom-de-votre-machine/HAOS_Multimachines/haos"
MODELS_DIR="/Users/Nom-de-votre-machine/Models"

# ── 1. Homebrew permissions ────────────────────────────────────────────────────
echo "→ Fix Homebrew permissions..."
sudo chown -R $(whoami) /opt/homebrew 2>/dev/null || true

# ── 2. Redis ───────────────────────────────────────────────────────────────────
echo "→ Installation Redis..."
brew install redis

# ── 3. Python deps ─────────────────────────────────────────────────────────────
echo "→ Installation dépendances Python..."
pip3 install fastapi "uvicorn[standard]" redis langgraph langchain-openai \
             httpx apscheduler pydantic "pydantic-settings" \
             python-dotenv aiosqlite

# ── 4. Dossiers ────────────────────────────────────────────────────────────────
echo "→ Création des dossiers..."
mkdir -p "$HAOS_ROOT/data"
mkdir -p ~/Library/Logs/HAOS

# ── 5. LaunchAgents ────────────────────────────────────────────────────────────
echo "→ Installation des LaunchAgents..."
cp "$HAOS_ROOT/launchd/"*.plist ~/Library/LaunchAgents/

# Décharger si déjà chargé (ignore les erreurs)
for plist in redis nano qwythos apex api; do
  launchctl unload ~/Library/LaunchAgents/com.haos.$plist.plist 2>/dev/null || true
done

# Charger les services dans le bon ordre
echo "→ Démarrage Redis..."
launchctl load ~/Library/LaunchAgents/com.haos.redis.plist
sleep 2

echo "→ Démarrage NANO (port 8083)..."
launchctl load ~/Library/LaunchAgents/com.haos.nano.plist
sleep 3

echo "→ Démarrage QWYTHOS (port 8082)..."
launchctl load ~/Library/LaunchAgents/com.haos.qwythos.plist
sleep 3

echo "→ Démarrage APEX (port 8081)..."
launchctl load ~/Library/LaunchAgents/com.haos.apex.plist
sleep 5

echo "→ Démarrage API FastAPI (port 8000)..."
launchctl load ~/Library/LaunchAgents/com.haos.api.plist
sleep 3

# ── 6. Vérification ────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════"
echo "  HAOS — Vérification des services"
echo "═══════════════════════════════════════"

check_service() {
  local name=$1
  local port=$2
  if curl -s --max-time 3 "http://localhost:$port" > /dev/null 2>&1; then
    echo "  ✅ $name (port $port)"
  else
    echo "  ⏳ $name (port $port) — démarrage en cours..."
  fi
}

# Redis
if redis-cli ping 2>/dev/null | grep -q PONG; then
  echo "  ✅ Redis"
else
  echo "  ❌ Redis — vérifier ~/Library/Logs/HAOS/redis.log"
fi

check_service "NANO LLM"    8083
check_service "QWYTHOS LLM" 8082
check_service "APEX LLM"    8081
check_service "FastAPI"     8000

echo ""
echo "  Logs : ~/Library/Logs/HAOS/"
echo "  API  : http://localhost:8000/docs"
echo "  Health : http://localhost:8000/health"
echo ""
echo "✅ Setup HAOS terminé !"
