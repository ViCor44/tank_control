import requests


def clamp(value, min_value, max_value):
    return max(min_value, min(value, max_value))


def calculate_level_percent(distance_cm, distance_empty_cm, distance_full_cm):
    usable_range = distance_empty_cm - distance_full_cm
    if usable_range <= 0:
        return 0

    filled_height = distance_empty_cm - distance_cm
    level_percent = (filled_height / usable_range) * 100
    return round(clamp(level_percent, 0, 100), 1)


def calculate_volume_liters(level_percent, capacity_liters):
    return round((level_percent / 100) * capacity_liters, 1)


def calculate_tank_status(level_percent, empty_percent, full_percent):
    if level_percent <= empty_percent:
        return "critical_low"
    if level_percent >= full_percent:
        return "full"
    if level_percent < 30:
        return "low"
    return "normal"


def get_tank_sensor_reading(tank, timeout_seconds=2):
    sensor = tank.get("sensor", {})
    calibration = tank.get("calibration", {})

    endpoint = sensor.get("endpoint")
    method = sensor.get("method", "GET").upper()
    level_key = sensor.get("level_key", "distance_cm")

    distance_empty_cm = calibration.get("distance_empty_cm", 150)
    distance_full_cm = calibration.get("distance_full_cm", 20)
    capacity_liters = tank.get("capacity_liters", 0)

    if not endpoint:
        return {"ok": False, "error": "missing_endpoint"}

    if method != "GET":
        return {"ok": False, "error": "unsupported_method"}

    response = requests.get(endpoint, timeout=timeout_seconds)
    response.raise_for_status()
    data = response.json()

    if level_key not in data:
        return {"ok": False, "error": "missing_level_key", "data": data}

    distance_cm = float(data[level_key])
    level_percent = calculate_level_percent(
        distance_cm,
        distance_empty_cm,
        distance_full_cm
    )
    volume_liters = calculate_volume_liters(level_percent, capacity_liters)

    return {
        "ok": True,
        "distance_cm": round(distance_cm, 2),
        "level_percent": level_percent,
        "volume_liters": volume_liters,
        "raw_data": data
    }


def apply_sensor_spike_filter(tank_state, new_distance_cm, threshold_cm, max_consecutive):
    """Reject sudden jumps in the sensor reading.

    Behaviour:
      - If there's no previous accepted distance, accept the new reading.
      - If |new - previous| <= threshold_cm, accept and reset the counter.
      - Otherwise increment the consecutive-spike counter and reject:
          * if counter < max_consecutive → 'rejected' (keep previous values)
          * if counter >= max_consecutive → 'persistent' (mark sensor faulty)

    Returns a dict with:
      status: 'accepted' | 'rejected' | 'persistent'
      count: current consecutive spike count
      previous_distance_cm, delta_cm (when not accepted)
    """
    previous = tank_state.get("distance_cm")
    if previous is None:
        return {"status": "accepted", "count": 0}

    try:
        delta = abs(float(new_distance_cm) - float(previous))
    except (TypeError, ValueError):
        return {"status": "accepted", "count": 0}

    if delta <= threshold_cm:
        return {"status": "accepted", "count": 0}

    count = int(tank_state.get("consecutive_spike_count", 0) or 0) + 1
    status = "persistent" if count >= max_consecutive else "rejected"

    return {
        "status": status,
        "count": count,
        "previous_distance_cm": float(previous),
        "delta_cm": round(delta, 2),
    }