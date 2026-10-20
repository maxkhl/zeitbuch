"""Zuordnungs- und Berechnungsregeln (Konzept Abschnitt 5). Reine Funktionen."""

import math
from dataclasses import dataclass, field
from datetime import date

ROUND_MINUTES = 15

PROJECT = "project"
POSITION = "position"


def round_up(minutes):
    """Immer aufwärts auf 15 Minuten (5.4): 1 → 15, 15 → 15, 16 → 30, 0 → 0."""
    return math.ceil(minutes / ROUND_MINUTES) * ROUND_MINUTES


@dataclass
class Row:
    """Eine Zeitzeile: Datum × Ziel, gerundet (5.3/5.4)."""

    date: date
    target: str  # idReadable der Abrechnungsposition bzw. des -projekts
    target_kind: str  # PROJECT oder POSITION
    billing_project: str  # idReadable des Abrechnungsprojekts
    billing_project_label: str
    yt_project: str  # shortName des YouTrack-Projekts
    label: str  # Titel des Ziels
    ext_key: str | None  # Externe Kennung (Ext-Position bzw. Ext-Projekt)
    external: bool  # erscheint in der Anzeige für externe Systeme
    raw_minutes: int = 0
    minutes: int = 0
    description: str = ""
    work_items: list = field(default_factory=list)  # IDs

    @property
    def key(self):
        return f"{self.date.isoformat()}|{self.target}"


@dataclass
class Problem:
    """Work Item unter „Nicht zuordenbar“ (5.2)."""

    work_item: object
    issue_summary: str
    reason: str


@dataclass
class NonBillable:
    work_item: object
    issue_summary: str
    billing_project: str | None
    billing_project_label: str | None


@dataclass
class Result:
    rows: list
    problems: list
    nonbillable: list


class _Unassignable(Exception):
    pass


class Resolver:
    def __init__(self, issues, yt):
        self.issues = issues
        self.yt = yt
        self._positions = None

    # -------------------------------------------------------- Feldzugriff
    def kind(self, issue):
        value = issue.fields.get(self.yt.field_type)
        if value == self.yt.type_project:
            return PROJECT
        if value == self.yt.type_position:
            return POSITION
        return None

    def value(self, issue, field_name):
        v = issue.fields.get(field_name)
        return v.strip() if isinstance(v, str) and v.strip() else None

    def is_yes(self, issue, field_name):
        return issue.fields.get(field_name) == self.yt.value_yes

    def bookable(self, node):
        # An Projekt und Position; leer gilt als nein (3.3)
        return self.is_yes(node, self.yt.field_bookable)

    def ext_project(self, project):
        return self.value(project, self.yt.field_ext_project)

    def ext_position(self, position):
        return self.value(position, self.yt.field_ext_position)

    # -------------------------------------------------------- Hierarchie
    def chain(self, issue_id):
        """Ticket selbst und alle Parents nach oben, mit Schutz vor Zyklen."""
        seen = set()
        current = self.issues.get(issue_id)
        while current is not None and current.id not in seen:
            seen.add(current.id)
            yield current
            current = self.issues.get(current.parent) if current.parent else None

    def billing_project_of(self, issue_id):
        for issue in self.chain(issue_id):
            if self.kind(issue) == PROJECT:
                return issue
        return None

    def positions_of(self, project_id):
        """Alle Abrechnungspositionen (beliebige Tiefe), deren nächstes Abrechnungsprojekt
        darüber project_id ist."""
        if self._positions is None:
            self._positions = {}
            for issue in self.issues.values():
                if self.kind(issue) != POSITION or not issue.parent:
                    continue
                project = self.billing_project_of(issue.parent)
                if project is not None:
                    self._positions.setdefault(project.id, []).append(issue)
            for items in self._positions.values():
                items.sort(key=lambda i: i.id)
        return self._positions.get(project_id, [])

    def coordination_positions(self, project_id):
        return [p for p in self.positions_of(project_id)
                if self.is_yes(p, self.yt.field_coordination)]

    def nearest_node(self, issue_id):
        """Erster Abrechnungsknoten (Position oder Projekt) in der Parent-Kette."""
        for issue in self.chain(issue_id):
            if self.kind(issue) is not None:
                return issue
        return None

    # -------------------------------------------------------- Ziel (5.1)
    def target(self, wi):
        """Liefert (Ziel, Abrechnungsprojekt, umgeleitet) oder wirft _Unassignable."""
        yt = self.yt
        node = self.nearest_node(wi.issue)
        project = self.billing_project_of(wi.issue)
        if project is None:
            raise _Unassignable("Kein Abrechnungsprojekt in der Parent-Kette")

        redirected = False
        if wi.work_type == yt.work_meeting:
            coord = self.coordination_positions(project.id)
            if len(coord) > 1:
                raise _Unassignable(
                    f"Besprechung, aber Abrechnungsprojekt {project.id} hat mehrere "
                    f"Abstimmungspositionen ({', '.join(p.id for p in coord)})")
            if coord:
                node = coord[0]
                redirected = True
            # keine Abstimmungsposition: wie jeder andere Arbeitstyp

        # Nie auf den nächsthöheren Knoten ausweichen
        if not self.bookable(node):
            hint = f"({yt.field_bookable} ≠ {yt.value_yes})"
            if node is project:
                what = f"Ziel ist die Projektebene, aber Abrechnungsprojekt {project.id}"
            elif redirected:
                what = f"Besprechung, aber Abstimmungsposition {node.id}"
            else:
                what = f"Abrechnungsposition {node.id}"
            raise _Unassignable(f"{what} ist nicht bebuchbar {hint}")
        if node is not project and self.ext_project(project) and not self.ext_position(node):
            raise _Unassignable(
                f"Abrechnungsposition {node.id} hat keine {yt.field_ext_position}, "
                f"Abrechnungsprojekt {project.id} aber eine {yt.field_ext_project}")
        return node, project, redirected

    def describe(self, wi, target, project, redirected):
        """Beschreibungsteil eines Work Items (5.3)."""
        logged = self.issues.get(wi.issue)
        text = (wi.text or "").strip()
        if redirected and logged is not None and logged.id not in (project.id, target.id):
            topic = f"{self.yt.work_meeting} {logged.summary}"
            return f"{topic}: {text}" if text else topic
        return text or (logged.summary if logged else wi.issue)


def evaluate(work_items, issues, yt):
    """Work Items → Zeitzeilen, nicht zuordenbare und nicht abrechenbare Einträge."""
    res = Resolver(issues, yt)
    rows = {}
    parts = {}  # Zeilenschlüssel → Beschreibungsteile in Reihenfolge, dedupliziert
    problems = []
    nonbillable = []

    for wi in sorted(work_items, key=lambda w: (w.date, w.created, w.id)):
        if wi.minutes <= 0:
            continue
        logged = issues.get(wi.issue)
        summary = logged.summary if logged else ""
        # Nicht abrechenbar zuerst: wird nie gebucht, Arbeitstyp und Ziel spielen keine Rolle
        if wi.attributes.get(yt.attr_billable) == yt.attr_billable_no:
            project = res.billing_project_of(wi.issue)
            nonbillable.append(NonBillable(
                wi, summary, project.id if project else None,
                project.summary if project else None))
            continue
        if not wi.work_type:
            problems.append(Problem(wi, summary, "Arbeitstyp fehlt"))
            continue
        try:
            target, project, redirected = res.target(wi)
        except _Unassignable as e:
            problems.append(Problem(wi, summary, str(e)))
            continue

        key = (wi.date, target.id)
        row = rows.get(key)
        if row is None:
            is_project = target is project
            ext_project = res.ext_project(project)
            row = rows[key] = Row(
                date=wi.date,
                target=target.id,
                target_kind=PROJECT if is_project else POSITION,
                billing_project=project.id,
                billing_project_label=project.summary,
                yt_project=project.project,
                label=target.summary,
                ext_key=ext_project if is_project else res.ext_position(target),
                external=bool(ext_project),
            )
        row.raw_minutes += wi.minutes
        row.work_items.append(wi.id)
        part = res.describe(wi, target, project, redirected)
        row_parts = parts.setdefault(key, [])
        if part and part not in row_parts:
            row_parts.append(part)

    result_rows = []
    for key, row in rows.items():
        row.minutes = round_up(row.raw_minutes)
        row.description = ", ".join(parts[key])
        result_rows.append(row)
    result_rows.sort(key=lambda r: (r.date, r.billing_project, r.target_kind != PROJECT, r.target))
    return Result(result_rows, problems, nonbillable)


def format_hm(minutes):
    """1:45"""
    return f"{minutes // 60}:{minutes % 60:02d}"


def format_decimal(minutes):
    """1,75h – für gerundete Werte (Vielfache von 15 Minuten) immer exakt."""
    return f"{minutes / 60:.2f}".replace(".", ",") + "h"
