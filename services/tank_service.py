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