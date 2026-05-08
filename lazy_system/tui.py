"""Textual TUI for lazy-system."""
import os
import subprocess
import time

from textual import on
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll, Grid
from textual.screen import ModalScreen
from textual.widgets import (
    Header, Footer, ListView, ListItem, Label, Static, Sparkline,
    Input, Button, DataTable, TabbedContent, TabPane, RichLog, Switch,
)

from . import apps, auth, config, history, schedule
from .paths import metrics_path, log_path

POLL_DETAIL_S = 1.0
POLL_LIST_S = 3.0


# ============================ helpers ============================

def _read_metrics(name: str, n: int = 240) -> list[dict]:
    import json
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


def _fmt_bytes(b: float) -> str:
    f = float(b)
    for unit in ("B", "K", "M", "G"):
        if f < 1024:
            return f"{f:.0f}{unit}"
        f /= 1024
    return f"{f:.1f}T"


def _read_log_tail(name: str, lines: int = 400) -> str:
    """Prefer the per-app log file (clean stdout). Fall back to journalctl -o cat."""
    p = log_path(name)
    if p.exists():
        try:
            data = p.read_bytes()
            tail = data[-300_000:].decode("utf-8", errors="replace")
            return "\n".join(tail.splitlines()[-lines:])
        except Exception:
            pass
    r = subprocess.run(
        ["journalctl", "-u", f"lazy-{name}.service",
         "-n", str(lines), "--no-pager", "-o", "cat"],
        capture_output=True, text=True,
    )
    return r.stdout or "(no logs yet)"


def _need_root_or_die() -> None:
    if os.geteuid() != 0:
        print("lazy-system: TUI must run as root (sudo lazysystem)")
        raise SystemExit(1)


# ============================ modal screens ============================

class ConfirmScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    ConfirmScreen { align: center middle; }
    ConfirmScreen > Vertical {
        background: $surface; border: thick $primary; padding: 1 2;
        width: 60; height: auto;
    }
    ConfirmScreen Horizontal { align: center middle; height: 3; }
    ConfirmScreen Button { margin: 0 1; }
    """

    def __init__(self, prompt: str, danger: bool = False):
        super().__init__()
        self.prompt = prompt
        self.danger = danger

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Static(self.prompt)
            with Horizontal():
                yield Button("Cancel", id="no")
                yield Button("Confirm", id="yes", variant="error" if self.danger else "primary")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(ev.button.id == "yes")

    def on_key(self, ev) -> None:
        if ev.key == "escape": self.dismiss(False)
        elif ev.key == "enter": self.dismiss(True)


class PromptScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    PromptScreen { align: center middle; }
    PromptScreen > Vertical {
        background: $surface; border: thick $primary; padding: 1 2;
        width: 70; height: auto;
    }
    PromptScreen Input { margin-top: 1; }
    PromptScreen Horizontal { align: right middle; height: 3; margin-top: 1; }
    """

    def __init__(self, label: str, default: str = "", password: bool = False, placeholder: str = ""):
        super().__init__()
        self.label = label
        self.default = default
        self.password = password
        self.placeholder = placeholder

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.label)
            yield Input(value=self.default, password=self.password,
                        placeholder=self.placeholder, id="inp")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("OK", id="ok", variant="primary")

    def on_mount(self) -> None:
        self.query_one("#inp", Input).focus()

    @on(Input.Submitted)
    def _on_submit(self, ev: Input.Submitted) -> None:
        self.dismiss(ev.value)

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        self.dismiss(self.query_one("#inp", Input).value if ev.button.id == "ok" else None)

    def on_key(self, ev) -> None:
        if ev.key == "escape": self.dismiss(None)


class ChoiceScreen(ModalScreen[str | None]):
    DEFAULT_CSS = """
    ChoiceScreen { align: center middle; }
    ChoiceScreen > Vertical {
        background: $surface; border: thick $primary; padding: 1 2;
        width: 60; height: auto; max-height: 24;
    }
    ChoiceScreen ListView { height: auto; max-height: 16; margin-top: 1; }
    """

    def __init__(self, title: str, options: list[tuple[str, str]]):
        """options is [(value, label)]"""
        super().__init__()
        self.title_text = title
        self.options = options

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title_text)
            yield ListView(*[ListItem(Label(lbl), id=f"opt-{i}")
                             for i, (_, lbl) in enumerate(self.options)], id="lv")

    def on_mount(self) -> None:
        self.query_one(ListView).focus()

    @on(ListView.Selected)
    def _picked(self, ev: ListView.Selected) -> None:
        idx = int(ev.item.id.split("-")[1])
        self.dismiss(self.options[idx][0])

    def on_key(self, ev) -> None:
        if ev.key == "escape": self.dismiss(None)


class InfoScreen(ModalScreen):
    DEFAULT_CSS = """
    InfoScreen { align: center middle; }
    InfoScreen > Vertical {
        background: $surface; border: thick $primary; padding: 1 2;
        width: 90%; height: 80%;
    }
    InfoScreen RichLog { height: 1fr; border: round $accent; }
    InfoScreen Horizontal { align: right middle; height: 3; margin-top: 1; }
    """

    def __init__(self, title: str, body: str):
        super().__init__()
        self.title_text = title
        self.body = body

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label(self.title_text)
            log = RichLog(wrap=True, highlight=True)
            yield log
            with Horizontal():
                yield Button("Close", id="close", variant="primary")

    def on_mount(self) -> None:
        self.query_one(RichLog).write(self.body)

    def on_button_pressed(self, _) -> None: self.dismiss(None)
    def on_key(self, ev) -> None:
        if ev.key in ("escape", "q", "enter"): self.dismiss(None)


class NewAppScreen(ModalScreen[dict | None]):
    DEFAULT_CSS = """
    NewAppScreen { align: center middle; }
    NewAppScreen > Vertical {
        background: $surface; border: thick $primary; padding: 1 2;
        width: 70; height: auto;
    }
    NewAppScreen Input, NewAppScreen Label { margin-top: 1; }
    NewAppScreen Horizontal { align: right middle; height: 3; margin-top: 1; }
    """

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Create new app")
            yield Label("name (lowercase, [a-z0-9_-])")
            yield Input(id="name", placeholder="my-app")
            yield Label("description (optional)")
            yield Input(id="desc")
            yield Label("shell")
            yield Input(value="auto", id="shell", placeholder="auto, bash, or fish")
            with Horizontal():
                yield Button("Cancel", id="cancel")
                yield Button("Create", id="ok", variant="primary")

    def on_mount(self) -> None: self.query_one("#name", Input).focus()

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id != "ok":
            self.dismiss(None); return
        self.dismiss({
            "name": self.query_one("#name", Input).value.strip(),
            "description": self.query_one("#desc", Input).value.strip(),
            "shell": (self.query_one("#shell", Input).value.strip() or "auto"),
        })

    def on_key(self, ev) -> None:
        if ev.key == "escape": self.dismiss(None)


class LimitsScreen(ModalScreen[dict | None]):
    DEFAULT_CSS = """
    LimitsScreen { align: center middle; }
    LimitsScreen > Vertical {
        background: $surface; border: thick $primary; padding: 1 2;
        width: 72; height: auto;
    }
    LimitsScreen Input, LimitsScreen Label { margin-top: 1; }
    LimitsScreen Horizontal#btns { align: right middle; height: 3; margin-top: 1; }
    LimitsScreen .hint { color: $text-muted; }
    """

    def __init__(self, current: dict):
        super().__init__()
        self.current = current

    def compose(self) -> ComposeResult:
        with Vertical():
            yield Label("Resource limits (leave blank to clear)")
            yield Label("CPU quota (e.g. 50% or 200%)", classes="hint")
            yield Input(value=self.current.get("cpu_quota", ""), id="cpu", placeholder="50%")
            yield Label("Memory max (e.g. 512M, 2G)", classes="hint")
            yield Input(value=self.current.get("memory_max", ""), id="mem", placeholder="512M")
            yield Label("Tasks max (process+thread cap)", classes="hint")
            yield Input(value=self.current.get("tasks_max", ""), id="tasks", placeholder="100")
            yield Label("IO weight (10–1000)", classes="hint")
            yield Input(value=self.current.get("io_weight", ""), id="io", placeholder="100")
            with Horizontal(id="btns"):
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="ok", variant="primary")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id != "ok":
            self.dismiss(None); return
        self.dismiss({
            "cpu_quota":  self.query_one("#cpu", Input).value.strip() or None,
            "memory_max": self.query_one("#mem", Input).value.strip() or None,
            "tasks_max":  self.query_one("#tasks", Input).value.strip() or None,
            "io_weight":  self.query_one("#io", Input).value.strip() or None,
        })

    def on_key(self, ev) -> None:
        if ev.key == "escape": self.dismiss(None)


class SettingsScreen(ModalScreen[bool]):
    DEFAULT_CSS = """
    SettingsScreen { align: center middle; }
    SettingsScreen > Vertical {
        background: $surface; border: thick $primary; padding: 1 2;
        width: 80; height: auto;
    }
    SettingsScreen Input, SettingsScreen Label { margin-top: 1; }
    SettingsScreen Horizontal { align: right middle; height: 3; margin-top: 1; }
    SettingsScreen .row { height: 3; align: left middle; }
    SettingsScreen .row Label { width: 30; }
    """

    def compose(self) -> ComposeResult:
        cfg = config.load()
        with Vertical():
            yield Label("Global settings")
            yield Label("web port")
            yield Input(value=str(cfg["webhook_port"]), id="port")
            yield Label("bind address (0.0.0.0 or 127.0.0.1)")
            yield Input(value=cfg["web_bind"], id="bind")
            yield Label("web username")
            yield Input(value=cfg["web_user"], id="user")
            yield Label("default shell (auto / bash / fish)")
            yield Input(value=cfg["shell"], id="shell")
            yield Label("metrics interval (seconds)")
            yield Input(value=str(cfg["metrics_interval_seconds"]), id="interval")
            with Horizontal(classes="row"):
                yield Label("save logs to file")
                yield Switch(value=bool(cfg["save_logs"]), id="logs")
            with Horizontal():
                yield Button("Set password…", id="pw")
                yield Button("Cancel", id="cancel")
                yield Button("Save", id="ok", variant="primary")

    def on_button_pressed(self, ev: Button.Pressed) -> None:
        if ev.button.id == "pw":
            self.app.push_screen(PromptScreen("new web password", password=True),
                                 self._got_pw)
            return
        if ev.button.id != "ok":
            self.dismiss(False); return
        cfg = config.load()
        port = self.query_one("#port", Input).value.strip()
        if port.isdigit(): cfg["webhook_port"] = int(port)
        cfg["web_bind"] = self.query_one("#bind", Input).value.strip() or cfg["web_bind"]
        cfg["web_user"] = self.query_one("#user", Input).value.strip() or cfg["web_user"]
        cfg["shell"] = self.query_one("#shell", Input).value.strip() or cfg["shell"]
        iv = self.query_one("#interval", Input).value.strip()
        if iv.isdigit(): cfg["metrics_interval_seconds"] = max(1, int(iv))
        cfg["save_logs"] = self.query_one("#logs", Switch).value
        config.save(cfg)
        apps.regenerate_all_units()
        subprocess.run(["systemctl", "restart", "lazy-webhook.service"], check=False)
        self.dismiss(True)

    def _got_pw(self, pw: str | None) -> None:
        if not pw: return
        def confirmed(pw2: str | None) -> None:
            if pw2 != pw:
                self.app.push_screen(InfoScreen("error", "passwords do not match"))
                return
            cfg = config.load()
            cfg["web_password"] = auth.hash_password(pw)
            config.save(cfg)
            subprocess.run(["systemctl", "restart", "lazy-webhook.service"], check=False)
            self.app.push_screen(InfoScreen("ok", "web password set"))
        self.app.push_screen(PromptScreen("confirm password", password=True), confirmed)

    def on_key(self, ev) -> None:
        if ev.key == "escape": self.dismiss(False)


# ============================ widgets ============================

class AppRow(ListItem):
    def __init__(self, name: str, running: bool):
        super().__init__()
        self.name_ = name
        self.running = running

    def compose(self) -> ComposeResult:
        dot = "[green]●[/]" if self.running else "[dim]○[/]"
        yield Static(f" {dot} {self.name_}")


class Detail(VerticalScroll):
    DEFAULT_CSS = """
    Detail { padding: 1 2; }
    Detail .title { text-style: bold; }
    Detail .muted { color: $text-muted; }
    Detail .ok { color: $success; }
    Detail .bad { color: $error; }
    Detail Sparkline { height: 3; margin: 0 0 1 0; }
    Detail .buttons { height: 3; margin-top: 1; }
    Detail .buttons Button { margin-right: 1; }
    Detail TabbedContent { margin-top: 1; }
    Detail #cpu { color: $accent; }
    Detail #ram { color: $warning; }
    Detail RichLog { height: 14; border: round $accent; }
    Detail DataTable { height: auto; max-height: 12; }
    Detail .hbar {
        height: 1; background: $boost;
    }
    """

    def __init__(self):
        super().__init__()
        self.app_name: str | None = None
        self._log_offset: int = 0
        self._title_static: Static | None = None
        self._cpu_spark: Sparkline | None = None
        self._ram_spark: Sparkline | None = None
        self._summary: Static | None = None
        self._sched: Static | None = None
        self._webhook: Static | None = None
        self._env: Static | None = None
        self._limits: Static | None = None
        self._uptime: Static | None = None
        self._log: RichLog | None = None
        self._history_table: DataTable | None = None

    def compose(self) -> ComposeResult:
        self._title_static = Static("select an app", classes="title")
        yield self._title_static
        with Horizontal(classes="buttons"):
            yield Button("Start",   id="b-start",   variant="success")
            yield Button("Stop",    id="b-stop")
            yield Button("Restart", id="b-restart")
            yield Button("Update",  id="b-update",  variant="primary")
            yield Button("Logs ↗",  id="b-logs-full")
            yield Button("Delete",  id="b-delete",  variant="error")
        with Horizontal(classes="buttons"):
            yield Button("Edit script…", id="b-edit")
            yield Button("Env vars…",    id="b-env")
            yield Button("Limits…",      id="b-limits")
            yield Button("Schedules…",   id="b-schedule")
            yield Button("Webhook…",     id="b-webhook")
        yield Static("CPU %", classes="muted")
        self._cpu_spark = Sparkline([0], id="cpu")
        yield self._cpu_spark
        yield Static("RAM (MB)", classes="muted")
        self._ram_spark = Sparkline([0], id="ram")
        yield self._ram_spark

        self._uptime = Static("", classes="muted")
        yield self._uptime
        self._summary = Static("", classes="muted")
        yield self._summary

        with TabbedContent():
            with TabPane("Overview", id="t-overview"):
                self._sched   = Static(""); yield self._sched
                self._webhook = Static("", classes="muted"); yield self._webhook
                self._env     = Static("", classes="muted"); yield self._env
                self._limits  = Static("", classes="muted"); yield self._limits
            with TabPane("History", id="t-history"):
                t = DataTable()
                t.add_columns("time", "event", "detail")
                self._history_table = t
                yield t
            with TabPane("Logs", id="t-logs"):
                self._log = RichLog(wrap=True, highlight=True, max_lines=500)
                yield self._log

    def set_app(self, name: str | None) -> None:
        self.app_name = name
        self._log_offset = 0
        if self._log:
            self._log.clear()
        if not name:
            self._title_static.update("select an app")
        self.refresh_data(initial=True)

    def refresh_data(self, initial: bool = False) -> None:
        name = self.app_name
        if not name or not apps.exists(name):
            return
        cfg = apps.load(name)
        running = apps.is_active(name)
        metrics = _read_metrics(name, 240)
        hist = history.read(name, 80)

        state_md = "[green]● running[/]" if running else "[dim]○ stopped[/]"
        desc = cfg.get("description") or ""
        self._title_static.update(
            f"[b]{name}[/]  {state_md}\n[dim]{desc}  · shell: {cfg.get('shell')}[/]"
        )

        cpu = [m.get("cpu", 0.0) for m in metrics] or [0.0]
        rss_mb = [m.get("rss", 0) / 1_048_576 for m in metrics] or [0.0]
        self._cpu_spark.data = cpu
        self._ram_spark.data = rss_mb
        cur_cpu = cpu[-1] if cpu else 0
        cur_rss = metrics[-1].get("rss", 0) if metrics else 0

        # uptime as a colored bar
        recent = metrics[-min(80, len(metrics)):]
        if recent:
            bar = "".join("[green]█[/]" if m.get("active") else "[red]█[/]" for m in recent)
        else:
            bar = "[dim]no samples yet[/]"
        self._uptime.update(f"uptime  {bar}")

        counts: dict[str, int] = {}
        for ev in hist:
            counts[ev["event"]] = counts.get(ev["event"], 0) + 1
        self._summary.update(
            f"cpu {cur_cpu:.1f}%  ram {_fmt_bytes(cur_rss)}  · "
            f"restarts: {counts.get('restart',0)}  failures: {counts.get('exit',0)}  "
            f"updates: {counts.get('update_ok',0)}/{counts.get('update_failed',0)} fail  "
            f"webhooks: {counts.get('webhook',0)}"
        )

        scheds = cfg.get("schedules", [])
        sched_str = ", ".join(f"[bold]{s['spec']}[/]" for s in scheds) or "[dim](none)[/]"
        self._sched.update(f"schedules: {sched_str}")

        g = config.load()
        if cfg.get("webhook_enabled", True):
            wh = f"http://localhost:{g['webhook_port']}/hook/{name}/{cfg['token']}?action=update"
            self._webhook.update(f"webhook: {wh}")
        else:
            self._webhook.update("[dim]webhook disabled[/]")

        env = cfg.get("env", {})
        self._env.update("env: " + (" ".join(f"{k}=…" for k in env) if env else "[dim](none)[/]"))

        lim = cfg.get("limits", {}) or {}
        if lim:
            self._limits.update("limits: " + " ".join(f"[b]{k}[/]={v}" for k, v in lim.items()))
        else:
            self._limits.update("[dim]limits: (none)[/]")

        # history table
        self._history_table.clear()
        for ev in reversed(hist[-80:]):
            ts = time.strftime("%m-%d %H:%M:%S", time.localtime(ev["t"]))
            self._history_table.add_row(ts, ev["event"], ev.get("detail", ""))

        self._refresh_logs(initial)

    def _refresh_logs(self, initial: bool) -> None:
        name = self.app_name
        if not name or not self._log:
            return
        p = log_path(name)
        if p.exists():
            try:
                size = p.stat().st_size
            except OSError:
                return
            if initial or size < self._log_offset:
                # first paint, or file truncated/rotated: show last ~300 lines
                tail = _read_log_tail(name, lines=300)
                self._log.clear()
                self._log.write(tail or "[dim](no output yet)[/]")
                self._log_offset = size
                return
            if size > self._log_offset:
                try:
                    with p.open("rb") as f:
                        f.seek(self._log_offset)
                        chunk = f.read(size - self._log_offset).decode("utf-8", errors="replace")
                except OSError:
                    return
                if chunk:
                    self._log.write(chunk.rstrip("\n"))
                self._log_offset = size
        elif initial:
            # no per-app file (save_logs disabled): seed once from journal
            self._log.clear()
            self._log.write(_read_log_tail(name, lines=300))


# ============================ main app ============================

class LazyApp(App):
    CSS = """
    Screen { layout: horizontal; }
    #sidebar { width: 32; border-right: solid $primary-darken-1; }
    #sidebar > Label { padding: 1 1 0 1; text-style: bold; }
    #sidebar-toolbar { height: 3; padding: 0 1; }
    #sidebar-toolbar Button { margin-right: 1; min-width: 6; }
    #applist { height: 1fr; }
    Detail { width: 1fr; }
    """

    BINDINGS = [
        Binding("n", "new", "new"),
        Binding("s", "start", "start"),
        Binding("x", "stop", "stop"),
        Binding("r", "restart", "restart"),
        Binding("u", "update", "update"),
        Binding("e", "edit", "edit"),
        Binding("E", "env", "env"),
        Binding("L", "limits", "limits"),
        Binding("t", "schedule", "sched"),
        Binding("w", "webhook", "webhook"),
        Binding("l", "logs_full", "logs"),
        Binding("delete", "delete", "delete"),
        Binding("D", "delete", "delete", show=False),
        Binding("g", "settings", "settings"),
        Binding("/", "filter", "filter"),
        Binding("?", "help", "help"),
        Binding("q", "quit", "quit"),
    ]

    def __init__(self):
        super().__init__()
        self.filter_str = ""
        self.detail: Detail | None = None
        self.list_view: ListView | None = None

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Horizontal():
            with Vertical(id="sidebar"):
                yield Label("apps")
                with Horizontal(id="sidebar-toolbar"):
                    yield Button("+ New",     id="b-new",      variant="primary")
                    yield Button("Filter",    id="b-filter")
                    yield Button("Settings",  id="b-settings")
                yield ListView(id="applist")
            self.detail = Detail()
            yield self.detail
        yield Footer()

    def on_mount(self) -> None:
        self.title = "lazy-system"
        self._update_subtitle()
        self.list_view = self.query_one("#applist", ListView)
        self.refresh_list()
        self.set_interval(POLL_DETAIL_S, self._tick_detail)
        self.set_interval(POLL_LIST_S, self.refresh_list)

    def _update_subtitle(self) -> None:
        cfg = config.load()
        auth_label = "🔒 auth" if cfg.get("web_password") else "⚠ no-password (loopback only)"
        self.sub_title = f"web :{cfg['webhook_port']} · {auth_label}"

    def _tick_detail(self) -> None:
        if self.detail and self.detail.app_name:
            try:
                self.detail.refresh_data()
            except Exception:
                pass

    def refresh_list(self) -> None:
        names = [n for n in apps.list_apps() if self.filter_str.lower() in n.lower()]
        statuses = {n: apps.is_active(n) for n in names}
        prev = self._current_name()
        self.list_view.clear()
        for name in names:
            self.list_view.append(AppRow(name, statuses.get(name, False)))
        if prev and prev in names:
            self.list_view.index = names.index(prev)
        elif names:
            self.list_view.index = 0
        self._sync_detail()
        self._update_subtitle()

    def _current_name(self) -> str | None:
        if not self.list_view or self.list_view.index is None:
            return None
        if self.list_view.index < 0 or self.list_view.index >= len(self.list_view.children):
            return None
        item = self.list_view.children[self.list_view.index]
        return getattr(item, "name_", None)

    def _sync_detail(self) -> None:
        name = self._current_name()
        if self.detail and self.detail.app_name != name:
            self.detail.set_app(name)

    @on(ListView.Highlighted)
    def _on_highlight(self, _) -> None:
        self._sync_detail()

    @on(Button.Pressed)
    def _on_button(self, ev: Button.Pressed) -> None:
        bid = ev.button.id or ""
        actions = {
            "b-start": self.action_start, "b-stop": self.action_stop,
            "b-restart": self.action_restart, "b-update": self.action_update,
            "b-edit": self.action_edit, "b-env": self.action_env,
            "b-limits": self.action_limits, "b-schedule": self.action_schedule,
            "b-webhook": self.action_webhook, "b-logs-full": self.action_logs_full,
            "b-delete": self.action_delete,
            "b-new": self.action_new, "b-settings": self.action_settings,
            "b-filter": self.action_filter,
        }
        fn = actions.get(bid)
        if fn: fn()

    # ---------- actions ----------

    def _need(self) -> str | None:
        n = self._current_name()
        if not n:
            self.notify("no app selected", severity="warning")
        return n

    def action_start(self) -> None:
        name = self._need()
        if not name: return
        try: apps.start(name); self.notify(f"started {name}")
        except Exception as e: self.notify(str(e), severity="error")

    def action_stop(self) -> None:
        name = self._need()
        if not name: return
        try: apps.stop(name); self.notify(f"stopped {name}")
        except Exception as e: self.notify(str(e), severity="error")

    def action_restart(self) -> None:
        name = self._need()
        if not name: return
        try: apps.restart(name); self.notify(f"restarted {name}")
        except Exception as e: self.notify(str(e), severity="error")

    def action_update(self) -> None:
        name = self._need()
        if not name: return
        def go(ok: bool) -> None:
            if not ok: return
            self.notify(f"updating {name}…")
            rc = apps.run_update(name)
            if rc == 0: self.notify(f"{name} updated")
            else:       self.notify(f"update failed (rc={rc})", severity="error")
        self.push_screen(ConfirmScreen(f"Update {name}? (stop → update → start)"), go)

    def action_edit(self) -> None:
        name = self._need()
        if not name: return
        def picked(kind: str | None) -> None:
            if not kind: return
            with self.suspend():
                apps.edit_script(name, kind)
        self.push_screen(ChoiceScreen("Edit which script?",
                                      [("run", "run"), ("stop", "stop"), ("update", "update")]), picked)

    def action_env(self) -> None:
        name = self._need()
        if not name: return
        cfg = apps.load(name)
        env = cfg.get("env", {})
        opts: list[tuple[str, str]] = [("__add__", "+ add or update KEY=VALUE")]
        opts += [(f"rm:{k}", f"✕ remove {k}={v}") for k, v in env.items()]
        def picked(choice: str | None) -> None:
            if not choice: return
            if choice == "__add__":
                self.push_screen(PromptScreen("KEY=VALUE", placeholder="API_KEY=..."), self._add_env)
            elif choice.startswith("rm:"):
                k = choice[3:]
                apps.set_env(name, k, None)
                self.notify(f"removed {k}")
        self.push_screen(ChoiceScreen(f"Env vars · {name}", opts), picked)

    def _add_env(self, kv: str | None) -> None:
        name = self._current_name()
        if not name or not kv or "=" not in kv: return
        k, v = kv.split("=", 1)
        apps.set_env(name, k.strip(), v)
        self.notify(f"set {k.strip()}")

    def action_limits(self) -> None:
        name = self._need()
        if not name: return
        cfg = apps.load(name)
        def saved(result: dict | None) -> None:
            if result is None: return
            try:
                apps.set_limits(name, **result)
                self.notify(f"limits updated for {name}")
            except Exception as e:
                self.notify(str(e), severity="error")
        self.push_screen(LimitsScreen(cfg.get("limits", {}) or {}), saved)

    def action_schedule(self) -> None:
        name = self._need()
        if not name: return
        cfg = apps.load(name)
        opts: list[tuple[str, str]] = [("__add__", "+ add new schedule")]
        opts += [(f"rm:{s['id']}", f"✕ {s['spec']}  ({s['oncalendar']})")
                 for s in cfg.get("schedules", [])]
        def picked(choice: str | None) -> None:
            if not choice: return
            if choice == "__add__":
                preset_opts = [(p, f"{p}  →  {schedule.PRESETS[p]}") for p in schedule.list_presets()]
                preset_opts.append(("__custom__", "custom OnCalendar…"))
                self.push_screen(ChoiceScreen("preset", preset_opts), self._add_sched)
            elif choice.startswith("rm:"):
                apps.remove_schedule(name, choice[3:])
                self.notify("schedule removed")
        self.push_screen(ChoiceScreen(f"Schedules · {name}", opts), picked)

    def _add_sched(self, choice: str | None) -> None:
        name = self._current_name()
        if not name or not choice: return
        if choice == "__custom__":
            self.push_screen(PromptScreen("OnCalendar expression",
                                          placeholder="Mon..Fri 09:00"), self._add_sched_raw)
        else:
            try:
                apps.add_schedule(name, choice)
                self.notify(f"added {choice}")
            except Exception as e:
                self.notify(str(e), severity="error")

    def _add_sched_raw(self, spec: str | None) -> None:
        name = self._current_name()
        if not name or not spec: return
        try:
            apps.add_schedule(name, spec)
            self.notify("schedule added")
        except Exception as e:
            self.notify(str(e), severity="error")

    def action_webhook(self) -> None:
        name = self._need()
        if not name: return
        cfg = apps.load(name)
        g = config.load()
        base = f"http://<host>:{g['webhook_port']}/hook/{name}/{cfg['token']}"
        body = "\n".join([
            "Per-app token, no basic auth required:",
            "",
            f"  update:  {base}?action=update",
            f"  start:   {base}?action=start",
            f"  stop:    {base}?action=stop",
            f"  restart: {base}?action=restart",
            "",
            "GET or POST both work.",
        ])
        self.push_screen(InfoScreen(f"webhook · {name}", body))

    def action_logs_full(self) -> None:
        name = self._need()
        if not name: return
        p = log_path(name)
        with self.suspend():
            if p.exists():
                # clean app stdout — tail -F follows rotations and truncation
                subprocess.run(["bash", "-lc", f"tail -n 500 -F '{p}'"])
            else:
                subprocess.run(["bash", "-lc",
                    f"journalctl -u lazy-{name}.service -n 500 -f -o cat"])

    def action_delete(self) -> None:
        name = self._need()
        if not name: return
        def go(ok: bool) -> None:
            if not ok: return
            apps.remove(name)
            self.notify(f"deleted {name}", severity="warning")
            self.refresh_list()
        self.push_screen(ConfirmScreen(f"Permanently delete '{name}'?", danger=True), go)

    def action_new(self) -> None:
        def created(result: dict | None) -> None:
            if not result or not result.get("name"): return
            try:
                apps.create(result["name"], shell=result["shell"], description=result["description"])
                self.notify(f"created {result['name']}")
                self.refresh_list()
            except Exception as e:
                self.notify(str(e), severity="error")
        self.push_screen(NewAppScreen(), created)

    def action_filter(self) -> None:
        def got(v: str | None) -> None:
            self.filter_str = v or ""
            self.refresh_list()
        self.push_screen(PromptScreen("filter apps", default=self.filter_str), got)

    def action_settings(self) -> None:
        def done(_): self._update_subtitle()
        self.push_screen(SettingsScreen(), done)

    def action_help(self) -> None:
        body = "\n".join([
            "Keyboard:",
            "",
            *(f"  {b.key:<10} {b.description}" for b in self.BINDINGS if b.show),
            "",
            "Mouse:",
            "  click in the sidebar to select an app",
            "  click action buttons in the detail panel",
            "  click tabs (Overview / History / Logs)",
            "",
            "lazy-system: one app = three scripts (run / stop / update).",
            "Updates always run: stop → update → start.",
        ])
        self.push_screen(InfoScreen("help", body))


def run() -> None:
    _need_root_or_die()
    LazyApp().run()


if __name__ == "__main__":
    run()
