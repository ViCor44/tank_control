from flask import Flask, render_template, request, redirect, url_for

from services.config_service import load_config, load_state, save_config


def enrich_tanks(tanks, tank_states):
    enriched = []

    for tank in tanks:
        state = tank_states.get(tank["id"], {})
        calibration = tank.get("calibration", {})

        enriched.append({
            **tank,
            "capacity_liters": tank.get("capacity_liters", 0),
            "calibration": {
                "distance_empty_cm": calibration.get("distance_empty_cm", 150),
                "distance_full_cm": calibration.get("distance_full_cm", 20),
            },
            "level_percent": state.get("level_percent", 0),
            "status": state.get("status", "unknown"),
            "distance_cm": state.get("distance_cm"),
            "volume_liters": state.get("volume_liters"),
            "sensor_ok": state.get("sensor_ok", False),
        })

    return enriched


def enrich_sources(sources, source_states):
    enriched = []

    for source in sources:
        state = source_states.get(source["id"], {})
        enriched.append({
            **source,
            "active": state.get("active", False),
            "current_tank": state.get("current_tank"),
        })

    return enriched


def get_tank_by_id(tanks, tank_id):
    return next((tank for tank in tanks if tank["id"] == tank_id), None)


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        config = load_config()
        state = load_state()

        tanks = enrich_tanks(config.get("tanks", []), state.get("tanks", {}))
        sources = enrich_sources(config.get("sources", []), state.get("sources", {}))
        routes = config.get("routes", [])
        alarms = state.get("alarms", [])

        return render_template(
            "dashboard.html",
            tank_count=len(tanks),
            source_count=len(sources),
            route_count=len(routes),
            system_mode=state.get("system_mode", "unknown"),
            tanks=tanks,
            sources=sources,
            routes=routes,
            alarms=alarms,
        )

    @app.route("/tanks")
    def tanks_page():
        config = load_config()
        state = load_state()

        tanks = enrich_tanks(config.get("tanks", []), state.get("tanks", {}))
        return render_template("tanks.html", tanks=tanks)

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

        if request.method == "POST":
            tank["name"] = request.form.get("name", "").strip()
            tank["enabled"] = request.form.get("enabled") == "on"
            tank["capacity_liters"] = int(request.form.get("capacity_liters", 0))
            tank["calibration"] = {
                "distance_empty_cm": int(request.form.get("distance_empty_cm", 0)),
                "distance_full_cm": int(request.form.get("distance_full_cm", 0)),
            }
            tank["sensor"]["type"] = request.form.get("sensor_type", "").strip()
            tank["sensor"]["controller"] = request.form.get("sensor_controller", "").strip()
            tank["sensor"]["endpoint"] = request.form.get("sensor_endpoint", "").strip()
            tank["sensor"]["method"] = request.form.get("sensor_method", "GET").strip()
            tank["sensor"]["level_key"] = request.form.get("sensor_level_key", "").strip()
            tank["thresholds"]["empty_percent"] = int(request.form.get("empty_percent", 0))
            tank["thresholds"]["full_percent"] = int(request.form.get("full_percent", 0))
            tank["relays"]["empty"] = int(request.form.get("relay_empty", 0))
            tank["relays"]["full"] = int(request.form.get("relay_full", 0))

            save_config(config)
            return redirect(url_for("tanks_page"))

        return render_template("tank_form.html", tank=tank)

    @app.route("/sources")
    def sources_page():
        config = load_config()
        state = load_state()

        sources = enrich_sources(config.get("sources", []), state.get("sources", {}))
        return render_template("sources.html", sources=sources)

    @app.route("/rules")
    def rules_page():
        config = load_config()
        return render_template("rules.html", rules=config.get("rules", {}))

    @app.route("/settings")
    def settings_page():
        config = load_config()
        return render_template(
            "settings.html",
            system=config.get("system", {}),
            relay_board=config.get("relay_board", {}),
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)