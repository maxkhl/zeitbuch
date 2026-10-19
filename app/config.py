"""Konfiguration ausschließlich aus Env-Variablen mit Präfix ZEITBUCH_."""

import os
import re
from dataclasses import dataclass, field, fields


def _env(name, default):
    value = os.environ.get(name)
    return default if value is None or value.strip() == "" else value.strip()


def _env_int(name, default):
    value = _env(name, None)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        raise ValueError(f"{name} muss eine ganze Zahl sein, ist aber {value!r}")


@dataclass(frozen=True)
class FieldConfig:
    """Namen und Werte der YouTrack-Felder (Konzept 8.1). Gilt für alle Projekte."""

    field_type: str = "Type"
    type_project: str = "Abrechnungsprojekt"
    type_position: str = "Abrechnungsposition"
    field_ext_project: str = "Ext-Projekt"
    field_ext_position: str = "Ext-Position"
    field_bookable: str = "Bebuchbar"
    field_coordination: str = "Abstimmung"
    value_yes: str = "ja"
    work_implementation: str = "Umsetzung"
    work_meeting: str = "Besprechung"
    work_nonbillable: str = "Nicht abrechenbar"
    authors: tuple = ()

    # Attribut → Env-Variable (ohne authors, das ist eine Liste)
    ENV = {
        "field_type": "ZEITBUCH_YT_FIELD_TYPE",
        "type_project": "ZEITBUCH_YT_TYPE_PROJECT",
        "type_position": "ZEITBUCH_YT_TYPE_POSITION",
        "field_ext_project": "ZEITBUCH_YT_FIELD_EXT_PROJECT",
        "field_ext_position": "ZEITBUCH_YT_FIELD_EXT_POSITION",
        "field_bookable": "ZEITBUCH_YT_FIELD_BOOKABLE",
        "field_coordination": "ZEITBUCH_YT_FIELD_COORDINATION",
        "value_yes": "ZEITBUCH_YT_VALUE_YES",
        "work_implementation": "ZEITBUCH_YT_WORK_IMPLEMENTATION",
        "work_meeting": "ZEITBUCH_YT_WORK_MEETING",
        "work_nonbillable": "ZEITBUCH_YT_WORK_NONBILLABLE",
    }

    @classmethod
    def from_env(cls):
        values = {attr: _env(env, getattr(cls, attr)) for attr, env in cls.ENV.items()}
        authors = _env("ZEITBUCH_YT_AUTHORS", "")
        values["authors"] = tuple(a.strip() for a in authors.split(",") if a.strip())
        return cls(**values)

    def as_list(self):
        """Für die Anzeige der aktiven Konfiguration (nur lesend)."""
        rows = [(env, getattr(self, attr)) for attr, env in self.ENV.items()]
        rows.append(("ZEITBUCH_YT_AUTHORS", ", ".join(self.authors) or "(alle)"))
        return rows


ENV_ATTRS = {
    "port": "ZEITBUCH_PORT",
    "bind": "ZEITBUCH_BIND",
    "db": "ZEITBUCH_DB",
    "backend": "ZEITBUCH_BACKEND",
    "youtrack_url": "ZEITBUCH_YOUTRACK_URL",
    "youtrack_token": "ZEITBUCH_YOUTRACK_TOKEN",
    "dolibarr_url": "ZEITBUCH_DOLIBARR_URL",
    "dolibarr_key": "ZEITBUCH_DOLIBARR_KEY",
    "http_timeout": "ZEITBUCH_HTTP_TIMEOUT",
}


@dataclass
class Config:
    port: int = 8000
    bind: str = "0.0.0.0"
    db: str = "/data/zeitbuch.db"
    # auto = je System echt, wenn URL und Zugang gesetzt sind, sonst Mock
    backend: str = "auto"
    youtrack_url: str = ""
    youtrack_token: str = ""
    dolibarr_url: str = ""
    dolibarr_key: str = ""
    http_timeout: int = 20
    yt: FieldConfig = field(default_factory=FieldConfig)

    @classmethod
    def from_env(cls):
        cfg = cls(
            port=_env_int("ZEITBUCH_PORT", 8000),
            bind=_env("ZEITBUCH_BIND", cls.bind),
            db=_env("ZEITBUCH_DB", cls.db),
            backend=_env("ZEITBUCH_BACKEND", "auto").lower(),
            youtrack_url=_env("ZEITBUCH_YOUTRACK_URL", "").rstrip("/"),
            youtrack_token=_env("ZEITBUCH_YOUTRACK_TOKEN", ""),
            dolibarr_url=_env("ZEITBUCH_DOLIBARR_URL", "").rstrip("/"),
            dolibarr_key=_env("ZEITBUCH_DOLIBARR_KEY", ""),
            http_timeout=_env_int("ZEITBUCH_HTTP_TIMEOUT", 20),
            yt=FieldConfig.from_env(),
        )
        cfg.validate()
        return cfg

    def validate(self):
        if self.backend not in ("auto", "real", "mock"):
            raise ValueError("ZEITBUCH_BACKEND muss auto, real oder mock sein")
        if not 1 <= self.port <= 65535:
            raise ValueError("ZEITBUCH_PORT muss zwischen 1 und 65535 liegen")
        if not 1 <= self.http_timeout <= 300:
            raise ValueError("ZEITBUCH_HTTP_TIMEOUT muss zwischen 1 und 300 Sekunden liegen")
        for name, url in (("ZEITBUCH_YOUTRACK_URL", self.youtrack_url),
                          ("ZEITBUCH_DOLIBARR_URL", self.dolibarr_url)):
            if url and not re.fullmatch(r"https?://[^\s\"'<>]+", url):
                raise ValueError(f"{name} muss mit http:// oder https:// beginnen")
        if self.backend == "real":
            missing = [env for attr, env in ENV_ATTRS.items()
                       if attr in ("youtrack_url", "youtrack_token", "dolibarr_url", "dolibarr_key")
                       and not getattr(self, attr)]
            if missing:
                raise ValueError("ZEITBUCH_BACKEND=real, aber nicht gesetzt: " + ", ".join(missing))
        for f in fields(FieldConfig):
            if f.name != "authors" and not getattr(self.yt, f.name):
                raise ValueError(f"{FieldConfig.ENV[f.name]} darf nicht leer sein")

    @property
    def youtrack_real(self):
        return self.backend == "real" or (
            self.backend == "auto" and bool(self.youtrack_url and self.youtrack_token))

    @property
    def dolibarr_real(self):
        return self.backend == "real" or (
            self.backend == "auto" and bool(self.dolibarr_url and self.dolibarr_key))
