"""App CRUD + systemd unit generation."""
import json
import os
import re
import secrets
import shutil
import subprocess

from . import config, history, schedule
from .paths import (
    APPS_DIR, SYSTEMD_DIR,
    app_dir, app_state_dir, app_config_path, log_path,
)

NAME_RE = re.compile(r"^[a-z][a-z0-9_-]{0,40}$")


def _validate_name(name: str) -> None:
    if not NAME_RE.match(name):
        raise ValueError(
            "app name must be lowercase, start with a letter, "
            "and use only [a-z0-9_-], max 41 chars"
        )


def list_apps() -> list[str]:
    if not APPS_DIR.exists():
        return []
    return sorted(p.name for p in APPS_DIR.iterdir() if p.is_dir())


def exists(name: str) -> bool:
    return app_config_path(name).exists()


def load(name: str) -> dict:
    return json.loads(app_config_path(name).read_text())


def save(name: str, cfg: dict) -> None:
    app_dir(name).mkdir(parents=True, exist_ok=True)
    app_config_path(name).write_text(json.dumps(cfg, indent=2))


def _shebang(shell: str) -> str:
    return "#!/usr/bin/env fish\n" if shell == "fish" else "#!/usr/bin/env bash\nset -euo pipefail\n"


def create(name: str, shell: str | None = None, description: str = "") -> dict:
    _validate_name(name)
    if exists(name):
        raise ValueError(f"app '{name}' already exists")

    g = config.load()
    chosen = config.detect_shell(shell or g.get("shell", "auto"))
    ext = "fish" if chosen == "fish" else "sh"

    d = app_dir(name)
    d.mkdir(parents=True, exist_ok=True)
    app_state_dir(name).mkdir(parents=True, exist_ok=True)

    for kind, body in (
        ("run", "# launch the main process in the foreground; systemd handles backgrounding\n"
                "echo \"running %s\"\n" % name),
        ("stop", "# called before update; default is a no-op (systemd stops the unit)\n"),
        ("update", "# pull/build/etc. before the next run\n"),
    ):
        f = d / f"{kind}.{ext}"
        f.write_text(_shebang(chosen) + body)
        f.chmod(0o755)

    cfg = {
        "name": name,
        "description": description,
        "shell": chosen,
        "token": secrets.token_urlsafe(16),
        "schedules": [],
        "webhook_enabled": True,
        "env": {},
        "working_dir": "",
        "limits": {},
        "created": _now(),
    }
    save(name, cfg)
    _write_units(name, cfg)
    history.record(name, "created")
    return cfg


def remove(name: str) -> None:
    if not exists(name):
        return
    try:
        subprocess.run(["systemctl", "stop", f"lazy-{name}.service"], check=False)
        subprocess.run(["systemctl", "disable", f"lazy-{name}.service"], check=False)
    except Exception:
        pass
    cfg = load(name)
    for sched in cfg.get("schedules", []):
        unit = f"lazy-{name}-update-{sched['id']}"
        subprocess.run(["systemctl", "stop", f"{unit}.timer"], check=False)
        subprocess.run(["systemctl", "disable", f"{unit}.timer"], check=False)
    for unit_glob in (f"lazy-{name}.service", f"lazy-{name}-update.service",
                      f"lazy-{name}-update-*.timer"):
        for p in SYSTEMD_DIR.glob(unit_glob):
            p.unlink(missing_ok=True)
    subprocess.run(["systemctl", "daemon-reload"], check=False)
    shutil.rmtree(app_dir(name), ignore_errors=True)
    shutil.rmtree(app_state_dir(name), ignore_errors=True)


# ---------- systemd units ----------

def _runner_cmd(name: str, kind: str) -> str:
    """Resolve script path at unit-generation time."""
    cfg_dir = app_dir(name)
    for ext in ("sh", "fish"):
        p = cfg_dir / f"{kind}.{ext}"
        if p.exists():
            interp = "/usr/bin/fish" if ext == "fish" else "/usr/bin/env bash"
            return f"{interp} {p}"
    return f"/usr/bin/env bash {cfg_dir}/{kind}.sh"


def _write_units(name: str, cfg: dict) -> None:
    g = config.load()
    save_logs = g.get("save_logs", True)
    log_target = (
        f"append:{log_path(name)}" if save_logs else "journal"
    )
    SYSTEMD_DIR.mkdir(parents=True, exist_ok=True)

    # main service: launches the run script (it should exec the main process)
    svc = SYSTEMD_DIR / f"lazy-{name}.service"
    env_lines = "\n".join(f"Environment={k}={_escape_env(v)}" for k, v in cfg.get("env", {}).items())
    wd = cfg.get("working_dir", "") or ""
    wd_line = f"WorkingDirectory={wd}\n" if wd else ""
    limits = cfg.get("limits", {}) or {}
    limit_lines = "\n".join(filter(None, [
        f"CPUQuota={limits['cpu_quota']}" if limits.get("cpu_quota") else "",
        f"MemoryMax={limits['memory_max']}" if limits.get("memory_max") else "",
        f"TasksMax={limits['tasks_max']}" if limits.get("tasks_max") else "",
        f"IOWeight={limits['io_weight']}" if limits.get("io_weight") else "",
    ]))
    svc.write_text(f"""[Unit]
Description=lazy-system app {name}
After=network-online.target

[Service]
Type=simple
{wd_line}{env_lines}
{limit_lines}
ExecStart={_runner_cmd(name, 'run')}
ExecStop={_runner_cmd(name, 'stop')}
Restart=on-failure
RestartSec=2
StandardOutput={log_target}
StandardError={log_target}
SyslogIdentifier=lazy-{name}

[Install]
WantedBy=multi-user.target
""")

    # update oneshot: stop -> update -> start (chained via ExecStartPre)
    upd = SYSTEMD_DIR / f"lazy-{name}-update.service"
    upd.write_text(f"""[Unit]
Description=lazy-system update for {name}

[Service]
Type=oneshot
ExecStart=/usr/bin/python3 -m lazy_system.cli _run-update {name}
StandardOutput={log_target}
StandardError={log_target}
SyslogIdentifier=lazy-{name}-update
""")

    # remove old timers and rewrite
    for old in SYSTEMD_DIR.glob(f"lazy-{name}-update-*.timer"):
        old.unlink(missing_ok=True)

    for sched in cfg.get("schedules", []):
        on_calendar = schedule.resolve(sched["spec"])
        sid = sched["id"]
        # Use templated naming (without instance semantics — just unique filenames)
        timer = SYSTEMD_DIR / f"lazy-{name}-update-{sid}.timer"
        timer.write_text(f"""[Unit]
Description=lazy-system schedule {sid} for {name}

[Timer]
OnCalendar={on_calendar}
Persistent=true
Unit=lazy-{name}-update.service

[Install]
WantedBy=timers.target
""")

    subprocess.run(["systemctl", "daemon-reload"], check=False)


def regenerate_all_units() -> None:
    for name in list_apps():
        _write_units(name, load(name))


# ---------- lifecycle ----------

def start(name: str) -> None:
    subprocess.run(["systemctl", "start", f"lazy-{name}.service"], check=True)
    history.record(name, "start")


def stop(name: str) -> None:
    subprocess.run(["systemctl", "stop", f"lazy-{name}.service"], check=False)
    history.record(name, "stop")


def restart(name: str) -> None:
    subprocess.run(["systemctl", "restart", f"lazy-{name}.service"], check=True)
    history.record(name, "restart")


def enable(name: str) -> None:
    subprocess.run(["systemctl", "enable", f"lazy-{name}.service"], check=False)
    cfg = load(name)
    for sched in cfg.get("schedules", []):
        subprocess.run(
            ["systemctl", "enable", "--now", f"lazy-{name}-update-{sched['id']}.timer"],
            check=False,
        )


def is_active(name: str) -> bool:
    r = subprocess.run(
        ["systemctl", "is-active", f"lazy-{name}.service"],
        capture_output=True, text=True,
    )
    return r.stdout.strip() == "active"


def status_text(name: str) -> str:
    r = subprocess.run(
        ["systemctl", "status", f"lazy-{name}.service", "--no-pager"],
        capture_output=True, text=True,
    )
    return r.stdout


def run_update(name: str) -> int:
    """Stop -> update -> start. Returns 0 on success."""
    history.record(name, "update_begin")
    if is_active(name):
        subprocess.run(["systemctl", "stop", f"lazy-{name}.service"], check=False)
    rc = subprocess.run(_runner_cmd(name, "update"), shell=True, check=False).returncode
    if rc != 0:
        history.record(name, "update_failed", detail=f"rc={rc}")
        # try to bring it back up anyway
        subprocess.run(["systemctl", "start", f"lazy-{name}.service"], check=False)
        return rc
    history.record(name, "update_ok")
    subprocess.run(["systemctl", "start", f"lazy-{name}.service"], check=False)
    return 0


# ---------- schedules ----------

def add_schedule(name: str, spec: str) -> dict:
    cfg = load(name)
    sid = secrets.token_hex(3)
    sched = {"id": sid, "spec": spec, "oncalendar": schedule.resolve(spec)}
    cfg.setdefault("schedules", []).append(sched)
    save(name, cfg)
    _write_units(name, cfg)
    subprocess.run(
        ["systemctl", "enable", "--now", f"lazy-{name}-update-{sid}.timer"],
        check=False,
    )
    history.record(name, "schedule_added", detail=spec)
    return sched


def remove_schedule(name: str, sid: str) -> None:
    cfg = load(name)
    cfg["schedules"] = [s for s in cfg.get("schedules", []) if s["id"] != sid]
    subprocess.run(["systemctl", "disable", "--now", f"lazy-{name}-update-{sid}.timer"], check=False)
    (SYSTEMD_DIR / f"lazy-{name}-update-{sid}.timer").unlink(missing_ok=True)
    save(name, cfg)
    _write_units(name, cfg)
    history.record(name, "schedule_removed", detail=sid)


# ---------- helpers ----------

def _now() -> float:
    import time
    return time.time()


def _escape_env(v: str) -> str:
    s = str(v).replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ")
    return f'"{s}"' if any(c in s for c in " \t\"#") else s


def set_env(name: str, key: str, value: str | None) -> None:
    cfg = load(name)
    env = cfg.setdefault("env", {})
    if value is None:
        env.pop(key, None)
    else:
        env[key] = value
    save(name, cfg)
    _write_units(name, cfg)
    if is_active(name):
        restart(name)
    history.record(name, "env_changed", detail=key)


VALID_LIMITS = ("cpu_quota", "memory_max", "tasks_max", "io_weight")


def set_limits(name: str, **kwargs) -> dict:
    """Set or clear per-app systemd resource limits. Pass value=None to clear."""
    cfg = load(name)
    limits = cfg.setdefault("limits", {})
    for k, v in kwargs.items():
        if k not in VALID_LIMITS:
            raise ValueError(f"unknown limit: {k}")
        if v is None or v == "":
            limits.pop(k, None)
        else:
            limits[k] = str(v)
    save(name, cfg)
    _write_units(name, cfg)
    if is_active(name):
        restart(name)
    history.record(name, "limits_changed")
    return limits


def export_app(name: str, target_dir: str) -> str:
    """Tar an app's config + scripts. Returns archive path."""
    import tarfile
    import time
    out = f"{target_dir.rstrip('/')}/lazy-{name}-{int(time.time())}.tar.gz"
    with tarfile.open(out, "w:gz") as tf:
        tf.add(app_dir(name), arcname=name)
    return out


def import_app(archive_path: str) -> str:
    import tarfile
    with tarfile.open(archive_path, "r:gz") as tf:
        names = tf.getnames()
        if not names:
            raise ValueError("empty archive")
        root = names[0].split("/", 1)[0]
        if exists(root):
            raise ValueError(f"app '{root}' already exists; remove it first")
        APPS_DIR.mkdir(parents=True, exist_ok=True)
        tf.extractall(APPS_DIR)
    cfg = load(root)
    app_state_dir(root).mkdir(parents=True, exist_ok=True)
    _write_units(root, cfg)
    history.record(root, "imported")
    return root


def doctor() -> list[tuple[str, str, str]]:
    """Return [(level, app, message)] for any issues found."""
    out: list[tuple[str, str, str]] = []
    for name in list_apps():
        cfg = load(name)
        for kind in ("run", "stop", "update"):
            ext = "fish" if cfg.get("shell") == "fish" else "sh"
            p = app_dir(name) / f"{kind}.{ext}"
            if not p.exists():
                out.append(("error", name, f"missing {kind}.{ext}"))
            elif not (p.stat().st_mode & 0o111):
                out.append(("warn", name, f"{kind} script not executable"))
        unit = SYSTEMD_DIR / f"lazy-{name}.service"
        if not unit.exists():
            out.append(("error", name, "service unit not generated; run lazysystem doctor --fix"))
        # restart-storm check
        events = history.read(name, limit=200)
        recent = [e for e in events if e["event"] == "exit"]
        if len(recent) >= 5:
            import time
            window = [e for e in recent if e["t"] > time.time() - 600]
            if len(window) >= 5:
                out.append(("warn", name, f"{len(window)} exits in last 10 min — restart-storm?"))
    return out


def fix_units() -> int:
    n = 0
    for name in list_apps():
        _write_units(name, load(name))
        n += 1
    return n


def edit_script(name: str, kind: str) -> None:
    if kind not in ("run", "stop", "update"):
        raise ValueError("kind must be run|stop|update")
    cfg = load(name)
    ext = "fish" if cfg.get("shell") == "fish" else "sh"
    target = app_dir(name) / f"{kind}.{ext}"
    if not target.exists():
        target.write_text(_shebang(cfg.get("shell", "bash")))
        target.chmod(0o755)
    editor = os.environ.get("EDITOR", "nano")
    subprocess.run([editor, str(target)])
