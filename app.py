from flask import Flask, render_template, request, redirect, url_for, jsonify

from services.config_service import load_config, load_state, save_config
from services.alarm_service import build_tank_alarms
from services.relay_inventory import (
    get_available_relay_options,
    get_used_relays,
    get_relay_boards,
    get_relay_count,
)


def enrich_tanks(tanks, tank_states):
    enriched = []

    for tank in tanks:
        state = tank_states.get(tank["id"], {})
        calibration = tank.get("calibration", {})
        volume_liters = state.get("volume_liters")
        volume_m3 = round(volume_liters / 1000, 2) if volume_liters is not None else None

        enriched.append({
            **tank,
            "capacity_liters": tank.get("capacity_liters", 0),
            "capacity_m3": round(tank.get("capacity_liters", 0) / 1000, 2),
            "calibration": {
                "distance_empty_cm": calibration.get("distance_empty_cm", 150),
                "distance_full_cm": calibration.get("distance_full_cm", 20),
            },
            "level_percent": state.get("level_percent", 0),
            "status": state.get("status", "unknown"),
            "distance_cm": state.get("distance_cm"),
            "volume_liters": volume_liters,
            "volume_m3": volume_m3,
            "sensor_ok": state.get("sensor_ok", False),
            "last_update": state.get("last_update"),
            "filling_by": state.get("filling_by"),
            "filling_by_name": state.get("filling_by_name"),
            "filling_by_sources": state.get("filling_by_sources", []) or [],
            "relay_empty_on": bool(state.get("relay_empty_on", False)),
            "relay_full_on": bool(state.get("relay_full_on", False)),
        })

    return enriched


def enrich_sources(sources, source_states):
    source_name_by_id = {s.get("id"): s.get("name", s.get("id")) for s in sources}
    enriched = []

    for source in sources:
        state = source_states.get(source["id"], {})
        blocked_by_id = state.get("blocked_by")
        enriched.append({
            **source,
            "active": state.get("active", False),
            "status": state.get("status", "idle"),
            "current_tank_id": state.get("current_tank_id"),
            "current_tank_name": state.get("current_tank_name"),
            "current_step_index": state.get("current_step_index"),
            "current_route_relay": state.get("current_route_relay", 0),
            "target_reason": state.get("target_reason"),
            "blocked_by": blocked_by_id,
            "blocked_by_name": source_name_by_id.get(blocked_by_id) if blocked_by_id else None,
            "last_update": state.get("last_update")
        })

    return enriched


def get_tank_by_id(tanks, tank_id):
    return next((tank for tank in tanks if tank["id"] == tank_id), None)


def get_source_by_id(sources, source_id):
    return next((source for source in sources if source.get("id") == source_id), None)


def build_dashboard_data():
    config = load_config()
    state = load_state()

    tanks = enrich_tanks(config.get("tanks", []), state.get("tanks", {}))
    sources = enrich_sources(config.get("sources", []), state.get("sources", {}))
    routes = config.get("routes", [])
    alarms = build_tank_alarms(config, state)

    return {
        "tank_count": len(tanks),
        "source_count": len(sources),
        "route_count": len(routes),
        "system_mode": state.get("system_mode", "unknown"),
        "tanks": tanks,
        "sources": sources,
        "routes": routes,
        "alarms": alarms,
        "state_last_updated": state.get("state_last_updated")
    }


def build_empty_tank():
    return {
        "id": "",
        "name": "",
        "enabled": True,
        "capacity_liters": 1000,
        "calibration": {
            "distance_empty_cm": 150,
            "distance_full_cm": 20
        },
        "sensor": {
            "type": "a02yyuw",
            "controller": "",
            "endpoint": "",
            "method": "GET",
            "level_key": "distance_cm"
        },
        "thresholds": {
            "empty_percent": 15,
            "full_percent": 90
        },
        "hysteresis_cm": 0,
        "relays": {
            "empty": 0,
            "full": 0
        }
    }


def build_empty_source():
    return {
        "id": "",
        "name": "",
        "enabled": True,
        "enable_relay": 0,
        "startup_delay_seconds": 0,
        "stop_delay_seconds": 0
    }


def populate_tank_from_form(tank, form):
    tank["id"] = form.get("id", "").strip()
    tank["name"] = form.get("name", "").strip()
    tank["enabled"] = form.get("enabled") == "on"
    tank["capacity_liters"] = int(form.get("capacity_liters", 0))

    tank["calibration"] = {
        "distance_empty_cm": int(form.get("distance_empty_cm", 0)),
        "distance_full_cm": int(form.get("distance_full_cm", 0)),
    }

    tank["sensor"] = {
        "type": form.get("sensor_type", "").strip(),
        "controller": form.get("sensor_controller", "").strip(),
        "endpoint": form.get("sensor_endpoint", "").strip(),
        "method": form.get("sensor_method", "GET").strip(),
        "level_key": form.get("sensor_level_key", "").strip(),
    }

    tank["thresholds"] = {
        "empty_percent": int(form.get("empty_percent", 0)),
        "full_percent": int(form.get("full_percent", 0)),
    }

    tank["hysteresis_cm"] = float(form.get("hysteresis_cm", 0) or 0)

    tank["relays"] = {
        "empty": int(form.get("relay_empty", 0) or 0),
        "full": int(form.get("relay_full", 0) or 0),
    }

    return tank


def populate_source_from_form(source, form):
    source["id"] = form.get("id", "").strip()
    source["name"] = form.get("name", "").strip()
    source["enabled"] = form.get("enabled") == "on"
    source["enable_relay"] = int(form.get("enable_relay", 0) or 0)
    source["startup_delay_seconds"] = float(form.get("startup_delay_seconds", 0) or 0)
    source["stop_delay_seconds"] = float(form.get("stop_delay_seconds", 0) or 0)
    return source


def _parse_index(raw_value, length):
    try:
        index = int(raw_value)
    except (TypeError, ValueError):
        return None
    if index < 0 or index >= length:
        return None
    return index


def _validate_tank_relays(config, tank):
    empty = tank.get("relays", {}).get("empty", 0) or 0
    full = tank.get("relays", {}).get("full", 0) or 0

    if empty and full and empty == full:
        return "Os relés 'vazio' e 'cheio' têm que ser diferentes"

    used = get_used_relays(config, exclude=[tank])

    for label, value in (("vazio", empty), ("cheio", full)):
        if value <= 0:
            continue
        if value in used:
            return f"Relé {value} ({label}) já está em uso"

    return None


def _validate_source_relay(config, source):
    relay = source.get("enable_relay", 0) or 0
    if relay <= 0:
        return None

    used = get_used_relays(config, exclude=[source])

    if relay in used:
        return f"Relé {relay} já está em uso"

    return None


def find_route(routes, source_id, tank_id):
    for route in routes:
        if route.get("source_id") == source_id and route.get("tank_id") == tank_id:
            return route
    return None


def _validate_route_relay(config, route):
    relay = route.get("valve_relay", 0) or 0
    if relay <= 0:
        return None
    used = get_used_relays(config, exclude=[route])
    if relay in used:
        return f"Relé {relay} já está em uso"
    return None


def _normalized_boards_list(config):
    """Return editable list of boards. Migrates legacy relay_board into relay_boards."""
    if "relay_boards" in config and config["relay_boards"]:
        return config["relay_boards"]
    boards = get_relay_boards(config)
    return list(boards)


def _next_start_relay(boards):
    if not boards:
        return 1
    last = boards[-1]
    return int(last.get("start_relay", 1)) + int(last.get("channels", 0))


def _recompute_start_relays(boards):
    offset = 1
    for board in boards:
        board["start_relay"] = offset
        offset += int(board.get("channels", 0))


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        data = build_dashboard_data()
        return render_template("dashboard.html", **data)

    @app.route("/api/state")
    def api_state():
        return jsonify(build_dashboard_data())

    @app.route("/tanks")
    def tanks_page():
        config = load_config()
        state = load_state()

        tanks = enrich_tanks(config.get("tanks", []), state.get("tanks", {}))
        return render_template(
            "tanks.html",
            tanks=tanks,
            tank_count=len(tanks),
            state_last_updated=state.get("state_last_updated")
        )

    @app.route("/tanks/new", methods=["GET", "POST"])
    def new_tank():
        config = load_config()
        tanks = config.get("tanks", [])
        tank = build_empty_tank()

        if request.method == "POST":
            populate_tank_from_form(tank, request.form)

            if not tank["id"]:
                return "Tank ID is required", 400

            existing = get_tank_by_id(tanks, tank["id"])
            if existing is not None:
                return "Tank ID already exists", 400

            relay_error = _validate_tank_relays(config, tank)
            if relay_error:
                return relay_error, 400

            tanks.append(tank)
            save_config(config)
            return redirect(url_for("tanks_page"))

        return render_template(
            "tank_form.html",
            tank=tank,
            form_mode="new",
            relay_options_empty=get_available_relay_options(config),
            relay_options_full=get_available_relay_options(config),
        )

    @app.route("/tanks/<tank_id>/edit", methods=["GET", "POST"])
    def edit_tank(tank_id):
        config = load_config()
        tanks = config.get("tanks", [])
        tank = get_tank_by_id(tanks, tank_id)

        if tank is None:
            return "Tank not found", 404

        if "capacity_liters" not in tank:
            tank["capacity_liters"] = 1000

        if "calibration" not in tank:
            tank["calibration"] = {
                "distance_empty_cm": 150,
                "distance_full_cm": 20,
            }

        current_empty = tank.get("relays", {}).get("empty", 0)
        current_full = tank.get("relays", {}).get("full", 0)

        if request.method == "POST":
            original_id = tank["id"]
            populate_tank_from_form(tank, request.form)

            if not tank["id"]:
                return "Tank ID is required", 400

            if tank["id"] != original_id:
                existing = get_tank_by_id(tanks, tank["id"])
                if existing is not None and existing is not tank:
                    return "Tank ID already exists", 400

            relay_error = _validate_tank_relays(config, tank)
            if relay_error:
                return relay_error, 400

            save_config(config)
            return redirect(url_for("tanks_page"))

        return render_template(
            "tank_form.html",
            tank=tank,
            form_mode="edit",
            relay_options_empty=get_available_relay_options(config, include=[current_empty], exclude=[tank]),
            relay_options_full=get_available_relay_options(config, include=[current_full], exclude=[tank]),
        )

    @app.route("/sources")
    def sources_page():
        config = load_config()
        state = load_state()

        sources = enrich_sources(config.get("sources", []), state.get("sources", {}))
        return render_template(
            "sources.html",
            sources=sources,
            source_count=len(sources),
            state_last_updated=state.get("state_last_updated")
        )

    @app.route("/sources/new", methods=["GET", "POST"])
    def new_source():
        config = load_config()
        sources = config.get("sources", [])
        source = build_empty_source()

        if request.method == "POST":
            populate_source_from_form(source, request.form)

            if not source["id"]:
                return "Source ID is required", 400

            existing = get_source_by_id(sources, source["id"])
            if existing is not None:
                return "Source ID already exists", 400

            relay_error = _validate_source_relay(config, source)
            if relay_error:
                return relay_error, 400

            sources.append(source)
            save_config(config)
            return redirect(url_for("sources_page"))

        return render_template(
            "source_form.html",
            source=source,
            form_mode="new",
            relay_options=get_available_relay_options(config),
        )

    @app.route("/sources/<source_id>/edit", methods=["GET", "POST"])
    def edit_source(source_id):
        config = load_config()
        sources = config.get("sources", [])
        source = get_source_by_id(sources, source_id)

        if source is None:
            return "Source not found", 404

        if "enable_relay" not in source:
            source["enable_relay"] = source.get("valve_relay", 0) or 0

        current_enable = source.get("enable_relay", 0)

        if request.method == "POST":
            original_id = source["id"]
            populate_source_from_form(source, request.form)

            if not source["id"]:
                return "Source ID is required", 400

            if source["id"] != original_id:
                existing = get_source_by_id(sources, source["id"])
                if existing is not None and existing is not source:
                    return "Source ID already exists", 400

            relay_error = _validate_source_relay(config, source)
            if relay_error:
                return relay_error, 400

            save_config(config)
            return redirect(url_for("sources_page"))

        return render_template(
            "source_form.html",
            source=source,
            form_mode="edit",
            relay_options=get_available_relay_options(config, include=[current_enable], exclude=[source]),
        )

    @app.route("/rules")
    def rules_page():
        config = load_config()
        state = load_state()

        tanks = config.get("tanks", [])
        sources = config.get("sources", [])
        routes = config.get("routes", [])
        tank_states = state.get("tanks", {})

        available_tanks = [
            {"id": t["id"], "name": t.get("name", t["id"])}
            for t in tanks
        ]

        enriched_sources = []
        source_states = state.get("sources", {})
        for source in sources:
            source_id = source.get("id")
            src_state = source_states.get(source_id, {})
            current_step_index = src_state.get("current_step_index")
            source_status = src_state.get("status", "idle")
            source_active = bool(src_state.get("active", False))
            source_target_reason = src_state.get("target_reason")
            source_blocked_by_id = src_state.get("blocked_by")
            source_blocked_by_name = None
            if source_blocked_by_id:
                blocking_source = get_source_by_id(sources, source_blocked_by_id)
                if blocking_source:
                    source_blocked_by_name = blocking_source.get("name", source_blocked_by_id)

            steps = []
            for step_index, step in enumerate(source.get("sequence", [])):
                tank_id = step.get("tank_id")
                tank = get_tank_by_id(tanks, tank_id)
                tank_state = tank_states.get(tank_id, {})
                route = find_route(routes, source_id, tank_id)
                has_route = route is not None and (route.get("valve_relay", 0) or 0) > 0
                steps.append({
                    "tank_id": tank_id,
                    "tank_name": tank.get("name", tank_id) if tank else tank_id,
                    "tank_exists": tank is not None,
                    "enabled": step.get("enabled", True),
                    "level_percent": tank_state.get("level_percent"),
                    "status": tank_state.get("status", "unknown"),
                    "has_route": has_route,
                    "is_current": (current_step_index == step_index) and source_active,
                    "is_target": (current_step_index == step_index) and not source_active,
                })
            enriched_sources.append({
                "id": source_id,
                "name": source.get("name", source.get("id")),
                "enabled": source.get("enabled", True),
                "mode": source.get("mode", "sequence"),
                "repeat_sequence": source.get("repeat_sequence", True),
                "steps": steps,
                "runtime_status": source_status,
                "active": source_active,
                "target_reason": source_target_reason,
                "blocked_by_name": source_blocked_by_name,
                "current_tank_name": src_state.get("current_tank_name"),
                "current_route_relay": src_state.get("current_route_relay", 0),
            })

        return render_template(
            "rules.html",
            rules=config.get("rules", {}),
            sources=enriched_sources,
            available_tanks=available_tanks,
        )

    @app.route("/rules/flags", methods=["POST"])
    def rules_flags():
        config = load_config()
        rules = config.setdefault("rules", {})
        rule_keys = [
            "allow_multiple_sources_per_tank",
            "allow_multiple_tanks_per_source",
            "skip_disabled_tanks",
            "skip_full_tanks",
            "prioritize_empty_tanks",
        ]
        for key in rule_keys:
            rules[key] = request.form.get(key) == "on"

        save_config(config)
        return redirect(url_for("rules_page"))

    @app.route("/rules/sources/<source_id>", methods=["POST"])
    def rules_source_action(source_id):
        config = load_config()
        source = get_source_by_id(config.get("sources", []), source_id)

        if source is None:
            return "Source not found", 404

        source.setdefault("sequence", [])
        source.setdefault("mode", "sequence")
        source.setdefault("repeat_sequence", True)

        action = request.form.get("action", "").strip()
        sequence = source["sequence"]

        if action == "add_tank":
            tank_id = request.form.get("tank_id", "").strip()
            if not tank_id:
                return "tank_id is required", 400
            if get_tank_by_id(config.get("tanks", []), tank_id) is None:
                return "Tank not found", 404
            sequence.append({"tank_id": tank_id, "enabled": True})

        elif action == "remove_step":
            index = _parse_index(request.form.get("index"), len(sequence))
            if index is None:
                return "Invalid index", 400
            sequence.pop(index)

        elif action == "move_up":
            index = _parse_index(request.form.get("index"), len(sequence))
            if index is None or index == 0:
                return redirect(url_for("rules_page"))
            sequence[index - 1], sequence[index] = sequence[index], sequence[index - 1]

        elif action == "move_down":
            index = _parse_index(request.form.get("index"), len(sequence))
            if index is None or index >= len(sequence) - 1:
                return redirect(url_for("rules_page"))
            sequence[index + 1], sequence[index] = sequence[index], sequence[index + 1]

        elif action == "toggle_step":
            index = _parse_index(request.form.get("index"), len(sequence))
            if index is None:
                return "Invalid index", 400
            sequence[index]["enabled"] = not sequence[index].get("enabled", True)

        elif action == "update_settings":
            source["repeat_sequence"] = request.form.get("repeat_sequence") == "on"
            source["enabled"] = request.form.get("enabled") == "on"
            mode = request.form.get("mode", "sequence").strip()
            if mode in ("sequence", "manual"):
                source["mode"] = mode

        else:
            return "Unknown action", 400

        save_config(config)
        return redirect(url_for("rules_page"))

    @app.route("/routes")
    def routes_page():
        config = load_config()
        tanks = config.get("tanks", [])
        sources = config.get("sources", [])
        routes = config.get("routes", [])

        tank_by_id = {t["id"]: t for t in tanks}

        source_cards = []
        for source in sources:
            source_id = source["id"]
            source_routes = []

            for index, route in enumerate(routes):
                if route.get("source_id") != source_id:
                    continue
                tank_id = route.get("tank_id")
                tank = tank_by_id.get(tank_id)
                source_routes.append({
                    "index": index,
                    "source_id": source_id,
                    "tank_id": tank_id,
                    "tank_name": tank.get("name", tank_id) if tank else tank_id,
                    "tank_exists": tank is not None,
                    "enabled": route.get("enabled", True),
                    "valve_relay": route.get("valve_relay", 0) or 0,
                    "relay_options": get_available_relay_options(
                        config, include=[route.get("valve_relay", 0)], exclude=[route]
                    ),
                })

            existing_tank_ids = {r["tank_id"] for r in source_routes}
            available_tanks = [
                {"id": t["id"], "name": t.get("name", t["id"])}
                for t in tanks
                if t["id"] not in existing_tank_ids
            ]

            source_cards.append({
                "id": source_id,
                "name": source.get("name", source_id),
                "enabled": source.get("enabled", True),
                "routes": source_routes,
                "available_tanks": available_tanks,
                "add_relay_options": get_available_relay_options(config),
            })

        return render_template(
            "routes.html",
            source_cards=source_cards,
            source_count=len(sources),
            route_count=len(routes),
        )

    @app.route("/routes/add", methods=["POST"])
    def add_route():
        config = load_config()
        source_id = request.form.get("source_id", "").strip()
        tank_id = request.form.get("tank_id", "").strip()

        try:
            valve_relay = int(request.form.get("valve_relay", 0) or 0)
        except ValueError:
            return "Relé inválido", 400

        if not source_id or not tank_id:
            return "source_id e tank_id são obrigatórios", 400

        if get_source_by_id(config.get("sources", []), source_id) is None:
            return "Source not found", 404

        if get_tank_by_id(config.get("tanks", []), tank_id) is None:
            return "Tank not found", 404

        routes = config.setdefault("routes", [])
        if find_route(routes, source_id, tank_id) is not None:
            return "Rota já existe para este par fonte/tanque", 400

        route = {
            "source_id": source_id,
            "tank_id": tank_id,
            "enabled": True,
            "valve_relay": valve_relay,
        }

        relay_error = _validate_route_relay(config, route)
        if relay_error:
            return relay_error, 400

        routes.append(route)
        save_config(config)
        return redirect(url_for("routes_page"))

    @app.route("/routes/action", methods=["POST"])
    def route_action():
        config = load_config()
        routes = config.get("routes", [])

        source_id = request.form.get("source_id", "").strip()
        tank_id = request.form.get("tank_id", "").strip()
        action = request.form.get("action", "").strip()

        route = find_route(routes, source_id, tank_id)
        if route is None:
            return "Route not found", 404

        if action == "remove":
            routes.remove(route)

        elif action == "toggle":
            route["enabled"] = not route.get("enabled", True)

        elif action == "update_relay":
            try:
                new_relay = int(request.form.get("valve_relay", 0) or 0)
            except ValueError:
                return "Relé inválido", 400
            route["valve_relay"] = new_relay
            relay_error = _validate_route_relay(config, route)
            if relay_error:
                return relay_error, 400

        else:
            return "Unknown action", 400

        save_config(config)
        return redirect(url_for("routes_page"))

    @app.route("/settings")
    def settings_page():
        config = load_config()
        boards = get_relay_boards(config)
        used = get_used_relays(config)
        boards_view = []
        for board in boards:
            start = board["start_relay"]
            end = start + board["channels"] - 1
            used_in_board = sum(1 for r in used if start <= r <= end)
            boards_view.append({
                **board,
                "end_relay": end,
                "used_channels": used_in_board,
                "free_channels": board["channels"] - used_in_board,
            })

        return render_template(
            "settings.html",
            system=config.get("system", {}),
            boards=boards_view,
            total_relays=get_relay_count(config),
        )

    @app.route("/settings/system", methods=["POST"])
    def save_system_settings():
        config = load_config()
        system = config.setdefault("system", {})

        def _float_or_default(name, default):
            raw = request.form.get(name, "")
            try:
                return float(raw)
            except (TypeError, ValueError):
                return default

        system["decision_interval_seconds"] = _float_or_default(
            "decision_interval_seconds", system.get("decision_interval_seconds", 5)
        )
        system["poll_interval_seconds"] = _float_or_default(
            "poll_interval_seconds", system.get("poll_interval_seconds", 10)
        )
        system["sensor_request_timeout_seconds"] = _float_or_default(
            "sensor_request_timeout_seconds", system.get("sensor_request_timeout_seconds", 2)
        )
        system["default_hysteresis_cm"] = _float_or_default(
            "default_hysteresis_cm", system.get("default_hysteresis_cm", 0)
        )
        system["default_source_startup_delay_seconds"] = _float_or_default(
            "default_source_startup_delay_seconds",
            system.get("default_source_startup_delay_seconds", 0),
        )
        system["default_source_stop_delay_seconds"] = _float_or_default(
            "default_source_stop_delay_seconds",
            system.get("default_source_stop_delay_seconds", 0),
        )
        system["safe_mode_on_error"] = request.form.get("safe_mode_on_error") == "on"

        save_config(config)
        return redirect(url_for("settings_page"))

    @app.route("/settings/boards/add", methods=["POST"])
    def add_relay_board():
        config = load_config()
        boards = _normalized_boards_list(config)

        board_id = request.form.get("id", "").strip()
        if not board_id:
            return "board id é obrigatório", 400

        if any(b.get("id") == board_id for b in boards):
            return "Já existe um módulo com esse id", 400

        try:
            channels = int(request.form.get("channels", 0) or 0)
        except ValueError:
            return "channels inválido", 400
        if channels <= 0:
            return "channels tem de ser > 0", 400

        try:
            port = int(request.form.get("port", 502) or 502)
            unit_id = int(request.form.get("unit_id", 1) or 1)
        except ValueError:
            return "port/unit_id inválidos", 400

        start_relay = _next_start_relay(boards)

        boards.append({
            "id": board_id,
            "name": request.form.get("name", "").strip() or f"Módulo {board_id}",
            "type": request.form.get("type", "").strip(),
            "host": request.form.get("host", "").strip(),
            "port": port,
            "unit_id": unit_id,
            "channels": channels,
            "start_relay": start_relay,
        })

        config["relay_boards"] = boards
        config.pop("relay_board", None)
        save_config(config)
        return redirect(url_for("settings_page"))

    @app.route("/settings/boards/<board_id>", methods=["POST"])
    def update_relay_board(board_id):
        config = load_config()
        boards = _normalized_boards_list(config)
        board = next((b for b in boards if b.get("id") == board_id), None)
        if board is None:
            return "Módulo não encontrado", 404

        action = request.form.get("action", "").strip()

        if action == "delete":
            start = board.get("start_relay", 1)
            channels = board.get("channels", 0)
            used = get_used_relays(config)
            if any(start <= r < start + channels for r in used):
                return "Não é possível remover: existem relés deste módulo em uso", 400
            boards.remove(board)
            _recompute_start_relays(boards)
            config["relay_boards"] = boards
            config.pop("relay_board", None)
            save_config(config)
            return redirect(url_for("settings_page"))

        if action == "update":
            try:
                channels = int(request.form.get("channels", board["channels"]) or board["channels"])
                port = int(request.form.get("port", board["port"]) or board["port"])
                unit_id = int(request.form.get("unit_id", board["unit_id"]) or board["unit_id"])
            except ValueError:
                return "Valores numéricos inválidos", 400

            if channels <= 0:
                return "channels tem de ser > 0", 400

            # Prevent shrinking below used channels
            start = board.get("start_relay", 1)
            used = get_used_relays(config)
            max_used_in_board = max(
                (r for r in used if start <= r < start + board["channels"]),
                default=start - 1,
            )
            if max_used_in_board >= start + channels:
                return f"Não é possível reduzir para {channels} canais: relé {max_used_in_board} está em uso", 400

            board["name"] = request.form.get("name", board.get("name", "")).strip() or board.get("name", "")
            board["type"] = request.form.get("type", board.get("type", "")).strip()
            board["host"] = request.form.get("host", board.get("host", "")).strip()
            board["port"] = port
            board["unit_id"] = unit_id
            board["channels"] = channels

            _recompute_start_relays(boards)
            config["relay_boards"] = boards
            config.pop("relay_board", None)
            save_config(config)
            return redirect(url_for("settings_page"))

        return "Ação desconhecida", 400

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)