from datetime import datetime, timezone

from services.relay_service import build_relay_board_service


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _decide_full_relay(distance_cm, distance_full_cm, hysteresis_cm, previous_on):
    """Full relay ON when tank reaches full (distance drops to distance_full_cm)."""
    if distance_cm is None:
        return False
    if previous_on:
        return distance_cm <= distance_full_cm + hysteresis_cm
    return distance_cm <= distance_full_cm


def _decide_empty_relay(distance_cm, distance_empty_cm, hysteresis_cm, previous_on):
    """Empty relay ON when tank reaches empty (distance grows to distance_empty_cm)."""
    if distance_cm is None:
        return False
    if previous_on:
        return distance_cm >= distance_empty_cm - hysteresis_cm
    return distance_cm >= distance_empty_cm


def apply_tank_level_relays(config, state):
    relay_service = build_relay_board_service(config)
    tanks = config.get("tanks", [])
    tank_states = state.get("tanks", {})
    system = config.get("system", {})
    default_hysteresis = float(system.get("default_hysteresis_cm", 0) or 0)

    relay_results = []

    for tank in tanks:
        tank_id = tank.get("id")
        tank_state = tank_states.setdefault(tank_id, {})

        relays = tank.get("relays", {})
        empty_relay = relays.get("empty", 0) or 0
        full_relay = relays.get("full", 0) or 0

        prev_empty_on = bool(tank_state.get("relay_empty_on", False))
        prev_full_on = bool(tank_state.get("relay_full_on", False))

        if not tank.get("enabled", False) or not tank_state.get("sensor_ok", False):
            if empty_relay > 0:
                relay_results.append(relay_service.relay_off(empty_relay))
            if full_relay > 0:
                relay_results.append(relay_service.relay_off(full_relay))
            tank_state["relay_empty_on"] = False
            tank_state["relay_full_on"] = False
            continue

        distance_cm = tank_state.get("distance_cm")
        calibration = tank.get("calibration", {})
        distance_empty_cm = float(calibration.get("distance_empty_cm", 150))
        distance_full_cm = float(calibration.get("distance_full_cm", 20))
        hysteresis_cm = float(tank.get("hysteresis_cm", default_hysteresis) or 0)

        want_full_on = _decide_full_relay(distance_cm, distance_full_cm, hysteresis_cm, prev_full_on)
        want_empty_on = _decide_empty_relay(distance_cm, distance_empty_cm, hysteresis_cm, prev_empty_on)

        if want_full_on and want_empty_on:
            # Both cannot be on simultaneously — prioritize "full" (safety: close inlet).
            want_empty_on = False

        if empty_relay > 0:
            relay_results.append(
                relay_service.relay_on(empty_relay) if want_empty_on else relay_service.relay_off(empty_relay)
            )
        if full_relay > 0:
            relay_results.append(
                relay_service.relay_on(full_relay) if want_full_on else relay_service.relay_off(full_relay)
            )

        tank_state["relay_empty_on"] = want_empty_on
        tank_state["relay_full_on"] = want_full_on

    state["tank_relays"] = relay_results
    return state


def apply_source_relays(config, state):
    relay_service = build_relay_board_service(config)
    sources = config.get("sources", [])
    source_states = state.setdefault("sources", {})
    system = config.get("system", {})
    default_start_delay = float(system.get("default_source_startup_delay_seconds", 0) or 0)
    default_stop_delay = float(system.get("default_source_stop_delay_seconds", 0) or 0)

    now = datetime.now(timezone.utc)
    relay_results = []

    for source in sources:
        source_id = source.get("id")
        source_state = source_states.setdefault(source_id, {})
        enable_relay = source.get("enable_relay", source.get("valve_relay", 0)) or 0

        if not source.get("enabled", False):
            source_state["active"] = False
            source_state["status"] = "disabled"
            source_state["last_update"] = now_iso()
            source_state["desired_active_since"] = None
            if enable_relay > 0:
                relay_results.append(relay_service.relay_off(enable_relay))
            continue

        desired_active = bool(source_state.get("desired_active", source_state.get("active", False)))
        current_active = bool(source_state.get("active", False))
        startup_delay = float(source.get("startup_delay_seconds", default_start_delay) or 0)
        stop_delay = float(source.get("stop_delay_seconds", default_stop_delay) or 0)

        applied_active = current_active
        if desired_active != current_active:
            since_iso = source_state.get("desired_active_since")
            if since_iso:
                try:
                    since = datetime.fromisoformat(since_iso)
                except ValueError:
                    since = now
            else:
                since = now
                source_state["desired_active_since"] = now.isoformat()

            elapsed = (now - since).total_seconds()
            required = startup_delay if desired_active else stop_delay

            if elapsed >= required:
                applied_active = desired_active
                source_state["desired_active_since"] = None
        else:
            source_state["desired_active_since"] = None

        if enable_relay > 0:
            if applied_active:
                relay_results.append(relay_service.relay_on(enable_relay))
                source_state["status"] = "active"
            else:
                relay_results.append(relay_service.relay_off(enable_relay))
                source_state["status"] = "waiting" if desired_active != applied_active else "idle"
        else:
            source_state["status"] = "no_relay"

        source_state["active"] = applied_active
        source_state["last_update"] = now_iso()

    state["source_relays"] = relay_results
    return state
