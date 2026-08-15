"""Flask application serving the forecast page."""

from __future__ import annotations

import logging
from datetime import datetime

from flask import Flask, abort, redirect, render_template, request, url_for

from .config import UK_TZ, Config, ConfigError
from .datahub import DataHubError
from .season import palette_for
from .service import ForecastService

log = logging.getLogger(__name__)


def create_app(config: Config | None = None, service: ForecastService | None = None) -> Flask:
    app = Flask(__name__)
    app.config["WBL"] = config or Config.from_env()
    app.config["SERVICE"] = service or ForecastService(app.config["WBL"])

    def svc() -> ForecastService:
        return app.config["SERVICE"]

    @app.context_processor
    def season() -> dict[str, str]:
        """Every page carries the name of its masthead palette: the season, or a
        holiday's own colours on the days that have them."""
        return {"season": palette_for(datetime.now(UK_TZ).date())}

    @app.route("/")
    def index():
        site = svc().default_site()
        if site is None:
            abort(503, "No forecast sites available.")
        return redirect(url_for("forecast", site_id=site.id))

    @app.route("/search")
    def search():
        query = (request.args.get("q") or "").strip()
        results = svc().search(query) if query else []
        if len(results) == 1:
            return redirect(url_for("forecast", site_id=results[0].site.id, name=query))
        return render_template("search.html", query=query, results=results)

    @app.route("/forecast/<site_id>")
    def forecast(site_id: str):
        service = svc()
        site = service.site(site_id)
        if site is None:
            abort(404, f"Unknown site {site_id}")
        # A name carried over from a search beats the reverse-geocoded one.
        if request.args.get("name"):
            site = site.named(request.args["name"])

        data = service.forecast(site)
        selected = data.day(request.args.get("date"))
        return render_template("forecast.html", forecast=data, day=selected, site=site)

    @app.errorhandler(DataHubError)
    def datahub_error(exc: DataHubError):
        log.error("DataHub error: %s", exc)
        return render_template("error.html", message=str(exc)), 502

    @app.errorhandler(ConfigError)
    def config_error(exc: ConfigError):
        return render_template("error.html", message=str(exc)), 500

    return app
