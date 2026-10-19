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
- **Genau ein Prozess** (waitress mit Threads).
- **SQLite** über `sqlite3` in `/data`. Schema-Version über `PRAGMA user_version`,
  kein Migrations-Framework.
- **Keine eigene Anmeldung**; Authentik Forward Auth im NPM davor.
  Schreibende Requests verlangen `Content-Type: application/json` (sonst 415).
- **Konfiguration nur per Env-Variablen** mit Präfix `ZEITBUCH_`. Feldnamen,
  Werte und Arbeitstypen von YouTrack sind nie fest verdrahtet (`FieldConfig`).
- **Keine Features außerhalb des Konzepts.** Ideen als Vorschlag nennen.

## Arbeiten am Code

- Tests: `.venv/bin/python -m pytest -q` (venv aus `requirements-dev.txt`).
- Lokal: `ZEITBUCH_BACKEND=mock ZEITBUCH_DB=./data/zeitbuch.db .venv/bin/python -m app`.

## Öffentliches Repo

Keine echten Kunden-, Projekt- oder Personendaten in Code, Doku, Commits oder
`testdata/`. Beispiele sind fiktiv (`CON`, `XSYS`, `S-2026-MUSTER-001`).
Aufgezeichnete API-Antworten vor dem Commit anonymisieren.
