from datetime import date

import pytest

from app.backends import BackendError
from app.backends.youtrack import MockYouTrack, YouTrack, parse_issue, parse_work_item
from app.checks import check_youtrack_config
from app.config import FieldConfig
from app.rules import evaluate

YT = FieldConfig()
SEP = (date(2026, 9, 1), date(2026, 9, 30))


@pytest.fixture
def mock():
    return MockYouTrack(shift_month=False)


def test_parse_work_item_utc_datum():
    raw = {"id": "1-1", "date": 1759190400000,  # 2025-09-30T00:00:00Z
           "duration": {"minutes": 45}, "type": None, "text": None,
           "author": {"login": "x"}, "issue": {"idReadable": "CON-1"}}
    wi = parse_work_item(raw)
    assert wi.date == date(2025, 9, 30)
    assert wi.minutes == 45
    assert wi.work_type is None
    assert wi.text == ""


def test_parse_work_item_attribute():
    raw = {"id": "1-1", "date": 1759190400000, "duration": {"minutes": 45},
           "issue": {"idReadable": "CON-1"},
           "attributes": [{"name": "Abrechenbar", "value": {"name": "nein"}},
                          {"name": "Ort", "value": None}]}
    assert parse_work_item(raw).attributes == {"Abrechenbar": "nein", "Ort": None}
    assert parse_work_item({**raw, "attributes": None}).attributes == {}


def test_parse_issue_felder_und_parent():
    raw = {"idReadable": "CON-2", "summary": "Pos", "project": {"shortName": "CON"},
           "parent": {"issues": [{"idReadable": "CON-1"}]},
           "customFields": [
               {"name": "Type", "value": {"name": "Abrechnungsposition"}},
               {"name": "Ext-Position", "value": "S | 1"},
               {"name": "Abstimmung", "value": None},
               {"name": "Tags", "value": [{"name": "a"}, {"name": "b"}]},
               {"name": "Notiz", "value": {"text": "lang"}},
           ]}
    issue = parse_issue(raw)
    assert issue.parent == "CON-1"
    assert issue.fields == {"Type": "Abrechnungsposition", "Ext-Position": "S | 1",
                            "Abstimmung": None, "Tags": "a, b", "Notiz": "lang"}
    assert parse_issue({**raw, "parent": {"issues": []}}).parent is None


def test_mock_load_laedt_kette_und_abrechnungstickets(mock):
    items, issues = mock.load(*SEP, ["CON"], YT)
    assert items and all(wi.issue.startswith("CON-") for wi in items)
    # CON-11 → CON-10 → CON-2 → CON-1 komplett geladen
    assert {"CON-11", "CON-10", "CON-2", "CON-1", "CON-4"} <= set(issues)
    assert "XSYS-1" not in issues


def test_mock_autorenfilter(mock):
    items = mock.work_items(*SEP, ["CON"], ("assistent",))
    assert {wi.author for wi in items} == {"assistent"}


def test_mock_verschiebt_in_angefragten_monat():
    items = MockYouTrack().work_items(date(2027, 2, 1), date(2027, 2, 28), ["CON", "XSYS"])
    assert items and all(wi.date.month == 2 and wi.date.year == 2027 for wi in items)


def test_beispieldaten_decken_regeln_ab(mock):
    items, issues = mock.load(*SEP, ["CON", "XSYS"], YT)
    r = evaluate(items, issues, YT)
    targets = {row.target for row in r.rows}
    assert {"CON-2", "CON-3", "CON-4", "CON-7", "CON-20", "CON-32", "XSYS-1"} <= targets
    reasons = " | ".join(p.reason for p in r.problems)
    for expected in ("Kein Abrechnungsprojekt", "Arbeitstyp fehlt", "keine Ext-Position",
                     "Projektebene, aber Abrechnungsprojekt CON-1 ist nicht bebuchbar",
                     "Abrechnungsposition CON-8 ist nicht bebuchbar"):
        assert expected in reasons
    # CON-31 hat keine Abstimmungsposition: Besprechung an CON-32 bleibt dort
    meeting = next(row for row in r.rows if row.target == "CON-32" and row.date.day == 7)
    assert meeting.raw_minutes == 150
    assert len(r.nonbillable) == 2


def test_konfigurationspruefung(mock):
    assert check_youtrack_config(mock, ["CON"], YT) == []
    assert check_youtrack_config(mock, ["XSYS"], YT) == ["Feld „Abstimmung“ fehlt in Projekt XSYS"]
    assert check_youtrack_config(mock, ["NOPE"], YT) == ["Projekt NOPE nicht gefunden oder kein Zugriff"]
    msgs = check_youtrack_config(mock, ["INT"], YT)
    assert "Wert „Abrechnungsprojekt“ fehlt im Feld „Type“ in Projekt INT" in msgs
    assert "Feld „Bebuchbar“ fehlt in Projekt INT" in msgs
    assert "Zeiterfassung ist in Projekt INT nicht aktiviert" in msgs


def test_konfigurationspruefung_attribut(mock):
    msgs = check_youtrack_config(mock, ["CON"], FieldConfig(attr_billable="Abrechenbarkeit"))
    assert msgs == ["Work-Item-Attribut „Abrechenbarkeit“ fehlt in Projekt CON"]
    msgs = check_youtrack_config(mock, ["CON"], FieldConfig(attr_billable_no="no"))
    assert msgs == ["Wert „no“ fehlt im Work-Item-Attribut „Abrechenbar“ in Projekt CON"]


def test_konfigurationspruefung_tippfehler(mock):
    yt = FieldConfig(field_bookable="Bebuchbahr", work_meeting="Besprechungen")
    msgs = check_youtrack_config(mock, ["CON"], yt)
    assert msgs == ["Feld „Bebuchbahr“ fehlt in Projekt CON",
                    "Arbeitstyp „Besprechungen“ fehlt in Projekt CON"]


# ------------------------------------------------------------ echte Implementierung, Fake-HTTP

class FakeResponse:
    def __init__(self, status, data):
        self.status_code = status
        self._data = data

    def json(self):
        return self._data


class FakeSession:
    def __init__(self, handler):
        self.handler = handler
        self.calls = []
        self.headers = {}

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, params))
        return self.handler(url, params or {})


def make_real(handler):
    yt = YouTrack("https://yt.example", "geheim")
    yt.session = FakeSession(handler)
    return yt


def test_real_work_items_parameter_und_paging(monkeypatch):
    monkeypatch.setattr("app.backends.youtrack.PAGE", 2)
    raw = [{"id": f"1-{i}", "date": 1790812800000, "duration": {"minutes": 15},
            "issue": {"idReadable": "CON-1"}} for i in range(3)]

    def handler(url, params):
        return FakeResponse(200, raw[params["$skip"]:params["$skip"] + params["$top"]])

    yt = make_real(handler)
    items = yt.work_items(*SEP, ["CON", "XSYS"], ("max",))
    assert len(items) == 3
    url, params = yt.session.calls[0]
    assert url == "https://yt.example/api/workItems"
    assert params["startDate"] == "2026-09-01" and params["endDate"] == "2026-09-30"
    assert params["query"] == "project: {CON}, {XSYS}"
    assert params["author"] == ["max"]
    assert "attributes(name,value(name))" in params["fields"]
    assert len(yt.session.calls) == 2


def test_real_billing_query():
    yt = make_real(lambda url, params: FakeResponse(200, []))
    yt.billing_issues(["CON"], YT)
    assert yt.session.calls[0][1]["query"] == (
        "project: {CON} {Type}: {Abrechnungsprojekt}, {Abrechnungsposition}")


def test_real_fehlermeldungen():
    yt = make_real(lambda url, params: FakeResponse(401, {}))
    with pytest.raises(BackendError, match="Token prüfen"):
        yt.ping()
    yt = make_real(lambda url, params: FakeResponse(400, {"error_description": "Unbekanntes Feld"}))
    with pytest.raises(BackendError, match="Unbekanntes Feld"):
        yt.billing_issues(["CON"], YT)


def test_real_issues_fallback_bei_unbekannter_id():
    def handler(url, params):
        if url.endswith("/api/issues"):
            return FakeResponse(400, {"error_description": "Unknown issue"})
        if url.endswith("CON-404"):
            return FakeResponse(404, {})
        return FakeResponse(200, {"idReadable": "CON-1", "summary": "x", "project": {"shortName": "CON"}})

    yt = make_real(handler)
    assert [i.id for i in yt.issues(["CON-1", "CON-404"])] == ["CON-1"]


def test_load_gelöschtes_ticket_wird_nicht_zuordenbar(mock):
    mock.raw_issues = [r for r in mock.raw_issues if r["idReadable"] != "CON-30"]
    items, issues = mock.load(*SEP, ["CON"], YT)
    assert issues["CON-30"].fields == {}
    r = evaluate(items, issues, YT)
    assert any(p.work_item.issue == "CON-30" for p in r.problems)
