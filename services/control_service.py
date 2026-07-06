from datetime import datetime, timedelta, timezone

from services.relay_service import build_relay_board_service


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _parse_iso(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


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
    prioritize_empty = bool(rules.get("prioritize_empty_tanks", False))

    tank_states = state.get("tanks", {})
    source_states = state.setdefault("sources", {})

    route_lookup = _build_route_lookup(config)
    # Track which source claimed each tank so we can explain a "blocked" reason.
    claimed_by = {}

    def _to_float(v):
        try:
            return float(v) if v is not None and v != "" else None
        except (TypeError, ValueError):
            return None

    for source in config.get("sources", []):
        source_id = source.get("id")
        source_state = source_states.setdefault(source_id, {})
        # Snapshot the target we were serving last cycle, before we reset it.
        previous_target = source_state.get("current_tank_id")

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

        candidates = []
        last_reject_reason = None  # (reason, blocked_by) for UI feedback when nothing eligible

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
            level_percent = tank_state.get("level_percent")

            # Only skip when the tank is in a state we can't act on.
            # (sensor_ok is used only by the sensor-driven full/empty relays;
            # the sequencer relies on the latest known status.)
            if status in (None, "unknown", "disabled"):
                continue

            if skip_full and status == "full":
                continue

            # Per-step thresholds with hysteresis:
            #   start_below_percent — only start serving this step when level < this
            #   stop_at_percent     — while serving, stop when level >= this
            # Backward-compat: if only trigger_below_percent is set, use it for both
            # (hard cap behaviour).
            legacy_cap = _to_float(step.get("trigger_below_percent"))
            start_below = _to_float(step.get("start_below_percent"))
            stop_at = _to_float(step.get("stop_at_percent"))
            if start_below is None:
                start_below = legacy_cap
            if stop_at is None:
                stop_at = legacy_cap

            was_serving_this = (previous_target == tank_id)

            if was_serving_this:
                # Currently serving — stop only when we reach the stop threshold.
                if stop_at is not None and stop_at > 0:
                    if level_percent is not None and level_percent >= stop_at:
                        continue
            else:
                # Not currently serving — require level below the start threshold.
                if start_below is not None and start_below > 0:
                    if level_percent is None or level_percent >= start_below:
                        continue

            if not allow_multi_sources_per_tank and tank_id in claimed_by:
                last_reject_reason = ("blocked", claimed_by[tank_id])
                continue

            route = route_lookup.get((source_id, tank_id))
            if route is None or not route.get("enabled", True):
                last_reject_reason = ("no_route", None)
                continue

            valve_relay = int(route.get("valve_relay", 0) or 0)
            if valve_relay <= 0:
                last_reject_reason = ("no_route", None)
                continue

            # Treat missing level as "full" for ranking, so unknown never beats a real reading.
            sort_level = level_percent if level_percent is not None else 100.0

            candidates.append({
                "idx": idx,
                "tank": tank,
                "tank_id": tank_id,
                "valve_relay": valve_relay,
                "level_percent": sort_level,
                "was_serving": was_serving_this,
            })

        if not candidates:
            if last_reject_reason is not None:
                source_state["target_reason"] = last_reject_reason[0]
                source_state["blocked_by"] = last_reject_reason[1]
            else:
                source_state["target_reason"] = "no_step"
            continue

        if prioritize_empty:
            # Emptiest wins; sequence index as deterministic tie-breaker.
            candidates.sort(key=lambda c: (c["level_percent"], c["idx"]))
        else:
            candidates.sort(key=lambda c: c["idx"])

        chosen = candidates[0]
        tank = chosen["tank"]
        source_state["current_tank_id"] = chosen["tank_id"]
        source_state["current_tank_name"] = tank.get("name", chosen["tank_id"])
        source_state["current_step_index"] = chosen["idx"]
        source_state["current_route_relay"] = chosen["valve_relay"]
        source_state["desired_active"] = True
        source_state["target_reason"] = "target"
        source_state["blocked_by"] = None
        claimed_by[chosen["tank_id"]] = source_id

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
    default_valve_overlap = float(system.get("default_valve_overlap_seconds", 0) or 0)
    default_valve_close_delay = float(system.get("default_valve_close_delay_seconds", 0) or 0)

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
            source_state["physical_route_relay"] = 0
            source_state["pending_valve_close"] = {}
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
        valve_overlap = float(source.get("valve_overlap_seconds", default_valve_overlap) or 0)
        valve_close_delay = float(source.get("valve_close_delay_seconds", default_valve_close_delay) or 0)

        applied_active = current_active
        if desired_active != current_active:
            since_iso = source_state.get("desired_active_since")
            if since_iso:
                since = _parse_iso(since_iso) or now
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

        # --- Safety-aware valve state machine ---
        #
        # Two goals:
        #   1. On tank switch (same source): keep the OLD valve open for
        #      `valve_overlap` seconds after the NEW valve opens, so the pump
        #      never sees two closed valves at once.
        #   2. On source stop: close the equipment/enable relay first, and keep
        #      the last valve open for `valve_close_delay` seconds afterwards,
        #      so the pump can spin down without pressurizing a closed line.
        #
        # We track per source:
        #   - physical_route_relay: last valve we asked to be open (int)
        #   - pending_valve_close: {relay_str: iso_close_at} valves that must
        #     stay open until their scheduled close time.
        prev_physical = int(source_state.get("physical_route_relay", 0) or 0)
        pending_raw = source_state.get("pending_valve_close") or {}
        pending_valve_close = {}
        for relay_str, close_iso in pending_raw.items():
            try:
                relay_num = int(relay_str)
            except (TypeError, ValueError):
                continue
            if relay_num <= 0:
                continue
            pending_valve_close[str(relay_num)] = close_iso

        if applied_active and current_route_relay > 0:
            # If we just switched target, schedule the old valve for a delayed close.
            if prev_physical and prev_physical != current_route_relay and valve_overlap > 0:
                overlap_at = (now + timedelta(seconds=valve_overlap)).isoformat()
                pending_valve_close[str(prev_physical)] = overlap_at
            # The valve we want open right now must never be in pending_close.
            pending_valve_close.pop(str(current_route_relay), None)
            source_state["physical_route_relay"] = current_route_relay
        else:
            # Source is (or is becoming) inactive. Keep the last valve open for
            # `valve_close_delay` seconds so the pump can wind down safely.
            if prev_physical and str(prev_physical) not in pending_valve_close:
                if valve_close_delay > 0:
                    close_at = (now + timedelta(seconds=valve_close_delay)).isoformat()
                    pending_valve_close[str(prev_physical)] = close_at
                # If there's no delay configured we just close it immediately,
                # which is achieved by NOT adding it to pending_valve_close.

        # Compute which valves must be physically open this cycle.
        open_valves = set()
        if applied_active and current_route_relay > 0:
            open_valves.add(current_route_relay)

        for relay_str in list(pending_valve_close.keys()):
            close_at = _parse_iso(pending_valve_close[relay_str])
            if close_at is None or now >= close_at:
                pending_valve_close.pop(relay_str, None)
                continue
            open_valves.add(int(relay_str))

        # Once the last pending close has expired and we're inactive, we can
        # forget the physical valve tracker.
        if not applied_active and not pending_valve_close:
            source_state["physical_route_relay"] = 0

        source_state["pending_valve_close"] = pending_valve_close

        # Emit valve commands for every route belonging to this source.
        for route in source_routes:
            vr = int(route.get("valve_relay", 0) or 0)
            if vr <= 0:
                continue
            if vr in open_valves:
                relay_results.append(relay_service.relay_on(vr))
            else:
                relay_results.append(relay_service.relay_off(vr))

        # Source enable relay: OFF the moment we go inactive, ON while active.
        # The valve-close-delay above guarantees the pump stops before the
        # remaining valve closes.
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
