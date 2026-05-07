"""Curses TUI for lazy-system."""
import curses
import json
import os
import subprocess
import time
from . import apps, auth, config, history, schedule
from .paths import metrics_path

SPARK = " ▁▂▃▄▅▆▇█"
TICK = 1.0  # refresh seconds

KEYMAP = [
    ("↑/↓", "select"),
    ("enter", "details"),
    ("s", "start"),
    ("x", "stop"),
    ("r", "restart"),
    ("u", "update"),
    ("e", "edit"),
    ("E", "env"),
    ("l", "logs"),
    ("t", "schedule"),
    ("w", "webhook"),
    ("n", "new"),
    ("D", "delete"),
    ("/", "filter"),
    ("g", "settings"),
    ("?", "help"),
    ("q", "quit"),
]


# ------------------------- helpers -------------------------

def _need_root_or_die() -> None:
    if os.geteuid() != 0:
        print("lazy-system: TUI must run as root (sudo lazysystem)")
        raise SystemExit(1)


def _read_metrics(name: str, n: int = 200) -> list[dict]:
    p = metrics_path(name)
    if not p.exists():
        return []
    out = []
    for line in p.read_text().splitlines()[-n:]:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _spark(values: list[float], width: int) -> str:
    if not values or width <= 0:
        return " " * width
    vs = values[-width:]
    if len(vs) < width:
        vs = [0.0] * (width - len(vs)) + vs
    hi = max(vs) or 1.0
    out = []
    for v in vs:
        idx = int(v / hi * (len(SPARK) - 1))
        out.append(SPARK[max(0, min(len(SPARK) - 1, idx))])
    return "".join(out)


def _fmt_bytes(b: int) -> str:
    f = float(b)
    for unit in ("B", "K", "M", "G"):
        if f < 1024:
            return f"{f:.0f}{unit}"
        f /= 1024
    return f"{f:.1f}T"


def _safe_addstr(win, y, x, s, attr=0):
    try:
        h, w = win.getmaxyx()
        if y < 0 or y >= h or x >= w:
            return
        win.addnstr(y, x, s, max(0, w - x - 1), attr)
    except curses.error:
        pass


# ------------------------- modal helpers -------------------------

def _prompt(stdscr, label: str, default: str = "", secret: bool = False) -> str | None:
    """Single-line prompt at the bottom. Returns None on Esc."""
    h, w = stdscr.getmaxyx()
    win = curses.newwin(3, max(40, w - 6), h // 2 - 1, 3)
    win.box()
    _safe_addstr(win, 0, 2, f" {label} ", curses.A_BOLD)
    curses.curs_set(1)
    curses.echo(not secret)
    buf = list(default)
    while True:
        win.move(1, 2)
        win.clrtoeol()
        win.box()
        _safe_addstr(win, 0, 2, f" {label} ", curses.A_BOLD)
        shown = ("*" * len(buf)) if secret else "".join(buf)
        _safe_addstr(win, 1, 2, shown[-(win.getmaxyx()[1] - 4):])
        win.refresh()
        c = win.get_wch()
        if c in ("\n", "\r"):
            break
        if c == "\x1b":
            curses.noecho(); curses.curs_set(0)
            return None
        if c in ("\x7f", curses.KEY_BACKSPACE, "\b"):
            if buf:
                buf.pop()
            continue
        if isinstance(c, str) and c.isprintable():
            buf.append(c)
    curses.noecho(); curses.curs_set(0)
    return "".join(buf)


def _menu(stdscr, title: str, options: list[str]) -> int | None:
    h, w = stdscr.getmaxyx()
    width = max(len(title) + 4, max((len(o) for o in options), default=10) + 6)
    height = len(options) + 4
    win = curses.newwin(height, width, max(1, h // 2 - height // 2),
                        max(1, w // 2 - width // 2))
    sel = 0
    while True:
        win.erase(); win.box()
        _safe_addstr(win, 0, 2, f" {title} ", curses.A_BOLD)
        for i, o in enumerate(options):
            attr = curses.A_REVERSE if i == sel else 0
            _safe_addstr(win, 2 + i, 2, f" {o} ".ljust(width - 4), attr)
        win.refresh()
        c = win.getch()
        if c in (curses.KEY_UP, ord("k")): sel = (sel - 1) % len(options)
        elif c in (curses.KEY_DOWN, ord("j")): sel = (sel + 1) % len(options)
        elif c in (10, 13, curses.KEY_ENTER): return sel
        elif c in (27, ord("q")): return None


def _confirm(stdscr, msg: str) -> bool:
    return _menu(stdscr, msg, ["No", "Yes"]) == 1


def _info(stdscr, title: str, text: str) -> None:
    h, w = stdscr.getmaxyx()
    lines = []
    for line in text.splitlines():
        while len(line) > w - 8:
            lines.append(line[: w - 8]); line = line[w - 8:]
        lines.append(line)
    height = min(h - 4, len(lines) + 4)
    width = min(w - 4, max(len(title) + 4, max((len(l) for l in lines), default=20) + 4))
    win = curses.newwin(height, width, max(1, h // 2 - height // 2), max(1, w // 2 - width // 2))
    off = 0
    while True:
        win.erase(); win.box()
        _safe_addstr(win, 0, 2, f" {title} ", curses.A_BOLD)
        view = lines[off: off + height - 4]
        for i, ln in enumerate(view):
            _safe_addstr(win, 1 + i, 2, ln)
        _safe_addstr(win, height - 2, 2, "[↑/↓ scroll · q close]", curses.A_DIM)
        win.refresh()
        c = win.getch()
        if c in (ord("q"), 27, 10, 13): return
        if c == curses.KEY_UP and off > 0: off -= 1
        if c == curses.KEY_DOWN and off + height - 4 < len(lines): off += 1


def _shell_out(stdscr, cmd: list[str]) -> int:
    """Suspend curses, run cmd interactively, resume."""
    curses.def_prog_mode(); curses.endwin()
    try:
        rc = subprocess.run(cmd).returncode
    finally:
        stdscr.refresh(); curses.reset_prog_mode()
    return rc


# ------------------------- views -------------------------

def _draw_header(stdscr, total: int, running: int) -> None:
    _, w = stdscr.getmaxyx()
    cfg = config.load()
    auth_label = "🔒 auth" if cfg.get("web_password") else "⚠ no-password (loopback only)"
    title = f" lazy-system · {running}/{total} running · web :{cfg['webhook_port']} · {auth_label} "
    _safe_addstr(stdscr, 0, 0, title.ljust(w), curses.A_REVERSE)


def _draw_footer(stdscr) -> None:
    h, w = stdscr.getmaxyx()
    keys = "  ".join(f"{k}:{v}" for k, v in KEYMAP)
    _safe_addstr(stdscr, h - 1, 0, keys[: w - 1].ljust(w - 1), curses.A_REVERSE)


def _draw_sidebar(win, names: list[str], sel: int, filter_str: str, statuses: dict[str, bool]) -> None:
    win.erase(); win.box()
    _safe_addstr(win, 0, 2, f" apps ({len(names)}) ", curses.A_BOLD)
    if filter_str:
        _safe_addstr(win, 0, win.getmaxyx()[1] - len(filter_str) - 4, f" /{filter_str} ", curses.A_DIM)
    if not names:
        _safe_addstr(win, 2, 2, "no apps. press 'n' to create.", curses.A_DIM)
        return
    h, w = win.getmaxyx()
    visible = h - 2
    start = max(0, min(sel - visible // 2, len(names) - visible))
    for i, name in enumerate(names[start: start + visible]):
        idx = start + i
        attr = curses.A_REVERSE if idx == sel else 0
        running = statuses.get(name, False)
        dot = "●" if running else "○"
        line = f" {dot} {name} "
        _safe_addstr(win, 1 + i, 1, line.ljust(w - 2), attr)


def _draw_detail(win, name: str | None) -> None:
    win.erase(); win.box()
    if not name:
        _safe_addstr(win, 0, 2, " detail ", curses.A_BOLD)
        _safe_addstr(win, 2, 2, "select an app", curses.A_DIM)
        return
    cfg = apps.load(name)
    running = apps.is_active(name)
    metrics = _read_metrics(name, 600)
    hist = history.read(name, 60)
    h, w = win.getmaxyx()

    state = "● running" if running else "○ stopped"
    _safe_addstr(win, 0, 2, f" {name} · {state} ", curses.A_BOLD)
    desc = cfg.get("description", "") or "(no description)"
    _safe_addstr(win, 1, 2, desc[: w - 4], curses.A_DIM)
    _safe_addstr(win, 2, 2, f"shell: {cfg.get('shell')}   created: {time.strftime('%Y-%m-%d', time.localtime(cfg.get('created', 0)))}")

    # graphs
    spark_w = min(64, w - 18)
    cpu_vals = [m.get("cpu", 0.0) for m in metrics]
    rss_vals = [m.get("rss", 0) / 1_048_576 for m in metrics]
    cur_cpu = cpu_vals[-1] if cpu_vals else 0
    cur_rss = (metrics[-1].get("rss", 0) if metrics else 0)
    _safe_addstr(win, 4, 2, f"CPU {cur_cpu:5.1f}%  ")
    _safe_addstr(win, 4, 16, _spark(cpu_vals, spark_w), curses.color_pair(2))
    _safe_addstr(win, 5, 2, f"RAM {_fmt_bytes(cur_rss):>6}  ")
    _safe_addstr(win, 5, 16, _spark(rss_vals, spark_w), curses.color_pair(3))

    # uptime bar
    bucket = metrics[-(w - 6):] if metrics else []
    bar_y = 7
    _safe_addstr(win, bar_y, 2, "uptime")
    for i, m in enumerate(bucket):
        ch = "▇"
        attr = curses.color_pair(4 if m.get("active") else 5)
        _safe_addstr(win, bar_y + 1, 2 + i, ch, attr)

    # counters
    counts: dict[str, int] = {}
    for ev in hist:
        counts[ev["event"]] = counts.get(ev["event"], 0) + 1
    summary = (f"restarts: {counts.get('restart',0)}  "
               f"failures: {counts.get('exit',0)}  "
               f"updates: {counts.get('update_ok',0)} ok / {counts.get('update_failed',0)} fail  "
               f"webhooks: {counts.get('webhook',0)}")
    _safe_addstr(win, bar_y + 3, 2, summary, curses.A_DIM)

    # schedules + webhook
    g = config.load()
    scheds = cfg.get("schedules", [])
    sched_str = ", ".join(f"{s['spec']}" for s in scheds) or "(none)"
    _safe_addstr(win, bar_y + 5, 2, f"schedules: {sched_str}"[: w - 4])
    if cfg.get("webhook_enabled", True):
        wh = f"http://localhost:{g['webhook_port']}/hook/{name}/{cfg['token']}?action=update"
        _safe_addstr(win, bar_y + 6, 2, f"webhook: {wh}"[: w - 4], curses.A_DIM)

    # env
    env = cfg.get("env", {})
    if env:
        _safe_addstr(win, bar_y + 7, 2, f"env: " + " ".join(f"{k}=…" for k in env)[: w - 8], curses.A_DIM)

    # recent events
    _safe_addstr(win, bar_y + 9, 2, "recent events", curses.A_BOLD)
    for i, ev in enumerate(reversed(hist[-min(8, h - bar_y - 11):])):
        ts = time.strftime("%m-%d %H:%M:%S", time.localtime(ev["t"]))
        attr = curses.color_pair(5) if ev["event"] in ("exit", "update_failed") else 0
        _safe_addstr(win, bar_y + 10 + i, 4, f"{ts}  {ev['event']:<16} {ev.get('detail','')}", attr)


# ------------------------- actions -------------------------

def _action_create(stdscr) -> str | None:
    name = _prompt(stdscr, "new app name (lowercase, [a-z0-9_-])")
    if not name:
        return None
    desc = _prompt(stdscr, "description (optional)") or ""
    shell_idx = _menu(stdscr, "shell", ["auto", "bash", "fish"])
    shell = ["auto", "bash", "fish"][shell_idx] if shell_idx is not None else "auto"
    try:
        apps.create(name, shell=shell, description=desc)
    except Exception as e:
        _info(stdscr, "error", str(e))
        return None
    if _confirm(stdscr, f"edit run script for {name} now?"):
        apps.edit_script(name, "run")
    return name


def _action_edit(stdscr, name: str) -> None:
    idx = _menu(stdscr, "edit which script?", ["run", "stop", "update"])
    if idx is None: return
    apps.edit_script(name, ["run", "stop", "update"][idx])


def _action_logs(stdscr, name: str) -> None:
    _shell_out(stdscr, ["bash", "-lc",
                        f"journalctl -u lazy-{name}.service -n 500 --no-pager | less -R +G"])


def _action_schedule(stdscr, name: str) -> None:
    cfg = apps.load(name)
    options = ["+ add new"] + [f"{s['id']} · {s['spec']}" for s in cfg.get("schedules", [])]
    idx = _menu(stdscr, f"schedules for {name}", options)
    if idx is None: return
    if idx == 0:
        opts = schedule.list_presets() + ["custom OnCalendar..."]
        sub = _menu(stdscr, "preset or custom", opts)
        if sub is None: return
        if sub == len(opts) - 1:
            spec = _prompt(stdscr, "OnCalendar expression")
            if not spec: return
        else:
            spec = opts[sub]
        try:
            apps.add_schedule(name, spec)
        except Exception as e:
            _info(stdscr, "error", str(e))
    else:
        sid = cfg["schedules"][idx - 1]["id"]
        if _confirm(stdscr, f"remove schedule {sid}?"):
            apps.remove_schedule(name, sid)


def _action_env(stdscr, name: str) -> None:
    cfg = apps.load(name)
    while True:
        items = [f"{k}={v}" for k, v in cfg.get("env", {}).items()]
        opts = ["+ add/update"] + items + (["✕ remove all"] if items else []) + ["done"]
        idx = _menu(stdscr, f"env vars · {name}", opts)
        if idx is None or opts[idx] == "done": return
        if idx == 0:
            kv = _prompt(stdscr, "KEY=VALUE")
            if not kv or "=" not in kv:
                continue
            k, v = kv.split("=", 1)
            apps.set_env(name, k.strip(), v)
        elif idx == len(opts) - 2 and items:
            for k in list(cfg.get("env", {})):
                apps.set_env(name, k, None)
        else:
            k = items[idx - 1].split("=", 1)[0]
            if _confirm(stdscr, f"remove {k}?"):
                apps.set_env(name, k, None)
        cfg = apps.load(name)


def _action_webhook(stdscr, name: str) -> None:
    cfg = apps.load(name)
    g = config.load()
    base = f"http://<host>:{g['webhook_port']}/hook/{name}/{cfg['token']}"
    text = "\n".join([
        "Webhook URLs (per-app token, no basic auth required):",
        "",
        f"  update:  {base}?action=update",
        f"  start:   {base}?action=start",
        f"  stop:    {base}?action=stop",
        f"  restart: {base}?action=restart",
        "",
        "GET or POST — both work.",
    ])
    _info(stdscr, f"webhook · {name}", text)


def _action_settings(stdscr) -> None:
    while True:
        cfg = config.load()
        opts = [
            f"web port           [{cfg['webhook_port']}]",
            f"web bind address   [{cfg['web_bind']}]",
            f"web username       [{cfg['web_user']}]",
            f"set web password   [{'set' if cfg.get('web_password') else 'NOT SET'}]",
            f"clear web password",
            f"save logs to file  [{'yes' if cfg['save_logs'] else 'no'}]",
            f"default shell      [{cfg['shell']}]",
            f"metrics interval   [{cfg['metrics_interval_seconds']}s]",
            f"export all apps    →",
            f"import an app      →",
            f"doctor (check + fix)",
            "done",
        ]
        idx = _menu(stdscr, "global settings", opts)
        if idx is None or opts[idx] == "done": return
        new = dict(cfg)
        regenerate = False
        restart_web = False
        if idx == 0:
            v = _prompt(stdscr, "web port", str(cfg["webhook_port"]))
            if v and v.isdigit(): new["webhook_port"] = int(v); restart_web = True
        elif idx == 1:
            v = _prompt(stdscr, "bind address (0.0.0.0 or 127.0.0.1)", cfg["web_bind"])
            if v: new["web_bind"] = v; restart_web = True
        elif idx == 2:
            v = _prompt(stdscr, "username", cfg["web_user"])
            if v: new["web_user"] = v
        elif idx == 3:
            p1 = _prompt(stdscr, "new password", secret=True)
            if not p1: continue
            p2 = _prompt(stdscr, "confirm", secret=True)
            if p1 != p2:
                _info(stdscr, "error", "passwords do not match"); continue
            new["web_password"] = auth.hash_password(p1)
        elif idx == 4:
            if _confirm(stdscr, "really clear web password? web UI will be loopback-only"):
                new["web_password"] = None
        elif idx == 5:
            new["save_logs"] = not cfg["save_logs"]
            regenerate = True
        elif idx == 6:
            sub = _menu(stdscr, "default shell", ["auto", "bash", "fish"])
            if sub is not None: new["shell"] = ["auto", "bash", "fish"][sub]
        elif idx == 7:
            v = _prompt(stdscr, "metrics interval (seconds)", str(cfg["metrics_interval_seconds"]))
            if v and v.isdigit(): new["metrics_interval_seconds"] = max(1, int(v))
        elif idx == 8:
            for n in apps.list_apps():
                p = apps.export_app(n, "/var/lib/lazy-system")
                _info(stdscr, "exported", p)
            continue
        elif idx == 9:
            path = _prompt(stdscr, "path to .tar.gz")
            if not path: continue
            try:
                imported = apps.import_app(path)
                _info(stdscr, "imported", f"app: {imported}")
            except Exception as e:
                _info(stdscr, "error", str(e))
            continue
        elif idx == 10:
            issues = apps.doctor()
            if not issues:
                _info(stdscr, "doctor", "no issues")
            else:
                _info(stdscr, "doctor", "\n".join(f"[{lvl}] {n}: {m}" for lvl, n, m in issues))
            if _confirm(stdscr, "regenerate all unit files?"):
                n = apps.fix_units()
                _info(stdscr, "doctor", f"regenerated {n} unit set(s)")
            continue
        config.save(new)
        if regenerate:
            apps.regenerate_all_units()
        if restart_web:
            subprocess.run(["systemctl", "restart", "lazy-webhook.service"], check=False)


def _help_text() -> str:
    return ("\n".join(f"{k:<8} {v}" for k, v in KEYMAP) +
            "\n\nlazy-system: one app = three scripts (run/stop/update).\n"
            "Updates always run: stop → update → start.\n"
            "Webhook URLs use per-app tokens; web UI uses basic auth.")


# ------------------------- main loop -------------------------

def _main(stdscr) -> None:
    curses.curs_set(0)
    curses.use_default_colors()
    curses.start_color()
    curses.init_pair(1, curses.COLOR_GREEN, -1)
    curses.init_pair(2, curses.COLOR_CYAN, -1)
    curses.init_pair(3, curses.COLOR_YELLOW, -1)
    curses.init_pair(4, curses.COLOR_GREEN, -1)
    curses.init_pair(5, curses.COLOR_RED, -1)
    stdscr.nodelay(True)
    stdscr.timeout(int(TICK * 1000))

    sel = 0
    filter_str = ""
    last_status_check = 0.0
    statuses: dict[str, bool] = {}

    while True:
        all_names = apps.list_apps()
        names = [n for n in all_names if filter_str.lower() in n.lower()]
        if sel >= len(names): sel = max(0, len(names) - 1)

        # rate-limit systemctl calls
        if time.time() - last_status_check > 2:
            statuses = {n: apps.is_active(n) for n in all_names}
            last_status_check = time.time()

        h, w = stdscr.getmaxyx()
        stdscr.erase()
        if h < 12 or w < 60:
            _safe_addstr(stdscr, 0, 0, "terminal too small")
            stdscr.refresh()
        else:
            running = sum(1 for n in all_names if statuses.get(n))
            _draw_header(stdscr, len(all_names), running)
            sb_w = min(28, w // 4)
            sidebar = stdscr.derwin(h - 2, sb_w, 1, 0)
            detail  = stdscr.derwin(h - 2, w - sb_w, 1, sb_w)
            _draw_sidebar(sidebar, names, sel, filter_str, statuses)
            _draw_detail(detail, names[sel] if names else None)
            _draw_footer(stdscr)
            stdscr.refresh()

        try:
            c = stdscr.get_wch()
        except curses.error:
            continue

        cur = names[sel] if names else None

        if c in (curses.KEY_UP, "k") and names: sel = (sel - 1) % len(names)
        elif c in (curses.KEY_DOWN, "j") and names: sel = (sel + 1) % len(names)
        elif c == "q": return
        elif c == "?": _info(stdscr, "help", _help_text())
        elif c == "n":
            new_name = _action_create(stdscr)
            if new_name and new_name in apps.list_apps():
                sel = apps.list_apps().index(new_name)
        elif c == "/":
            v = _prompt(stdscr, "filter", filter_str)
            filter_str = v or ""
            sel = 0
        elif c == "g":
            _action_settings(stdscr)
        elif cur:
            try:
                if c == "s": apps.start(cur)
                elif c == "x": apps.stop(cur)
                elif c == "r": apps.restart(cur)
                elif c == "u":
                    if _confirm(stdscr, f"update {cur} now? (stop → update → start)"):
                        rc = apps.run_update(cur)
                        if rc != 0:
                            _info(stdscr, "update", f"update failed (rc={rc}); see logs")
                elif c == "e": _action_edit(stdscr, cur)
                elif c == "E": _action_env(stdscr, cur)
                elif c == "l": _action_logs(stdscr, cur)
                elif c == "t": _action_schedule(stdscr, cur)
                elif c == "w": _action_webhook(stdscr, cur)
                elif c == "D":
                    if _confirm(stdscr, f"PERMANENTLY remove {cur}?"):
                        apps.remove(cur)
                elif c in (10, 13, "\n"): _info(stdscr, cur, apps.status_text(cur))
            except subprocess.CalledProcessError as e:
                _info(stdscr, "systemctl error", str(e))
            except Exception as e:
                _info(stdscr, "error", str(e))


def run() -> None:
    _need_root_or_die()
    # if no password is set and bind is non-loopback, prompt now
    cfg = config.load()
    if not cfg.get("web_password") and cfg.get("web_bind", "0.0.0.0") != "127.0.0.1":
        print("lazy-system: web UI has no password set yet — it will only accept loopback")
        print("             connections. Set one in TUI → 'g' settings → 'set web password'.")
        try:
            input("press Enter to continue, Ctrl-C to abort...")
        except KeyboardInterrupt:
            return
    curses.wrapper(_main)


if __name__ == "__main__":
    run()
