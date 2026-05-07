"""Password hashing for the web UI."""
import base64
import hashlib
import secrets

ITER = 200_000


def hash_password(password: str) -> dict:
    salt = secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, ITER)
    return {
        "salt": base64.b64encode(salt).decode(),
        "hash": base64.b64encode(digest).decode(),
        "iter": ITER,
    }


def verify_password(password: str, stored: dict) -> bool:
    if not stored:
        return False
    try:
        salt = base64.b64decode(stored["salt"])
        expect = base64.b64decode(stored["hash"])
        got = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, int(stored["iter"]))
        return secrets.compare_digest(expect, got)
    except Exception:
        return False
