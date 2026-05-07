# lazy-system

Tiny TUI + web wrapper around systemd for running small "apps" on Ubuntu.
Each app is three scripts — **run**, **stop**, **update** — and lazy-system
takes care of the rest:

- `run` is launched as a systemd `Type=simple` service (it should `exec`
  the main process in the foreground)
- `stop` runs as `ExecStop` before the service is stopped
- `update` runs every time the app is updated; the flow is always
  **stop → update → start**

## Install

```bash
git clone https://github.com/DanielBash/lazy-system.git
cd lazy-system
sudo bash install.sh
```

Installs `lazysystem` (alias `lazy`) on `$PATH`, plus two systemd services
that come up on boot:

- `lazy-webhook.service` — web UI + webhook receiver
- `lazy-monitor.service` — CPU/RAM sampler

After install:

```bash
sudo lazysystem passwd      # set a web password (required for non-loopback)
sudo lazysystem              # launch the TUI
```

## TUI

`sudo lazysystem` opens a curses dashboard:

- live CPU% and RAM sparklines
- uptime bar with restart / failure / update history
- per-app: start, stop, restart, update, edit (run/stop/update), env vars,
  schedules, webhook URL, logs
- global settings: web port, bind address, username/password, default shell,
  log saving, metrics interval, export/import apps, doctor (auto-fix unit files)

Keys (also shown on the bottom bar):

```
↑/↓ select  enter status   s start  x stop  r restart  u update
e edit      E env vars     l logs   t schedule  w webhook
n new       D delete       / filter g settings ? help  q quit
```

## CLI

Every TUI action also has a direct command:

```bash
sudo lazysystem create myapp --shell bash -d "my little daemon"
sudo lazysystem edit   myapp run
sudo lazysystem enable myapp
sudo lazysystem start  myapp
sudo lazysystem stop   myapp
sudo lazysystem update myapp                    # stop → update → start
sudo lazysystem schedule add myapp every-weekend
sudo lazysystem schedule add myapp 'Mon..Fri 09:00'
sudo lazysystem env set myapp API_KEY=xxx
sudo lazysystem webhook myapp
sudo lazysystem logs   myapp -f
sudo lazysystem status myapp
sudo lazysystem export myapp --dir ~/backup
sudo lazysystem import ~/backup/lazy-myapp-*.tar.gz
sudo lazysystem doctor --fix
sudo lazysystem passwd
sudo lazysystem config --set webhook_port=9001 --set save_logs=false
```

## Web UI

Same actions in a browser at `http://<host>:<port>/`. Defaults to
**loopback only until a web password is set** — protect yourself before
exposing the port. Once a password is set, the UI requires HTTP basic auth.

Webhook URLs use a per-app token (no basic auth needed) and survive reboots:

```
http://host:8765/hook/<app>/<token>?action=update     # also: start, stop, restart
```

## Schedule presets

`every-second`, `every-minute`, `every-5-minutes`, `every-15-minutes`,
`every-hour`, `every-day`, `every-midnight`, `every-noon`, `every-monday`,
`every-weekend`, `weekly`, `monthly`. Or pass any
[`OnCalendar`](https://www.freedesktop.org/software/systemd/man/systemd.time.html)
expression.

## Files

- `/etc/lazy-system/config.json` — global config
- `/etc/lazy-system/apps/<name>/` — `app.json`, `run.sh`, `stop.sh`, `update.sh`
- `/var/lib/lazy-system/apps/<name>/` — `metrics.jsonl`, `history.jsonl`, `app.log`
- `/etc/systemd/system/lazy-*.{service,timer}` — generated systemd units
