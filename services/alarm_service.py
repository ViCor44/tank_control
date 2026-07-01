from datetime import datetime, timezone


SEVERITY_HIGH = "high"
SEVERITY_MEDIUM = "medium"
SEVERITY_LOW = "low"


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def build_tank_alarms(config, state):
    alarms = []
    tank_states = state.get("tanks", {})

    for tank in config.get("tanks", []):
        tank_id = tank.get("id")
        tank_name = tank.get("name", tank_id)

        if not tank.get("enabled", False):
            continue

        tank_state = tank_states.get(tank_id, {})
        status = tank_state.get("status")
        level_percent = tank_state.get("level_percent")
        sensor_ok = tank_state.get("sensor_ok", False)
        last_update = tank_state.get("last_update")

        if not sensor_ok and status != "disabled":
            alarms.append({
                "id": f"tank_{tank_id}_sensor_offline",
                "severity": SEVERITY_HIGH,
                "message": f"Tanque {tank_name}: sensor offline",
                "tank_id": tank_id,
                "level_percent": level_percent,
                "detected_at": last_update or now_iso(),
            })
            continue

        if status == "critical_low":
            alarms.append({
                "id": f"tank_{tank_id}_critical_low",
                "severity": SEVERITY_HIGH,
                "message": f"Tanque {tank_name} em nível crítico ({level_percent}%)",
                "tank_id": tank_id,
                "level_percent": level_percent,
                "detected_at": last_update or now_iso(),
            })
        elif status == "low":
            alarms.append({
                "id": f"tank_{tank_id}_low",
                "severity": SEVERITY_MEDIUM,
                "message": f"Tanque {tank_name} em nível baixo ({level_percent}%)",
                "tank_id": tank_id,
                "level_percent": level_percent,
                "detected_at": last_update or now_iso(),
            })
        elif status == "full":
            alarms.append({
                "id": f"tank_{tank_id}_full",
                "severity": SEVERITY_LOW,
                "message": f"Tanque {tank_name} cheio ({level_percent}%)",
                "tank_id": tank_id,
                "level_percent": level_percent,
                "detected_at": last_update or now_iso(),
            })

    severity_order = {SEVERITY_HIGH: 0, SEVERITY_MEDIUM: 1, SEVERITY_LOW: 2}
    alarms.sort(key=lambda a: (severity_order.get(a["severity"], 99), a["tank_id"]))

    return alarms
