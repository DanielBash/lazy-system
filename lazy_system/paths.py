from pathlib import Path

ETC = Path("/etc/lazy-system")
VAR = Path("/var/lib/lazy-system")

GLOBAL_CONFIG = ETC / "config.json"
APPS_DIR = ETC / "apps"
STATE_DIR = VAR / "apps"
SYSTEMD_DIR = Path("/etc/systemd/system")


def app_dir(name: str) -> Path:
    return APPS_DIR / name


def app_state_dir(name: str) -> Path:
    return STATE_DIR / name


def app_config_path(name: str) -> Path:
    return app_dir(name) / "app.json"


def app_script(name: str, kind: str) -> Path:
    cfg_dir = app_dir(name)
    for ext in ("sh", "fish"):
        p = cfg_dir / f"{kind}.{ext}"
        if p.exists():
            return p
    return cfg_dir / f"{kind}.sh"


def history_path(name: str) -> Path:
    return app_state_dir(name) / "history.jsonl"


def metrics_path(name: str) -> Path:
    return app_state_dir(name) / "metrics.jsonl"


def log_path(name: str) -> Path:
    return app_state_dir(name) / "app.log"
