"""Dolibarr 23: Aufgaben und Zeitbuchungen. Echte REST-Anbindung und Mock.

Befunde zur API stehen in docs/api-verifikation.md. Kurz:
- addtimespent liefert keine ID → Vorher/Nachher-Vergleich der Zeilen.
- Fakturiert (invoice_id) gibt es nur im Einzelabruf getTimeSpent.
- PUT timespent setzt invoice_id zurück → nur nicht fakturierte Buchungen ändern.

Es gibt bewusst keine Methode zum Löschen.
"""

import itertools
import logging
import threading

from . import BackendError

log = logging.getLogger(__name__)

# Uhrzeit der Buchung (GMT); hält den Tag in europäischen Zeitzonen stabil.
BOOKING_TIME = "12:00:00"


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _invoiced(raw):
    return bool(_int(raw.get("invoice_id")))


class DolibarrBase:
    mode = "real"

    def task_url(self, task_id):
        return f"{self.url}/projet/tasks/task.php?id={task_id}"

    def time_url(self, task_id):
        return f"{self.url}/projet/tasks/time.php?id={task_id}"

    def project_url(self, project_id):
        return f"{self.url}/projet/card.php?id={project_id}"


class Dolibarr(DolibarrBase):
    def __init__(self, url, key, timeout=20):
        import requests

        self.url = url
        self.api = url + "/api/index.php"
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({"DOLAPIKEY": key, "Accept": "application/json"})
        # Vorher/Nachher-Vergleich beim Anlegen darf nicht parallel laufen
        self._add_lock = threading.Lock()

    def _request(self, method, path, params=None, json=None, not_found_ok=False):
        import requests

        try:
            r = self.session.request(method, self.api + path, params=params, json=json,
                                     timeout=self.timeout)
        except requests.RequestException as e:
            raise BackendError(f"Dolibarr nicht erreichbar: {e.__class__.__name__}")
        if r.status_code == 404 and not_found_ok:
            return None
        if r.status_code in (401, 403):
            raise BackendError(f"Dolibarr verweigert den Zugriff (HTTP {r.status_code}) – "
                               "API-Key und Rechte prüfen", r.status_code)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = (r.json().get("error") or {}).get("message") or ""
            except (ValueError, AttributeError):
                pass
            raise BackendError(f"Dolibarr-Fehler HTTP {r.status_code} bei {method} {path}"
                               + (f": {detail}" if detail else ""), r.status_code)
        try:
            return r.json()
        except ValueError:
            raise BackendError(f"Dolibarr lieferte kein JSON bei {path} – URL prüfen")

    def ping(self):
        info = self._request("GET", "/users/info")
        return info.get("login") or "?"

    def projects(self):
        result = []
        for page in itertools.count():
            batch = self._request("GET", "/projects", {"sortfield": "t.ref", "sortorder": "ASC",
                                                       "limit": 100, "page": page},
                                  not_found_ok=True) or []
            result.extend({"id": _int(p.get("id")), "ref": p.get("ref") or "",
                           "title": p.get("title") or ""} for p in batch)
            if len(batch) < 100:
                return result

    def project(self, project_id):
        p = self._request("GET", f"/projects/{project_id}", not_found_ok=True)
        if not p:
            return None
        return {"id": _int(p.get("id")), "ref": p.get("ref") or "", "title": p.get("title") or ""}

    def task(self, task_id):
        t = self._request("GET", f"/tasks/{task_id}", not_found_ok=True)
        if not t:
            return None
        return {"id": _int(t.get("id")), "label": t.get("label") or "",
                "fk_project": _int(t.get("fk_project")),
                "fk_task_parent": _int(t.get("fk_task_parent")) or None}

    def create_task(self, project_id, label, parent_task_id=None):
        data = {"ref": "auto", "label": label, "fk_project": project_id, "billable": 1}
        if parent_task_id:
            data["fk_task_parent"] = parent_task_id
        task_id = _int(self._request("POST", "/tasks", json=data))
        if not task_id:
            raise BackendError("Dolibarr hat beim Anlegen der Aufgabe keine ID geliefert")
        return task_id

    def rename_task(self, task_id, label):
        self._request("PUT", f"/tasks/{task_id}", json={"label": label})

    def time_ids(self, task_id):
        lines = self._request("GET", f"/tasks/{task_id}/timespent") or []
        return {_int(line.get("timespent_line_id")) for line in lines} - {None}

    def time_entry(self, task_id, time_id):
        raw = self._request("GET", f"/tasks/{task_id}/getTimeSpent/{time_id}", not_found_ok=True)
        if not raw or _int(raw.get("fk_element")) not in (None, task_id):
            return None
        return {"id": time_id, "task_id": task_id,
                "seconds": _int(raw.get("element_duration")) or 0,
                "note": (raw.get("note_private") or raw.get("note") or "").strip(),
                "invoiced": _invoiced(raw)}

    def add_time(self, task_id, day, seconds, note):
        with self._add_lock:
            before = self.time_ids(task_id)
            self._request("POST", f"/tasks/{task_id}/addtimespent", json={
                "date": f"{day} {BOOKING_TIME}", "duration": seconds, "user_id": 0, "note": note})
            new = self.time_ids(task_id) - before
        if len(new) != 1:
            raise BackendError(
                f"Zeitbuchung auf Aufgabe {task_id} angelegt, aber ihre ID ist nicht eindeutig "
                f"({len(new)} neue Einträge). Bitte in Dolibarr prüfen; zeitbuch bucht diese "
                "Zeile erst wieder, wenn das geklärt ist.")
        return new.pop()

    def update_time(self, task_id, time_id, day, seconds, note):
        self._request("PUT", f"/tasks/{task_id}/timespent/{time_id}", json={
            "date": f"{day} {BOOKING_TIME}", "duration": seconds, "user_id": 0, "note": note})


class MockDolibarr(DolibarrBase):
    """Im Arbeitsspeicher; startet mit zwei fiktiven Projekten."""

    mode = "mock"

    def __init__(self):
        self.url = "https://dolibarr.example.org"
        self._ids = itertools.count(100)
        self._lock = threading.Lock()
        self.project_list = {
            1: {"id": 1, "ref": "PJ2601-0001", "title": "Beispiel Consulting – Rahmenvertrag"},
            2: {"id": 2, "ref": "PJ2601-0002", "title": "Beispiel Systemhaus – Betreuung"},
        }
        self.tasks = {}  # id → dict
        self.times = {}  # id → dict
        self.calls = []  # für Tests: (Methode, Argumente)

    def ping(self):
        return "mock"

    def projects(self):
        return list(self.project_list.values())

    def project(self, project_id):
        return self.project_list.get(project_id)

    def task(self, task_id):
        t = self.tasks.get(task_id)
        return dict(t) if t else None

    def create_task(self, project_id, label, parent_task_id=None):
        with self._lock:
            if project_id not in self.project_list:
                raise BackendError(f"Dolibarr-Fehler HTTP 404: Projekt {project_id} nicht gefunden", 404)
            task_id = next(self._ids)
            self.tasks[task_id] = {"id": task_id, "label": label, "fk_project": project_id,
                                   "fk_task_parent": parent_task_id, "billable": 1}
            self.calls.append(("create_task", project_id, label, parent_task_id))
            return task_id

    def rename_task(self, task_id, label):
        self.tasks[task_id]["label"] = label
        self.calls.append(("rename_task", task_id, label))

    def time_ids(self, task_id):
        return {i for i, t in self.times.items() if t["task_id"] == task_id}

    def time_entry(self, task_id, time_id):
        t = self.times.get(time_id)
        if not t or t["task_id"] != task_id or task_id not in self.tasks:
            return None
        return {k: t[k] for k in ("id", "task_id", "seconds", "note", "invoiced")}

    def add_time(self, task_id, day, seconds, note):
        with self._lock:
            if task_id not in self.tasks:
                raise BackendError("Dolibarr-Fehler HTTP 404: Task not found", 404)
            time_id = next(self._ids)
            self.times[time_id] = {"id": time_id, "task_id": task_id, "date": day,
                                   "seconds": seconds, "note": note.strip(), "invoiced": False}
            self.calls.append(("add_time", task_id, day, seconds, note))
            return time_id

    def update_time(self, task_id, time_id, day, seconds, note):
        t = self.times[time_id]
        # Wie Dolibarr 23: PUT setzt das Fakturiert-Kennzeichen zurück
        t.update({"date": day, "seconds": seconds, "note": note.strip(), "invoiced": False})
        self.calls.append(("update_time", task_id, time_id, day, seconds, note))

    # Nur für Tests/Demo: Dolibarr-Seite simulieren
    def invoice(self, time_id):
        self.times[time_id]["invoiced"] = True

    def delete_by_hand(self, time_id=None, task_id=None):
        if time_id:
            self.times.pop(time_id, None)
        if task_id:
            self.tasks.pop(task_id, None)
            for i in [i for i, t in self.times.items() if t["task_id"] == task_id]:
                del self.times[i]
