import json
import shutil
from .paths import GLOBAL_CONFIG, ETC

DEFAULTS = {
    "webhook_port": 8765,
    "save_logs": True,
    "shell": "auto",
    "metrics_interval_seconds": 5,
    "metrics_retention_points": 4320,
    "retention_days": 3,
    "log_max_bytes": 10 * 1024 * 1024,
    "web_user": "admin",
    "web_password": None,
    "web_bind": "0.0.0.0",
}


def load() -> dict:
    if not GLOBAL_CONFIG.exists():
        ETC.mkdir(parents=True, exist_ok=True)
        save(DEFAULTS.copy())
    data = json.loads(GLOBAL_CONFIG.read_text())
    return {**DEFAULTS, **data}


def save(cfg: dict) -> None:
    ETC.mkdir(parents=True, exist_ok=True)
    GLOBAL_CONFIG.write_text(json.dumps(cfg, indent=2))


def detect_shell(pref: str = "auto") -> str:
    if pref in ("bash", "fish"):
        return pref
    if shutil.which("fish"):
        return "fish"
    return "bash"
