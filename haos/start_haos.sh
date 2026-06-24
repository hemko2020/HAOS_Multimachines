#!/bin/bash
# HAOS Start — Lance depuis ton Terminal
# Usage: chmod +x start_haos.sh && ./start_haos.sh

HAOS_ROOT="/Users/hemko/HAOS_Multimachines/haos"
VENV="$HAOS_ROOT/.venv"

echo "╔══════════════════════════════════════╗"
echo "║        HAOS Start                    ║"
echo "╚══════════════════════════════════════╝"

# ── Redis ──────────────────────────────────────────────────────────────────────
echo ""
echo "→ Redis..."
pkill -f redis-server 2>/dev/null || true
sleep 1
redis-server --daemonize yes --port 6379 --bind 127.0.0.1
sleep 2
redis-cli ping | grep -q PONG && echo "  ✅ Redis :6379" || echo "  ❌ Redis KO"

# ── NANO ───────────────────────────────────────────────────────────────────────
echo ""
echo "→ NANO LLM (port 8083)..."
pkill -f "llama-server.*8083" 2>/dev/null || true
sleep 1
nohup llama-server \
  -m /Users/hemko/Models/mythos-nano-f16.gguf \
  --port 8083 --ctx-size 8192 -ngl 99 \
  --host 127.0.0.1 --parallel 4 \
  > ~/Library/Logs/HAOS/nano.log 2>&1 &
echo "  PID NANO: $!"

# ── QWYTHOS ────────────────────────────────────────────────────────────────────
echo ""
echo "→ QWYTHOS LLM (port 8082)..."
pkill -f "llama-server.*8082" 2>/dev/null || true
sleep 1
nohup llama-server \
  -m /Users/hemko/Models/Qwythos-9B-Claude-Mythos-5-1M-MTP-BF16.gguf \
  --port 8082 --ctx-size 32768 -ngl 99 \
  --host 127.0.0.1 --parallel 2 \
  > ~/Library/Logs/HAOS/qwythos.log 2>&1 &
echo "  PID QWYTHOS: $!"

# ── APEX ───────────────────────────────────────────────────────────────────────
echo ""
echo "→ APEX LLM (port 8081)..."
pkill -f "llama-server.*8081" 2>/dev/null || true
sleep 1
nohup llama-server \
  -m /Users/hemko/Models/Qwen3.6-35B-A3B-APEX-Balanced.gguf \
  --port 8081 --ctx-size 32768 -ngl 99 \
  --host 127.0.0.1 --parallel 1 \
  > ~/Library/Logs/HAOS/apex.log 2>&1 &
echo "  PID APEX: $!"

# ── Attente chargement modèles ─────────────────────────────────────────────────
echo ""
echo "→ Attente chargement des modèles (30s)..."
sleep 30

# ── FastAPI ────────────────────────────────────────────────────────────────────
echo ""
echo "→ FastAPI (port 8000)..."
pkill -f "uvicorn api.main" 2>/dev/null || true
sleep 1
cd "$HAOS_ROOT"
nohup "$VENV/bin/python" -m uvicorn api.main:app \
  --host 0.0.0.0 --port 8000 --log-level info \
  > ~/Library/Logs/HAOS/api.log 2>&1 &
echo "  PID API: $!"
sleep 5

# ── Vérification ───────────────────────────────────────────────────────────────
echo ""
echo "╔══════════════════════════════════════╗"
echo "║         Statut HAOS                  ║"
echo "╚══════════════════════════════════════╝"

redis-cli ping 2>/dev/null | grep -q PONG \
  && echo "  ✅ Redis         :6379" \
  || echo "  ❌ Redis"

for entry in "NANO:8083" "QWYTHOS:8082" "APEX:8081"; do
  name="${entry%%:*}"; port="${entry##*:}"
  curl -s --max-time 5 "http://localhost:$port/v1/models" > /dev/null 2>&1 \
    && echo "  ✅ $name LLM   :$port" \
    || echo "  ⏳ $name LLM   :$port (chargement...)"
done

curl -s --max-time 5 "http://localhost:8000/health" > /dev/null 2>&1 \
  && echo "  ✅ FastAPI       :8000" \
  || echo "  ⏳ FastAPI       :8000 (démarrage...)"

echo ""
echo "  API docs  : http://localhost:8000/docs"
echo "  Health    : http://localhost:8000/health"
echo "  Agents    : http://localhost:8000/agents"
echo ""
echo "  Logs      : tail -f ~/Library/Logs/HAOS/api.log"
echo "  Stop      : pkill -f llama-server; pkill -f uvicorn; pkill -f redis-server"
echo ""
echo "✅ HAOS démarré !"
