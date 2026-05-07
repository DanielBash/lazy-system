#!/usr/bin/env bash
# lazy-system installer for Ubuntu / systemd
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "lazy-system: please run as root (sudo bash install.sh)"
    exit 1
fi

PREFIX="/opt/lazy-system"
VENV="$PREFIX/venv"
ETC="/etc/lazy-system"
VAR="/var/lib/lazy-system"
BIN="/usr/local/bin/lazysystem"
ALIAS="/usr/local/bin/lazy"
PORT_DEFAULT=8765
NONINTERACTIVE="${LAZY_NONINTERACTIVE:-0}"

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y --no-install-recommends python3 python3-venv python3-pip >/dev/null
apt-get install -y --no-install-recommends fish >/dev/null 2>&1 || true

echo "==> Laying out directories"
mkdir -p "$PREFIX" "$ETC/apps" "$VAR/apps" "$VAR/state"
chmod 755 "$ETC" "$VAR"

echo "==> Creating venv at $VENV"
if [[ ! -x "$VENV/bin/python" ]]; then
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --upgrade pip wheel --quiet
"$VENV/bin/pip" install --quiet textual psutil

echo "==> Copying source"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
rm -rf "$PREFIX/lazy_system"
cp -r "$SRC_DIR/lazy_system" "$PREFIX/"

# Make package importable from venv python
SITE_DIR="$("$VENV/bin/python" -c 'import site,sys; print(site.getsitepackages()[0])')"
mkdir -p "$SITE_DIR"
echo "$PREFIX" >"$SITE_DIR/lazy_system.pth"

echo "==> Installing CLI shim"
cat >"$BIN" <<EOF
#!/usr/bin/env bash
exec $VENV/bin/python -m lazy_system.cli "\$@"
EOF
chmod +x "$BIN"
ln -sf "$BIN" "$ALIAS"

echo "==> Seeding global config"
if [[ ! -f "$ETC/config.json" ]]; then
    cat >"$ETC/config.json" <<EOF
{
  "webhook_port": $PORT_DEFAULT,
  "save_logs": true,
  "shell": "auto",
  "metrics_interval_seconds": 5,
  "metrics_retention_points": 4320,
  "web_user": "admin",
  "web_password": null,
  "web_bind": "0.0.0.0"
}
EOF
fi

echo "==> Installing systemd units"
cat >/etc/systemd/system/lazy-webhook.service <<EOF
[Unit]
Description=lazy-system webhook + dashboard
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$VENV/bin/python -m lazy_system.webhook
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

cat >/etc/systemd/system/lazy-monitor.service <<EOF
[Unit]
Description=lazy-system resource monitor
After=multi-user.target

[Service]
Type=simple
ExecStart=$VENV/bin/python -m lazy_system.monitor
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload

# ----- password prompt -----
NEED_PW=1
if "$VENV/bin/python" -c '
import json
cfg = json.load(open("/etc/lazy-system/config.json"))
import sys; sys.exit(0 if cfg.get("web_password") else 1)
'; then
    NEED_PW=0
    echo "==> Web password already set, skipping prompt"
fi

if [[ "$NEED_PW" == "1" && "$NONINTERACTIVE" != "1" && -t 0 ]]; then
    echo
    echo "==> Set web UI password"
    echo "    The web UI is loopback-only until a password is set."
    echo "    Press Enter on a blank prompt to skip (you can set it later via 'sudo lazysystem passwd')."
    while true; do
        read -rs -p "    new password: " PW1; echo
        if [[ -z "$PW1" ]]; then
            echo "    (skipped — set later with 'sudo lazysystem passwd')"
            break
        fi
        read -rs -p "    confirm:      " PW2; echo
        if [[ "$PW1" != "$PW2" ]]; then
            echo "    passwords do not match, try again"
            continue
        fi
        PW="$PW1" "$VENV/bin/python" -c '
import json, os
from lazy_system.auth import hash_password
path = "/etc/lazy-system/config.json"
cfg = json.load(open(path))
cfg["web_password"] = hash_password(os.environ["PW"])
with open(path + ".tmp", "w") as f:
    json.dump(cfg, f, indent=2)
os.replace(path + ".tmp", path)
'
        echo "    ✓ password set"
        break
    done
fi

systemctl enable --now lazy-webhook.service lazy-monitor.service >/dev/null

PORT=$("$VENV/bin/python" -c 'import json;print(json.load(open("/etc/lazy-system/config.json"))["webhook_port"])')
echo
echo "lazy-system installed."
echo "  TUI:        sudo lazysystem"
echo "  CLI:        sudo lazysystem --help    (alias: lazy)"
echo "  Dashboard:  http://localhost:$PORT/"
