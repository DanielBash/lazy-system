"""lazysystem CLI. Bare invocation launches the TUI."""
import argparse
import json
import os
import subprocess
import sys

from . import apps, auth, config, history, schedule


def _need_root() -> None:
    if os.geteuid() != 0:
        sys.exit("lazysystem: this command needs root (sudo lazysystem ...)")


def cmd_list(_args) -> None:
    names = apps.list_apps()
    if not names:
        print("(no apps)")
        return
    print(f"{'NAME':<20} {'STATE':<10} {'SCHEDULES':<25} DESCRIPTION")
    for name in names:
        cfg = apps.load(name)
        state = "running" if apps.is_active(name) else "stopped"
        scheds = ", ".join(s["spec"] for s in cfg.get("schedules", [])) or "-"
        print(f"{name:<20} {state:<10} {scheds:<25} {cfg.get('description','')}")


def cmd_create(args) -> None:
    _need_root()
    cfg = apps.create(args.name, shell=args.shell, description=args.description or "")
    ext = "fish" if cfg["shell"] == "fish" else "sh"
    print(f"created '{args.name}' (shell={cfg['shell']})")
    print(f"  scripts: /etc/lazy-system/apps/{args.name}/{{run,stop,update}}.{ext}")
    print(f"  next:    sudo lazysystem edit {args.name} run")


def cmd_remove(args) -> None:
    _need_root()
    apps.remove(args.name)
    print(f"removed '{args.name}'")


def cmd_edit(args) -> None:
    _need_root()
    apps.edit_script(args.name, args.kind)


def cmd_start(args) -> None:    _need_root(); apps.start(args.name)
def cmd_stop(args) -> None:     _need_root(); apps.stop(args.name)
def cmd_restart(args) -> None:  _need_root(); apps.restart(args.name)
def cmd_update(args) -> None:   _need_root(); sys.exit(apps.run_update(args.name))


def cmd_enable(args) -> None:
    _need_root(); apps.enable(args.name)
    print(f"enabled '{args.name}'")


def cmd_logs(args) -> None:
    if not apps.exists(args.name):
        sys.exit(f"no such app: {args.name}")
    cmd = ["journalctl", "-u", f"lazy-{args.name}.service", "--no-pager"]
    if args.follow: cmd.append("-f")
    else:           cmd += ["-n", str(args.lines)]
    subprocess.run(cmd)


def cmd_status(args) -> None:
    if not apps.exists(args.name):
        sys.exit(f"no such app: {args.name}")
    print(apps.status_text(args.name))
    print("--- recent history ---")
    import time
    for h in history.read(args.name, limit=20):
        print(f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(h['t']))}  {h['event']:<16} {h.get('detail','')}")


def cmd_schedule(args) -> None:
    if args.action == "list":
        if args.name:
            for s in apps.load(args.name).get("schedules", []):
                print(f"{s['id']}  {s['spec']:<22} ({s['oncalendar']})")
        else:
            print("presets:")
            for p in schedule.list_presets():
                print(f"  {p:<22} -> {schedule.PRESETS[p]}")
            print("\nor pass any systemd OnCalendar expression directly.")
    elif args.action == "add":
        _need_root()
        s = apps.add_schedule(args.name, args.spec)
        print(f"added schedule {s['id']}: {s['spec']} -> {s['oncalendar']}")
    elif args.action == "remove":
        _need_root()
        apps.remove_schedule(args.name, args.id)
        print(f"removed schedule {args.id}")


def cmd_webhook(args) -> None:
    if not apps.exists(args.name):
        sys.exit(f"no such app: {args.name}")
    cfg = apps.load(args.name)
    g = config.load()
    base = f"http://{args.host or 'localhost'}:{g['webhook_port']}/hook/{args.name}/{cfg['token']}"
    for action in ("update", "start", "stop", "restart"):
        print(f"{action:<8} curl -X POST '{base}?action={action}'")


def cmd_env(args) -> None:
    _need_root()
    if args.action == "list":
        for k, v in apps.load(args.name).get("env", {}).items():
            print(f"{k}={v}")
    elif args.action == "set":
        if "=" not in args.kv: sys.exit("expected KEY=VALUE")
        k, v = args.kv.split("=", 1); apps.set_env(args.name, k, v)
    elif args.action == "unset":
        apps.set_env(args.name, args.key, None)


def cmd_export(args) -> None:
    p = apps.export_app(args.name, args.dir or ".")
    print(p)


def cmd_import(args) -> None:
    _need_root()
    name = apps.import_app(args.archive)
    print(f"imported '{name}'")


def cmd_doctor(args) -> None:
    issues = apps.doctor()
    if args.fix:
        _need_root()
        n = apps.fix_units()
        print(f"regenerated {n} unit set(s)")
    for lvl, name, msg in issues:
        print(f"[{lvl:<5}] {name:<20} {msg}")
    if not issues:
        print("ok — no issues")


def cmd_passwd(_args) -> None:
    _need_root()
    from getpass import getpass
    p1 = getpass("new web password: ")
    p2 = getpass("confirm:           ")
    if p1 != p2: sys.exit("mismatch")
    cfg = config.load()
    cfg["web_password"] = auth.hash_password(p1)
    config.save(cfg)
    subprocess.run(["systemctl", "restart", "lazy-webhook.service"], check=False)
    print("web password set; lazy-webhook restarted")


def cmd_config(args) -> None:
    cfg = config.load()
    if args.set:
        _need_root()
        for kv in args.set:
            if "=" not in kv: sys.exit(f"bad --set: {kv}")
            k, v = kv.split("=", 1)
            if k not in cfg: sys.exit(f"unknown key: {k}")
            if isinstance(cfg[k], bool):  cfg[k] = v.lower() in ("1", "true", "yes", "on")
            elif isinstance(cfg[k], int): cfg[k] = int(v)
            else:                         cfg[k] = v
        config.save(cfg)
        apps.regenerate_all_units()
        if any(kv.startswith(("webhook_port=", "web_bind=", "web_user=")) for kv in args.set):
            subprocess.run(["systemctl", "restart", "lazy-webhook.service"], check=False)
    print(json.dumps({k: ("***" if k == "web_password" and v else v) for k, v in cfg.items()}, indent=2))


def cmd_tui(_args) -> None:
    from . import tui
    tui.run()


def cmd_run_update(args) -> None:
    sys.exit(apps.run_update(args.name))


def main() -> None:
    p = argparse.ArgumentParser(prog="lazysystem", description="lazy-system: tiny apps, big systemd")
    sub = p.add_subparsers(dest="cmd")

    sub.add_parser("tui", help="launch interactive TUI (default)").set_defaults(fn=cmd_tui)
    sub.add_parser("list", help="list apps").set_defaults(fn=cmd_list)

    s = sub.add_parser("create", help="create a new app")
    s.add_argument("name"); s.add_argument("--shell", choices=["bash", "fish", "auto"])
    s.add_argument("--description", "-d"); s.set_defaults(fn=cmd_create)

    s = sub.add_parser("remove", help="remove an app"); s.add_argument("name"); s.set_defaults(fn=cmd_remove)

    s = sub.add_parser("edit", help="edit run|stop|update script")
    s.add_argument("name"); s.add_argument("kind", choices=["run", "stop", "update"])
    s.set_defaults(fn=cmd_edit)

    for action, fn in (("start", cmd_start), ("stop", cmd_stop), ("restart", cmd_restart),
                       ("enable", cmd_enable), ("update", cmd_update)):
        s = sub.add_parser(action); s.add_argument("name"); s.set_defaults(fn=fn)

    s = sub.add_parser("logs"); s.add_argument("name")
    s.add_argument("-f", "--follow", action="store_true"); s.add_argument("-n", "--lines", type=int, default=200)
    s.set_defaults(fn=cmd_logs)

    s = sub.add_parser("status"); s.add_argument("name"); s.set_defaults(fn=cmd_status)

    s = sub.add_parser("schedule")
    ss = s.add_subparsers(dest="action", required=True)
    a = ss.add_parser("list"); a.add_argument("name", nargs="?")
    a = ss.add_parser("add");  a.add_argument("name"); a.add_argument("spec")
    a = ss.add_parser("remove"); a.add_argument("name"); a.add_argument("id")
    s.set_defaults(fn=cmd_schedule)

    s = sub.add_parser("webhook"); s.add_argument("name"); s.add_argument("--host"); s.set_defaults(fn=cmd_webhook)

    s = sub.add_parser("env")
    ss = s.add_subparsers(dest="action", required=True)
    a = ss.add_parser("list");  a.add_argument("name")
    a = ss.add_parser("set");   a.add_argument("name"); a.add_argument("kv", help="KEY=VALUE")
    a = ss.add_parser("unset"); a.add_argument("name"); a.add_argument("key")
    s.set_defaults(fn=cmd_env)

    s = sub.add_parser("export"); s.add_argument("name"); s.add_argument("--dir"); s.set_defaults(fn=cmd_export)
    s = sub.add_parser("import"); s.add_argument("archive"); s.set_defaults(fn=cmd_import)

    s = sub.add_parser("doctor"); s.add_argument("--fix", action="store_true"); s.set_defaults(fn=cmd_doctor)
    s = sub.add_parser("passwd", help="set web UI password"); s.set_defaults(fn=cmd_passwd)

    s = sub.add_parser("config")
    s.add_argument("--set", action="append", default=[], metavar="KEY=VALUE")
    s.set_defaults(fn=cmd_config)

    s = sub.add_parser("_run-update"); s.add_argument("name"); s.set_defaults(fn=cmd_run_update)

    if len(sys.argv) == 1:
        return cmd_tui(None)
    args = p.parse_args()
    if not getattr(args, "fn", None):
        return cmd_tui(None)
    args.fn(args)


if __name__ == "__main__":
    main()
