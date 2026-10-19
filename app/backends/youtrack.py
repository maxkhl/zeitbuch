"""YouTrack: nur lesend. Echte REST-Anbindung und Mock auf Basis von testdata/."""

import json
import logging
from datetime import date, datetime, timezone
from pathlib import Path

from ..model import Issue, WorkItem
from . import BackendError

log = logging.getLogger(__name__)

TESTDATA = Path(__file__).resolve().parent.parent.parent / "testdata" / "youtrack"

WORK_ITEM_FIELDS = "id,date,duration(minutes),type(name),text,author(login),issue(idReadable),created"
ISSUE_FIELDS = ("idReadable,summary,project(shortName),parent(issues(idReadable)),"
                "customFields(name,value(name,text,login))")
PAGE = 500
ISSUE_BATCH = 40


# ------------------------------------------------------------ Parser (echt und Mock)

def _field_value(value):
    if value is None:
        return None
    if isinstance(value, dict):
        v = value.get("name") or value.get("text") or value.get("login")
        return str(v) if v is not None else None
    if isinstance(value, list):
        names = [_field_value(v) for v in value]
        return ", ".join(n for n in names if n) or None
    return str(value)


def parse_work_item(raw):
    ms = raw.get("date")
    if ms is None:
        raise BackendError(f"Work Item {raw.get('id')} ohne Datum")
    # date ist ein UTC-Zeitstempel auf Mitternacht des Tages
    day = datetime.fromtimestamp(ms / 1000, tz=timezone.utc).date()
    return WorkItem(
        id=str(raw["id"]),
        issue=(raw.get("issue") or {}).get("idReadable", ""),
        date=day,
        minutes=int((raw.get("duration") or {}).get("minutes") or 0),
        work_type=(raw.get("type") or {}).get("name") or None,
        text=raw.get("text") or "",
        author=(raw.get("author") or {}).get("login"),
        created=int(raw.get("created") or 0),
    )


def parse_issue(raw):
    parents = (raw.get("parent") or {}).get("issues") or []
    return Issue(
        id=raw["idReadable"],
        summary=raw.get("summary") or "",
        project=(raw.get("project") or {}).get("shortName", ""),
        parent=parents[0]["idReadable"] if parents else None,
        fields={cf["name"]: _field_value(cf.get("value")) for cf in raw.get("customFields") or []},
    )


def parse_project_config(project_fields, time_tracking):
    """→ {"fields": {Name: [Werte] | None}, "time_tracking": bool, "work_types": [...]}"""
    fields = {}
    for pf in project_fields or []:
        name = (pf.get("field") or {}).get("name")
        if not name:
            continue
        bundle = pf.get("bundle")
        fields[name] = [v.get("name") for v in bundle.get("values") or []] if bundle else None
    tt = time_tracking or {}
    return {
        "fields": fields,
        "time_tracking": bool(tt.get("enabled")),
        "work_types": [t.get("name") for t in tt.get("workItemTypes") or []],
    }


def _quote(value):
    # Werte und Feldnamen in YouTrack-Suchanfragen in {} – erlaubt Leer- und Sonderzeichen
    return "{" + value.replace("}", "") + "}"


def _project_query(projects):
    return "project: " + ", ".join(_quote(p) for p in projects)


class YouTrackBase:
    mode = "real"

    def load(self, start, end, projects, yt):
        """Work Items des Zeitraums plus alle nötigen Tickets (Kette und Abrechnungstickets)."""
        if not projects:
            return [], {}
        items = self.work_items(start, end, projects, yt.authors)
        issues = {i.id: i for i in self.billing_issues(projects, yt)}
        wanted = {wi.issue for wi in items}
        while True:
            missing = {i for i in wanted if i and i not in issues}
            if not missing:
                break
            loaded = self.issues(sorted(missing))
            for issue in loaded:
                issues[issue.id] = issue
            for i in missing - {issue.id for issue in loaded}:
                # Gelöscht oder kein Zugriff: als Ticket ohne Felder führen, damit es
                # unter „Nicht zuordenbar“ erscheint statt die Suche zu wiederholen.
                issues[i] = Issue(i, "", i.split("-")[0])
            wanted = {i.parent for i in issues.values() if i.parent}
        return items, issues


class YouTrack(YouTrackBase):
    def __init__(self, url, token, timeout=20):
        import requests

        self.url = url
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update({
            "Authorization": f"Bearer {token}",
            "Accept": "application/json",
        })

    def issue_url(self, issue_id):
        return f"{self.url}/issue/{issue_id}"

    def _get(self, path, params=None):
        import requests

        try:
            r = self.session.get(self.url + path, params=params, timeout=self.timeout)
        except requests.RequestException as e:
            raise BackendError(f"YouTrack nicht erreichbar: {e.__class__.__name__}")
        if r.status_code in (401, 403):
            raise BackendError(f"YouTrack verweigert den Zugriff (HTTP {r.status_code}) – Token prüfen",
                               r.status_code)
        if r.status_code >= 400:
            detail = ""
            try:
                detail = r.json().get("error_description") or ""
            except ValueError:
                pass
            raise BackendError(f"YouTrack-Fehler HTTP {r.status_code} bei {path}"
                               + (f": {detail}" if detail else ""), r.status_code)
        try:
            return r.json()
        except ValueError:
            raise BackendError(f"YouTrack lieferte kein JSON bei {path} – URL prüfen")

    def _paged(self, path, params):
        skip = 0
        while True:
            page = self._get(path, {**params, "$top": PAGE, "$skip": skip})
            yield from page
            if len(page) < PAGE:
                return
            skip += PAGE

    def ping(self):
        me = self._get("/api/users/me", {"fields": "login,name"})
        return me.get("login") or me.get("name") or "?"

    def projects(self):
        raw = self._paged("/api/admin/projects", {"fields": "id,shortName,name,archived"})
        return [{"id": p["id"], "short_name": p["shortName"], "name": p.get("name") or ""}
                for p in raw if not p.get("archived")]

    def work_items(self, start, end, projects, authors=()):
        params = {
            "fields": WORK_ITEM_FIELDS,
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "query": _project_query(projects),
        }
        if authors:
            params["author"] = list(authors)
        return [parse_work_item(r) for r in self._paged("/api/workItems", params)]

    def issues(self, ids):
        result = []
        for i in range(0, len(ids), ISSUE_BATCH):
            batch = ids[i:i + ISSUE_BATCH]
            query = "issue id: " + ", ".join(batch)
            try:
                raw = self._get("/api/issues", {"query": query, "fields": ISSUE_FIELDS,
                                                "$top": len(batch) * 2})
            except BackendError as e:
                if e.status != 400:
                    raise
                # Unbekannte ID in der Suche (gelöschtes Ticket): einzeln laden
                raw = []
                for issue_id in batch:
                    try:
                        raw.append(self._get(f"/api/issues/{issue_id}", {"fields": ISSUE_FIELDS}))
                    except BackendError as e2:
                        if e2.status != 404:
                            raise
            result.extend(parse_issue(r) for r in raw)
        return result

    def billing_issues(self, projects, yt):
        query = (f"{_project_query(projects)} {_quote(yt.field_type)}: "
                 f"{_quote(yt.type_project)}, {_quote(yt.type_position)}")
        raw = self._paged("/api/issues", {"query": query, "fields": ISSUE_FIELDS})
        return [parse_issue(r) for r in raw]

    def project_config(self, short_name):
        match = [p for p in self.projects() if p["short_name"] == short_name]
        if not match:
            return None
        pid = match[0]["id"]
        fields = self._get(f"/api/admin/projects/{pid}/customFields",
                           {"fields": "field(name),bundle(values(name))", "$top": 500})
        tt = self._get(f"/api/admin/projects/{pid}/timeTrackingSettings",
                       {"fields": "enabled,workItemTypes(name)"})
        return parse_project_config(fields, tt)


class MockYouTrack(YouTrackBase):
    """Liest anonymisierte API-Antworten aus testdata/youtrack/.

    Die Work Items liegen in einem Beispielmonat; der Mock verschiebt sie in den
    angefragten Monat (gleicher Tag), damit die UI in jedem Monat Daten zeigt.
    """

    mode = "mock"

    def __init__(self, directory=TESTDATA, shift_month=True):
        def load(name):
            return json.loads((Path(directory) / name).read_text(encoding="utf-8"))

        self.raw_work_items = load("workitems.json")
        self.raw_issues = load("issues.json")
        self.raw_projects = load("projects.json")
        self.shift_month = shift_month

    def issue_url(self, issue_id):
        return f"https://youtrack.example.org/issue/{issue_id}"

    def ping(self):
        return "mock"

    def projects(self):
        return [{"id": p["id"], "short_name": p["shortName"], "name": p["name"]}
                for p in self.raw_projects]

    def work_items(self, start, end, projects, authors=()):
        result = []
        for raw in self.raw_work_items:
            wi = parse_work_item(raw)
            if self.shift_month:
                try:
                    wi = WorkItem(**{**wi.__dict__, "date": date(start.year, start.month, wi.date.day)})
                except ValueError:  # 31. im kürzeren Monat
                    continue
            if not start <= wi.date <= end:
                continue
            if wi.issue.split("-")[0] not in projects:
                continue
            if authors and wi.author not in authors:
                continue
            result.append(wi)
        return result

    def issues(self, ids):
        wanted = set(ids)
        return [parse_issue(r) for r in self.raw_issues if r["idReadable"] in wanted]

    def billing_issues(self, projects, yt):
        result = []
        for raw in self.raw_issues:
            issue = parse_issue(raw)
            if issue.project in projects and issue.fields.get(yt.field_type) in (
                    yt.type_project, yt.type_position):
                result.append(issue)
        return result

    def project_config(self, short_name):
        for p in self.raw_projects:
            if p["shortName"] == short_name:
                return parse_project_config(p["customFields"], p["timeTrackingSettings"])
        return None
