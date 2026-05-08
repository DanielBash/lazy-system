"""Resource monitor daemon. Polls each app's main pid via systemd, samples cpu/mem."""
import json
import subprocess
import time

import psutil

from . import apps, config, history
from .paths import metrics_path, app_state_dir, history_path, log_path


def _main_pid(name: str) -> int | None:
    r = subprocess.run(
        ["systemctl", "show", "-p", "MainPID", "--value", f"lazy-{name}.service"],
        capture_output=True, text=True,
    )
    try:
        pid = int(r.stdout.strip())
        return pid if pid > 0 else None
    except ValueError:
        return None


def _sample(name: str) -> dict:
    pid = _main_pid(name)
    cpu, rss = 0.0, 0
    active = pid is not None
    if pid:
        try:
            p = psutil.Process(pid)
            with p.oneshot():
                # include children for shell-launched apps
                procs = [p] + p.children(recursive=True)
                cpu = sum(pp.cpu_percent(interval=None) for pp in procs)
                rss = sum(pp.memory_info().rss for pp in procs)
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            active = False
    return {"t": time.time(), "active": active, "cpu": round(cpu, 2), "rss": rss}


def _trim(name: str, max_points: int) -> None:
    p = metrics_path(name)
    if not p.exists():
        return
    lines = p.read_text().splitlines()
    if len(lines) > max_points:
        p.write_text("\n".join(lines[-max_points:]) + "\n")


def _trim_jsonl_by_age(path, max_age_s: float) -> None:
    if not path.exists():
        return
    cutoff = time.time() - max_age_s
    try:
        lines = path.read_text().splitlines()
    except OSError:
        return
    keep: list[str] = []
    for line in lines:
        try:
            t = json.loads(line).get("t", 0)
        except Exception:
            continue
        if t >= cutoff:
            keep.append(line)
    if len(keep) != len(lines):
        path.write_text(("\n".join(keep) + "\n") if keep else "")


def _trim_log_by_size(path, max_bytes: int) -> None:
    if not path.exists() or max_bytes <= 0:
        return
    try:
        size = path.stat().st_size
    except OSError:
        return
    if size <= max_bytes:
        return
    try:
        with path.open("rb") as f:
            f.seek(size - max_bytes)
            tail = f.read()
        nl = tail.find(b"\n")
        if 0 <= nl < len(tail) - 1:
            tail = tail[nl + 1:]
        path.write_bytes(tail)
    except OSError:
        pass


def loop() -> None:
    # prime cpu_percent baselines
    for name in apps.list_apps():
        pid = _main_pid(name)
        if pid:
            try:
                psutil.Process(pid).cpu_percent(interval=None)
            except Exception:
                pass

    last_active: dict[str, bool] = {}
    last_age_trim = 0.0

    while True:
        cfg = config.load()
        interval = max(1, int(cfg.get("metrics_interval_seconds", 10)))
        retention = int(cfg.get("metrics_retention_points", 4320))
        retention_days = max(0, int(cfg.get("retention_days", 3)))
        log_max = int(cfg.get("log_max_bytes", 10 * 1024 * 1024))
        max_age_s = retention_days * 86400

        for name in apps.list_apps():
            try:
                s = _sample(name)
                app_state_dir(name).mkdir(parents=True, exist_ok=True)
                with metrics_path(name).open("a") as f:
                    f.write(json.dumps(s) + "\n")
                _trim(name, retention)

                prev = last_active.get(name)
                if prev is True and not s["active"]:
                    history.record(name, "exit")
                elif prev is False and s["active"]:
                    history.record(name, "up")
                last_active[name] = s["active"]
            except Exception as e:
                history.record(name, "monitor_error", detail=str(e))

        # age-based trimming runs at most every 5 minutes
        now = time.time()
        if max_age_s and now - last_age_trim > 300:
            for name in apps.list_apps():
                try:
                    _trim_jsonl_by_age(metrics_path(name), max_age_s)
                    _trim_jsonl_by_age(history_path(name), max_age_s)
                    _trim_log_by_size(log_path(name), log_max)
                except Exception:
                    pass
            last_age_trim = now

        time.sleep(interval)


if __name__ == "__main__":
    loop()
