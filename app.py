from flask import Flask, render_template

from services.config_service import load_config, load_state


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        config = load_config()
        state = load_state()
        tanks = config.get("tanks", [])
        sources = config.get("sources", [])
        routes = config.get("routes", [])

        return render_template(
            "dashboard.html",
            tank_count=len(tanks),
            source_count=len(sources),
            route_count=len(routes),
            system_mode=state.get("system_mode", "unknown"),
            tanks=tanks,
            sources=sources,
            routes=routes,
        )

    @app.route("/tanks")
    def tanks_page():
        config = load_config()
        return render_template("tanks.html", tanks=config.get("tanks", []))

    @app.route("/sources")
    def sources_page():
        config = load_config()
        return render_template("sources.html", sources=config.get("sources", []))

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