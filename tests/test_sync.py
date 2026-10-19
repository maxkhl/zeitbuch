"""Abgleich mit Dolibarr über die API-Routen, gegen beide Mocks."""

import pytest

from .conftest import MONTH


@pytest.fixture
def con_only(db):
    db.set_project("CON", 1)
    return db


def preview(client, month=MONTH):
    r = client.get(f"/api/dolibarr/preview?month={month}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def apply(client, plan, month=MONTH):
    r = client.post("/api/dolibarr/apply", json={"month": month, "fingerprint": plan["fingerprint"]})
    return r


def time_item(plan, key):
    return next(t for t in plan["times"] if t["key"] == key)


def test_vorschau_legt_nichts_an(client, con_only, dolibarr):
    plan = preview(client)
    assert dolibarr.calls == []
    labels = [c["label"] for c in plan["tasks_create"]]
    assert "CON-1 Webshop-Integration" in labels
    assert "CON-2 1. Kundenstammdaten" in labels
    assert "CON-20 Support-Projekt" in labels
    # Nur benötigte Aufgaben: CON-33 ist wegen fehlender Ext-Position nicht zuordenbar
    assert "CON-33 2. Umsetzung" not in labels
    assert all(t["action"] == "create" for t in plan["times"])
    assert plan["pending"]
    texts = " | ".join(w["text"] for w in plan["warnings"])
    assert "nicht zuordenbar" in texts


def test_nicht_gemappte_projekte(client, db):
    db.set_project("CON", 1)
    db.set_project("XSYS", None)
    plan = preview(client)
    assert all(t["yt_project"] == "CON" for t in plan["times"])
    assert any("XSYS ist keinem Dolibarr-Projekt zugeordnet" in w["text"] for w in plan["warnings"])


def test_uebertragen_struktur_und_buchungen(client, con_only, dolibarr):
    plan = preview(client)
    r = apply(client, plan)
    assert r.status_code == 200, r.get_json()
    done = r.get_json()["done"]
    assert "error" not in done
    assert done["tasks_created"] == len(plan["tasks_create"])
    assert done["times_created"] == len(plan["times"])

    tasks = {t["label"]: t for t in dolibarr.tasks.values()}
    project = tasks["CON-1 Webshop-Integration"]
    assert project["fk_task_parent"] is None and project["billable"] == 1
    assert tasks["CON-2 1. Kundenstammdaten"]["fk_task_parent"] == project["id"]
    assert tasks["CON-20 Support-Projekt"]["fk_task_parent"] is None

    # 1:1 zur Zeitzeile (5.5)
    month = client.get(f"/api/month?month={MONTH}").get_json()
    con_rows = [r for r in month["rows"] if r["yt_project"] == "CON"]
    assert sum(t["seconds"] for t in dolibarr.times.values()) == sum(r["minutes"] for r in con_rows) * 60
    booked = {(t["date"], t["seconds"]) for t in dolibarr.times.values()}
    assert ("2026-09-01", 75 * 60) in booked

    # Danach ist nichts mehr offen
    plan2 = r.get_json()["plan"]
    assert not plan2["pending"]
    assert {t["action"] for t in plan2["times"]} == {"unchanged"}


def test_idempotent_kein_duplikat(client, con_only, dolibarr):
    apply(client, preview(client))
    n = len(dolibarr.times)
    calls = len(dolibarr.calls)
    r = apply(client, preview(client))
    assert r.status_code == 200
    assert len(dolibarr.times) == n
    assert len(dolibarr.calls) == calls


def test_aenderung_aktualisiert_eigene_buchung(client, con_only, dolibarr, youtrack):
    apply(client, preview(client))
    n = len(dolibarr.times)
    wi = next(w for w in youtrack.raw_work_items if w["id"] == "141-5")  # CON-3, 95 min
    wi["duration"] = {"minutes": 100}
    wi["text"] = "Partnerrollen konfiguriert und getestet"
    plan = preview(client)
    item = time_item(plan, "2026-09-02|CON-3")
    assert item["action"] == "update"
    assert item["old"]["hm"] == "1:45" and item["hm"] == "1:45"  # 100 → 105, gleiche Rundung
    assert item["old"]["note"] == "Partnerrollen konfiguriert"
    apply(client, plan)
    assert len(dolibarr.times) == n
    assert dolibarr.times[item["time_id"]]["note"] == "Partnerrollen konfiguriert und getestet"


def test_fakturierte_buchung_bleibt_gesperrt(client, con_only, dolibarr, youtrack):
    apply(client, preview(client))
    plan = preview(client)
    item = time_item(plan, "2026-09-02|CON-3")
    dolibarr.invoice(item["time_id"])
    wi = next(w for w in youtrack.raw_work_items if w["id"] == "141-5")
    wi["duration"] = {"minutes": 200}
    plan = preview(client)
    item = time_item(plan, "2026-09-02|CON-3")
    assert item["action"] == "locked_diff"
    assert any("fakturiert" in w["text"] for w in plan["warnings"])
    calls = len(dolibarr.calls)
    apply(client, plan)
    assert len(dolibarr.calls) == calls
    assert dolibarr.times[item["time_id"]]["invoiced"] is True
    assert dolibarr.times[item["time_id"]]["seconds"] == 105 * 60


def test_inzwischen_fakturiert_wird_beim_uebertragen_uebersprungen(client, con_only, dolibarr,
                                                                   youtrack, app, monkeypatch):
    apply(client, preview(client))
    wi = next(w for w in youtrack.raw_work_items if w["id"] == "141-5")
    wi["duration"] = {"minutes": 200}
    plan = preview(client)
    item = time_item(plan, "2026-09-02|CON-3")
    assert item["action"] == "update"

    # Zwischen Vorschau-Neuberechnung und PUT fakturiert jemand in Dolibarr
    import app.sync as sync

    original = sync.build_plan

    def build_then_invoice(*args, **kwargs):
        p = original(*args, **kwargs)
        if not dolibarr.times[item["time_id"]]["invoiced"]:
            dolibarr.invoice(item["time_id"])
        return p

    monkeypatch.setattr("app.web.build_plan", build_then_invoice)
    done = apply(client, plan).get_json()["done"]
    assert done["times_updated"] == 0
    assert "inzwischen fakturiert" in done["skipped"][0]
    assert dolibarr.times[item["time_id"]]["invoiced"] is True


def test_verwaiste_buchung_wird_nie_geloescht(client, con_only, dolibarr, youtrack):
    apply(client, preview(client))
    youtrack.raw_work_items = [w for w in youtrack.raw_work_items if w["id"] != "141-5"]
    n = len(dolibarr.times)
    plan = preview(client)
    assert [o["key"] for o in plan["orphans"]] == ["2026-09-02|CON-3"]
    assert plan["orphans"][0]["url"].startswith("https://dolibarr.example.org/projet/tasks/time.php?id=")
    assert any("Verwaiste Buchung" in w["text"] for w in plan["warnings"])
    apply(client, plan)
    assert len(dolibarr.times) == n

    # Von Hand gelöscht → Warnung verschwindet, Zuordnung wird vergessen
    dolibarr.delete_by_hand(time_id=plan["orphans"][0]["time_id"])
    plan = preview(client)
    assert plan["orphans"] == []
    apply(client, plan)
    assert preview(client)["forget_times"] == []


def test_von_hand_geloeschte_buchung_wird_neu_angelegt(client, con_only, dolibarr):
    apply(client, preview(client))
    item = time_item(preview(client), "2026-09-01|CON-2")
    dolibarr.delete_by_hand(time_id=item["time_id"])
    plan = preview(client)
    item = time_item(plan, "2026-09-01|CON-2")
    assert item["action"] == "create" and "nicht mehr vorhanden" in item["hint"]
    apply(client, plan)
    assert time_item(preview(client), "2026-09-01|CON-2")["action"] == "unchanged"


def test_titelaenderung_benennt_um(client, con_only, dolibarr, youtrack):
    apply(client, preview(client))
    raw = next(i for i in youtrack.raw_issues if i["idReadable"] == "CON-2")
    raw["summary"] = "1. Kundenstamm"
    plan = preview(client)
    assert [(r["old"], r["new"]) for r in plan["tasks_rename"]] == [
        ("CON-2 1. Kundenstammdaten", "CON-2 1. Kundenstamm")]
    apply(client, plan)
    assert "CON-2 1. Kundenstamm" in {t["label"] for t in dolibarr.tasks.values()}
    assert preview(client)["tasks_rename"] == []


def test_strukturwarnungen(client, con_only, dolibarr, youtrack):
    apply(client, preview(client))
    tasks_before = dict(dolibarr.tasks)
    # Position umhängen und Type entfernen
    raw = next(i for i in youtrack.raw_issues if i["idReadable"] == "CON-3")
    raw["parent"]["issues"] = [{"idReadable": "CON-20"}]
    raw20 = next(i for i in youtrack.raw_issues if i["idReadable"] == "CON-20")
    for cf in raw20["customFields"]:
        if cf["name"] == "Type":
            cf["value"] = {"name": "Task"}
    plan = preview(client)
    texts = " | ".join(w["text"] for w in plan["warnings"])
    assert "CON-20 ist in YouTrack gelöscht oder kein Abrechnungsticket mehr" in texts
    apply(client, plan)
    # nichts gelöscht, nichts verschoben
    for task_id, t in tasks_before.items():
        assert dolibarr.tasks[task_id]["fk_task_parent"] == t["fk_task_parent"]


def test_position_umgehaengt_in_anderes_projekt(client, con_only, dolibarr, youtrack):
    apply(client, preview(client))
    raw = next(i for i in youtrack.raw_issues if i["idReadable"] == "CON-32")
    raw["parent"]["issues"] = [{"idReadable": "CON-1"}]
    plan = preview(client)
    assert any("CON-32 wurde in YouTrack umgehängt (CON-31 → CON-1)" in w["text"]
               for w in plan["warnings"])


def test_von_hand_geloeschte_aufgabe_wird_neu_angelegt(client, con_only, dolibarr):
    apply(client, preview(client))
    task_id = next(i for i, t in dolibarr.tasks.items() if t["label"].startswith("CON-20 "))
    dolibarr.delete_by_hand(task_id=task_id)
    plan = preview(client)
    assert [c["yt_issue"] for c in plan["tasks_create"]] == ["CON-20"]
    assert "CON-20" in plan["forget_tasks"]
    r = apply(client, plan)
    assert "error" not in r.get_json()["done"]
    assert not r.get_json()["plan"]["pending"]


def test_veraltete_vorschau_409(client, con_only, youtrack):
    plan = preview(client)
    youtrack.raw_work_items[0]["duration"] = {"minutes": 300}
    r = apply(client, plan)
    assert r.status_code == 409
    assert "veraltet" in r.get_json()["error"]


def test_dolibarr_projekt_fehlt(client, db):
    db.set_project("CON", 99)
    plan = preview(client)
    assert plan["times"] == []
    assert any("Dolibarr-Projekt 99" in w["text"] for w in plan["warnings"])


def test_handangelegte_aufgaben_bleiben_unberuehrt(client, con_only, dolibarr):
    manual = dolibarr.create_task(1, "Altbestand")
    dolibarr.add_time(manual, "2026-09-01", 3600, "von Hand")
    dolibarr.calls.clear()
    apply(client, preview(client))
    assert dolibarr.tasks[manual]["label"] == "Altbestand"
    assert all(c[1] != manual for c in dolibarr.calls if c[0] in ("rename_task", "update_time"))
    assert preview(client)["orphans"] == []


def test_info_zeigt_mock_modus(client):
    assert client.get("/api/info").get_json()["mock"] == {"youtrack": True, "dolibarr": True}


def test_verbindungstest_dolibarr(client, db):
    db.set_project("CON", 99)
    data = client.post("/api/check", json={}).get_json()
    assert data["dolibarr"]["ok"]
    assert "Dolibarr-Projekt 99 (Mapping für CON) nicht gefunden" in data["config"]
