"""Webhook receiver + dashboard. Single stdlib HTTP server."""
import json
import os
import subprocess
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import apps, auth, config, history
from .paths import metrics_path, log_path
import base64

DASHBOARD_HTML = (Path(__file__).parent / "dashboard.html").read_text()


def _read_metrics(name: str, limit: int = 720) -> list[dict]:
    p = metrics_path(name)
    if not p.exists():
        return []
    lines = p.read_text().splitlines()[-limit:]
    out = []
    for line in lines:
        try:
            out.append(json.loads(line))
        except Exception:
            pass
    return out


def _read_logs(name: str, lines: int = 200) -> str:
    p = log_path(name)
    if p.exists():
        try:
            data = p.read_bytes()
            tail = data[-200_000:].decode("utf-8", errors="replace")
            return "\n".join(tail.splitlines()[-lines:])
        except Exception as e:
            return f"<error reading log: {e}>"
    # fall back to journalctl
    r = subprocess.run(
        ["journalctl", "-u", f"lazy-{name}.service", "-n", str(lines), "--no-pager"],
        capture_output=True, text=True,
    )
    return r.stdout


def _client_is_local(addr: str) -> bool:
    return addr in ("127.0.0.1", "::1", "localhost")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):  # quiet
        return

    def _authed(self) -> bool:
        cfg = config.load()
        stored = cfg.get("web_password")
        if not stored:
            # no password set: allow only loopback
            return _client_is_local(self.client_address[0])
        h = self.headers.get("Authorization", "")
        if not h.startswith("Basic "):
            return False
        try:
            user, _, pw = base64.b64decode(h[6:]).decode().partition(":")
        except Exception:
            return False
        return user == cfg.get("web_user", "admin") and auth.verify_password(pw, stored)

    def _require_auth(self) -> bool:
        if self._authed():
            return True
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="lazy-system"')
        self.send_header("Content-Length", "0")
        self.end_headers()
        return False

    def _send_json(self, code: int, body) -> None:
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _send_text(self, code: int, body: str, ctype: str = "text/plain") -> None:
        data = body.encode("utf-8", errors="replace")
        self.send_response(code)
        self.send_header("Content-Type", f"{ctype}; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    # ----- routing -----

    def do_GET(self):
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        q = urllib.parse.parse_qs(u.query)

        # webhook uses its own per-app token, not basic auth
        if len(parts) == 3 and parts[0] == "hook":
            return self._handle_hook(parts[1], parts[2], q)

        if not self._require_auth():
            return

        if not parts:
            return self._send_text(200, DASHBOARD_HTML, "text/html")

        if parts == ["api", "apps"]:
            return self._send_json(200, self._apps_summary())

        if len(parts) == 3 and parts[0] == "api" and parts[2] == "metrics":
            name = parts[1]
            if not apps.exists(name):
                return self._send_json(404, {"error": "no such app"})
            return self._send_json(200, _read_metrics(name))

        if len(parts) == 3 and parts[0] == "api" and parts[2] == "history":
            name = parts[1]
            if not apps.exists(name):
                return self._send_json(404, {"error": "no such app"})
            return self._send_json(200, history.read(name))

        if len(parts) == 3 and parts[0] == "api" and parts[2] == "logs":
            name = parts[1]
            if not apps.exists(name):
                return self._send_text(404, "no such app")
            n = int(q.get("n", ["200"])[0])
            return self._send_text(200, _read_logs(name, n))

        if parts == ["api", "config"]:
            cfg = config.load()
            cfg["web_password"] = bool(cfg.get("web_password"))
            return self._send_json(200, cfg)

        return self._send_text(404, "not found")

    def do_POST(self):
        u = urllib.parse.urlparse(self.path)
        parts = [p for p in u.path.split("/") if p]
        q = urllib.parse.parse_qs(u.query)

        # webhook entry: /hook/<app>/<token>?action=update|start|stop|restart
        if len(parts) == 3 and parts[0] == "hook":
            return self._handle_hook(parts[1], parts[2], q)

        if not self._require_auth():
            return

        if parts == ["api", "config"]:
            length = int(self.headers.get("Content-Length", "0"))
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except Exception:
                return self._send_json(400, {"error": "invalid json"})
            cfg = config.load()
            cfg.update({k: v for k, v in body.items() if k in cfg})
            config.save(cfg)
            apps.regenerate_all_units()
            return self._send_json(200, cfg)

        # admin actions: /api/<app>/{start,stop,restart,update}
        if len(parts) == 3 and parts[0] == "api":
            name, action = parts[1], parts[2]
            if not apps.exists(name):
                return self._send_json(404, {"error": "no such app"})
            try:
                if action == "start":
                    apps.start(name)
                elif action == "stop":
                    apps.stop(name)
                elif action == "restart":
                    apps.restart(name)
                elif action == "update":
                    threading.Thread(target=apps.run_update, args=(name,), daemon=True).start()
                else:
                    return self._send_json(400, {"error": "unknown action"})
            except subprocess.CalledProcessError as e:
                return self._send_json(500, {"error": str(e)})
            return self._send_json(200, {"ok": True})

        # GET-style trigger via POST to /hook
        return self._send_text(404, "not found")

    def _handle_hook(self, name: str, token: str, q: dict) -> None:
        if not apps.exists(name):
            return self._send_json(404, {"error": "no such app"})
        cfg = apps.load(name)
        if not cfg.get("webhook_enabled", True):
            return self._send_json(403, {"error": "webhook disabled"})
        if token != cfg.get("token"):
            return self._send_json(401, {"error": "bad token"})
        action = q.get("action", ["update"])[0]
        history.record(name, "webhook", detail=action)
        if action == "update":
            threading.Thread(target=apps.run_update, args=(name,), daemon=True).start()
        elif action == "start":
            apps.start(name)
        elif action == "stop":
            apps.stop(name)
        elif action == "restart":
            apps.restart(name)
        else:
            return self._send_json(400, {"error": "unknown action"})
        return self._send_json(200, {"ok": True, "action": action})

    def _apps_summary(self) -> list[dict]:
        out = []
        for name in apps.list_apps():
            cfg = apps.load(name)
            out.append({
                "name": name,
                "description": cfg.get("description", ""),
                "shell": cfg.get("shell"),
                "active": apps.is_active(name),
                "schedules": cfg.get("schedules", []),
                "webhook_enabled": cfg.get("webhook_enabled", True),
                "webhook_path": f"/hook/{name}/{cfg.get('token')}",
            })
        return out


def main() -> None:
    cfg = config.load()
    port = int(os.environ.get("LAZY_PORT", "0")) or cfg.get("webhook_port", 8765)
    bind = cfg.get("web_bind", "0.0.0.0")
    srv = ThreadingHTTPServer((bind, port), Handler)
    auth_state = "auth-required" if cfg.get("web_password") else "loopback-only (no password set)"
    print(f"lazy-system web on {bind}:{port} ({auth_state})", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
