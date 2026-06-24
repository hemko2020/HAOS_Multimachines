.PHONY: install start stop status logs dev launchd-install launchd-uninstall clean

HAOS_ROOT := $(shell pwd)
LAUNCHD_DIR := $(HOME)/Library/LaunchAgents
LOG_DIR := $(HOME)/Library/Logs/HAOS

# ─────────────────────────────────────────────
# Installation
# ─────────────────────────────────────────────

install:
	@echo "==> Installation des dépendances Python..."
	pip install -r requirements.txt
	@echo "==> Création des dossiers de données..."
	mkdir -p $(HAOS_ROOT)/data
	mkdir -p $(LOG_DIR)
	@echo "==> Copie de .env.example vers .env si absent..."
	@test -f .env || cp .env.example .env && echo "  .env créé — pensez à le configurer!"
	@echo "✅ Installation terminée."

# ─────────────────────────────────────────────
# Gestion des services macOS (launchd)
# ─────────────────────────────────────────────

launchd-install:
	@echo "==> Installation des Launch Agents HAOS..."
	mkdir -p $(LAUNCHD_DIR)
	mkdir -p $(LOG_DIR)
	cp launchd/com.haos.apex.plist $(LAUNCHD_DIR)/
	cp launchd/com.haos.qwythos.plist $(LAUNCHD_DIR)/
	cp launchd/com.haos.nano.plist $(LAUNCHD_DIR)/
	cp launchd/com.haos.redis.plist $(LAUNCHD_DIR)/
	cp launchd/com.haos.api.plist $(LAUNCHD_DIR)/
	launchctl load $(LAUNCHD_DIR)/com.haos.redis.plist
	launchctl load $(LAUNCHD_DIR)/com.haos.nano.plist
	launchctl load $(LAUNCHD_DIR)/com.haos.qwythos.plist
	launchctl load $(LAUNCHD_DIR)/com.haos.apex.plist
	launchctl load $(LAUNCHD_DIR)/com.haos.api.plist
	@echo "✅ Launch Agents installés et démarrés."

launchd-uninstall:
	@echo "==> Désinstallation des Launch Agents HAOS..."
	-launchctl unload $(LAUNCHD_DIR)/com.haos.api.plist
	-launchctl unload $(LAUNCHD_DIR)/com.haos.apex.plist
	-launchctl unload $(LAUNCHD_DIR)/com.haos.qwythos.plist
	-launchctl unload $(LAUNCHD_DIR)/com.haos.nano.plist
	-launchctl unload $(LAUNCHD_DIR)/com.haos.redis.plist
	-rm $(LAUNCHD_DIR)/com.haos.*.plist
	@echo "✅ Launch Agents désinstallés."

# ─────────────────────────────────────────────
# Démarrage / Arrêt manuel
# ─────────────────────────────────────────────

start:
	@echo "==> Démarrage de HAOS..."
	launchctl start com.haos.redis
	@sleep 1
	launchctl start com.haos.nano
	launchctl start com.haos.qwythos
	launchctl start com.haos.apex
	@sleep 2
	launchctl start com.haos.api
	@echo "✅ HAOS démarré. API disponible sur http://localhost:8000"

stop:
	@echo "==> Arrêt de HAOS..."
	-launchctl stop com.haos.api
	-launchctl stop com.haos.apex
	-launchctl stop com.haos.qwythos
	-launchctl stop com.haos.nano
	-launchctl stop com.haos.redis
	@echo "✅ HAOS arrêté."

restart: stop
	@sleep 2
	$(MAKE) start

# ─────────────────────────────────────────────
# Statut et monitoring
# ─────────────────────────────────────────────

status:
	@echo "=== Statut HAOS ==="
	@echo "--- Services launchd ---"
	@launchctl list | grep com.haos || echo "  Aucun service HAOS actif"
	@echo ""
	@echo "--- Ports en écoute ---"
	@lsof -i :8081 -i :8082 -i :8083 -i :6379 -i :8000 2>/dev/null | grep LISTEN || echo "  Aucun port HAOS actif"
	@echo ""
	@echo "--- Santé API ---"
	@curl -s http://localhost:8000/health 2>/dev/null | python3 -m json.tool || echo "  API non disponible"

logs:
	@echo "==> Logs HAOS (tail -f) — Ctrl+C pour quitter"
	tail -f $(LOG_DIR)/api.log $(LOG_DIR)/apex.log $(LOG_DIR)/qwythos.log $(LOG_DIR)/nano.log 2>/dev/null

logs-api:
	tail -f $(LOG_DIR)/api.log

logs-apex:
	tail -f $(LOG_DIR)/apex.log

logs-nano:
	tail -f $(LOG_DIR)/nano.log

# ─────────────────────────────────────────────
# Développement
# ─────────────────────────────────────────────

dev:
	@echo "==> Démarrage en mode développement (hot-reload)..."
	uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload --log-level debug

dev-redis:
	redis-server --daemonize yes
	@echo "✅ Redis démarré en arrière-plan"

# ─────────────────────────────────────────────
# Utilitaires
# ─────────────────────────────────────────────

check-models:
	@echo "==> Vérification des modèles..."
	@source .env && test -f "$$MODELS_DIR/$$APEX_MODEL_FILE" && echo "✅ APEX: $$APEX_MODEL_FILE" || echo "❌ APEX: MANQUANT"
	@source .env && test -f "$$MODELS_DIR/$$QWYTHOS_MODEL_FILE" && echo "✅ QWYTHOS: $$QWYTHOS_MODEL_FILE" || echo "❌ QWYTHOS: MANQUANT"
	@source .env && test -f "$$MODELS_DIR/$$NANO_MODEL_FILE" && echo "✅ NANO: $$NANO_MODEL_FILE" || echo "❌ NANO: MANQUANT"

clean:
	@echo "==> Nettoyage..."
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -name "*.pyc" -delete 2>/dev/null || true
	@echo "✅ Nettoyage terminé."
