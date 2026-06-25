from services.relay_service import build_relay_board_service


def apply_tank_level_relays(config, state):
    relay_service = build_relay_board_service(config)
    tanks = config.get("tanks", [])
    tank_states = state.get("tanks", {})

    relay_results = []

    for tank in tanks:
        tank_id = tank.get("id")
        tank_state = tank_states.get(tank_id, {})

        empty_relay = tank.get("relays", {}).get("empty", 0)
        full_relay = tank.get("relays", {}).get("full", 0)

        if not tank.get("enabled", False):
            if empty_relay > 0:
                relay_results.append(relay_service.relay_off(empty_relay))
            if full_relay > 0:
                relay_results.append(relay_service.relay_off(full_relay))
            continue

        if not tank_state.get("sensor_ok", False):
            if empty_relay > 0:
                relay_results.append(relay_service.relay_off(empty_relay))
            if full_relay > 0:
                relay_results.append(relay_service.relay_off(full_relay))
            continue

        level_percent = tank_state.get("level_percent", 0)
        empty_percent = tank.get("thresholds", {}).get("empty_percent", 15)
        full_percent = tank.get("thresholds", {}).get("full_percent", 90)

        if level_percent <= empty_percent:
            if empty_relay > 0:
                relay_results.append(relay_service.relay_on(empty_relay))
            if full_relay > 0:
                relay_results.append(relay_service.relay_off(full_relay))

        elif level_percent >= full_percent:
            if empty_relay > 0:
                relay_results.append(relay_service.relay_off(empty_relay))
            if full_relay > 0:
                relay_results.append(relay_service.relay_on(full_relay))

        else:
            if empty_relay > 0:
                relay_results.append(relay_service.relay_off(empty_relay))
            if full_relay > 0:
                relay_results.append(relay_service.relay_off(full_relay))

    state["tank_relays"] = relay_results
    return state