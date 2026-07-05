import json
import time
from pathlib import Path
from datetime import datetime, timezone

from services.config_service import load_config, load_state
from services.tank_service import (
    apply_sensor_spike_filter,
    calculate_tank_status,
    get_tank_sensor_reading,
)
from services.control_service import apply_tank_level_relays, apply_source_relays
from services.alarm_service import build_tank_alarms


BASE_DIR = Path(__file__).resolve().parent
STATE_PATH = BASE_DIR / "config" / "state.json"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def save_state(state):
    with open(STATE_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)


def update_tank_states():
    config = load_config()
    state = load_state()

    if "tanks" not in state:
        state["tanks"] = {}

    if "sources" not in state:
        state["sources"] = {}

    system = config.get("system", {})
    spike_threshold_cm = float(system.get("sensor_spike_threshold_cm", 7) or 7)
    spike_max_consecutive = int(system.get("sensor_spike_max_consecutive", 3) or 3)

    for tank in config.get("tanks", []):
        tank_id = tank["id"]

        if tank_id not in state["tanks"]:
            state["tanks"][tank_id] = {}

        if not tank.get("enabled", False):
            state["tanks"][tank_id]["sensor_ok"] = False
            state["tanks"][tank_id]["status"] = "disabled"
            state["tanks"][tank_id]["consecutive_spike_count"] = 0
            state["tanks"][tank_id]["last_update"] = now_iso()
            continue

        try:
            reading = get_tank_sensor_reading(
                tank,
                timeout_seconds=system.get("sensor_request_timeout_seconds", 2)
            )

            if reading.get("ok"):
                tank_state = state["tanks"][tank_id]
                filter_result = apply_sensor_spike_filter(
                    tank_state,
                    reading["distance_cm"],
                    spike_threshold_cm,
                    spike_max_consecutive,
                )

                if filter_result["status"] == "accepted":
                    level_percent = reading["level_percent"]
                    status = calculate_tank_status(
                        level_percent,
                        tank["thresholds"]["empty_percent"],
                        tank["thresholds"]["full_percent"]
                    )

                    tank_state.update({
                        "distance_cm": reading["distance_cm"],
                        "level_percent": level_percent,
                        "volume_liters": reading["volume_liters"],
                        "status": status,
                        "sensor_ok": True,
                        "consecutive_spike_count": 0,
                        "last_raw_distance_cm": reading["distance_cm"],
                        "last_spike_delta_cm": None,
                        "last_update": now_iso(),
                    })
                    tank_state.pop("last_error", None)

                elif filter_result["status"] == "rejected":
                    # Transient spike — keep previous stable values.
                    tank_state["consecutive_spike_count"] = filter_result["count"]
                    tank_state["last_raw_distance_cm"] = reading["distance_cm"]
                    tank_state["last_spike_delta_cm"] = filter_result["delta_cm"]
                    tank_state["last_update"] = now_iso()

                else:  # 'persistent'
                    tank_state["consecutive_spike_count"] = filter_result["count"]
                    tank_state["last_raw_distance_cm"] = reading["distance_cm"]
                    tank_state["last_spike_delta_cm"] = filter_result["delta_cm"]
                    tank_state["sensor_ok"] = False
                    tank_state["status"] = "unknown"
                    tank_state["last_error"] = "sensor_spike_persistent"
                    tank_state["last_update"] = now_iso()
            else:
                state["tanks"][tank_id]["sensor_ok"] = False
                state["tanks"][tank_id]["last_error"] = reading.get("error", "unknown_error")
                state["tanks"][tank_id]["last_update"] = now_iso()

        except Exception as e:
            state["tanks"][tank_id]["sensor_ok"] = False
            state["tanks"][tank_id]["last_error"] = str(e)
            state["tanks"][tank_id]["last_update"] = now_iso()

    state = apply_tank_level_relays(config, state)
    state = apply_source_relays(config, state)
    state["alarms"] = build_tank_alarms(config, state)
    state["state_last_updated"] = now_iso()
    save_state(state)


def main():
    while True:
        update_tank_states()
        config = load_config()
        interval = float(
            config.get("system", {}).get("poll_interval_seconds", 10) or 10
        )
        time.sleep(max(1.0, interval))


if __name__ == "__main__":
    main()