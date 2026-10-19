"""Abgleich mit Dolibarr (Konzept 7): Vorschau und Übertragen.

Grundsätze:
- zeitbuch löscht und verschiebt in Dolibarr nie etwas.
- Angefasst werden nur Aufgaben und Buchungen mit gespeicherter ID.
- Fakturierte Buchungen werden nie geändert; Abweichungen sind nur Warnungen.
- Übertragen berechnet die Vorschau neu und bricht ab, wenn sie sich seit der
  Anzeige geändert hat (Fingerprint).
"""

import hashlib
import json
import threading

from .backends import BackendError
from .month import duration
from .rules import POSITION, PROJECT, Resolver


class SyncError(Exception):
    pass


class StalePlan(SyncError):
    pass


_apply_lock = threading.Lock()


def task_label(issue):
    return f"{issue.id} {issue.summary}".strip()


def _warn(text, url=None, level="warn"):
    return {"text": text, "url": url, "level": level}


def build_plan(month, result, issues, db, doli, yt, youtrack):
    """Vorschau für einen Monat. Liest Dolibarr, schreibt nichts."""
    res = Resolver(issues, yt)
    mapping = {p["short_name"]: p["dolibarr_project_id"] for p in db.projects()}
    task_map = db.tasks()
    warnings = []
    forget_tasks, forget_times = set(), set()

    # --- gemappte Dolibarr-Projekte prüfen
    doli_projects = {}
    for short, pid in mapping.items():
        if pid is None:
            continue
        project = doli.project(pid)
        if project is None:
            warnings.append(_warn(f"Dolibarr-Projekt {pid} (Mapping für {short}) nicht gefunden – "
                                  f"Zeilen aus {short} werden nicht gebucht", level="error"))
        else:
            doli_projects[short] = project

    rows, skipped = [], {}
    for row in result.rows:
        if row.yt_project in doli_projects:
            rows.append(row)
        else:
            skipped[row.yt_project] = skipped.get(row.yt_project, 0) + 1
    for short, n in sorted(skipped.items()):
        if mapping.get(short) is None:
            warnings.append(_warn(f"YouTrack-Projekt {short} ist keinem Dolibarr-Projekt "
                                  f"zugeordnet – {n} Zeile(n) werden nicht gebucht", level="info"))
    if result.problems:
        warnings.append(_warn(f"{len(result.problems)} Work Item(s) nicht zuordenbar – "
                              "sie fehlen in den Buchungen, bis sie in YouTrack korrigiert sind"))

    # --- Aufgaben (7.1)
    task_ids = {}  # yt_issue → Dolibarr-ID (bestehend) oder None (wird angelegt)
    creates = []
    task_exists = {}

    def existing_task(yt_issue):
        """Gespeicherte Aufgabe, die in Dolibarr noch existiert, sonst None."""
        if yt_issue not in task_exists:
            m = task_map.get(yt_issue)
            ok = m is not None and doli.task(m["task_id"]) is not None
            if m is not None and not ok:
                forget_tasks.add(yt_issue)
                warnings.append(_warn(f"Dolibarr-Aufgabe zu {yt_issue} existiert nicht mehr – "
                                      "wird neu angelegt", level="info"))
            task_exists[yt_issue] = ok
        return task_map[yt_issue] if task_exists[yt_issue] else None

    def need(issue, parent, short):
        if issue.id in task_ids:
            return
        m = existing_task(issue.id)
        if m is not None:
            task_ids[issue.id] = m["task_id"]
            return
        task_ids[issue.id] = None
        creates.append({"yt_issue": issue.id, "label": task_label(issue),
                        "parent_yt_issue": parent.id if parent else None,
                        "dolibarr_project_id": doli_projects[short]["id"],
                        "dolibarr_project": doli_projects[short]["ref"],
                        "kind": POSITION if parent else PROJECT})

    for row in rows:
        project = issues[row.billing_project]
        need(project, None, row.yt_project)
        if row.target_kind == POSITION:
            need(issues[row.target], project, row.yt_project)

    # Umbenennungen und Strukturwarnungen für alle gespeicherten Aufgaben
    renames = []
    for yt_issue, m in sorted(task_map.items()):
        if yt_issue in forget_tasks:
            continue
        issue = issues.get(yt_issue)
        kind = res.kind(issue) if issue else None
        url = doli.task_url(m["task_id"])
        if kind is None:
            short = yt_issue.split("-")[0]
            if short in mapping:
                warnings.append(_warn(
                    f"{yt_issue} ist in YouTrack gelöscht oder kein Abrechnungsticket mehr – "
                    f"Dolibarr-Aufgabe „{m['label']}“ von Hand prüfen", url))
            continue
        if (kind == POSITION) != bool(m["parent_yt_issue"]):
            warnings.append(_warn(
                f"{yt_issue} hat in YouTrack die Ebene gewechselt (Projekt ↔ Position) – "
                f"Dolibarr-Aufgabe „{m['label']}“ wird nicht verschoben", url))
        elif kind == POSITION:
            parent = res.billing_project_of(issue.parent) if issue.parent else None
            if parent is None or parent.id != m["parent_yt_issue"]:
                warnings.append(_warn(
                    f"{yt_issue} wurde in YouTrack umgehängt "
                    f"({m['parent_yt_issue']} → {parent.id if parent else 'ohne Abrechnungsprojekt'}) – "
                    f"Dolibarr-Aufgabe „{m['label']}“ wird nicht verschoben", url))
        if issue.project in doli_projects and doli_projects[issue.project]["id"] != m["dolibarr_project_id"]:
            warnings.append(_warn(
                f"Dolibarr-Aufgabe „{m['label']}“ liegt in einem anderen Dolibarr-Projekt als das "
                f"aktuelle Mapping von {issue.project} – es wird weiter auf diese Aufgabe gebucht", url))
        if existing_task(yt_issue) is None:
            continue
        new = task_label(issue)
        if new != m["label"]:
            renames.append({"yt_issue": yt_issue, "task_id": m["task_id"], "old": m["label"],
                            "new": new, "url": url})

    # --- Zeitbuchungen (7.2)
    times_map = db.times(month)
    times = []
    row_keys = set()
    for row in rows:
        row_keys.add(row.key)
        seconds = row.minutes * 60
        note = row.description.strip()
        task_id = task_ids[row.target]
        item = {"key": row.key, "date": row.date.isoformat(), "target": row.target,
                "target_label": task_label(issues[row.target]), "yt_project": row.yt_project,
                "task_id": task_id, "seconds": seconds, **_dur(seconds), "note": note,
                "yt_url": youtrack.issue_url(row.target),
                "url": doli.time_url(task_id) if task_id else None}
        stored = times_map.get(row.key)
        entry = None
        if stored is not None and task_id is not None and stored["task_id"] == task_id:
            entry = doli.time_entry(stored["task_id"], stored["time_id"])
            if entry is None:
                forget_times.add(row.key)
                item["hint"] = "Buchung in Dolibarr nicht mehr vorhanden – wird neu angelegt"
        elif stored is not None:
            # Aufgabe wurde neu angelegt; die alte Buchung hängt an der alten Aufgabe
            entry = doli.time_entry(stored["task_id"], stored["time_id"])
            if entry is not None:
                warnings.append(_warn(
                    f"Buchung {row.key} hängt an einer anderen Dolibarr-Aufgabe als dem aktuellen "
                    "Ziel – bitte von Hand prüfen", doli.time_url(stored["task_id"])))
                item["action"] = "conflict"
                times.append(item)
                continue
            forget_times.add(row.key)

        if entry is None:
            item["action"] = "create"
        else:
            item.update(time_id=entry["id"], old={**_dur(entry["seconds"]), "seconds": entry["seconds"],
                                                   "note": entry["note"]})
            same = entry["seconds"] == seconds and entry["note"] == note
            if entry["invoiced"]:
                item["action"] = "locked" if same else "locked_diff"
                if not same:
                    warnings.append(_warn(
                        f"{row.date.isoformat()} {row.target}: Buchung ist fakturiert, weicht aber "
                        f"von YouTrack ab ({_dur(entry['seconds'])['hm']} → {_dur(seconds)['hm']}) – "
                        "wird nicht geändert", item["url"]))
            else:
                item["action"] = "unchanged" if same else "update"
        times.append(item)

    # Verwaiste Buchungen: gespeichert, aber keine Zeile mehr dazu
    orphans = []
    for key, stored in sorted(times_map.items()):
        if key in row_keys:
            continue
        entry = doli.time_entry(stored["task_id"], stored["time_id"])
        if entry is None:
            forget_times.add(key)
            continue
        orphans.append({"key": key, "date": stored["date"], "target": stored["target"],
                        "task_id": stored["task_id"], "time_id": stored["time_id"],
                        **_dur(entry["seconds"]), "note": entry["note"],
                        "invoiced": entry["invoiced"], "url": doli.time_url(stored["task_id"])})
    for o in orphans:
        warnings.append(_warn(
            f"Verwaiste Buchung {o['date']} {o['target']} ({o['hm']})"
            + (" – fakturiert" if o["invoiced"] else "")
            + " – die Zeile gibt es in YouTrack nicht mehr; von Hand löschen", o["url"]))

    plan = {
        "tasks_create": creates,
        "tasks_rename": renames,
        "times": times,
        "orphans": orphans,
        "warnings": warnings,
        "forget_tasks": sorted(forget_tasks),
        "forget_times": sorted(forget_times),
        "counts": {
            "create_tasks": len(creates),
            "rename_tasks": len(renames),
            **{a: sum(1 for t in times if t["action"] == a)
               for a in ("create", "update", "unchanged", "locked", "locked_diff", "conflict")},
            "orphans": len(orphans),
        },
    }
    plan["fingerprint"] = fingerprint(plan)
    plan["pending"] = bool(creates or renames or plan["counts"]["create"] or plan["counts"]["update"])
    return plan


def _dur(seconds):
    return duration(seconds // 60)


def fingerprint(plan):
    actions = {
        "c": [(c["yt_issue"], c["label"], c["parent_yt_issue"], c["dolibarr_project_id"])
              for c in plan["tasks_create"]],
        "r": [(r["yt_issue"], r["new"]) for r in plan["tasks_rename"]],
        "t": [(t["key"], t["action"], t["seconds"], t["note"]) for t in plan["times"]
              if t["action"] in ("create", "update")],
    }
    return hashlib.sha256(json.dumps(actions, sort_keys=True).encode()).hexdigest()[:16]


def apply_plan(plan, db, doli, expected_fingerprint):
    """Führt eine frisch berechnete Vorschau aus. Jeder Schritt wird sofort gespeichert,
    damit ein Abbruch beim nächsten Übertragen sauber weiterläuft."""
    if plan["fingerprint"] != expected_fingerprint:
        raise StalePlan("Die Vorschau ist veraltet – YouTrack oder Dolibarr haben sich seitdem "
                        "geändert. Bitte Vorschau neu laden und erneut prüfen.")
    if not _apply_lock.acquire(blocking=False):
        raise SyncError("Eine Übertragung läuft bereits")
    done = {"tasks_created": 0, "tasks_renamed": 0, "times_created": 0, "times_updated": 0,
            "skipped": []}
    try:
        for yt_issue in plan["forget_tasks"]:
            db.forget_task(yt_issue)
        for key in plan["forget_times"]:
            db.forget_time(key)

        task_ids = {k: v["task_id"] for k, v in db.tasks().items()}
        # Projekte vor Positionen, damit die übergeordnete Aufgabe schon existiert
        for c in sorted(plan["tasks_create"], key=lambda c: c["kind"] != PROJECT):
            parent_id = task_ids.get(c["parent_yt_issue"]) if c["parent_yt_issue"] else None
            if c["parent_yt_issue"] and not parent_id:
                raise SyncError(f"Übergeordnete Aufgabe zu {c['yt_issue']} fehlt")
            task_id = doli.create_task(c["dolibarr_project_id"], c["label"], parent_id)
            db.save_task(c["yt_issue"], task_id, c["dolibarr_project_id"], c["parent_yt_issue"],
                         c["label"])
            task_ids[c["yt_issue"]] = task_id
            done["tasks_created"] += 1

        for r in plan["tasks_rename"]:
            doli.rename_task(r["task_id"], r["new"])
            db.set_task_label(r["yt_issue"], r["new"])
            done["tasks_renamed"] += 1

        for t in plan["times"]:
            if t["action"] == "create":
                task_id = t["task_id"] or task_ids[t["target"]]
                time_id = doli.add_time(task_id, t["date"], t["seconds"], t["note"])
                db.save_time(t["key"], t["date"], t["target"], task_id, time_id, t["seconds"], t["note"])
                done["times_created"] += 1
            elif t["action"] == "update":
                # Unmittelbar vorher erneut lesen: PUT würde das Fakturiert-Kennzeichen löschen
                entry = doli.time_entry(t["task_id"], t["time_id"])
                if entry is None or entry["invoiced"]:
                    done["skipped"].append(f"{t['date']} {t['target']}: inzwischen "
                                           + ("fakturiert" if entry else "gelöscht")
                                           + " – nicht geändert")
                    continue
                doli.update_time(t["task_id"], t["time_id"], t["date"], t["seconds"], t["note"])
                db.save_time(t["key"], t["date"], t["target"], t["task_id"], t["time_id"],
                             t["seconds"], t["note"])
                done["times_updated"] += 1
    except BackendError as e:
        done["error"] = str(e)
    finally:
        _apply_lock.release()
    return done
