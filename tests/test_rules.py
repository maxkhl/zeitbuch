from datetime import date

import pytest

from app.config import FieldConfig
from app.model import Issue, WorkItem
from app.rules import POSITION, PROJECT, evaluate, format_decimal, format_hm, round_up

YT = FieldConfig()
D1 = date(2026, 10, 5)
D2 = date(2026, 10, 6)


def project(id, summary, ext=None, bookable=None, parent=None):
    return Issue(id, summary, id.split("-")[0], parent,
                 {"Type": "Abrechnungsprojekt", "Ext-Projekt": ext, "Bebuchbar": bookable})


def position(id, summary, parent, ext=None, coord=None, bookable="ja"):
    return Issue(id, summary, id.split("-")[0], parent,
                 {"Type": "Abrechnungsposition", "Ext-Position": ext, "Abstimmung": coord,
                  "Bebuchbar": bookable})


def ticket(id, summary, parent=None, type="Aufgabe"):
    return Issue(id, summary, id.split("-")[0], parent, {"Type": type})


def issues_beispiel():
    """Hierarchie aus Konzept 3.1, ergänzt um ein bebuchbares Support-Projekt."""
    items = [
        project("CON-1", "Webshop-Integration", ext="S-2026-MUSTER-001", bookable="nein"),
        position("CON-2", "1. Kundenstammdaten", "CON-1", ext="S-2026-MUSTER-001 | 1"),
        position("CON-3", "2. Partnerrollen", "CON-1", ext="S-2026-MUSTER-001 | 2"),
        position("CON-4", "9. Koordination / PM", "CON-1", ext="S-2026-MUSTER-001 | 9", coord="ja"),
        ticket("CON-10", "Filter Replikation", "CON-2"),
        ticket("CON-11", "Unteraufgabe", "CON-10"),
        ticket("CON-12", "Rollenmodell", "CON-3"),
        project("CON-20", "Support-Projekt", bookable="ja"),
        ticket("CON-21", "Fehler im Export", "CON-20"),
        ticket("CON-99", "Lose Aufgabe"),
    ]
    return {i.id: i for i in items}


_n = 0


def wi(issue, minutes, type="Umsetzung", text="", day=D1, billable=None):
    global _n
    _n += 1
    attrs = {} if billable is None else {"Abrechenbar": billable}
    return WorkItem(f"wi-{_n}", issue, day, minutes, type, text, "max", _n, attrs)


def run(items, issues=None):
    return evaluate(items, issues or issues_beispiel(), YT)


# ------------------------------------------------------------ Rundung 5.4

@pytest.mark.parametrize("raw, rounded", [(0, 0), (1, 15), (15, 15), (16, 30), (50, 60), (61, 75)])
def test_rundung(raw, rounded):
    assert round_up(raw) == rounded


def test_formate():
    assert format_hm(105) == "1:45"
    assert format_hm(15) == "0:15"
    assert format_decimal(105) == "1,75h"
    assert format_decimal(30) == "0,50h"
    assert format_decimal(120) == "2,00h"
    assert format_decimal(40) == "0,67h"  # Rohzeit, nicht gerundet


def test_rundung_auf_tagessumme_nicht_je_work_item():
    r = run([wi("CON-10", 10), wi("CON-11", 10)])
    assert len(r.rows) == 1
    assert r.rows[0].raw_minutes == 20
    assert r.rows[0].minutes == 30  # nicht 15 + 15


def test_null_minuten_keine_zeile():
    assert run([wi("CON-10", 0)]).rows == []


# ------------------------------------------------------------ Auflösung 5.1

def test_umsetzung_wandert_zur_fachposition():
    (row,) = run([wi("CON-11", 30)]).rows
    assert row.target == "CON-2"
    assert row.target_kind == POSITION
    assert row.billing_project == "CON-1"
    assert row.ext_key == "S-2026-MUSTER-001 | 1"
    assert row.label == "1. Kundenstammdaten"
    assert row.external


def test_umsetzung_direkt_an_position():
    (row,) = run([wi("CON-3", 15)]).rows
    assert row.target == "CON-3"


def test_besprechung_geht_immer_zur_abstimmungsposition():
    r = run([wi("CON-10", 30, "Besprechung"), wi("CON-1", 30, "Besprechung")])
    assert [row.target for row in r.rows] == ["CON-4"]
    assert r.rows[0].minutes == 60


def test_besprechung_ohne_positionen_auf_projektebene():
    (row,) = run([wi("CON-21", 20, "Besprechung")]).rows
    assert row.target == "CON-20"
    assert row.target_kind == PROJECT


def test_umsetzung_bebuchbares_projekt_auf_projektebene():
    (row,) = run([wi("CON-21", 20)]).rows
    assert row.target == "CON-20"
    assert row.ext_key is None
    assert not row.external  # kein Ext-Projekt → nicht in der externen Anzeige


def test_projektebene_mit_ext_projekt():
    issues = issues_beispiel()
    issues["CON-20"].fields["Ext-Projekt"] = "S-2026-MUSTER-002"
    (row,) = run([wi("CON-21", 20)], issues).rows
    assert row.ext_key == "S-2026-MUSTER-002"
    assert row.external


def test_nicht_abrechenbar_nur_informativ():
    r = run([wi("CON-10", 30, billable="nein"), wi("CON-99", 30, "Besprechung", billable="nein")])
    assert r.rows == [] and r.problems == []
    assert [(n.billing_project, n.work_item.minutes) for n in r.nonbillable] == [
        ("CON-1", 30), (None, 30)]


@pytest.mark.parametrize("billable", [None, "ja"])
def test_abrechenbar_leer_oder_ja(billable):
    (row,) = run([wi("CON-10", 30, billable=billable)]).rows
    assert row.target == "CON-2"


def test_nicht_abrechenbar_ohne_arbeitstyp_ist_kein_fehler():
    r = run([wi("CON-10", 30, type=None, billable="nein")])
    assert r.problems == [] and len(r.nonbillable) == 1


def test_nicht_abrechenbar_zaehlt_nicht_zur_tagessumme():
    (row,) = run([wi("CON-10", 10), wi("CON-10", 50, billable="nein")]).rows
    assert row.raw_minutes == 10 and row.minutes == 15


def test_alter_arbeitstyp_nicht_abrechenbar_ist_unbekannt():
    assert reasons([wi("CON-10", 15, type="Nicht abrechenbar")]) == [
        "Unbekannter Arbeitstyp „Nicht abrechenbar“"]


def test_konfiguriertes_attribut():
    yt = FieldConfig(attr_billable="Billable", attr_billable_no="No")
    items = [WorkItem("1", "CON-10", D1, 20, "Umsetzung", attributes={"Billable": "No"}),
             WorkItem("2", "CON-10", D1, 20, "Umsetzung", attributes={"Abrechenbar": "nein"})]
    r = evaluate(items, issues_beispiel(), yt)
    assert len(r.nonbillable) == 1 and r.rows[0].raw_minutes == 20


def test_gruppierung_datum_mal_ziel():
    r = run([wi("CON-10", 10), wi("CON-10", 10, day=D2), wi("CON-12", 10), wi("CON-21", 10)])
    assert [(row.date, row.target) for row in r.rows] == [
        (D1, "CON-2"), (D1, "CON-3"), (D1, "CON-20"), (D2, "CON-2")]


def test_verschachtelte_position_naechste_gewinnt():
    issues = issues_beispiel()
    issues["CON-30"] = position("CON-30", "1.1 Unterposition", "CON-2", ext="S-2026-MUSTER-001 | 1.1")
    issues["CON-31"] = ticket("CON-31", "Detail", "CON-30")
    (row,) = run([wi("CON-31", 15)], issues).rows
    assert row.target == "CON-30"
    assert row.ext_key == "S-2026-MUSTER-001 | 1.1"  # eigene Ext-Position


def test_verschachtelte_position_eigene_ext_position_pflicht():
    issues = issues_beispiel()
    issues["CON-30"] = position("CON-30", "1.1 Unterposition", "CON-2")
    (reason,) = reasons([wi("CON-30", 15)], issues)
    assert "CON-30 hat keine Ext-Position" in reason


def test_position_nicht_bebuchbar_kein_ausweichen():
    issues = issues_beispiel()
    issues["CON-30"] = position("CON-30", "1.1 Unterposition", "CON-2", ext="S | 1.1", bookable=None)
    issues["CON-31"] = ticket("CON-31", "Detail", "CON-30")
    # CON-2 darüber wäre bebuchbar – trotzdem nicht zuordenbar
    (reason,) = reasons([wi("CON-31", 15)], issues)
    assert reason.startswith("Abrechnungsposition CON-30 ist nicht bebuchbar")


def test_sammelposition_nicht_bebuchbar_unterposition_schon():
    issues = issues_beispiel()
    issues["CON-3"].fields["Bebuchbar"] = "nein"
    issues["CON-30"] = position("CON-30", "2.1 Rollenmodell", "CON-3", ext="S | 2.1")
    r = run([wi("CON-3", 15), wi("CON-30", 15)], issues)
    assert [row.target for row in r.rows] == ["CON-30"]
    assert "CON-3 ist nicht bebuchbar" in r.problems[0].reason


@pytest.mark.parametrize("bookable", ["nein", None, "Nein"])
def test_position_bebuchbar_leer_ist_nein(bookable):
    issues = issues_beispiel()
    issues["CON-2"].fields["Bebuchbar"] = bookable
    (reason,) = reasons([wi("CON-10", 15)], issues)
    assert "CON-2 ist nicht bebuchbar" in reason


def test_bebuchbares_projekt_hilft_position_nicht():
    issues = issues_beispiel()
    issues["CON-1"].fields["Bebuchbar"] = "ja"
    issues["CON-2"].fields["Bebuchbar"] = "nein"
    assert reasons([wi("CON-10", 15)], issues) != []


def test_zyklus_in_parent_kette_haengt_nicht():
    issues = {"X-1": ticket("X-1", "a", "X-2"), "X-2": ticket("X-2", "b", "X-1")}
    r = run([wi("X-1", 15)], issues)
    assert r.problems[0].reason == "Kein Abrechnungsprojekt in der Parent-Kette"


# ------------------------------------------------------------ Fehlerfälle 5.2

def reasons(items, issues=None):
    return [p.reason for p in run(items, issues).problems]


def test_kein_abrechnungsprojekt():
    assert reasons([wi("CON-99", 15)]) == ["Kein Abrechnungsprojekt in der Parent-Kette"]


def test_arbeitstyp_leer_kein_default():
    r = run([wi("CON-10", 15, type=None)])
    assert r.rows == []
    assert r.problems[0].reason == "Arbeitstyp fehlt"


def test_unbekannter_arbeitstyp():
    assert reasons([wi("CON-10", 15, type="Development")]) == ["Unbekannter Arbeitstyp „Development“"]


def test_besprechung_ohne_abstimmungsposition_wie_umsetzung():
    issues = issues_beispiel()
    issues["CON-4"].fields["Abstimmung"] = None
    r = run([wi("CON-10", 15, "Besprechung"), wi("CON-12", 15, "Besprechung", text="Workshop")], issues)
    assert r.problems == []
    assert [(row.target, row.description) for row in r.rows] == [
        ("CON-2", "Filter Replikation"), ("CON-3", "Workshop")]  # kein „Besprechung …“-Präfix


def test_besprechung_ohne_abstimmungsposition_am_projekt_nicht_bebuchbar():
    issues = issues_beispiel()
    issues["CON-4"].fields["Abstimmung"] = None
    (reason,) = reasons([wi("CON-1", 15, "Besprechung")], issues)
    assert "Projektebene" in reason and "nicht bebuchbar" in reason


def test_abstimmungsposition_in_beliebiger_tiefe():
    issues = issues_beispiel()
    issues["CON-4"].fields["Abstimmung"] = None
    issues["CON-40"] = position("CON-40", "2.9 Abstimmung Rollen", "CON-3", ext="S | 2.9", coord="ja")
    (row,) = run([wi("CON-10", 15, "Besprechung")], issues).rows
    assert row.target == "CON-40"
    assert row.description == "Besprechung Filter Replikation"


def test_abstimmungsposition_nicht_bebuchbar():
    issues = issues_beispiel()
    issues["CON-4"].fields["Bebuchbar"] = None
    (reason,) = reasons([wi("CON-10", 15, "Besprechung")], issues)
    assert reason.startswith("Besprechung, aber Abstimmungsposition CON-4 ist nicht bebuchbar")


def test_abstimmungsposition_anderes_projekt_zaehlt_nicht():
    issues = issues_beispiel()
    issues["CON-22"] = position("CON-22", "Abstimmung Support", "CON-20", coord="ja")
    r = run([wi("CON-10", 15, "Besprechung"), wi("CON-21", 15, "Besprechung")], issues)
    assert [row.target for row in r.rows] == ["CON-4", "CON-22"]


def test_besprechung_mehrere_abstimmungspositionen():
    issues = issues_beispiel()
    issues["CON-3"].fields["Abstimmung"] = "ja"
    (reason,) = reasons([wi("CON-10", 15, "Besprechung")], issues)
    assert "mehrere Abstimmungspositionen (CON-3, CON-4)" in reason


def test_position_ohne_ext_position_bei_ext_projekt():
    issues = issues_beispiel()
    issues["CON-2"].fields["Ext-Position"] = ""
    (reason,) = reasons([wi("CON-10", 15)], issues)
    assert "CON-2 hat keine Ext-Position" in reason


def test_position_ohne_ext_position_ohne_ext_projekt_ist_ok():
    issues = issues_beispiel()
    issues["CON-1"].fields["Ext-Projekt"] = None
    issues["CON-2"].fields["Ext-Position"] = None
    (row,) = run([wi("CON-10", 15)], issues).rows
    assert row.target == "CON-2" and not row.external


@pytest.mark.parametrize("bookable", ["nein", None])
def test_projektebene_nicht_bebuchbar(bookable):
    issues = issues_beispiel()
    issues["CON-1"].fields["Bebuchbar"] = bookable
    (reason,) = reasons([wi("CON-1", 15)], issues)
    assert "nicht bebuchbar" in reason


def test_besprechung_ohne_positionen_nicht_bebuchbar():
    issues = issues_beispiel()
    issues["CON-20"].fields["Bebuchbar"] = "nein"
    (reason,) = reasons([wi("CON-21", 15, "Besprechung")], issues)
    assert "nicht bebuchbar" in reason


def test_fehlerhafte_items_gehen_in_keine_zeile():
    r = run([wi("CON-10", 15), wi("CON-10", 15, type=None)])
    assert r.rows[0].raw_minutes == 15


# ------------------------------------------------------------ Beschreibung 5.3

def test_beschreibung_dedupliziert_mit_fallback_titel():
    r = run([wi("CON-10", 5, text="Mapping angepasst"), wi("CON-11", 5, text="Mapping angepasst"),
             wi("CON-10", 5), wi("CON-11", 5, text="Test, Transport")])
    assert r.rows[0].description == "Mapping angepasst, Filter Replikation, Test, Transport"


def test_besprechung_mit_thema():
    r = run([wi("CON-12", 30, "Besprechung"), wi("CON-12", 15, "Besprechung", text="Workshop")])
    assert r.rows[0].description == "Besprechung Rollenmodell, Besprechung Rollenmodell: Workshop"


def test_besprechung_am_projekt_ohne_thema():
    r = run([wi("CON-1", 30, "Besprechung", text="Jour Fix"), wi("CON-1", 30, "Besprechung")])
    assert r.rows[0].description == "Jour Fix, Webshop-Integration"


def test_besprechung_an_abstimmungsposition_selbst():
    r = run([wi("CON-4", 30, "Besprechung")])
    assert r.rows[0].description == "9. Koordination / PM"


def test_konfigurierte_feldnamen():
    yt = FieldConfig(field_type="Typ", type_project="AP", type_position="APos",
                     field_bookable="Direkt", value_yes="Yes", work_implementation="Doing")
    issues = {
        "A-1": Issue("A-1", "Projekt", "A", None, {"Typ": "AP", "Direkt": "Yes"}),
        "A-2": Issue("A-2", "Task", "A", "A-1", {"Typ": "Bug"}),
    }
    (row,) = evaluate([WorkItem("1", "A-2", D1, 20, "Doing")], issues, yt).rows
    assert row.target == "A-1" and row.minutes == 30
