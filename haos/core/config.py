"""
Configuration centrale HAOS.
Charge toutes les variables depuis .env via pydantic-settings.
"""

from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class HAOSConfig(BaseSettings):
    """Configuration complète du système HAOS."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ─── Chemins principaux ───────────────────────────────────────────────────
    haos_root: Path = Field(
        default=Path("/Users/hemko/HAOS_Multimachines/haos"),
        description="Répertoire racine du projet HAOS",
    )
    models_dir: Path = Field(
        default=Path("/Users/hemko/Models"),
        description="Répertoire contenant les fichiers .gguf",
    )
    vault_db_path: Path = Field(
        default=Path("/Users/hemko/HAOS_Multimachines/haos/data/vault.db"),
        description="Chemin vers la base SQLite (vault)",
    )

    # ─── Redis ────────────────────────────────────────────────────────────────
    redis_url: str = Field(
        default="redis://localhost:6379",
        description="URL de connexion Redis",
    )

    # ─── Modèles APEX ─────────────────────────────────────────────────────────
    apex_port: int = Field(default=8081)
    apex_model_file: str = Field(
        default="Qwen3.6-35B-A3B-APEX-Balanced.gguf",
        description="Nom du fichier modèle APEX",
    )

    # ─── Modèles QWYTHOS ──────────────────────────────────────────────────────
    qwythos_port: int = Field(default=8082)
    qwythos_model_file: str = Field(
        default="Qwythos-9B-Claude-Mythos-5-1M-MTP-BF16.gguf",
        description="Nom du fichier modèle QWYTHOS",
    )

    # ─── Modèles NANO ─────────────────────────────────────────────────────────
    nano_port: int = Field(default=8083)
    nano_model_file: str = Field(
        default="mythos-nano-f16.gguf",
        description="Nom du fichier modèle NANO",
    )

    # ─── API ──────────────────────────────────────────────────────────────────
    api_port: int = Field(default=8000)
    api_secret_key: str = Field(
        default="changeme_generate_a_secure_key",
        description="Clé secrète pour sécuriser l'API",
    )

    # ─── Réseau Tailscale ─────────────────────────────────────────────────────
    tailscale_ip: str = Field(
        default="100.64.0.1",
        description="IP Tailscale du Mac (cerveau principal)",
    )
    tailscale_notify_url: str = Field(
        default="http://100.64.0.2:9090/notify",
        description="URL de notification Tailscale (iPhone/iPad)",
    )

    # ─── Logging ──────────────────────────────────────────────────────────────
    log_level: str = Field(default="INFO")

    # ─── Propriétés calculées ─────────────────────────────────────────────────

    @computed_field
    @property
    def apex_base_url(self) -> str:
        return f"http://localhost:{self.apex_port}/v1"

    @computed_field
    @property
    def qwythos_base_url(self) -> str:
        return f"http://localhost:{self.qwythos_port}/v1"

    @computed_field
    @property
    def nano_base_url(self) -> str:
        return f"http://localhost:{self.nano_port}/v1"

    @computed_field
    @property
    def apex_model_path(self) -> Path:
        return self.models_dir / self.apex_model_file

    @computed_field
    @property
    def qwythos_model_path(self) -> Path:
        return self.models_dir / self.qwythos_model_file

    @computed_field
    @property
    def nano_model_path(self) -> Path:
        return self.models_dir / self.nano_model_file

    @computed_field
    @property
    def identities_dir(self) -> Path:
        return self.haos_root / "identities"

    @computed_field
    @property
    def data_dir(self) -> Path:
        return self.haos_root / "data"


@lru_cache(maxsize=1)
def get_config() -> HAOSConfig:
    """Retourne l'instance singleton de la configuration (mise en cache)."""
    return HAOSConfig()


# Instance globale accessible directement
settings = get_config()
