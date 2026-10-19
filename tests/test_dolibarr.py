"""Echte Dolibarr-Implementierung gegen ein Fake-HTTP nach Dolibarr 23.0.2."""

import pytest

from app.backends import BackendError
from app.backends.dolibarr import Dolibarr


class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class FakeDolibarrServer:
    """Bildet das Verhalten der Tasks-API aus docs/api-verifikation.md nach."""

    def __init__(self):
        self.headers = {}
        self.calls = []
        self.tasks = {7: {"id": "7", "label": "CON-1 Webshop", "fk_project": "1", "fk_task_parent": "0"}}
        self.times = {}  # id → dict
        self.next_id = 50
        self.extra_on_add = 0  # simuliert parallele Handbuchungen

    def request(self, method, url, params=None, json=None, timeout=None):
        path = url.split("/api/index.php", 1)[1]
        self.calls.append((method, path, json))
        parts = path.strip("/").split("/")
        if parts[0] == "tasks" and len(parts) == 1 and method == "POST":
            for f in ("ref", "label", "fk_project"):
                if f not in json:
                    return FakeResponse(400, {"error": {"code": 400, "message": f"{f} field missing"}})
            self.next_id += 1
            self.tasks[self.next_id] = {"id": str(self.next_id), **json}
            return FakeResponse(200, self.next_id)
        task_id = int(parts[1])
        if task_id not in self.tasks:
            return FakeResponse(404, {"error": {"code": 404, "message": "Task not found"}})
        if len(parts) == 2 and method == "GET":
            return FakeResponse(200, self.tasks[task_id])
        if len(parts) == 2 and method == "PUT":
            self.tasks[task_id].update(json)
            return FakeResponse(200, self.tasks[task_id])
        if parts[2] == "timespent" and method == "GET":
            return FakeResponse(200, [
                {"timespent_line_id": str(i), "timespent_line_duration": str(t["element_duration"]),
                 "timespent_line_note": t["note"]}
                for i, t in self.times.items() if t["fk_element"] == task_id])
        if parts[2] == "addtimespent":
            for _ in range(1 + self.extra_on_add):
                self.next_id += 1
                self.times[self.next_id] = {"fk_element": task_id, "element_duration": json["duration"],
                                            "note": json["note"], "invoice_id": None}
            # Wie Dolibarr 23: keine ID in der Antwort
            return FakeResponse(200, {"success": {"code": 200, "message": "Time spent added"}})
        if parts[2] == "getTimeSpent":
            t = self.times.get(int(parts[3]))
            if t is None:
                return FakeResponse(404, {"error": {"code": 404, "message": "Timespent not found"}})
            return FakeResponse(200, {"id": parts[3], "fk_element": str(t["fk_element"]),
                                      "element_duration": str(t["element_duration"]),
                                      "note_private": t["note"], "invoice_id": t["invoice_id"]})
        if parts[2] == "timespent" and method == "PUT":
            t = self.times[int(parts[3])]
            t.update(element_duration=json["duration"], note=json["note"], invoice_id=None)
            return FakeResponse(200, {"success": {"code": 200}})
        raise AssertionError(f"unerwartet: {method} {path}")


@pytest.fixture
def server():
    return FakeDolibarrServer()


@pytest.fixture
def doli(server):
    d = Dolibarr("https://doli.example", "key")
    d.session = server
    return d


def test_aufgabe_anlegen_mit_parent_und_billable(doli, server):
    task_id = doli.create_task(1, "CON-2 1. Kundenstammdaten", 7)
    method, path, data = server.calls[-1]
    assert (method, path) == ("POST", "/tasks")
    assert data == {"ref": "auto", "label": "CON-2 1. Kundenstammdaten", "fk_project": 1,
                    "billable": 1, "fk_task_parent": 7}
    assert doli.task(task_id)["fk_task_parent"] == 7


def test_aufgabe_ohne_parent(doli, server):
    doli.create_task(1, "CON-20 Support")
    assert "fk_task_parent" not in server.calls[-1][2]
    assert doli.task(7)["fk_task_parent"] is None


def test_umbenennen(doli, server):
    doli.rename_task(7, "CON-1 Neu")
    assert server.calls[-1] == ("PUT", "/tasks/7", {"label": "CON-1 Neu"})


def test_aufgabe_404_ist_none(doli):
    assert doli.task(999) is None


def test_zeit_anlegen_ermittelt_id_per_vergleich(doli, server):
    first = doli.add_time(7, "2026-09-01", 4500, "Mapping")
    second = doli.add_time(7, "2026-09-02", 900, "Test")
    assert first != second
    post = [c for c in server.calls if c[1].endswith("addtimespent")][0]
    assert post[2] == {"date": "2026-09-01 12:00:00", "duration": 4500, "user_id": 0, "note": "Mapping"}
    entry = doli.time_entry(7, first)
    assert entry == {"id": first, "task_id": 7, "seconds": 4500, "note": "Mapping", "invoiced": False}


def test_zeit_anlegen_id_nicht_eindeutig(doli, server):
    server.extra_on_add = 1
    with pytest.raises(BackendError, match="nicht eindeutig"):
        doli.add_time(7, "2026-09-01", 900, "x")


def test_fakturiert_aus_invoice_id(doli, server):
    tid = doli.add_time(7, "2026-09-01", 900, "x")
    server.times[tid]["invoice_id"] = "12"
    assert doli.time_entry(7, tid)["invoiced"] is True
    server.times[tid]["invoice_id"] = "0"
    assert doli.time_entry(7, tid)["invoiced"] is False


def test_zeit_aendern(doli, server):
    tid = doli.add_time(7, "2026-09-01", 900, "x")
    doli.update_time(7, tid, "2026-09-01", 1800, "y")
    assert server.calls[-1] == ("PUT", f"/tasks/7/timespent/{tid}",
                                {"date": "2026-09-01 12:00:00", "duration": 1800, "user_id": 0, "note": "y"})
    assert doli.time_entry(7, tid)["seconds"] == 1800


def test_zeit_404_und_falsche_aufgabe(doli, server):
    assert doli.time_entry(7, 12345) is None
    tid = doli.add_time(7, "2026-09-01", 900, "x")
    server.tasks[8] = {"id": "8", "label": "andere"}
    assert doli.time_entry(8, tid) is None


def test_fehlermeldungen(doli, server):
    server.request = lambda *a, **k: FakeResponse(401, {})
    with pytest.raises(BackendError, match="API-Key"):
        doli.ping()
    server.request = lambda *a, **k: FakeResponse(500, {"error": {"code": 500, "message": "kaputt"}})
    with pytest.raises(BackendError, match="kaputt"):
        doli.create_task(1, "x")


def test_kein_loeschen_im_backend():
    for cls in (Dolibarr, __import__("app.backends.dolibarr", fromlist=["MockDolibarr"]).MockDolibarr):
        assert not [m for m in dir(cls) if "delete" in m.lower() and not m.startswith("delete_by_hand")]
