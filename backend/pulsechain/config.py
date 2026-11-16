"""Runtime configuration, read from environment variables (see .env.example)."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Minimal .env loader (avoids a python-dotenv dependency). Real environment variables win."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def _bool(name: str, default: bool = False) -> bool:
    return os.environ.get(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Settings:
    host: str
    port: int
    db_path: Path
    model_dir: Path
    seed: int
    alert_risk_threshold: str
    sms_mode: str
    sms_allow_live: bool
    twilio_account_sid: str
    twilio_auth_token: str
    twilio_from_number: str
    open_meteo_enabled: bool
    open_meteo_timeout: float
    frontend_dir: Path

    @property
    def twilio_configured(self) -> bool:
        return bool(self.twilio_account_sid and self.twilio_auth_token and self.twilio_from_number)

    @property
    def live_sms_possible(self) -> bool:
        return self.sms_mode == "live" and self.sms_allow_live and self.twilio_configured


def _resolve(path_str: str) -> Path:
    p = Path(path_str)
    return p if p.is_absolute() else REPO_ROOT / p


def load_settings() -> Settings:
    _load_dotenv(REPO_ROOT / ".env")
    threshold = os.environ.get("ALERT_RISK_THRESHOLD", "high").strip().lower()
    if threshold not in {"moderate", "high", "critical"}:
        threshold = "high"
    mode = os.environ.get("SMS_MODE", "simulated").strip().lower()
    if mode not in {"simulated", "live"}:
        mode = "simulated"
    return Settings(
        host=os.environ.get("PULSECHAIN_HOST", "127.0.0.1"),
        port=int(os.environ.get("PULSECHAIN_PORT", "8000")),
        db_path=_resolve(os.environ.get("PULSECHAIN_DB_PATH", "data/pulsechain.db")),
        model_dir=_resolve(os.environ.get("PULSECHAIN_MODEL_DIR", "data/models")),
        seed=int(os.environ.get("PULSECHAIN_SEED", "42")),
        alert_risk_threshold=threshold,
        sms_mode=mode,
        sms_allow_live=_bool("SMS_ALLOW_LIVE", False),
        twilio_account_sid=os.environ.get("TWILIO_ACCOUNT_SID", ""),
        twilio_auth_token=os.environ.get("TWILIO_AUTH_TOKEN", ""),
        twilio_from_number=os.environ.get("TWILIO_FROM_NUMBER", ""),
        open_meteo_enabled=_bool("OPEN_METEO_ENABLED", False),
        open_meteo_timeout=float(os.environ.get("OPEN_METEO_TIMEOUT_SECONDS", "6")),
        frontend_dir=REPO_ROOT / "frontend",
    )
