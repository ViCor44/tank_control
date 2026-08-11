import json
import os
from pathlib import Path
import threading


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.json"
STATE_PATH = BASE_DIR / "config" / "state.json"
_state_file_lock = threading.RLock()


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    with _state_file_lock:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)


def save_config(config):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2, ensure_ascii=False)


def save_state(state):
    """Atomically replace the state file, avoiding partial JSON reads."""
    with _state_file_lock:
        temporary_path = STATE_PATH.with_suffix(".json.tmp")
        with open(temporary_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temporary_path, STATE_PATH)


def save_state_preserving_sensor_resets(state, loaded_reset_tokens):
    """Do not let an in-flight poll overwrite a newer sensor reset."""
    with _state_file_lock:
        with open(STATE_PATH, "r", encoding="utf-8") as f:
            current = json.load(f)

        current_tanks = current.get("tanks", {})
        next_tanks = state.setdefault("tanks", {})
        for tank_id, current_tank in current_tanks.items():
            current_token = current_tank.get("sensor_reset_token")
            if current_token != loaded_reset_tokens.get(tank_id):
                next_tanks[tank_id] = current_tank

        save_state(state)
