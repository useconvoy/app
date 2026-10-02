from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path


def _bool(name: str, default: bool = False) -> bool:
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class Settings:
    execution_signing_keys_file: str | None = field(
        default_factory=lambda: os.environ.get("CONVOY_EXECUTION_SIGNING_KEYS_FILE"), repr=False
    )
    execution_secret: str | None = field(
        default_factory=lambda: os.environ.get("CONVOY_EXECUTION_SECRET"), repr=False
    )
    planner_execution_secret: str | None = field(
        default_factory=lambda: os.environ.get("CONVOY_PLANNER_EXECUTION_SECRET"), repr=False
    )
    data_dir: Path = field(default_factory=lambda: Path(os.environ.get("CONVOY_DATA_DIR", "./data")))
    database_url: str | None = field(default_factory=lambda: os.environ.get("DATABASE_URL"))
    simulator: bool = field(default_factory=lambda: _bool("CONVOY_SIMULATOR", False))
    scheduler_inprocess: bool = field(default_factory=lambda: _bool("CONVOY_SCHEDULER_INPROCESS", False))
    scheduler_interval_s: float = field(
        default_factory=lambda: float(os.environ.get("CONVOY_SCHEDULER_INTERVAL_S", "2"))
    )
    offline_after_s: int = field(default_factory=lambda: int(os.environ.get("CONVOY_OFFLINE_AFTER_S", "90")))
    heartbeat_interval_s: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_HEARTBEAT_INTERVAL_S", "15"))
    )
    session_ttl_s: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_SESSION_TTL_S", str(14 * 86400)))
    )
    enrollment_ttl_s: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_ENROLLMENT_TTL_S", "3600"))
    )
    public_url: str = field(
        default_factory=lambda: os.environ.get("CONVOY_PUBLIC_URL", "http://localhost:8080")
    )
    secure_cookies: bool = field(default_factory=lambda: _bool("CONVOY_SECURE_COOKIES", False))
    bootstrap_admin_email: str | None = field(default_factory=lambda: os.environ.get("CONVOY_ADMIN_EMAIL"))
    bootstrap_admin_password: str | None = field(
        default_factory=lambda: os.environ.get("CONVOY_ADMIN_PASSWORD")
    )
    hf_endpoint: str = field(default_factory=lambda: os.environ.get("HF_ENDPOINT", "https://huggingface.co"))
    web_dist: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["CONVOY_WEB_DIST"]) if os.environ.get("CONVOY_WEB_DIST") else None
        )
    )
    seed_simulator: bool = field(default_factory=lambda: _bool("CONVOY_SEED_SIMULATOR", False))
    log_level: str = field(default_factory=lambda: os.environ.get("CONVOY_LOG_LEVEL", "INFO"))
    sqlite_wal: bool = field(default_factory=lambda: _bool("CONVOY_SQLITE_WAL", False))
    grant_ttl_s: int = field(default_factory=lambda: int(os.environ.get("CONVOY_GRANT_TTL_S", "30")))
    max_upload_bytes: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_MAX_UPLOAD_BYTES", str(2 * 1024**3)))
    )
    artifact_quota_bytes: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_ARTIFACT_QUOTA_BYTES", str(20 * 1024**3)))
    )
    robot_asset_quota_bytes: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_ROBOT_ASSET_QUOTA_BYTES", str(1024**3)))
    )
    login_throttle: int = field(default_factory=lambda: int(os.environ.get("CONVOY_LOGIN_THROTTLE", "10")))
    enroll_throttle: int = field(default_factory=lambda: int(os.environ.get("CONVOY_ENROLL_THROTTLE", "30")))
    backup_dir: Path | None = field(
        default_factory=lambda: (
            Path(os.environ["CONVOY_BACKUP_DIR"]) if os.environ.get("CONVOY_BACKUP_DIR") else None
        )
    )
    retention_detail_days: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_RETENTION_DETAIL_DAYS", "7"))
    )
    retention_aggregate_days: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_RETENTION_AGGREGATE_DAYS", "30"))
    )
    retention_audit_days: int = field(
        default_factory=lambda: int(os.environ.get("CONVOY_RETENTION_AUDIT_DAYS", "365"))
    )
    maintenance_window: str | None = field(
        default_factory=lambda: os.environ.get("CONVOY_MAINTENANCE_WINDOW")
    )

    @property
    def db_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'convoy.db').as_posix()}"

    @property
    def artifacts_dir(self) -> Path:
        return self.data_dir / "artifacts"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def set_settings(s: Settings) -> None:
    global _settings
    _settings = s
