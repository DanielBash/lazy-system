#!/usr/bin/env bash
# lazy-system installer for Ubuntu / systemd
set -euo pipefail

if [[ $EUID -ne 0 ]]; then
    echo "lazy-system: please run as root (sudo bash install.sh)"
    exit 1
fi

PREFIX="/opt/lazy-system"
ETC="/etc/lazy-system"
VAR="/var/lib/lazy-system"
BIN="/usr/local/bin/lazysystem"
ALIAS="/usr/local/bin/lazy"
PORT_DEFAULT=8765

echo "==> Installing system packages"
apt-get update -qq
apt-get install -y --no-install-recommends python3 python3-psutil python3-yaml fish >/dev/null || \
    apt-get install -y --no-install-recommends python3 python3-psutil python3-yaml >/dev/null

echo "==> Laying out directories"
mkdir -p "$PREFIX" "$ETC/apps" "$VAR/apps" "$VAR/state"
chmod 755 "$ETC" "$VAR"

echo "==> Copying source"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)"
rm -rf "$PREFIX/lazy_system"
cp -r "$SRC_DIR/lazy_system" "$PREFIX/"

echo "==> Installing CLI shim"
cat >"$BIN" <<EOF
#!/usr/bin/env bash
exec python3 -m lazy_system.cli "\$@"
EOF
chmod +x "$BIN"
ln -sf "$BIN" "$ALIAS"

# Make the package importable for python3 -m
SITE_DIR=$(python3 -c "import site,sys; print(site.getsitepackages()[0])")
mkdir -p "$SITE_DIR"
cat >"$SITE_DIR/lazy_system.pth" <<EOF
$PREFIX
EOF

echo "==> Seeding global config"
if [[ ! -f "$ETC/config.json" ]]; then
    cat >"$ETC/config.json" <<EOF
{
  "webhook_port": $PORT_DEFAULT,
  "save_logs": true,
  "shell": "auto",
  "metrics_interval_seconds": 10,
  "metrics_retention_points": 4320
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
ExecStart=/usr/bin/python3 -m lazy_system.webhook
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
ExecStart=/usr/bin/python3 -m lazy_system.monitor
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now lazy-webhook.service lazy-monitor.service >/dev/null

PORT=$(python3 -c 'import json;print(json.load(open("/etc/lazy-system/config.json"))["webhook_port"])')
echo
echo "lazy-system installed."
echo "  TUI:        sudo lazysystem"
echo "  CLI:        sudo lazysystem --help    (alias: lazy)"
echo "  Dashboard:  http://localhost:$PORT/"
echo "  Webhooks:   http://<host>:$PORT/hook/<app>/<token>"
echo
echo "  Set web password (required for non-loopback access):"
echo "    sudo lazysystem passwd"
