"""
SQLiteVault — Persistance centrale HAOS.

Tables :
  - agent_memories      : mémoires par agent (sessions, notes)
  - agent_sessions      : résumés de sessions d'agents
  - scheduled_tasks     : tâches planifiées (APScheduler backup)
  - event_log           : journal d'activité complet
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import aiosqlite

from core.config import settings

logger = logging.getLogger(__name__)

# Schéma SQL de création des tables
CREATE_SCHEMA = """
-- Mémoires des agents (persistances longue durée)
CREATE TABLE IF NOT EXISTS agent_memories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id     TEXT NOT NULL,
    memory_type  TEXT NOT NULL DEFAULT 'session',
    content      TEXT NOT NULL,
    metadata     TEXT DEFAULT '{}',
    created_at   TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_memories_agent ON agent_memories(agent_id);
CREATE INDEX IF NOT EXISTS idx_memories_type  ON agent_memories(memory_type);
CREATE INDEX IF NOT EXISTS idx_memories_date  ON agent_memories(created_at DESC);

-- Sessions complètes des agents
CREATE TABLE IF NOT EXISTS agent_sessions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id        TEXT NOT NULL,
    task            TEXT NOT NULL,
    output          TEXT,
    success         INTEGER DEFAULT 1,
    execution_ms    REAL DEFAULT 0,
    model_tier      TEXT,
    tokens_used     INTEGER DEFAULT 0,
    started_at      TEXT NOT NULL DEFAULT (datetime('now')),
    completed_at    TEXT
);

CREATE INDEX IF NOT EXISTS idx_sessions_agent ON agent_sessions(agent_id);
CREATE INDEX IF NOT EXISTS idx_sessions_date  ON agent_sessions(started_at DESC);

-- Tâches planifiées
CREATE TABLE IF NOT EXISTS scheduled_tasks (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id     TEXT UNIQUE NOT NULL,
    name        TEXT NOT NULL,
    agent_id    TEXT NOT NULL,
    task        TEXT NOT NULL,
    trigger     TEXT NOT NULL DEFAULT 'interval',
    config      TEXT DEFAULT '{}',
    enabled     INTEGER DEFAULT 1,
    last_run    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Journal d'événements
CREATE TABLE IF NOT EXISTS event_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp   TEXT NOT NULL DEFAULT (datetime('now')),
    channel     TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    agent_id    TEXT,
    data        TEXT DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_events_timestamp  ON event_log(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_events_agent      ON event_log(agent_id);
CREATE INDEX IF NOT EXISTS idx_events_channel    ON event_log(channel);
CREATE INDEX IF NOT EXISTS idx_events_type       ON event_log(event_type);
"""


class SQLiteVault:
    """
    Vault de persistance SQLite asynchrone pour HAOS.

    Toutes les méthodes sont asynchrones (aiosqlite).
    Une seule connexion est maintenue (WAL mode pour la concurrence).
    """

    def __init__(self, db_path: Path | None = None) -> None:
        self._db_path = db_path or settings.vault_db_path
        self._initialized: bool = False

    async def initialize(self) -> None:
        """Crée les tables si elles n'existent pas."""
        if self._initialized:
            return

        # Créer le répertoire si nécessaire
        self._db_path.parent.mkdir(parents=True, exist_ok=True)

        async with aiosqlite.connect(str(self._db_path)) as db:
            # Activer WAL pour les accès concurrents
            await db.execute("PRAGMA journal_mode=WAL")
            await db.execute("PRAGMA foreign_keys=ON")
            await db.executescript(CREATE_SCHEMA)
            await db.commit()

        self._initialized = True
        logger.info("SQLiteVault initialisé: %s", self._db_path)

    def _ensure_initialized(self) -> None:
        if not self._initialized:
            raise RuntimeError(
                "SQLiteVault non initialisé. Appelez await initialize() d'abord."
            )

    # ─── Mémoires ─────────────────────────────────────────────────────────────

    async def save_memory(
        self,
        agent_id: str,
        memory_type: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> int:
        """
        Sauvegarde une mémoire pour un agent.

        Returns:
            ID de la mémoire créée.
        """
        self._ensure_initialized()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                """
                INSERT INTO agent_memories (agent_id, memory_type, content, metadata)
                VALUES (?, ?, ?, ?)
                """,
                (
                    agent_id,
                    memory_type,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_memories(
        self,
        agent_id: str,
        memory_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Récupère les mémoires récentes d'un agent.

        Args:
            agent_id: ID de l'agent
            memory_type: Filtre optionnel par type ('session', 'manual', etc.)
            limit: Nombre maximum de mémoires

        Returns:
            Liste de mémoires (plus récentes en premier).
        """
        self._ensure_initialized()
        query = """
            SELECT id, agent_id, memory_type, content, metadata, created_at
            FROM agent_memories
            WHERE agent_id = ?
        """
        params: list[Any] = [agent_id]

        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)

        query += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "agent_id": row["agent_id"],
                    "memory_type": row["memory_type"],
                    "content": row["content"],
                    "metadata": json.loads(row["metadata"] or "{}"),
                    "created_at": row["created_at"],
                }
                for row in rows
            ]

    async def clear_memories(
        self,
        agent_id: str,
        memory_type: str | None = None,
    ) -> int:
        """
        Efface les mémoires d'un agent.

        Returns:
            Nombre de mémoires supprimées.
        """
        self._ensure_initialized()
        query = "DELETE FROM agent_memories WHERE agent_id = ?"
        params: list[Any] = [agent_id]

        if memory_type:
            query += " AND memory_type = ?"
            params.append(memory_type)

        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(query, params)
            await db.commit()
            return cursor.rowcount

    # ─── Journal d'événements ─────────────────────────────────────────────────

    async def log_event(
        self,
        channel: str,
        event_type: str,
        agent_id: str | None = None,
        data: dict[str, Any] | None = None,
        timestamp: str | None = None,
    ) -> int:
        """
        Enregistre un événement dans le journal.

        Returns:
            ID de l'événement créé.
        """
        self._ensure_initialized()
        ts = timestamp or datetime.now(timezone.utc).isoformat()

        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                """
                INSERT INTO event_log (timestamp, channel, event_type, agent_id, data)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    ts,
                    channel,
                    event_type,
                    agent_id,
                    json.dumps(data or {}, ensure_ascii=False),
                ),
            )
            await db.commit()
            return cursor.lastrowid

    async def get_events(
        self,
        agent_id: str | None = None,
        channel: str | None = None,
        event_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """
        Récupère les événements récents du journal.

        Args:
            agent_id: Filtre par agent (optionnel)
            channel: Filtre par canal Redis (optionnel)
            event_type: Filtre par type d'événement (optionnel)
            limit: Nombre max d'événements

        Returns:
            Liste d'événements (plus récents en premier).
        """
        self._ensure_initialized()
        query = "SELECT id, timestamp, channel, event_type, agent_id, data FROM event_log WHERE 1=1"
        params: list[Any] = []

        if agent_id:
            query += " AND agent_id = ?"
            params.append(agent_id)
        if channel:
            query += " AND channel LIKE ?"
            params.append(f"{channel}%")
        if event_type:
            query += " AND event_type = ?"
            params.append(event_type)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query, params)
            rows = await cursor.fetchall()
            return [
                {
                    "id": row["id"],
                    "timestamp": row["timestamp"],
                    "channel": row["channel"],
                    "event_type": row["event_type"],
                    "agent_id": row["agent_id"],
                    "data": json.loads(row["data"] or "{}"),
                }
                for row in rows
            ]

    # ─── Tâches planifiées ────────────────────────────────────────────────────

    async def save_scheduled_task(
        self,
        task_id: str,
        name: str,
        agent_id: str,
        task: str,
        trigger: str,
        config: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> None:
        """Sauvegarde ou met à jour une tâche planifiée."""
        self._ensure_initialized()
        async with aiosqlite.connect(str(self._db_path)) as db:
            await db.execute(
                """
                INSERT OR REPLACE INTO scheduled_tasks
                    (task_id, name, agent_id, task, trigger, config, enabled)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    task_id,
                    name,
                    agent_id,
                    task,
                    trigger,
                    json.dumps(config or {}, ensure_ascii=False),
                    1 if enabled else 0,
                ),
            )
            await db.commit()

    async def get_scheduled_tasks(
        self,
        enabled_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Récupère toutes les tâches planifiées."""
        self._ensure_initialized()
        query = "SELECT * FROM scheduled_tasks"
        if enabled_only:
            query += " WHERE enabled = 1"

        async with aiosqlite.connect(str(self._db_path)) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(query)
            rows = await cursor.fetchall()
            return [
                {
                    **dict(row),
                    "config": json.loads(row["config"] or "{}"),
                    "enabled": bool(row["enabled"]),
                }
                for row in rows
            ]

    # ─── Sessions ─────────────────────────────────────────────────────────────

    async def save_session(
        self,
        agent_id: str,
        task: str,
        output: str,
        success: bool = True,
        execution_ms: float = 0.0,
        model_tier: str = "NANO",
        tokens_used: int = 0,
    ) -> int:
        """Enregistre une session complète d'agent."""
        self._ensure_initialized()
        async with aiosqlite.connect(str(self._db_path)) as db:
            cursor = await db.execute(
                """
                INSERT INTO agent_sessions
                    (agent_id, task, output, success, execution_ms, model_tier, tokens_used, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
                """,
                (
                    agent_id,
                    task,
                    output,
                    1 if success else 0,
                    execution_ms,
                    model_tier,
                    tokens_used,
                ),
            )
            await db.commit()
            return cursor.lastrowid


# ─── Singleton ────────────────────────────────────────────────────────────────

_vault_instance: SQLiteVault | None = None


async def get_vault() -> SQLiteVault:
    """Retourne l'instance singleton du vault (initialisée)."""
    global _vault_instance
    if _vault_instance is None:
        _vault_instance = SQLiteVault()
    if not _vault_instance._initialized:
        await _vault_instance.initialize()
    return _vault_instance
