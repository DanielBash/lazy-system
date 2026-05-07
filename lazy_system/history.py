import json
import time
from .paths import history_path, app_state_dir


def record(app: str, event: str, detail: str = "") -> None:
    app_state_dir(app).mkdir(parents=True, exist_ok=True)
    line = json.dumps({"t": time.time(), "event": event, "detail": detail})
    with history_path(app).open("a") as f:
        f.write(line + "\n")


def read(app: str, limit: int = 200) -> list[dict]:
    p = history_path(app)
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
