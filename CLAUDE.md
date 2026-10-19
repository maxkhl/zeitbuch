# CLAUDE.md – zeitbuch

Kleine Web-App: liest Work Items aus YouTrack, ordnet sie Abrechnungsprojekten
und -positionen zu, zeigt gerundete Zeitzeilen zum Übertragen in externe
Systeme und bucht sie per API nach Dolibarr. Fachliche Grundlage ist
`docs/konzept.md`; die API-Befunde stehen in `docs/api-verifikation.md`.
UI, Doku und Fehlermeldungen sind auf Deutsch.

## Entscheidungen (nicht ohne Grund ändern)

- **Stack:** Python 3.13 + Flask + waitress + requests, Vanilla-JS/CSS ohne
  Build-Schritt und ohne CDN (muss offline im LAN laufen). Kein Go, kein PHP.
  Im Container kommen die Pakete per apt aus Debian trixie, nicht per pip.
  Weitere Abhängigkeiten nur nach Rücksprache.
- **Genau ein Prozess** (waitress mit Threads). Die Übertragungssperre
  (`_apply_lock` in `app/sync.py`) liegt im Arbeitsspeicher.
- **SQLite** über `sqlite3` in `/data`, WAL-Modus. Schema-Version über
  `PRAGMA user_version`; neue Migration = neuer Eintrag am Ende von
  `MIGRATIONS` in `app/db.py`, bestehende nie ändern.
- **Keine eigene Anmeldung**; Authentik Forward Auth im NPM davor.
  Schreibende Requests verlangen `Content-Type: application/json` (sonst 415).
  CSP `default-src 'self'`: keine Inline-Skripte, keine `style=`-Attribute.
- **Konfiguration nur per Env-Variablen** mit Präfix `ZEITBUCH_`. Feldnamen,
  Werte und Arbeitstypen von YouTrack sind nie fest verdrahtet (`FieldConfig`).
  Neue Variable → `config.py`, `.env.example`, README-Tabelle.
- **Keine Features außerhalb des Konzepts.** Ideen als Vorschlag nennen.

## Aufbau

- `app/rules.py` – Zuordnung, Fehlerfälle, Aggregation, Rundung (Konzept 5).
  Ziel ist immer der nächste Abrechnungsknoten, der bebuchbar sein muss; nie
  auf höhere Knoten ausweichen. Abstimmung optional (0 = keine Umleitung).
  Nicht abrechenbar = Work-Item-Attribut (`ZEITBUCH_YT_ATTR_BILLABLE`), wird vor
  allen anderen Regeln aussortiert; es gibt keinen Arbeitstyp dafür.
  Reine Funktionen, keine I/O. Hier liegt die Fachlogik; Änderungen nur mit Test.
- `app/month.py` – Monatsauswertung für die UI, übertragen-Zustand.
- `app/sync.py` – Dolibarr-Vorschau und -Übertragung (Konzept 7).
- `app/backends/` – `youtrack.py` und `dolibarr.py` mit je echter und
  Mock-Implementierung. `ZEITBUCH_BACKEND=auto` nimmt je System den Mock, wenn
  URL oder Zugang fehlen (laut geloggt, UI-Banner „MOCK-MODUS“).
- `app/checks.py` – Konfigurationsprüfung (Konzept 8.1).

## YouTrack

- **Nur lesend.** Kein Schreibzugriff, auch nicht „nur kurz“.
- Work Items über `/api/workItems` mit `startDate`/`endDate` (inklusive) und
  `query=project: {A}, {B}`; immer mit `$top`/`$skip` paginieren (Default 42).
- `date` eines Work Items ist UTC-Mitternacht → UTC-Datum verwenden.
- `load()` holt Abrechnungstickets der Projekte und alle Tickets der
  Parent-Ketten. Nicht ladbare Tickets (gelöscht) werden zu leeren Tickets und
  landen unter „Nicht zuordenbar“.

## Dolibarr (Details: docs/api-verifikation.md)

- **Nie löschen, nie verschieben.** Die Backends haben absichtlich keine
  Löschmethode (ein Test prüft das). `delete_by_hand` im Mock simuliert nur
  Handarbeit in Dolibarr für Tests.
- Nur Aufgaben/Buchungen mit gespeicherter ID anfassen (`dolibarr_task`,
  `dolibarr_time`); nie über Namen suchen.
- `addtimespent` liefert keine ID → Vorher/Nachher-Vergleich unter Sperre;
  nicht eindeutig = Abbruch mit Meldung, nie raten.
- Fakturiert = `invoice_id`, nur per `getTimeSpent` lesbar. `PUT timespent`
  setzt `invoice_id` zurück → direkt vor jedem PUT erneut lesen und bei
  fakturiert überspringen.
- Abrechenbar ist `billable` der Aufgabe; zeitbuch legt Aufgaben mit 1 an.
- Datum wird als `JJJJ-MM-TT 12:00:00` (GMT) gesendet.
- Gebucht wird auf `ZEITBUCH_DOLIBARR_USER_ID` (0 = API-Benutzer). Dolibarr prüft
  die ID nicht → zeitbuch prüft per `GET /users/{id}`; unbekannt = Übertragen
  gesperrt, 403 (Recht fehlt) = nur Warnung.
- Übertragen berechnet die Vorschau neu und vergleicht den Fingerprint; bei
  Abweichung 409 statt etwas anderes auszuführen als angezeigt.

## Arbeiten am Code

- Tests: `.venv/bin/python -m pytest -q` (venv aus `requirements-dev.txt`).
- Lokal: `ZEITBUCH_BACKEND=mock ZEITBUCH_DB=./data/zeitbuch.db .venv/bin/python -m app`.
- Der YouTrack-Mock verschiebt die Beispieldaten (September 2026) in den
  angefragten Monat; Tests nutzen `MockYouTrack(shift_month=False)` und `2026-09`.
- Ein neuer Backend-Aufruf gehört in beide Implementierungen und in einen Test
  (echte Implementierung gegen Fake-HTTP, siehe `tests/test_dolibarr.py`).

## Öffentliches Repo

Keine echten Kunden-, Projekt- oder Personendaten in Code, Doku, Commits oder
`testdata/`. Beispiele sind fiktiv (`CON`, `XSYS`, `S-2026-MUSTER-001`).
`testdata/youtrack/*.json` folgt dem Format echter API-Antworten; neue
Mitschnitte vor dem Commit anonymisieren.
