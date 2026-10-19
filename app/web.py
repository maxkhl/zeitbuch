"""Routen und API. Keine eigene Anmeldung (Authentik Forward Auth davor)."""

import logging
import re
import threading

from flask import Flask, jsonify, request, send_from_directory

from . import __version__
from .backends import BackendError, create_youtrack
from .checks import check_youtrack_config
from .config import Config
from .db import Database
from .month import month_view, parse_month

log = logging.getLogger(__name__)

SHORT_NAME = re.compile(r"[A-Za-z0-9_-]{1,50}")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class CheckState:
    """Ergebnis der letzten Konfigurationsprüfung (Start und Verbindungstest)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.result = {"done": False, "youtrack": None, "config": []}

    def set(self, result):
        with self.lock:
            self.result = {"done": True, **result}

    def get(self):
        with self.lock:
            return dict(self.result)


def create_app(cfg=None, youtrack=None, startup_check=False):
    cfg = cfg or Config.from_env()
    youtrack = youtrack or create_youtrack(cfg)
    db = Database(cfg.db)
    check = CheckState()

    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.extensions["zeitbuch"] = {"cfg": cfg, "db": db, "youtrack": youtrack, "check": check}

    def run_check():
        result = {"youtrack": None, "config": []}
        try:
            result["youtrack"] = {"ok": True, "user": youtrack.ping()}
        except BackendError as e:
            result["youtrack"] = {"ok": False, "error": str(e)}
        projects = [p["short_name"] for p in db.projects()]
        if result["youtrack"]["ok"]:
            result["config"] = check_youtrack_config(youtrack, projects, cfg.yt)
        for msg in result["config"]:
            log.warning("Konfigurationsprüfung: %s", msg)
        check.set(result)
        return check.get()

    if startup_check:
        # Im Hintergrund: Die App startet auch, wenn YouTrack gerade nicht erreichbar ist.
        threading.Thread(target=run_check, name="startup-check", daemon=True).start()

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

    @app.errorhandler(BackendError)
    def backend_error(e):
        return jsonify({"error": str(e)}), 502

    def body():
        data = request.get_json(silent=True)
        return data if isinstance(data, dict) else {}

    def month_arg(value):
        try:
            parse_month(value)
        except ValueError as e:
            raise ApiError(400, str(e))
        return value

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
        return jsonify({
            "version": __version__,
            "mock": {"youtrack": youtrack.mode == "mock"},
            "check": check.get(),
        })

    # ------------------------------------------------------------ Monat
    @app.get("/api/month")
    def month():
        return jsonify(month_view(youtrack, db, cfg.yt, month_arg(request.args.get("month"))))

    @app.post("/api/transfer")
    def transfer():
        data = body()
        row = data.get("row")
        if not isinstance(row, dict):
            raise ApiError(400, "Zeile fehlt")
        key = row.get("key")
        if not isinstance(key, str) or "|" not in key:
            raise ApiError(400, "Ungültiger Zeilenschlüssel")
        if not data.get("done"):
            db.unmark_transfer(key)
            return jsonify({"ok": True})
        day, target = key.split("|", 1)
        try:
            month_arg(day[:7])
            minutes = int(row.get("minutes"))
        except (TypeError, ValueError):
            raise ApiError(400, "Ungültige Zeile")
        ext_key = row.get("ext_key")
        if ext_key is not None and not isinstance(ext_key, str):
            raise ApiError(400, "Ungültige externe Kennung")
        db.mark_transfer(key, day, target, minutes, ext_key,
                         str(row.get("label") or ""), str(row.get("description") or ""))
        return jsonify({"ok": True})

    # ------------------------------------------------------------ Einstellungen
    @app.get("/api/settings")
    def settings():
        available, error = [], None
        try:
            available = youtrack.projects()
        except BackendError as e:
            error = str(e)
        return jsonify({
            "projects": db.projects(),
            "youtrack_projects": available,
            "youtrack_error": error,
            "field_config": cfg.yt.as_list(),
        })

    @app.post("/api/settings/projects")
    def save_project():
        data = body()
        short = data.get("short_name")
        if not isinstance(short, str) or not SHORT_NAME.fullmatch(short):
            raise ApiError(400, "Ungültiges YouTrack-Projekt")
        doli = data.get("dolibarr_project_id")
        if doli in ("", None):
            doli = None
        else:
            try:
                doli = int(doli)
            except (TypeError, ValueError):
                raise ApiError(400, "Ungültige Dolibarr-Projekt-ID")
        db.set_project(short, doli)
        return jsonify({"ok": True, "projects": db.projects()})

    @app.delete("/api/settings/projects/<short>")
    def delete_project(short):
        db.delete_project(short)
        return jsonify({"ok": True, "projects": db.projects()})

    @app.post("/api/check")
    def run_check_api():
        return jsonify(run_check())

    return app
