import json
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
CONFIG_PATH = BASE_DIR / "config" / "config.json"
STATE_PATH = BASE_DIR / "config" / "state.json"


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_state():
    with open(STATE_PATH, "r", encoding="utf-8") as f:
        return json.load(f)
