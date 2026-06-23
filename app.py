from flask import Flask, render_template

from services.config_service import load_config, load_state


def create_app():
    app = Flask(__name__)

    @app.route("/")
    def index():
        config = load_config()
        state = load_state()
        return render_template(
            "dashboard.html",
            tank_count=len(config.get("tanks", [])),
            source_count=len(config.get("sources", [])),
            route_count=len(config.get("routes", [])),
            system_mode=state.get("system_mode", "unknown"),
        )

    return app


app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
