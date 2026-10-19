"""Routen und API. Keine eigene Anmeldung (Authentik Forward Auth davor)."""

import logging

from flask import Flask, jsonify, request, send_from_directory

from . import __version__
from .config import Config

log = logging.getLogger(__name__)


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


def create_app(cfg=None, startup_check=False):
    cfg = cfg or Config.from_env()

    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.extensions["zeitbuch"] = {"cfg": cfg}

    # ------------------------------------------------------------ Allgemein
    @app.before_request
    def require_json():
        # Schutz gegen Cross-Site-Formulare: die können kein application/json senden.
        if request.method in ("POST", "PUT", "DELETE") and not request.is_json:
            raise ApiError(415, "Content-Type: application/json erforderlich")

    @app.after_request
    def headers(resp):
        resp.headers["X-Content-Type-Options"] = "nosniff"
        resp.headers["Referrer-Policy"] = "no-referrer"
        resp.headers["Content-Security-Policy"] = (
            "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'"
        )
        if request.path.startswith("/api/"):
            resp.headers["Cache-Control"] = "no-store"
        return resp

    @app.errorhandler(ApiError)
    def api_error(e):
        return jsonify({"error": e.message}), e.status

    # ------------------------------------------------------------ Seiten
    @app.get("/")
    def index():
        resp = send_from_directory(app.static_folder, "index.html")
        resp.headers["Cache-Control"] = "no-cache"
        return resp

    @app.get("/healthz")
    def healthz():
        return "ok\n", 200, {"Content-Type": "text/plain"}

    @app.get("/api/info")
    def info():
        return jsonify({"version": __version__})

    return app
