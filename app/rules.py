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

    def bookable(self, project):
        # Leer gilt als nein (3.3)
        return self.is_yes(project, self.yt.field_bookable)

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
        """Alle Abrechnungspositionen, deren nächstes Abrechnungsprojekt darüber project_id ist."""
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

    # -------------------------------------------------------- Ziel (5.1)
    def target(self, wi):
        """Liefert (Ziel-Ticket, Abrechnungsprojekt) oder wirft _Unassignable."""
        yt = self.yt
        project = None
        position = None
        for issue in self.chain(wi.issue):
            kind = self.kind(issue)
            if kind == POSITION and position is None:
                position = issue
            elif kind == PROJECT:
                project = issue
                break
        if project is None:
            raise _Unassignable("Kein Abrechnungsprojekt in der Parent-Kette")

        if wi.work_type == yt.work_meeting:
            if self.positions_of(project.id):
                coord = self.coordination_positions(project.id)
                if not coord:
                    raise _Unassignable(
                        f"Besprechung, aber Abrechnungsprojekt {project.id} hat keine "
                        f"Abstimmungsposition ({yt.field_coordination} = {yt.value_yes})")
                if len(coord) > 1:
                    raise _Unassignable(
                        f"Besprechung, aber Abrechnungsprojekt {project.id} hat mehrere "
                        f"Abstimmungspositionen ({', '.join(p.id for p in coord)})")
                target = coord[0]
            else:
                target = project
        else:  # Umsetzung
            target = position or project

        if target is project:
            if not self.bookable(project):
                raise _Unassignable(
                    f"Ziel wäre die Projektebene, aber Abrechnungsprojekt {project.id} "
                    f"ist nicht bebuchbar ({yt.field_bookable} ≠ {yt.value_yes})")
        elif self.ext_project(project) and not self.ext_position(target):
            raise _Unassignable(
                f"Abrechnungsposition {target.id} hat keine {yt.field_ext_position}, "
                f"Abrechnungsprojekt {project.id} aber eine {yt.field_ext_project}")
        return target, project

    def describe(self, wi, target, project):
        """Beschreibungsteil eines Work Items (5.3)."""
        logged = self.issues.get(wi.issue)
        text = (wi.text or "").strip()
        base = text or (logged.summary if logged else wi.issue)
        if (wi.work_type == self.yt.work_meeting and target is not project
                and logged is not None and logged.id not in (project.id, target.id)):
            topic = f"{self.yt.work_meeting} {logged.summary}"
            return f"{topic}: {text}" if text else topic
        return base


def evaluate(work_items, issues, yt):
    """Work Items → Zeitzeilen, nicht zuordenbare und nicht abrechenbare Einträge."""
    res = Resolver(issues, yt)
    rows = {}
    parts = {}  # Zeilenschlüssel → Beschreibungsteile in Reihenfolge, dedupliziert
    problems = []
    nonbillable = []
    known_types = (yt.work_implementation, yt.work_meeting, yt.work_nonbillable)

    for wi in sorted(work_items, key=lambda w: (w.date, w.created, w.id)):
        if wi.minutes <= 0:
            continue
        logged = issues.get(wi.issue)
        summary = logged.summary if logged else ""
        if not wi.work_type:
            problems.append(Problem(wi, summary, "Arbeitstyp fehlt"))
            continue
        if wi.work_type == yt.work_nonbillable:
            project = res.billing_project_of(wi.issue)
            nonbillable.append(NonBillable(
                wi, summary, project.id if project else None,
                project.summary if project else None))
            continue
        if wi.work_type not in known_types:
            problems.append(Problem(wi, summary, f"Unbekannter Arbeitstyp „{wi.work_type}“"))
            continue
        try:
            target, project = res.target(wi)
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
        part = res.describe(wi, target, project)
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
    """1,75h – durch die Rundung auf 15 Minuten immer exakt."""
    return f"{minutes // 60},{minutes % 60 * 100 // 60:02d}h"
