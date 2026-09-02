import json
import os
import secrets
import threading
from datetime import datetime, timezone
from pathlib import Path

from werkzeug.security import check_password_hash, generate_password_hash

CONFIG_DIR = Path(__file__).resolve().parent.parent / "config"
SECURITY_FILE = CONFIG_DIR / "security.json"
AUDIT_FILE = CONFIG_DIR / "audit.jsonl"
SECRET_FILE = CONFIG_DIR / ".session_secret"
_lock = threading.RLock()

def _default_security():
    initial_pin = os.environ.get("TANK_CONTROL_MASTER_PIN", "1234")
    return {"master_pin_hash": generate_password_hash(initial_pin), "master_pin_is_default": "TANK_CONTROL_MASTER_PIN" not in os.environ, "users": []}

def load_security():
    with _lock:
        if not SECURITY_FILE.exists():
            data = _default_security()
            save_security(data)
            return data
        with SECURITY_FILE.open("r", encoding="utf-8") as handle:
            return json.load(handle)

def save_security(data):
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        temporary = SECURITY_FILE.with_suffix(".tmp")
        with temporary.open("w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=2)
        temporary.replace(SECURITY_FILE)

def get_session_secret():
    configured = os.environ.get("TANK_CONTROL_SECRET_KEY")
    if configured:
        return configured
    with _lock:
        if SECRET_FILE.exists():
            return SECRET_FILE.read_text(encoding="utf-8").strip()
        value = secrets.token_hex(32)
        SECRET_FILE.write_text(value, encoding="utf-8")
        return value

def verify_master_pin(pin):
    data = load_security()
    return bool(pin) and check_password_hash(data["master_pin_hash"], pin)

def find_user_by_pin(pin):
    if not pin:
        return None
    for user in load_security().get("users", []):
        if user.get("active", True) and check_password_hash(user.get("pin_hash", ""), pin):
            return user
    return None

def find_active_user(user_id):
    return next((user for user in load_security().get("users", []) if user.get("id") == user_id and user.get("active", True)), None)

def pin_in_use(pin, exclude_user_id=None):
    data = load_security()
    if check_password_hash(data["master_pin_hash"], pin):
        return True
    return any(user.get("id") != exclude_user_id and check_password_hash(user.get("pin_hash", ""), pin) for user in data.get("users", []))

def hash_pin(pin):
    return generate_password_hash(pin)

def append_audit(actor, action, target, detail="", ip=""):
    entry = {"timestamp": datetime.now(timezone.utc).isoformat(), "actor": actor, "action": action, "target": target, "detail": detail, "ip": ip}
    with _lock:
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        with AUDIT_FILE.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(entry, ensure_ascii=False) + "\n")

def load_audit(limit=500):
    with _lock:
        if not AUDIT_FILE.exists():
            return []
        lines = AUDIT_FILE.read_text(encoding="utf-8").splitlines()
    entries = []
    for line in lines[-limit:]:
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return list(reversed(entries))
