# zeitbuch

Zeit wird genau einmal erfasst – als Work Item an YouTrack-Tickets. zeitbuch
leitet daraus ab:

1. **Zeitzeilen für externe Systeme** – eine neutrale Liste (Datum, Dauer,
   externe Kennung, Bezeichnung, Beschreibung) zum Übertragen von Hand in die
   Zeiterfassung eines Auftraggebers, mit Copy-Buttons und Häkchen „übertragen“.
2. **Dolibarr-Zeitbuchungen** per API als Grundlage der Monatsrechnung. Die
   Aufgabenstruktur legt zeitbuch selbst an; fakturiert wird weiter in Dolibarr.

Zuordnung zu Abrechnungsprojekten und -positionen inklusive Besprechungsregel
und Rundung (immer aufwärts auf 15 Minuten je Tag und Ziel) passieren
automatisch. Fachliche Grundlage: [docs/konzept.md](docs/konzept.md).

zeitbuch schreibt **nie** nach YouTrack und **löscht oder verschiebt in Dolibarr
nie etwas**.

## Inhalt

1. [Setup mit Docker Compose / Dockge](#setup-mit-docker-compose--dockge)
2. [Konfiguration](#konfiguration)
3. [Hinter Nginx Proxy Manager mit Authentik](#hinter-nginx-proxy-manager-mit-authentik)
4. [Einrichtung in YouTrack](#einrichtung-in-youtrack)
5. [Einrichtung in Dolibarr](#einrichtung-in-dolibarr)
6. [Bedienung](#bedienung)
7. [Regeln in Kürze](#regeln-in-kürze)
8. [Datensicherung](#datensicherung)
9. [Entwicklung](#entwicklung)
10. [API](#api)

## Setup mit Docker Compose / Dockge

```bash
# [Host]
git clone https://github.com/maxkhl/zeitbuch.git
cd zeitbuch
cp .env.example .env
# .env ausfüllen: URLs, Token/Key, ggf. Feldkonfiguration
docker compose up -d --build
```

In Dockge: neuen Stack anlegen, Inhalt von `docker-compose.yml` einfügen und die
Variablen aus `.env.example` im Env-Bereich des Stacks setzen. Secrets gehören
nur dorthin – nie ins Image, nie ins Repo.

Danach unter **Einstellungen** die YouTrack-Projekte hinzufügen, je Projekt das
Dolibarr-Projekt wählen und den **Verbindungstest** ausführen.

### Fertiges Image aus GHCR

Jeder Push auf `main` baut nach erfolgreichen Tests ein Image für `linux/amd64`:

- `ghcr.io/maxkhl/zeitbuch:latest`
- `ghcr.io/maxkhl/zeitbuch:<short-sha>` (für feste Versionen)

In `docker-compose.yml` die Zeile `build: .` auskommentieren und
`image: ghcr.io/maxkhl/zeitbuch:latest` aktivieren. Die Version (Short-SHA)
steht unten in der Oberfläche.

> Neue GHCR-Pakete sind zunächst **privat**. Einmalig auf GitHub unter
> *Packages → zeitbuch → Package settings → Change visibility* auf **Public**
> stellen, sonst braucht `docker pull` eine Anmeldung.

### Daten und Rechte

Die SQLite-Datenbank liegt im benannten Volume `zeitbuch-data` unter
`/data/zeitbuch.db`. Der Container läuft als unprivilegierter Benutzer
`zeitbuch` (UID/GID 10001); ein benanntes Volume übernimmt beim ersten Start die
Rechte aus dem Image. Bei einem Bind-Mount muss das Verzeichnis UID 10001 gehören:

```bash
# [Host]
mkdir -p ./data && sudo chown 10001:10001 ./data
```

## Konfiguration

Alle Einstellungen kommen aus Env-Variablen (siehe `.env.example`). Das
Mapping YouTrack-Projekt → Dolibarr-Projekt wird in der Oberfläche gepflegt und
in der Datenbank gespeichert.

### Betrieb und Zugänge

| Variable | Default | Bedeutung |
|---|---|---|
| `ZEITBUCH_PUBLISH` | `127.0.0.1:8000` | Nur Compose: Adresse:Port auf dem Host |
| `ZEITBUCH_PORT` | `8000` | Port im Container |
| `ZEITBUCH_BIND` | `0.0.0.0` | Bind-Adresse von waitress im Container |
| `ZEITBUCH_DB` | `/data/zeitbuch.db` | SQLite-Datei, muss im Volume liegen |
| `ZEITBUCH_BACKEND` | `auto` | `auto`: je System echt, wenn URL und Zugang gesetzt sind, sonst Mock. `real`: beide echt, fehlende Angaben sind ein Startfehler. `mock`: beide simuliert. Im Betrieb `real`. |
| `ZEITBUCH_HTTP_TIMEOUT` | `20` | Zeitlimit für API-Aufrufe in Sekunden |
| `ZEITBUCH_LOG_LEVEL` | `INFO` | Log-Level |
| `ZEITBUCH_YOUTRACK_URL` | – | Basis-URL von YouTrack ohne `/api` |
| `ZEITBUCH_YOUTRACK_TOKEN` | – | Permanent Token eines eigenen Benutzers mit **nur Leserechten** |
| `ZEITBUCH_DOLIBARR_URL` | – | Basis-URL von Dolibarr ohne `/api` |
| `ZEITBUCH_DOLIBARR_KEY` | – | API-Key eines eigenen Benutzers (Rechte siehe unten) |
| `ZEITBUCH_DOLIBARR_USER_ID` | leer | ID des Dolibarr-Benutzers, auf den die Zeiten gebucht werden (sein Stundensatz gilt). Leer = API-Benutzer. |

Im Mock-Modus zeigt die Oberfläche ein rotes Banner „MOCK-MODUS“. Der Mock
liest fiktive Beispieldaten aus `testdata/youtrack/` und hält Dolibarr nur im
Arbeitsspeicher.

### YouTrack-Feldkonfiguration

Feldnamen, Werte und Arbeitstypen sind nicht fest verdrahtet. Die Konfiguration
gilt für alle eingerichteten YouTrack-Projekte.

| Variable | Default | Bedeutung |
|---|---|---|
| `ZEITBUCH_YT_FIELD_TYPE` | `Type` | Feld, das Abrechnungsprojekt/-position kennzeichnet |
| `ZEITBUCH_YT_TYPE_PROJECT` | `Abrechnungsprojekt` | Wert für Abrechnungsprojekt |
| `ZEITBUCH_YT_TYPE_POSITION` | `Abrechnungsposition` | Wert für Abrechnungsposition |
| `ZEITBUCH_YT_FIELD_EXT_PROJECT` | `Ext-Projekt` | Feld externe Projektkennung |
| `ZEITBUCH_YT_FIELD_EXT_POSITION` | `Ext-Position` | Feld externe Positionskennung |
| `ZEITBUCH_YT_FIELD_BOOKABLE` | `Bebuchbar` | Feld Bebuchbar |
| `ZEITBUCH_YT_FIELD_COORDINATION` | `Abstimmung` | Feld Abstimmungsposition |
| `ZEITBUCH_YT_VALUE_YES` | `ja` | Enum-Wert für „ja“ in Bebuchbar/Abstimmung |
| `ZEITBUCH_YT_WORK_IMPLEMENTATION` | `Umsetzung` | Arbeitstyp Umsetzung |
| `ZEITBUCH_YT_WORK_MEETING` | `Besprechung` | Arbeitstyp Besprechung |
| `ZEITBUCH_YT_WORK_NONBILLABLE` | `Nicht abrechenbar` | Arbeitstyp Nicht abrechenbar |
| `ZEITBUCH_YT_AUTHORS` | leer | Kommagetrennte Logins, deren Work Items zählen; leer = alle |

**Konfigurationsprüfung:** Beim Start und im Verbindungstest prüft zeitbuch je
YouTrack-Projekt, ob alle Felder, Werte und Arbeitstypen existieren, und meldet
Lücken konkret (z. B. „Feld ‚Bebuchbar‘ fehlt in Projekt XSYS“). Die App startet
trotzdem und zeigt die Meldungen als Banner oben an.

## Hinter Nginx Proxy Manager mit Authentik

zeitbuch hat **keine eigene Anmeldung**. Es darf nur über den NPM mit Authentik
Forward Auth erreichbar sein.

1. In Authentik einen Proxy-Provider (*Forward auth, single application*) und
   eine Anwendung für `zeitbuch.example.org` anlegen und dem Outpost zuweisen.
2. Im NPM einen Proxy Host `zeitbuch.example.org` → `http://<host>:8000`
   anlegen, SSL aktivieren und unter *Advanced* die Forward-Auth-Konfiguration
   von Authentik eintragen (Vorlage in der Authentik-Doku: „Nginx Proxy Manager“).
3. Den Port per `ZEITBUCH_PUBLISH` nur lokal bzw. per Firewall nur für den NPM
   freigeben.

Schreibende Requests verlangen `Content-Type: application/json` (sonst 415);
damit können fremde Seiten keine Formulare an zeitbuch schicken.

## Einrichtung in YouTrack

Einmalig, von Hand (Konzept Abschnitt 10):

1. Zeiterfassung im Auftraggeber-Projekt aktivieren, Arbeitstypen `Umsetzung`,
   `Besprechung`, `Nicht abrechenbar`; die Standardtypen deaktivieren.
2. Im Feld **Type** die Werte `Abrechnungsprojekt` und `Abrechnungsposition`
   anlegen. Nutzt das Feld ein projektübergreifendes Bundle, erscheinen sie auch
   in anderen Projekten.
3. Felder `Ext-Projekt` (string), `Bebuchbar` (enum ja/nein), `Ext-Position`
   (string) und `Abstimmung` (enum ja/nein) anlegen, bedingt sichtbar je Type;
   `Bebuchbar` bei Abrechnungsprojekt **und** Abrechnungsposition.
4. Je Kundenprojekt ein Abrechnungsprojekt mit seinen Positionen als Subtasks;
   Positionen dürfen verschachtelt sein, jede mit eigener `Ext-Position`.
   `Bebuchbar = ja` an jedem Knoten setzen, auf den gebucht wird – **leer gilt
   als nein**. Optional genau eine Position mit `Abstimmung = ja`.
5. Bestehende Arbeitstickets unter die passenden Positionen hängen.
6. Legt ein Assistenz-Benutzer Work Items an, ihm das Recht dazu geben und ihn
   ggf. in `ZEITBUCH_YT_AUTHORS` aufnehmen.

Der Token-Benutzer braucht nur Leserechte: Tickets, Work Items und die
Projekteinstellungen (Felder, Arbeitstypen) der eingerichteten Projekte.

## Einrichtung in Dolibarr

- Version 23, Module **Projekte** mit Aufgaben und **API** aktiv.
- In jedem Dolibarr-Projekt eines Auftraggebers **„Zeit abrechnen“ aktivieren**
  (Projektkarte → Ändern). zeitbuch stellt das nicht selbst ein.
- Eigener technischer Benutzer für zeitbuch mit API-Key. Rechte:
  - Projekte: lesen und anlegen/ändern (**kein** Löschen nötig), Zugriff auf die
    betroffenen Projekte (Recht „alle Projekte“ oder als Kontakt eingetragen),
  - Benutzer: lesen – nur damit die Konfigurationsprüfung
    `ZEITBUCH_DOLIBARR_USER_ID` prüfen kann.
- `ZEITBUCH_DOLIBARR_USER_ID` auf den Benutzer setzen, auf den die Zeiten
  gebucht werden sollen (ID in Dolibarr unter Benutzer & Gruppen, `id=` in der
  URL). Dessen Stundensatz übernimmt Dolibarr in die Buchung. Leer = API-Benutzer.
  Existiert die ID nicht, sperrt zeitbuch das Übertragen.

Details zu den genutzten Endpunkten und ihren Eigenheiten in
[docs/api-verifikation.md](docs/api-verifikation.md).

**Übergang:** Bisherige Aufgaben und Zeiten in Dolibarr bleiben unangetastet;
zeitbuch legt seine Aufgaben daneben an. Dieselbe Zeit nicht zusätzlich von
Hand in Dolibarr buchen – zeitbuch sieht manuelle Einträge nicht.

## Bedienung

Oben den Monat wählen. „Neu laden“ liest YouTrack erneut.

- **Übersicht:** Summen je Abrechnungsprojekt und Position, Ampel (rot = nicht
  zuordenbare Work Items oder Konfigurationsproblem, gelb = übertragene Zeilen
  haben sich geändert), nicht abrechenbare Zeiten zur Information.
- **Externe Systeme:** Zeitzeilen mit Copy-Button je Feld; Dauer als `1:45` und
  `1,75h`. Häkchen „übertragen“ setzen, sobald die Zeile im externen System
  steht. Ändert sich eine markierte Zeile später, wird sie gelb mit alt → neu
  angezeigt („Neu bestätigen“). Entfallene Zeilen erscheinen oben als Hinweis.
- **Nicht zuordenbar:** Work Items mit Grund und Link ins Ticket. Sie fehlen in
  allen Ausgaben, bis sie in YouTrack korrigiert sind.
- **Dolibarr:** „Vorschau laden“ zeigt neue Aufgaben, Umbenennungen und
  Buchungen samt Status (neu, geändert, unverändert, fakturiert) und Warnungen.
  „Nach Dolibarr übertragen“ führt genau diese Vorschau aus; hat sich seither
  etwas geändert, bricht zeitbuch ab und zeigt die neue Vorschau.
- **Einstellungen:** Mapping, Verbindungstest mit Konfigurationsprüfung, aktive
  Feldkonfiguration (nur lesend).

## Regeln in Kürze

- Ziel einer **Umsetzung** ist der nächste Abrechnungsknoten (Position jeder
  Ebene oder Projekt) in der Parent-Kette. Er muss `Bebuchbar = ja` haben; es
  wird nie auf einen höheren Knoten ausgewichen.
- Ziel einer **Besprechung**: Hat das Abrechnungsprojekt genau eine
  Abstimmungsposition (beliebige Tiefe), diese; hat es keine, wie Umsetzung.
- **Nicht abrechenbar** wird nur angezeigt.
- Nie stillschweigend: Fehlender Arbeitstyp, kein Abrechnungsprojekt, nicht
  bebuchbares Ziel, mehrere Abstimmungspositionen und fehlende Ext-Position bei
  gesetztem Ext-Projekt landen unter „Nicht zuordenbar“.
- Zeilen je **Datum × Ziel**, Dauer = Tagessumme, **aufwärts auf 15 Minuten**.
- Eine Zeile = eine Dolibarr-Buchung. Fakturierte Buchungen werden nie
  geändert; verwaiste Buchungen erscheinen als Warnung mit Link und werden von
  Hand gelöscht.

## Datensicherung

Gesichert werden muss nur die SQLite-Datei. Sie enthält das Mapping, die
Häkchen „übertragen“ und die Zuordnung zu Dolibarr-Aufgaben und -Buchungen.
Ohne diese Zuordnung würde zeitbuch Aufgaben und Buchungen neu anlegen.

Konsistente Kopie im laufenden Betrieb (WAL-Modus, daher nicht einfach die
Datei kopieren):

```bash
# [Host]
docker exec zeitbuch python3 -c "import sqlite3; s=sqlite3.connect('/data/zeitbuch.db'); d=sqlite3.connect('/data/backup.db'); s.backup(d); d.close()"
docker cp zeitbuch:/data/backup.db ./zeitbuch-$(date +%F).db
```

Alternativ das Volume `zeitbuch-data` bei gestopptem Container sichern.
Wiederherstellen: Container stoppen, Datei als `/data/zeitbuch.db` zurücklegen
(Rechte UID 10001), `zeitbuch.db-wal` und `zeitbuch.db-shm` entfernen, starten.

## Entwicklung

```bash
# [Dev]
python3 -m venv .venv
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/python -m pytest -q

# Lokal mit Mock-Daten
ZEITBUCH_BACKEND=mock ZEITBUCH_DB=./data/zeitbuch.db .venv/bin/python -m app
# → http://127.0.0.1:8000
```

Die Tests laufen nur gegen die Mocks und gegen nachgebildete API-Antworten; CI
hat keinen Zugriff auf YouTrack oder Dolibarr. Beispieldaten in `testdata/`
sind fiktiv.

## API

Alle Antworten JSON; Fehler als `{"error": "…"}`. Schreibende Requests nur mit
`Content-Type: application/json`.

| Methode | Pfad | Zweck |
|---|---|---|
| GET | `/healthz` | Healthcheck |
| GET | `/api/info` | Version, Mock-Modus, Ergebnis der Konfigurationsprüfung |
| GET | `/api/month?month=JJJJ-MM` | Zeitzeilen, Übersicht, nicht zuordenbar, übertragen-Zustand |
| POST | `/api/transfer` | `{"row": {…}, "done": true\|false}` – Häkchen setzen/entfernen |
| GET | `/api/dolibarr/preview?month=JJJJ-MM` | Vorschau, schreibt nichts |
| POST | `/api/dolibarr/apply` | `{"month", "fingerprint"}` – führt die Vorschau aus (409 bei veralteter Vorschau) |
| GET | `/api/settings` | Mapping, verfügbare Projekte, Feldkonfiguration |
| POST | `/api/settings/projects` | `{"short_name", "dolibarr_project_id"}` – Mapping anlegen/ändern |
| DELETE | `/api/settings/projects/<KURZNAME>` | Mapping entfernen |
| POST | `/api/check` | Verbindungstest und Konfigurationsprüfung |
