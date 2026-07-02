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


def _build_route_lookup(config):
    lookup = {}
    for route in config.get("routes", []):
        key = (route.get("source_id"), route.get("tank_id"))
        lookup[key] = route
    return lookup


def _tank_by_id(config, tank_id):
    for tank in config.get("tanks", []):
        if tank.get("id") == tank_id:
            return tank
    return None


def compute_source_targets(config, state):
    """Decide, per source, which sequence step should be active now.

    Writes the following keys on each source_state:
      - current_tank_id: id of tank being served (or None)
      - current_tank_name
      - current_step_index
      - current_route_relay: valve relay for the selected route (or 0)
      - desired_active: True if a target was found and enabled_relay > 0
      - target_reason: short string ("target"|"no_step"|"disabled"|"no_route"|"blocked")
    """
    rules = config.get("rules", {})
    skip_full = bool(rules.get("skip_full_tanks", True))
    skip_disabled_tanks = bool(rules.get("skip_disabled_tanks", True))
    allow_multi_sources_per_tank = bool(rules.get("allow_multiple_sources_per_tank", False))

    tank_states = state.get("tanks", {})
    source_states = state.setdefault("sources", {})

    route_lookup = _build_route_lookup(config)
    # Track which source claimed each tank so we can explain a "blocked" reason.
    claimed_by = {}

    for source in config.get("sources", []):
        source_id = source.get("id")
        source_state = source_states.setdefault(source_id, {})

        source_state["current_tank_id"] = None
        source_state["current_tank_name"] = None
        source_state["current_step_index"] = None
        source_state["current_route_relay"] = 0
        source_state["desired_active"] = False
        source_state["target_reason"] = "idle"
        source_state["blocked_by"] = None

        if not source.get("enabled", False):
            source_state["target_reason"] = "disabled"
            continue

        sequence = source.get("sequence", []) or []

        for idx, step in enumerate(sequence):
            if not step.get("enabled", True):
                continue

            tank_id = step.get("tank_id")
            tank = _tank_by_id(config, tank_id)
            if tank is None:
                continue

            if skip_disabled_tanks and not tank.get("enabled", False):
                continue

            tank_state = tank_states.get(tank_id, {})
            status = tank_state.get("status")

            # Only skip when the tank is in a state we can't act on.
            # (sensor_ok is used only by the sensor-driven full/empty relays;
            # the sequencer relies on the latest known status.)
            if status in (None, "unknown", "disabled"):
                continue

            if skip_full and status == "full":
                continue

            if not allow_multi_sources_per_tank and tank_id in claimed_by:
                source_state["target_reason"] = "blocked"
                source_state["blocked_by"] = claimed_by[tank_id]
                continue

            route = route_lookup.get((source_id, tank_id))
            if route is None or not route.get("enabled", True):
                source_state["target_reason"] = "no_route"
                continue

            valve_relay = int(route.get("valve_relay", 0) or 0)
            if valve_relay <= 0:
                source_state["target_reason"] = "no_route"
                continue

            source_state["current_tank_id"] = tank_id
            source_state["current_tank_name"] = tank.get("name", tank_id)
            source_state["current_step_index"] = idx
            source_state["current_route_relay"] = valve_relay
            source_state["desired_active"] = True
            source_state["target_reason"] = "target"
            source_state["blocked_by"] = None
            claimed_by[tank_id] = source_id
            break
        else:
            if source_state["target_reason"] == "idle":
                source_state["target_reason"] = "no_step"

    return state


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
    """Apply source enable relays + route valve relays based on sequencer decision."""
    compute_source_targets(config, state)

    relay_service = build_relay_board_service(config)
    sources = config.get("sources", [])
    source_states = state.setdefault("sources", {})
    tank_states = state.setdefault("tanks", {})
    system = config.get("system", {})
    default_start_delay = float(system.get("default_source_startup_delay_seconds", 0) or 0)
    default_stop_delay = float(system.get("default_source_stop_delay_seconds", 0) or 0)

    # Clear filling markers on every cycle before we set new ones.
    for ts in tank_states.values():
        ts["filling_by"] = None
        ts["filling_by_name"] = None
        ts["filling_by_sources"] = []

    routes_by_source = {}
    for route in config.get("routes", []):
        routes_by_source.setdefault(route.get("source_id"), []).append(route)

    now = datetime.now(timezone.utc)
    relay_results = []

    for source in sources:
        source_id = source.get("id")
        source_state = source_states.setdefault(source_id, {})
        enable_relay = source.get("enable_relay", source.get("valve_relay", 0)) or 0
        source_routes = routes_by_source.get(source_id, [])

        if not source.get("enabled", False):
            source_state["active"] = False
            source_state["status"] = "disabled"
            source_state["last_update"] = now_iso()
            source_state["desired_active_since"] = None
            if enable_relay > 0:
                relay_results.append(relay_service.relay_off(enable_relay))
            for route in source_routes:
                vr = int(route.get("valve_relay", 0) or 0)
                if vr > 0:
                    relay_results.append(relay_service.relay_off(vr))
            continue

        desired_active = bool(source_state.get("desired_active", False))
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

        current_route_relay = int(source_state.get("current_route_relay", 0) or 0)

        # Route valves: only the currently-selected route is on (and only while pumping).
        for route in source_routes:
            vr = int(route.get("valve_relay", 0) or 0)
            if vr <= 0:
                continue
            if applied_active and vr == current_route_relay:
                relay_results.append(relay_service.relay_on(vr))
            else:
                relay_results.append(relay_service.relay_off(vr))

        # Source enable relay
        if enable_relay > 0:
            if applied_active:
                relay_results.append(relay_service.relay_on(enable_relay))
            else:
                relay_results.append(relay_service.relay_off(enable_relay))

        # Status derivation for UI
        reason = source_state.get("target_reason", "idle")
        if enable_relay <= 0:
            source_state["status"] = "no_relay"
        elif applied_active:
            source_state["status"] = "active"
        elif desired_active and not applied_active:
            source_state["status"] = "waiting"
        elif reason == "blocked":
            source_state["status"] = "blocked"
        elif reason == "no_route":
            source_state["status"] = "no_route"
        elif reason in ("no_step", "idle"):
            source_state["status"] = "idle"
        else:
            source_state["status"] = "idle"

        # Mark filling target on the tank state (only if we're actually pumping)
        current_tank_id = source_state.get("current_tank_id")
        if applied_active and current_tank_id:
            ts = tank_states.setdefault(current_tank_id, {})
            sources_list = ts.setdefault("filling_by_sources", [])
            entry = {"id": source_id, "name": source.get("name", source_id)}
            if entry not in sources_list:
                sources_list.append(entry)
            # Backward-compat scalar fields = first source
            if not ts.get("filling_by"):
                ts["filling_by"] = source_id
                ts["filling_by_name"] = source.get("name", source_id)

        source_state["active"] = applied_active
        source_state["last_update"] = now_iso()

    state["source_relays"] = relay_results
    return state
