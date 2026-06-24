from flask import Flask, render_template

from services.config_service import load_config, load_state


def enrich_tanks(tanks, tank_states):
    enriched = []

    for tank in tanks:
        state = tank_states.get(tank["id"], {})
        enriched.append({
            **tank,
            "level_percent": state.get("level_percent", 0),
            "status": state.get("status", "unknown")
        })

    return enriched


def enrich_sources(sources, source_states):
    enriched = []

    for source in sources:
        state = source_states.get(source["id"], {})
        enriched.append({
            **source,
            "active": state.get("active", False),
            "current_tank": state.get("current_tank")
        })

    return enriched


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        config = load_config()
        state = load_state()

        tanks = enrich_tanks(
            config.get("tanks", []),
            state.get("tanks", {})
        )
        sources = enrich_sources(
            config.get("sources", []),
            state.get("sources", {})
        )
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

        tanks = enrich_tanks(
            config.get("tanks", []),
            state.get("tanks", {})
        )

        return render_template("tanks.html", tanks=tanks)

    @app.route("/sources")
    def sources_page():
        config = load_config()
        state = load_state()

        sources = enrich_sources(
            config.get("sources", []),
            state.get("sources", {})
        )

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