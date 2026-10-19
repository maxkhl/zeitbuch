"""Konfigurationsprüfung der YouTrack-Felder (Konzept 8.1)."""

from .backends import BackendError


def check_youtrack_config(youtrack, projects, yt):
    """Prüft je YouTrack-Projekt Felder, Werte und Arbeitstypen.

    Liefert eine Liste deutscher Meldungen; leer = alles vorhanden.
    """
    messages = []
    for project in projects:
        try:
            config = youtrack.project_config(project)
        except BackendError as e:
            messages.append(f"Projekt {project}: Konfiguration nicht lesbar – {e}")
            continue
        if config is None:
            messages.append(f"Projekt {project} nicht gefunden oder kein Zugriff")
            continue
        fields = config["fields"]

        def need_field(name):
            if name not in fields:
                messages.append(f"Feld „{name}“ fehlt in Projekt {project}")
                return False
            return True

        def need_values(name, values):
            if not need_field(name):
                return
            available = fields[name]
            if available is None:
                messages.append(f"Feld „{name}“ in Projekt {project} hat keine Werteliste")
                return
            for v in values:
                if v not in available:
                    messages.append(f"Wert „{v}“ fehlt im Feld „{name}“ in Projekt {project}")

        need_values(yt.field_type, (yt.type_project, yt.type_position))
        need_field(yt.field_ext_project)
        need_field(yt.field_ext_position)
        need_values(yt.field_bookable, (yt.value_yes,))
        need_values(yt.field_coordination, (yt.value_yes,))

        if not config["time_tracking"]:
            messages.append(f"Zeiterfassung ist in Projekt {project} nicht aktiviert")
        else:
            for t in (yt.work_implementation, yt.work_meeting):
                if t not in config["work_types"]:
                    messages.append(f"Arbeitstyp „{t}“ fehlt in Projekt {project}")
            attrs = config.get("attributes") or {}
            if yt.attr_billable not in attrs:
                messages.append(f"Work-Item-Attribut „{yt.attr_billable}“ fehlt in Projekt {project}")
            elif yt.attr_billable_no not in attrs[yt.attr_billable]:
                messages.append(f"Wert „{yt.attr_billable_no}“ fehlt im Work-Item-Attribut "
                                f"„{yt.attr_billable}“ in Projekt {project}")
    return messages
