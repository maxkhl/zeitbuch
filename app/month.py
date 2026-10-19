"""Monatsauswertung: YouTrack laden, Regeln anwenden, übertragen-Zustand ergänzen."""

import calendar
import re
from datetime import date

from .rules import PROJECT, evaluate, format_decimal, format_hm


def parse_month(value):
    """'2026-09' → (date(2026, 9, 1), date(2026, 9, 30)); ValueError bei Unsinn."""
    m = re.fullmatch(r"(\d{4})-(\d{2})", value or "")
    if not m or not 1 <= int(m.group(2)) <= 12:
        raise ValueError("Monat muss die Form JJJJ-MM haben")
    year, month = int(m.group(1)), int(m.group(2))
    return date(year, month, 1), date(year, month, calendar.monthrange(year, month)[1])


def duration(minutes):
    return {"minutes": minutes, "hm": format_hm(minutes), "decimal": format_decimal(minutes)}


def load_month(youtrack, db, yt, month):
    """→ (Result der Regeln, Tickets). Nur gemappte YouTrack-Projekte."""
    start, end = parse_month(month)
    projects = [p["short_name"] for p in db.projects()]
    items, issues = youtrack.load(start, end, projects, yt)
    return evaluate(items, issues, yt), issues


def transfer_state(row, snapshot):
    """None = nicht übertragen, sonst {"state": "ok" | "changed", "old": …}."""
    if snapshot is None:
        return None
    changed = (snapshot["minutes"] != row.minutes or snapshot["ext_key"] != row.ext_key
               or snapshot["description"] != row.description)
    return {
        "state": "changed" if changed else "ok",
        "marked_at": snapshot["marked_at"],
        "old": {**duration(snapshot["minutes"]), "ext_key": snapshot["ext_key"],
                "description": snapshot["description"], "label": snapshot["label"]},
    }


def month_view(youtrack, db, yt, month):
    result, issues = load_month(youtrack, db, yt, month)
    snapshots = db.transfers(month)
    url = youtrack.issue_url

    rows = []
    for row in result.rows:
        rows.append({
            "key": row.key,
            "date": row.date.isoformat(),
            "target": row.target,
            "target_kind": row.target_kind,
            "billing_project": row.billing_project,
            "billing_project_label": row.billing_project_label,
            "yt_project": row.yt_project,
            "label": row.label,
            "ext_key": row.ext_key,
            "external": row.external,
            "raw_minutes": row.raw_minutes,
            **duration(row.minutes),
            "description": row.description,
            "url": url(row.target),
            "transfer": transfer_state(row, snapshots.get(row.key)) if row.external else None,
        })
    current = {r["key"] for r in rows if r["external"]}
    # Als übertragen markierte Zeilen, die es nicht mehr gibt: im externen System entfernen
    removed = [{**s, **duration(s["minutes"]), "key": s["row_key"]}
               for k, s in snapshots.items() if k not in current]

    def work_item(wi):
        return {"id": wi.id, "issue": wi.issue, "date": wi.date.isoformat(), **duration(wi.minutes),
                "type": wi.work_type, "text": wi.text, "author": wi.author,
                "attributes": wi.attributes}

    problems = [{"work_item": work_item(p.work_item), "issue_summary": p.issue_summary,
                 "reason": p.reason, "url": url(p.work_item.issue)} for p in result.problems]
    nonbillable = [{"work_item": work_item(n.work_item), "issue_summary": n.issue_summary,
                    "billing_project": n.billing_project,
                    "billing_project_label": n.billing_project_label,
                    "url": url(n.work_item.issue)} for n in result.nonbillable]

    overview = {}

    def project_entry(billing_project, label, yt_project):
        return overview.setdefault(billing_project, {
            "billing_project": billing_project, "label": label, "yt_project": yt_project,
            "url": url(billing_project) if billing_project else None,
            "minutes": 0, "nonbillable_minutes": 0, "targets": {}})

    for row in result.rows:
        proj = project_entry(row.billing_project, row.billing_project_label, row.yt_project)
        proj["minutes"] += row.minutes
        t = proj["targets"].setdefault(row.target, {
            "target": row.target, "label": row.label, "target_kind": row.target_kind,
            "ext_key": row.ext_key, "minutes": 0})
        t["minutes"] += row.minutes
    # Nicht abrechenbar: eigene Summe je Abrechnungsprojekt (roh, nicht gerundet, nie gebucht)
    for n in result.nonbillable:
        if n.billing_project:
            proj = project_entry(n.billing_project, n.billing_project_label,
                                 issues[n.billing_project].project)
        else:
            proj = project_entry(None, "Ohne Abrechnungsprojekt", n.work_item.issue.split("-")[0])
        proj["nonbillable_minutes"] += n.work_item.minutes
    overview_list = []
    for proj in sorted(overview.values(),
                       key=lambda p: (p["billing_project"] is None, p["yt_project"], p["billing_project"] or "")):
        targets = sorted(proj.pop("targets").values(),
                         key=lambda t: (t["target_kind"] != PROJECT, t["label"]))
        overview_list.append({**proj, **duration(proj["minutes"]),
                              "nonbillable": duration(proj.pop("nonbillable_minutes")),
                              "targets": [{**t, **duration(t["minutes"])} for t in targets]})

    changed = sum(1 for r in rows if r["transfer"] and r["transfer"]["state"] == "changed")
    total = sum(row.minutes for row in result.rows)
    return {
        "month": month,
        "projects": [p["short_name"] for p in db.projects()],
        "rows": rows,
        "removed_transfers": removed,
        "problems": problems,
        "nonbillable": nonbillable,
        "overview": overview_list,
        "totals": {
            **duration(total),
            "external": duration(sum(r["minutes"] for r in rows if r["external"])),
            "nonbillable": duration(sum(n.work_item.minutes for n in result.nonbillable)),
            "rows": len(rows),
            "problems": len(problems),
            "changed": changed + len(removed),
            "open": sum(1 for r in rows if r["external"] and not r["transfer"]),
        },
    }
