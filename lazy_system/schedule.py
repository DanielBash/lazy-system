"""Schedule presets -> systemd OnCalendar expressions."""

PRESETS = {
    "every-second":   "*-*-* *:*:00/1",
    "every-minute":   "*-*-* *:*:00",
    "every-5-minutes": "*-*-* *:0/5:00",
    "every-15-minutes": "*-*-* *:0/15:00",
    "every-hour":     "hourly",
    "every-day":      "daily",
    "every-midnight": "*-*-* 00:00:00",
    "every-noon":     "*-*-* 12:00:00",
    "every-monday":   "Mon *-*-* 00:00:00",
    "every-weekend":  "Sat,Sun *-*-* 00:00:00",
    "weekly":         "weekly",
    "monthly":        "monthly",
}


def resolve(spec: str) -> str:
    """Turn a preset name or raw OnCalendar into an OnCalendar expression."""
    if not spec:
        raise ValueError("empty schedule")
    if spec in PRESETS:
        return PRESETS[spec]
    return spec


def list_presets() -> list[str]:
    return list(PRESETS.keys())
