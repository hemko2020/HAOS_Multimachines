#!/bin/bash
# HAOS Bootstrap — Lance depuis ton Terminal
set -e

HAOS_ROOT="/Users/hemko/HAOS_Multimachines/haos"
MODELS_DIR="/Users/hemko/Models"
VENV="$HAOS_ROOT/.venv"
LOG_DIR="$HOME/Library/Logs/HAOS"
LAUNCHD_DIR="$HOME/Library/LaunchAgents"

echo "╔══════════════════════════════════════╗"
echo "║       HAOS Bootstrap v1.1            ║"
echo "╚══════════════════════════════════════╝"

# ── 1. Venv dédié ──────────────────────────────────────────────────────────────
echo ""
echo "→ [1/6] Création du venv Python..."
python3 -m venv "$VENV"
source "$VENV/bin/activate"
pip install --upgrade pip --quiet

echo "→ [2/6] Installation des dépendances Python..."
pip install fastapi "uvicorn[standard]" redis langgraph langchain-openai \
            httpx apscheduler pydantic "pydantic-settings" \
            python-dotenv aiosqlite --quiet
FASTAPI_VER=$(python -c "import fastapi; print(fastapi.__version__)")
echo "  OK fastapi $FASTAPI_VER"

# ── 2. Dossiers ────────────────────────────────────────────────────────────────
echo ""
echo "→ [3/6] Création des dossiers..."
mkdir -p "$HAOS_ROOT/data"
mkdir -p "$LOG_DIR"
for svc in nano qwythos apex api redis; do
  touch "$LOG_DIR/$svc.log" "$LOG_DIR/$svc.error.log"
done

# ── 3. Plists avec les bons chemins ───────────────────────────────────────────
echo ""
echo "→ [4/6] Génération des plists..."
LLAMA_BIN="$(which llama-server)"
UVICORN_BIN="$VENV/bin/uvicorn"
echo "  llama-server : $LLAMA_BIN"
echo "  uvicorn      : $UVICORN_BIN"

generate_llm_plist() {
  local label=$1
  local model_file=$2
  local port=$3
  local ctx=$4
  local parallel=$5
  local throttle=$6
  cat > "$LAUNCHD_DIR/${label}.plist" << PLIST_CONTENT
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${LLAMA_BIN}</string>
    <string>-m</string><string>${MODELS_DIR}/${model_file}</string>
    <string>--port</string><string>${port}</string>
    <string>--ctx-size</string><string>${ctx}</string>
    <string>-ngl</string><string>99</string>
    <string>--host</string><string>127.0.0.1</string>
    <string>--parallel</string><string>${parallel}</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG_DIR}/${label##*.}.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/${label##*.}.error.log</string>
  <key>WorkingDirectory</key><string>${MODELS_DIR}</string>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>${HOME}</string>
    <key>PATH</key><string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
  </dict>
  <key>ProcessType</key><string>Background</string>
  <key>ThrottleInterval</key><integer>${throttle}</integer>
</dict></plist>
PLIST_CONTENT
}

generate_llm_plist "com.haos.nano"    "mythos-nano-f16.gguf"                         8083 8192  4 10
generate_llm_plist "com.haos.qwythos" "Qwythos-9B-Claude-Mythos-5-1M-MTP-BF16.gguf" 8082 32768 2 10
generate_llm_plist "com.haos.apex"    "Qwen3.6-35B-A3B-APEX-Balanced.gguf"           8081 32768 1 30

# API plist
cat > "$LAUNCHD_DIR/com.haos.api.plist" << PLIST_CONTENT
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.haos.api</string>
  <key>ProgramArguments</key>
  <array>
    <string>${UVICORN_BIN}</string>
    <string>api.main:app</string>
    <string>--host</string><string>0.0.0.0</string>
    <string>--port</string><string>8000</string>
    <string>--workers</string><string>1</string>
    <string>--log-level</string><string>info</string>
  </array>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>StandardOutPath</key><string>${LOG_DIR}/api.log</string>
  <key>StandardErrorPath</key><string>${LOG_DIR}/api.error.log</string>
  <key>WorkingDirectory</key><string>${HAOS_ROOT}</string>
  <key>EnvironmentVariables</key><dict>
    <key>HOME</key><string>${HOME}</string>
    <key>PATH</key><string>${VENV}/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin</string>
    <key>PYTHONPATH</key><string>${HAOS_ROOT}</string>
    <key>PYTHONUNBUFFERED</key><string>1</string>
    <key>VIRTUAL_ENV</key><string>${VENV}</string>
  </dict>
  <key>ProcessType</key><string>Background</string>
  <key>ThrottleInterval</key><integer>15</integer>
</dict></plist>
PLIST_CONTENT

echo "  OK plists générés"

# ── 4. Charger les services ────────────────────────────────────────────────────
echo ""
echo "→ [5/6] Démarrage des services..."

for svc in nano qwythos apex api; do
  launchctl unload "$LAUNCHD_DIR/com.haos.$svc.plist" 2>/dev/null || true
done

launchctl load "$LAUNCHD_DIR/com.haos.nano.plist"
echo "  NANO chargé (port 8083) — attente 20s pour le modèle..."
sleep 20

launchctl load "$LAUNCHD_DIR/com.haos.qwythos.plist"
echo "  QWYTHOS chargé (port 8082)..."
sleep 5

launchctl load "$LAUNCHD_DIR/com.haos.apex.plist"
echo "  APEX chargé (port 8081)..."
sleep 5

launchctl load "$LAUNCHD_DIR/com.haos.api.plist"
echo "  API chargée (port 8000) — attente 5s..."
sleep 5

# ── 5. Vérification ────────────────────────────────────────────────────────────
echo ""
echo "→ [6/6] Vérification..."
echo ""

redis-cli ping 2>/dev/null | grep -q PONG \
  && echo "  OK Redis       :6379" \
  || echo "  KO Redis"

for entry in "NANO:8083:v1/models" "QWYTHOS:8082:v1/models" "APEX:8081:v1/models" "API:8000:health"; do
  name="${entry%%:*}"; rest="${entry#*:}"; port="${rest%%:*}"; path="${rest#*:}"
  if curl -s --max-time 6 "http://localhost:$port/$path" > /dev/null 2>&1; then
    echo "  OK $name  :$port"
  else
    echo "  WAIT $name  :$port (modele en cours de chargement...)"
  fi
done

echo ""
echo "  Logs : tail -f ~/Library/Logs/HAOS/nano.log"
echo "  API  : http://localhost:8000/docs"
echo "  Note : APEX et QWYTHOS peuvent prendre 1-2 min a charger"
echo ""
echo "OK Bootstrap HAOS termine !"
echo "  -> make status   (verifier dans 2 min)"
echo "  -> make logs     (logs en direct)"
