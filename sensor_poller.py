import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

from services.config_service import (
    load_config,
    load_state,
    save_state_preserving_sensor_resets,
)
from services.tank_service import (
    apply_sensor_spike_filter,
    calculate_tank_status,
    get_tank_sensor_reading,
)
from services.control_service import apply_tank_level_relays, apply_source_relays
from services.alarm_service import build_tank_alarms


logger = logging.getLogger(__name__)
_poller_thread = None
_poller_lock = threading.Lock()


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def read_sensors_parallel(tanks, timeout_seconds):
    """Read enabled tanks concurrently without sharing mutable state."""
    enabled_tanks = [tank for tank in tanks if tank.get("enabled", False)]
    if not enabled_tanks:
        return {}

    readings = {}
    with ThreadPoolExecutor(max_workers=len(enabled_tanks), thread_name_prefix="tank-sensor") as executor:
        futures = {
            executor.submit(
                get_tank_sensor_reading,
                tank,
                timeout_seconds=timeout_seconds,
            ): tank["id"]
            for tank in enabled_tanks
        }
        for future in as_completed(futures):
            tank_id = futures[future]
            try:
                readings[tank_id] = future.result()
            except Exception as exc:
                readings[tank_id] = {"ok": False, "error": str(exc)}

    return readings


def update_tank_states():
    config = load_config()
    state = load_state()
    loaded_reset_tokens = {
        tank_id: tank_state.get("sensor_reset_token")
        for tank_id, tank_state in state.get("tanks", {}).items()
    }

    if "tanks" not in state:
        state["tanks"] = {}

    if "sources" not in state:
        state["sources"] = {}

    system = config.get("system", {})
    spike_threshold_cm = float(system.get("sensor_spike_threshold_cm", 7) or 7)
    spike_max_consecutive = int(system.get("sensor_spike_max_consecutive", 3) or 3)
    tanks = config.get("tanks", [])
    readings = read_sensors_parallel(
        tanks,
        timeout_seconds=system.get("sensor_request_timeout_seconds", 2),
    )

    for tank in tanks:
        tank_id = tank["id"]

        if tank_id not in state["tanks"]:
            state["tanks"][tank_id] = {}

        if not tank.get("enabled", False):
            state["tanks"][tank_id]["sensor_ok"] = False
            state["tanks"][tank_id]["sensor_reading_valid"] = False
            state["tanks"][tank_id]["status"] = "disabled"
            state["tanks"][tank_id]["consecutive_spike_count"] = 0
            state["tanks"][tank_id]["last_update"] = now_iso()
            continue

        try:
            reading = readings.get(tank_id, {"ok": False, "error": "missing_sensor_result"})

            if reading.get("ok"):
                tank_state = state["tanks"][tank_id]
                # "Online" is determined by this very same successful request
                # that returned distance_cm, independently of the spike filter.
                tank_state["sensor_ok"] = True
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
                        "sensor_reading_valid": True,
                        "consecutive_spike_count": 0,
                        "last_raw_distance_cm": reading["distance_cm"],
                        "last_spike_delta_cm": None,
                        "last_update": now_iso(),
                    })
                    tank_state.pop("last_error", None)

                elif filter_result["status"] == "rejected":
                    # Transient spike — keep previous stable values.
                    tank_state["sensor_reading_valid"] = True
                    tank_state["consecutive_spike_count"] = filter_result["count"]
                    tank_state["last_raw_distance_cm"] = reading["distance_cm"]
                    tank_state["last_spike_delta_cm"] = filter_result["delta_cm"]
                    tank_state["last_update"] = now_iso()

                else:  # 'persistent'
                    tank_state["consecutive_spike_count"] = filter_result["count"]
                    tank_state["last_raw_distance_cm"] = reading["distance_cm"]
                    tank_state["last_spike_delta_cm"] = filter_result["delta_cm"]
                    # The controller answered and supplied distance_cm, so it is
                    # online; only the measurement is unsafe for control.
                    tank_state["sensor_reading_valid"] = False
                    tank_state["status"] = "unknown"
                    tank_state["last_error"] = "sensor_spike_persistent"
                    tank_state["last_update"] = now_iso()
            else:
                state["tanks"][tank_id]["sensor_ok"] = False
                state["tanks"][tank_id]["sensor_reading_valid"] = False
                state["tanks"][tank_id]["last_error"] = reading.get("error", "unknown_error")
                state["tanks"][tank_id]["last_update"] = now_iso()

        except Exception as e:
            state["tanks"][tank_id]["sensor_ok"] = False
            state["tanks"][tank_id]["sensor_reading_valid"] = False
            state["tanks"][tank_id]["last_error"] = str(e)
            state["tanks"][tank_id]["last_update"] = now_iso()

    state = apply_tank_level_relays(config, state)
    state = apply_source_relays(config, state)
    state["alarms"] = build_tank_alarms(config, state)
    state["state_last_updated"] = now_iso()
    save_state_preserving_sensor_resets(state, loaded_reset_tokens)


def poll_forever():
    while True:
        cycle_started = time.monotonic()
        interval = 5.0
        try:
            update_tank_states()
            config = load_config()
            interval = float(
                config.get("system", {}).get("poll_interval_seconds", 5) or 5
            )
        except Exception:
            # A temporary network/configuration error must not permanently stop
            # sensor discovery. The next cycle tries again automatically.
            logger.exception("Erro no ciclo de leitura dos sensores")
        # Keep a start-to-start cadence. Previously the sensor/network work
        # was added on top of the configured interval.
        elapsed = time.monotonic() - cycle_started
        time.sleep(max(0.1, interval - elapsed))


def start_background_poller():
    """Start exactly one sensor polling thread in this Python process."""
    global _poller_thread
    with _poller_lock:
        if _poller_thread is not None and _poller_thread.is_alive():
            return _poller_thread

        _poller_thread = threading.Thread(
            target=poll_forever,
            name="sensor-poller",
            daemon=True,
        )
        _poller_thread.start()
        return _poller_thread


def main():
    poll_forever()


if __name__ == "__main__":
    main()
