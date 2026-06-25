import json
import time
from pathlib import Path

from services.config_service import load_config, load_state
from services.tank_service import get_tank_sensor_reading, calculate_tank_status
from services.control_service import apply_tank_level_relays


BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "config" / "state.json"


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def update_tank_states():
    config = load_config()
    state = load_state()

    if "tanks" not in state:
        state["tanks"] = {}

    for tank in config.get("tanks", []):
        tank_id = tank["id"]

        if tank_id not in state["tanks"]:
            state["tanks"][tank_id] = {}

        if not tank.get("enabled", False):
            state["tanks"][tank_id]["sensor_ok"] = False
            state["tanks"][tank_id]["status"] = "disabled"
            continue

        try:
            reading = get_tank_sensor_reading(
                tank,
                timeout_seconds=config.get("system", {}).get("sensor_request_timeout_seconds", 2)
            )

            if reading.get("ok"):
                level_percent = reading["level_percent"]
                status = calculate_tank_status(
                    level_percent,
                    tank["thresholds"]["empty_percent"],
                    tank["thresholds"]["full_percent"]
                )

                state["tanks"][tank_id] = {
                    "distance_cm": reading["distance_cm"],
                    "level_percent": level_percent,
                    "volume_liters": reading["volume_liters"],
                    "status": status,
                    "sensor_ok": True
                }
            else:
                state["tanks"][tank_id]["sensor_ok"] = False
                state["tanks"][tank_id]["last_error"] = reading.get("error", "unknown_error")

        except Exception as e:
            state["tanks"][tank_id]["sensor_ok"] = False
            state["tanks"][tank_id]["last_error"] = str(e)

    state = apply_tank_level_relays(config, state)
    save_state(state)


def main():
    while True:
        update_tank_states()
        time.sleep(20)


if __name__ == "__main__":
    main()