import pytest

from app.month import parse_month

from .conftest import MONTH


def get_month(client, month=MONTH):
    r = client.get(f"/api/month?month={month}")
    assert r.status_code == 200, r.get_json()
    return r.get_json()


def row(data, key):
    return next(r for r in data["rows"] if r["key"] == key)


def test_parse_month():
    assert [d.isoformat() for d in parse_month("2026-02")] == ["2026-02-01", "2026-02-28"]
    for bad in ("2026-13", "2026-1", "", None, "x"):
        with pytest.raises(ValueError):
            parse_month(bad)


def test_monat_ohne_mapping_ist_leer(client):
    data = get_month(client)
    assert data["rows"] == [] and data["problems"] == []


def test_ungueltiger_monat(client):
    assert client.get("/api/month?month=09-2026").status_code == 400


def test_monat_zeilen_und_formate(client, mapped):
    data = get_month(client)
    r = row(data, "2026-09-01|CON-2")
    # 50 + 20 Minuten an CON-11/CON-10 → 70 → 75
    assert r["raw_minutes"] == 70 and r["minutes"] == 75
    assert r["hm"] == "1:15" and r["decimal"] == "1,25h"
    assert r["ext_key"] == "S-2026-MUSTER-001 | 1"
    assert r["description"] == "Mapping angepasst, Filter Replikation"
    assert r["url"].endswith("/issue/CON-2")
    # Besprechung an CON-12 → Abstimmungsposition mit Thema
    assert row(data, "2026-09-01|CON-4")["description"] == "Besprechung Rollenmodell abstimmen"
    # Support-Projekt ohne Ext-Projekt: nicht extern, aber vorhanden (Dolibarr)
    assert row(data, "2026-09-03|CON-20")["external"] is False


def test_monat_uebersicht_und_summen(client, mapped):
    data = get_month(client)
    con1 = next(p for p in data["overview"] if p["billing_project"] == "CON-1")
    # CON-7 ist eine verschachtelte Position unter CON-3
    assert [t["target"] for t in con1["targets"]] == ["CON-2", "CON-3", "CON-7", "CON-4"]
    assert con1["minutes"] == sum(t["minutes"] for t in con1["targets"])
    assert data["totals"]["minutes"] == sum(r["minutes"] for r in data["rows"])
    assert data["totals"]["problems"] == len(data["problems"]) == 5
    assert data["totals"]["nonbillable"]["minutes"] == 60
    # Eigene Summe je Abrechnungsprojekt, auch ohne Abrechnungsprojekt
    assert con1["nonbillable"]["minutes"] == 20
    assert data["overview"][-1]["billing_project"] is None
    assert data["overview"][-1]["nonbillable"]["minutes"] == 40


def test_nicht_zuordenbar_mit_link(client, mapped):
    data = get_month(client)
    p = next(p for p in data["problems"] if p["reason"] == "Arbeitstyp fehlt")
    assert p["work_item"]["issue"] == "CON-11"
    assert p["url"].endswith("/issue/CON-11")


def test_uebertragen_und_geaendert(client, mapped, youtrack):
    data = get_month(client)
    r = row(data, "2026-09-02|CON-3")
    assert r["transfer"] is None
    resp = client.post("/api/transfer", json={"row": r, "done": True})
    assert resp.status_code == 200
    r = row(get_month(client), "2026-09-02|CON-3")
    assert r["transfer"]["state"] == "ok"

    # Nachträglich weiteres Work Item am selben Tag
    extra = dict(youtrack.raw_work_items[0])
    extra.update({"id": "141-99", "date": youtrack.raw_work_items[3]["date"],
                  "issue": {"idReadable": "CON-3"}, "duration": {"minutes": 20}, "text": "Nachtrag"})
    youtrack.raw_work_items.append(extra)
    data = get_month(client)
    r = row(data, "2026-09-02|CON-3")
    assert r["transfer"]["state"] == "changed"
    assert r["transfer"]["old"]["hm"] == "1:45" and r["hm"] == "2:00"
    assert data["totals"]["changed"] == 1

    # Erneut bestätigen
    client.post("/api/transfer", json={"row": r, "done": True})
    assert row(get_month(client), "2026-09-02|CON-3")["transfer"]["state"] == "ok"
    client.post("/api/transfer", json={"row": r, "done": False})
    assert row(get_month(client), "2026-09-02|CON-3")["transfer"] is None


def test_entfallene_uebertragene_zeile(client, mapped, youtrack):
    r = row(get_month(client), "2026-09-08|XSYS-1")
    client.post("/api/transfer", json={"row": r, "done": True})
    youtrack.raw_work_items = [w for w in youtrack.raw_work_items
                               if w["issue"]["idReadable"] != "XSYS-2"]
    data = get_month(client)
    assert [x["key"] for x in data["removed_transfers"]] == ["2026-09-08|XSYS-1"]
    assert data["totals"]["changed"] == 1


def test_transfer_validierung(client):
    assert client.post("/api/transfer", json={"row": {"key": "x"}, "done": True}).status_code == 400
    assert client.post("/api/transfer", json={"done": True}).status_code == 400
    bad = {"key": "2026-09-01|CON-2", "minutes": "viel"}
    assert client.post("/api/transfer", json={"row": bad, "done": True}).status_code == 400


def test_einstellungen(client):
    r = client.post("/api/settings/projects", json={"short_name": "CON", "dolibarr_project_id": "5"})
    assert r.get_json()["projects"] == [{"short_name": "CON", "dolibarr_project_id": 5}]
    assert client.post("/api/settings/projects", json={"short_name": "C O N"}).status_code == 400
    data = client.get("/api/settings").get_json()
    assert {p["short_name"] for p in data["youtrack_projects"]} == {"CON", "XSYS", "INT"}
    assert ["ZEITBUCH_YT_FIELD_TYPE", "Type"] in data["field_config"]
    r = client.delete("/api/settings/projects/CON", json={})
    assert r.get_json()["projects"] == []


def test_verbindungstest_mit_konfigurationspruefung(client, mapped):
    data = client.post("/api/check", json={}).get_json()
    assert data["youtrack"] == {"ok": True, "user": "mock"}
    assert data["config"] == ["Feld „Abstimmung“ fehlt in Projekt XSYS"]
    assert client.get("/api/info").get_json()["check"]["config"] == data["config"]


def test_hinweis_wenn_dolibarr_keine_projekte_liefert(client, dolibarr):
    assert client.get("/api/settings").get_json()["dolibarr_hint"] is None
    dolibarr.project_list = {}
    hint = client.get("/api/settings").get_json()["dolibarr_hint"]
    assert "Zugriff auf alle Geschäftspartner erweitern" in hint
