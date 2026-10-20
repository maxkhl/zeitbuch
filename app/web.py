"""Routen und API. Keine eigene Anmeldung (Authentik Forward Auth davor)."""

import logging
import re
import threading

from flask import Flask, jsonify, request, send_from_directory

from . import __version__
from .backends import BackendError, create_dolibarr, create_youtrack
from .checks import check_youtrack_config
from .config import Config
from .db import Database
from .month import load_month, month_view, parse_month
from .sync import (StalePlan, SyncError, apply_plan, build_plan, check_booking_product,
                   check_booking_user, product_label)

log = logging.getLogger(__name__)

SHORT_NAME = re.compile(r"[A-Za-z0-9_-]{1,50}")

# GET /projects filtert ohne societe->client->voir auf Geschäftspartner, bei denen der
# API-Benutzer Vertriebsmitarbeiter ist (api_projects.class.php, index()).
NO_DOLIBARR_PROJECTS = (
    "Dolibarr liefert keine Projekte. Meist fehlt dem API-Benutzer das Recht "
    "„Zugriff auf alle Geschäftspartner erweitern“ (Geschäftspartner); Projektrechte "
    "allein reichen nicht. Alternativ den API-Benutzer beim Geschäftspartner als "
    "Vertriebsmitarbeiter eintragen. Siehe README, Einrichtung in Dolibarr.")


class ApiError(Exception):
    def __init__(self, status, message):
        super().__init__(message)
        self.status = status
        self.message = message


class CheckState:
    """Ergebnis der letzten Konfigurationsprüfung (Start und Verbindungstest)."""

    def __init__(self):
        self.lock = threading.Lock()
        self.result = {"done": False, "youtrack": None, "dolibarr": None, "config": []}

    def set(self, result):
        with self.lock:
            self.result = {"done": True, **result}

    def get(self):
        with self.lock:
            return dict(self.result)


def create_app(cfg=None, youtrack=None, dolibarr=None, startup_check=False):
    cfg = cfg or Config.from_env()
    youtrack = youtrack or create_youtrack(cfg)
    dolibarr = dolibarr or create_dolibarr(cfg)
    db = Database(cfg.db)
    check = CheckState()

    app = Flask(__name__, static_folder="static", static_url_path="/static")
    app.extensions["zeitbuch"] = {"cfg": cfg, "db": db, "youtrack": youtrack,
                                  "dolibarr": dolibarr, "check": check}

    def run_check():
        result = {"youtrack": None, "dolibarr": None, "config": []}
        for name, backend in (("youtrack", youtrack), ("dolibarr", dolibarr)):
            try:
                result[name] = {"ok": True, "user": backend.ping()}
            except BackendError as e:
                result[name] = {"ok": False, "error": str(e)}
        mapping = db.projects()
        if result["youtrack"]["ok"]:
            result["config"] = check_youtrack_config(
                youtrack, [p["short_name"] for p in mapping], cfg.yt)
        if result["dolibarr"]["ok"]:
            try:
                login, msg, _ = check_booking_user(dolibarr, cfg.dolibarr_user_id)
                result["dolibarr"]["booking_user"] = login or (
                    result["dolibarr"]["user"] if not cfg.dolibarr_user_id else None)
                if msg:
                    result["config"].append(msg)
            except BackendError as e:
                result["config"].append(f"Dolibarr-Benutzer {cfg.dolibarr_user_id}: {e}")
            try:
                product, msg, _ = check_booking_product(dolibarr, cfg.dolibarr_product_id)
                result["dolibarr"]["product"] = product_label(product)
                if msg:
                    result["config"].append(msg)
            except BackendError as e:
                result["config"].append(f"Dolibarr-Produkt {cfg.dolibarr_product_id}: {e}")
            for p in mapping:
                if p["dolibarr_project_id"] is None:
                    continue
                try:
                    if dolibarr.project(p["dolibarr_project_id"]) is None:
                        result["config"].append(
                            f"Dolibarr-Projekt {p['dolibarr_project_id']} (Mapping für "
                            f"{p['short_name']}) nicht gefunden")
                except BackendError as e:
                    result["config"].append(f"Dolibarr-Projekt {p['dolibarr_project_id']}: {e}")
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
            "mock": {"youtrack": youtrack.mode == "mock", "dolibarr": dolibarr.mode == "mock"},
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

    # ------------------------------------------------------------ Dolibarr
    def plan_for(month):
        result, issues = load_month(youtrack, db, cfg.yt, month)
        return build_plan(month, result, issues, db, dolibarr, cfg.yt, youtrack,
                          cfg.dolibarr_user_id, cfg.dolibarr_product_id)

    @app.get("/api/dolibarr/preview")
    def dolibarr_preview():
        return jsonify(plan_for(month_arg(request.args.get("month"))))

    @app.post("/api/dolibarr/apply")
    def dolibarr_apply():
        data = body()
        month = month_arg(data.get("month"))
        plan = plan_for(month)
        try:
            done = apply_plan(plan, db, dolibarr, data.get("fingerprint"),
                              cfg.dolibarr_user_id, cfg.dolibarr_product_id)
        except StalePlan as e:
            return jsonify({"error": str(e), "plan": plan}), 409
        except SyncError as e:
            raise ApiError(409, str(e))
        return jsonify({"done": done, "plan": plan_for(month)})

    # ------------------------------------------------------------ Einstellungen
    @app.get("/api/settings")
    def settings():
        lists = {}
        for name, backend in (("youtrack", youtrack), ("dolibarr", dolibarr)):
            try:
                lists[name] = (backend.projects(), None)
            except BackendError as e:
                lists[name] = ([], str(e))
        return jsonify({
            "projects": db.projects(),
            "youtrack_projects": lists["youtrack"][0],
            "youtrack_error": lists["youtrack"][1],
            "dolibarr_projects": lists["dolibarr"][0],
            "dolibarr_error": lists["dolibarr"][1],
            "dolibarr_hint": (NO_DOLIBARR_PROJECTS
                              if not lists["dolibarr"][0] and not lists["dolibarr"][1] else None),
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
