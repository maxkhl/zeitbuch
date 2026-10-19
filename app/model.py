"""Backend-neutrale Datenobjekte für YouTrack-Daten."""

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True)
class WorkItem:
    id: str
    issue: str  # idReadable des Tickets, an dem geloggt wurde
    date: date
    minutes: int
    work_type: str | None  # Name des Arbeitstyps, None = leer
    text: str = ""
    author: str | None = None
    created: int = 0  # ms, nur für eine stabile Reihenfolge
    # Work-Item-Attribute: Name → Wert, None = leer
    attributes: dict = field(default_factory=dict, compare=False)


@dataclass
class Issue:
    id: str  # idReadable, z. B. CON-010
    summary: str
    project: str  # shortName des YouTrack-Projekts
    parent: str | None = None  # idReadable des Parents
    # Custom Fields: Name → Wert als Text (Enum: Name des Werts), None = leer
    fields: dict = field(default_factory=dict)
