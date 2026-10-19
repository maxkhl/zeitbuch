# Konzept: Zeiterfassung über YouTrack → Zeitdaten für externe Systeme & Dolibarr

Stand: 20.10.2026 · Status: Entwurf zur Umsetzung mit Claude Code · Name der App/des Repos: `zeitbuch`

> Dieses Dokument liegt in einem **öffentlichen** Repository. Alle Beispiele (Kunden, Projektnummern, Positionen) sind fiktiv. Echte Kunden- und Projektdaten gehören nicht ins Repo – weder in Doku noch in Testdaten.

> Ergebnisse der API-Prüfung (Abschnitte 7.2 und 8) und die daraus folgenden Umsetzungsdetails stehen in [api-verifikation.md](api-verifikation.md).

## 1. Ziel und Ausgangslage

**Heute:** Zeiten werden teils von Hand in externen Zeiterfassungssystemen von Auftraggebern erfasst (oft mit Buchung auf Projekt-Teilpositionen), dann von Hand nach Dolibarr übertragen; daraus wird am Monatsanfang die Rechnung für den Vormonat erzeugt. YouTrack wird nur für Aufgaben genutzt.

**Ziel:** Zeit wird genau einmal erfasst – als Work Item an YouTrack-Tickets, während der Arbeit. Eine kleine Web-App leitet daraus ab:

1. **Zeitdaten zur Übernahme in externe Systeme** (Datum, Dauer, externes Projekt/Position, Beschreibung) – eine neutrale Anzeige zum Übertragen von Hand. Externe Systeme werden **nicht** angebunden.
2. **Dolibarr-Zeitbuchungen** per API (eigenes System) als Grundlage der Monatsrechnung.

Der manuelle Übertragungsschritt nach Dolibarr entfällt; die Zuordnung zu Teilpositionen inkl. der Besprechungsregel passiert automatisch.

zeitbuch ist eine allgemeine Lösung und kennt kein bestimmtes externes System. „Extern“ meint im Folgenden immer: ein beliebiges Zeiterfassungssystem eines Auftraggebers, in das von Hand übertragen wird.

## 2. Rahmenbedingungen

| Thema | Festlegung |
|---|---|
| YouTrack | Self-hosted. Ein YouTrack-Projekt pro Auftraggeber (z. B. `CON`, `XSYS`). Keine YouTrack-Projekte pro Endkunde. |
| Dolibarr | 23.0.2, self-hosted, Module Projekte/Aufgaben aktiv. Je Auftraggeber ein Dolibarr-Projekt mit Teilaufgaben; Zeit wird auf die Teilaufgaben gebucht, ist abrechenbar und trägt das Kennzeichen **fakturiert ja/nein**, das Dolibarr beim Fakturieren setzt. Abrechnung: **eine Rechnungsposition pro Monat** je Auftraggeber. |
| Externe Systeme | Nur neutrale Anzeige der Zeitdaten zum Übertragen von Hand. Keine Anbindung, keine systemspezifischen Formate. |
| Rundung | **Immer aufwärts auf 15 Minuten**, pro Tag und Position (Details 5.4). |
| Betrieb | Docker-Container im Homelab, eingebunden per Dockge, hinter Nginx Proxy Manager mit Authentik Forward Auth. |
| Stack, Repo, CI | Siehe Abschnitt 12. |

## 3. Datenmodell in YouTrack

### 3.1 Hierarchie (Beispiel, fiktiv)

```
CON-001  [Type: Abrechnungsprojekt]  Webshop-Integration
         Ext-Projekt = "S-2026-MUSTER-001"
         Bebuchbar  = nein                        ← Zeit nur auf Positionen
├─ CON-002 [Type: Abrechnungsposition] 1. Kundenstammdaten
│    Ext-Position = "S-2026-MUSTER-001 | 1"
│    └─ CON-010  Filter Replikation               ← normales Ticket, hier wird Zeit geloggt
│         └─ (beliebig tiefe Subtasks)
├─ CON-003 [Type: Abrechnungsposition] 2. Partnerrollen
│    Ext-Position = "S-2026-MUSTER-001 | 2"
└─ CON-004 [Type: Abrechnungsposition] 9. Koordination / Projektmanagement
     Ext-Position = "S-2026-MUSTER-001 | 9"
     Abstimmung  = ja                             ← genau eine je Abrechnungsprojekt
```

Beziehung = YouTrack-Standardlink „subtask of“ / „parent for“.

### 3.2 Type-Werte

Neue Werte im Feld **Type**: `Abrechnungsprojekt`, `Abrechnungsposition`. Bewusst **nicht** Epic – Epic bleibt frei für fachliche Gruppierung.

> Hinweis Einrichtung: Prüfen, ob das Type-Feld ein projektübergreifend geteiltes Bundle nutzt. Falls ja, tauchen die Werte auch in anderen Projekten auf – entweder akzeptieren oder für die Auftraggeber-Projekte ein eigenes Bundle verwenden.

### 3.3 Custom Fields (bedingt sichtbar)

| Feld | Typ | Sichtbar wenn | Inhalt |
|---|---|---|---|
| Ext-Projekt | string | Type = Abrechnungsprojekt | Projektkennung im externen System, z. B. `S-2026-MUSTER-001` – optional (leer = keine Anzeige für externe Systeme) |
| Bebuchbar | enum (ja/nein) | Type = Abrechnungsprojekt | Ob direkt auf das Projekt gebucht werden darf (entspricht bebuchbaren/nicht bebuchbaren Hauptprojekten in externen Systemen). Leer gilt als `nein`. |
| Ext-Position | string | Type = Abrechnungsposition | Positionskennung im externen System, z. B. `S-2026-MUSTER-001 \| 9` |
| Abstimmung | enum (ja/nein) | Type = Abrechnungsposition | `ja` markiert die Abstimmungsposition des Projekts |

Bedingte Felder in YouTrack hängen an einem enum-artigen Feld (hier Type); Werte in ausgeblendeten Feldern werden geleert.

Der lesbare Positionsname für die Ausgabe ist der Ticket-Titel der Position – kein eigenes Feld nötig.

### 3.4 Arbeitstypen (Work Item Types)

| Typ | Bedeutung |
|---|---|
| Umsetzung | Entwicklung, Analyse, Test, Transport, Doku – alles „Doing“ |
| Besprechung | Calls, Jour Fix, Abstimmungen, Anforderungsklärung im Gespräch |
| Nicht abrechenbar | z. B. eigene Tool-/Infrastrukturprobleme – wird erfasst, aber nie gebucht |

Die Standardtypen (Development/Testing/Documentation) im Projekt deaktivieren, damit nur diese drei zur Auswahl stehen.

## 4. Erfassung im Alltag

- **Umsetzung** an dem Ticket loggen, an dem gearbeitet wurde.
- **Besprechung zu einem Thema** ebenfalls am Themen-Ticket loggen (Typ Besprechung) – keine eigenen Besprechungs-Tickets.
- **Allgemeine Besprechung** (Jour Fix) direkt am Abrechnungsprojekt loggen.
- Kommentar des Work Items = Beschreibungstext der Zeitzeile. Leer ist erlaubt (Fallback siehe 5.3).

## 5. Zuordnungs- und Berechnungsregeln

### 5.1 Auflösung der Position

Für jedes Work Item:

1. Vom Ticket aus die Parent-Kette nach oben laufen (das Ticket selbst eingeschlossen).
2. Erstes Ticket mit Type = Abrechnungsposition → **Fachposition** (kann fehlen).
3. Erstes Ticket mit Type = Abrechnungsprojekt → **Abrechnungsprojekt** (muss existieren).
4. Ziel nach Arbeitstyp:

| Arbeitstyp | Ziel |
|---|---|
| Besprechung | Abstimmungsposition des Abrechnungsprojekts (Kind mit Abstimmung = ja) – unabhängig davon, wo geloggt wurde. Hat das Projekt gar keine Positionen: Projektebene (nur wenn Bebuchbar = ja). |
| Umsetzung | Fachposition aus Schritt 2; gibt es keine, die **Projektebene** (direkt am Abrechnungsprojekt) – nur wenn Bebuchbar = ja |
| Nicht abrechenbar | keins – wird nur informativ angezeigt |

Normale Tickets (weder Abrechnungsprojekt noch -position) sind nie selbst ein Ziel; ihre Zeit wandert immer zur nächsten Abrechnungsposition bzw. zum Abrechnungsprojekt darüber. Gebucht werden kann also auf zwei Ebenen: **Abrechnungsprojekt** und **Abrechnungsposition**.

### 5.2 Fehlerfälle – nie stillschweigend zuordnen

Folgende Work Items erscheinen in der UI unter **„Nicht zuordenbar“** und gehen in keine Ausgabe, bis sie in YouTrack korrigiert sind:

- Kein Abrechnungsprojekt in der Parent-Kette.
- Besprechung, aber das Projekt hat Positionen und keine oder mehr als eine Abstimmungsposition.
- Arbeitstyp leer. *(Bewusst kein Default auf „Umsetzung“: Eine vergessene Typangabe bei einer Besprechung würde sonst die Fachposition belasten.)*
- Abrechnungsposition ohne Ext-Position, obwohl das Projekt ein Ext-Projekt hat.
- Ziel wäre die Projektebene, aber das Abrechnungsprojekt ist nicht bebuchbar (Bebuchbar = nein oder leer).

Ist ein Projekt bebuchbar, sind Buchungen auf Projektebene regulär und werden ohne Warnung übernommen.

### 5.3 Aggregation zu Zeitzeilen

- Gruppierung: **Datum × Ziel** (Ziel = Abrechnungsposition oder Abrechnungsprojekt).
- Externe Kennung: bei einer Position deren Ext-Position, auf Projektebene das Ext-Projekt. Zeilen von Abrechnungsprojekten ohne Ext-Projekt erscheinen nicht in der Anzeige für externe Systeme, gehen aber nach Dolibarr.
- Dauer: Summe der Rohminuten, dann gerundet (5.4).
- Beschreibung: Kommentare der Work Items, dedupliziert, mit „, “ verbunden. Fallback je Work Item: Ticket-Titel. Bei Besprechungen auf der Abstimmungsposition wird das Thema mitgegeben, z. B. „Besprechung Partnerrollen“ (Titel des Tickets, an dem geloggt wurde, sofern es nicht das Abrechnungsprojekt selbst ist).

### 5.4 Rundung

- Einheit 15 Minuten, **immer aufwärts**, angewandt **auf die Tagessumme je Ziel** (nicht auf einzelne Work Items).
- Formel: `gerundet = ceil(rohminuten / 15) * 15`. Beispiele: 1 → 15, 15 → 15, 16 → 30, 50 → 60.
- Rohzeit 0 → keine Zeile.

### 5.5 Konsistenz Dolibarr ↔ externes System

Eine gerundete Zeile (Datum × Ziel) wird **1:1** zu einer Dolibarr-Zeitbuchung auf der Aufgabe, die dieses Ziel spiegelt (Abschnitt 7). Damit ist die Rechnungssumme identisch mit dem, was ins externe System übertragen wird.

## 6. Anzeige für externe Systeme

Neutrale Anzeige der Zeitzeilen zur Übernahme von Hand in ein beliebiges externes System – ohne systemspezifisches Format.

Ansicht je Monat (und filterbar je Tag/Abrechnungsprojekt):

| Datum | Dauer | Externe Kennung | Bezeichnung | Beschreibung | übertragen |
|---|---|---|---|---|---|

- Externe Kennung = Ext-Position bzw. auf Projektebene Ext-Projekt (5.3); Bezeichnung = Titel der Abrechnungsposition bzw. des Abrechnungsprojekts.
- Dauer wird in zwei Formaten angezeigt, jeweils mit eigenem Copy-Button: Stunden:Minuten (`1:45`) und Dezimalstunden mit Komma und Einheit (`1,75h`). Durch die Rundung auf 15 Minuten sind die Dezimalwerte immer exakt (Schritte von 0,25h).

- Copy-Button je Zeile/Feld zum Abtippen.
- Checkbox „übertragen“, Zustand wird in der App gespeichert.
- Ändert sich eine bereits als übertragen markierte Zeile nachträglich (neues/geändertes Work Item), wird sie als **geändert** markiert und zeigt alt → neu.

## 7. Ausgabe Dolibarr

Ablauf heute: Je Auftraggeber gibt es ein Dolibarr-Projekt mit Teilaufgaben. Zeiten werden als abrechenbare Zeit auf die Teilaufgaben gebucht. Beim Fakturieren erzeugt Dolibarr die Rechnung aus diesen Zeiten und setzt das Kennzeichen „fakturiert“. Die App übernimmt das Anlegen der Aufgabenstruktur und das Buchen der Zeiten – das Fakturieren bleibt in Dolibarr.

### 7.1 Mapping und Aufgabenstruktur

- In der App wird je **YouTrack-Projekt ein Dolibarr-Projekt** zugeordnet (z. B. `CON` → Dolibarr-Projekt des Auftraggebers). Das ist das einzige manuell gepflegte Mapping.
- Darunter spiegelt die App die YouTrack-Struktur als Aufgaben:

```
Dolibarr-Projekt (Auftraggeber)            ← Mapping in der App
├─ Aufgabe  CON-001 Webshop-Integration    ← je Abrechnungsprojekt
│  ├─ Unteraufgabe  CON-002 1. Kundenstammdaten   ← je Abrechnungsposition
│  ├─ Unteraufgabe  CON-003 2. Partnerrollen
│  └─ Unteraufgabe  CON-004 9. Koordination / PM
└─ Aufgabe  CON-020 Support-Projekt        ← Abrechnungsprojekt ohne Positionen
```

- Bezeichnung = YouTrack-ID + Titel. Die Zuordnung YouTrack-Ticket → Dolibarr-Aufgaben-ID wird in der App gespeichert, nicht über den Namen gesucht.
- Gespiegelt werden nur Abrechnungsprojekte und -positionen. Normale Tickets werden nie als Aufgabe angelegt; ihre Zeit wandert nach 5.1 nach oben.
- Anlegen fehlender Aufgaben passiert in der Dolibarr-Vorschau („wird angelegt“) und erst beim Bestätigen.
- Titeländerung in YouTrack → Bezeichnung wird beim nächsten Abgleich angepasst.
- Die App **löscht oder verschiebt nie** Dolibarr-Aufgaben. Wird ein Abrechnungsprojekt/-position in YouTrack gelöscht, umgehängt oder der Type entfernt, zeigt die App eine Warnung; aufgeräumt wird von Hand.

### 7.2 Zeitbuchungen

- Eine Zeitbuchung **je Tag und Ziel**, auf der gespiegelten Aufgabe (Projektebene → Aufgabe des Abrechnungsprojekts, Position → Unteraufgabe). Dauer und Notiz = die gerundete Zeile aus 5.3/5.4.
- Gebucht wird als **abrechenbar** und **nicht fakturiert**.
- Übertragung nur per Button mit Vorschau (Datum, Dauer, Zielaufgabe), nie automatisch.
- **Idempotenz:** Die App speichert je übertragenem Eintrag die Dolibarr-ID. Erneutes Übertragen erzeugt keine Duplikate, sondern aktualisiert Dauer/Notiz eigener, nicht fakturierter Einträge.
- **Grundsatz: Die App löscht in Dolibarr nie etwas** – keine Aufgaben, keine Zeitbuchungen. Fällt eine Zeile weg (Work Item in YouTrack gelöscht oder umgehängt), zeigt die App den verwaisten Dolibarr-Eintrag als Warnung mit Link; gelöscht wird von Hand.
- Die App fasst nur Aufgaben und Zeitbuchungen an, die sie selbst angelegt hat (über die gespeicherten IDs). Von Hand angelegte Aufgaben und Zeiten im selben Dolibarr-Projekt bleiben unberührt.
- **Sperre über das Fakturiert-Kennzeichen:** Vor jeder Übertragung liest die App den Status der betroffenen Dolibarr-Zeitbuchungen. Bereits fakturierte Einträge werden nie geändert oder gelöscht; Abweichungen zu YouTrack werden dort nur als Warnung angezeigt. Eine eigene Monatssperre in der App entfällt damit.
- **Zu verifizieren** gegen die Dolibarr-23-API:
  - Anlegen von Aufgaben mit übergeordneter Aufgabe (Unteraufgaben) und Ändern der Bezeichnung.
  - Anlegen/Ändern/Lesen von Zeitbuchungen auf Aufgaben (vermutlich Tasks-API „addtimespent“) und wie dort „abrechenbar“ und „fakturiert“ abgebildet sind.
- Fakturieren: Dolibarr fasst beim Erzeugen der Rechnung auf Wunsch alle Zeitdatensätze zu einer Position zusammen (im Einsatz, funktioniert). Die Aufgabenstruktur ändert daran nichts.

## 8. Schnittstellen und Zugänge

| System | Zugriff | Zweck |
|---|---|---|
| YouTrack REST-API | Permanent Token eines eigenen Benutzers mit **nur Leserechten** | Work Items je Zeitraum lesen (vermutlich `/api/workItems` mit Datumsfilter – zu verifizieren), Tickets inkl. Parent-Kette, Type und Custom Fields, Projektfelder und Arbeitstypen für die Konfigurationsprüfung (8.1) |
| Dolibarr REST-API | API-Key eines eigenen Benutzers, Rechte nur auf Projekte/Aufgaben/Zeiten | Aufgaben anlegen/umbenennen, Zeitbuchungen anlegen/ändern/lesen |
| Externe Systeme | keiner | – |

Secrets ausschließlich per Umgebungsvariablen im Dockge-Stack – nie im Image, nie im Repo. Im Repo nur `.env.example` mit Platzhaltern.

### 8.1 Konfiguration der YouTrack-Felder

Feldnamen, Werte und Arbeitstypen sind **nicht im Code fest verdrahtet**, sondern Env-Variablen mit Defaults gemäß diesem Konzept. Eine Konfiguration gilt für alle gemappten YouTrack-Projekte (Custom Fields sind in YouTrack global definiert und den Projekten nur zugewiesen).

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

**Konfigurationsprüfung** beim Start und im Verbindungstest: Für jedes gemappte YouTrack-Projekt prüft die App, ob alle Felder, Werte und Arbeitstypen existieren, und meldet Lücken konkret (z. B. „Feld ‚Bebuchbar‘ fehlt in Projekt XSYS“). Ein Tippfehler darf nicht stillschweigend dazu führen, dass alles unter „Nicht zuordenbar“ landet. Die App startet trotzdem und zeigt die Meldungen prominent an.

## 9. UI (Umfang MVP)

1. **Monatsübersicht:** Summen je Abrechnungsprojekt und Position, Ampel für „Nicht zuordenbar“ und Warnungen.
2. **Zeitzeilen für externe Systeme** (Abschnitt 6).
3. **Nicht zuordenbar:** Liste mit Grund und Direktlink ins YouTrack-Ticket.
4. **Dolibarr:** Vorschau (neu anzulegende Aufgaben, Umbenennungen, Zeitbuchungen), Übertragen, Anzeige fakturierter/gesperrter Einträge, Strukturwarnungen.
5. **Einstellungen:** Mapping YouTrack-Projekt → Dolibarr-Projekt, Verbindungstest beider APIs inkl. Konfigurationsprüfung (8.1), Anzeige der aktiven Feldkonfiguration (nur lesend).

UI, Doku und Fehlermeldungen auf Deutsch. Kein Schreibzugriff auf YouTrack im MVP.

## 10. Einmalige Einrichtung in YouTrack (manuell, nicht Teil der App)

1. Time Tracking im Auftraggeber-Projekt aktivieren (falls noch nicht), Arbeitstypen gemäß 3.4.
2. Type-Werte `Abrechnungsprojekt`, `Abrechnungsposition` anlegen (Bundle-Hinweis 3.2).
3. Felder Ext-Projekt, Bebuchbar, Ext-Position, Abstimmung anlegen, bedingt auf Type.
4. Je Kundenprojekt ein Abrechnungsprojekt mit seinen Positionen als Subtasks anlegen; genau eine Position mit Abstimmung = ja.
5. Bestehende Arbeitstickets unter die passenden Positionen hängen.
6. Falls ein Assistenz-Benutzer (z. B. „Claude“) Work Items für dich anlegt (siehe 11, Erfassung per Assistent): diesem Benutzer im Projekt das Recht zum Anlegen von Work Items geben und ihn ggf. in `ZEITBUCH_YT_AUTHORS` aufnehmen.

## 11. Entscheidungen

| Thema | Entscheidung |
|---|---|
| Rechnung | Entsteht in Dolibarr aus den Zeitbuchungen der Aufgaben; Dolibarr fasst beim Fakturieren alle Zeiten zu einer Position zusammen. |
| Dolibarr-Mapping | Nur YouTrack-Projekt → Dolibarr-Projekt; die Aufgabenstruktur legt die App an. |
| Projektebene | Buchungen direkt aufs Abrechnungsprojekt steuert das Feld Bebuchbar. |
| Positionen bei Direktkunden | Möglich, aber optional. Ohne Ext-Projekt keine Anzeige für externe Systeme, nur Dolibarr-Buchungen. |
| Übergang | Fließend, kein Stichtag. Ab sofort Zeiten in YouTrack statt direkt in Dolibarr erfassen. Bisherige Aufgaben und Zeiten in Dolibarr bleiben unangetastet; die App legt ihre Struktur daneben an. **Nichts wird gelöscht.** Hinweis: Dieselbe Zeit nicht zusätzlich von Hand in Dolibarr buchen – die App sieht manuelle Einträge nicht. |
| Erfassung per Assistent | Gewünscht: Ein Assistent (Claude über die YouTrack-Anbindung) legt Work Items aus Stichpunkten an. Das passiert **außerhalb von zeitbuch** direkt in YouTrack; zeitbuch selbst schreibt weiterhin nicht nach YouTrack und behandelt diese Work Items wie alle anderen. |
| Feldkonfiguration | Env-Variablen mit Defaults und Konfigurationsprüfung (8.1). |
| Rundung | Immer aufwärts auf 15 Minuten je Tag und Ziel. |
| Name | `zeitbuch` |

Offen: keine.

## 12. Stack, Repository und CI

Vorlage für Aufbau, Container und CI ist **vmdash** (github.com/maxkhl/vmdash) – dasselbe Muster wie sapfav, aber in Python. Von sapfav werden Workflow-Aufbau, Image-Tags und Doku-Stil übernommen, **nicht** der Go-Stack.

### 12.1 Stack – bewusst gewählt

- **Python 3.13 + Flask + waitress**, genau ein Prozess (waitress mit Threads). **Kein Go, kein PHP.**
- Pakete im Container per **apt aus Debian trixie** (`python3-flask`, `python3-waitress`, `python3-requests`), nicht per pip – wie vmdash. Weitere Abhängigkeiten nur nach Rücksprache.
- **SQLite** über das Standardmodul `sqlite3` im Volume `/data` (Übertragungsstand inkl. Dolibarr-IDs, „übertragen“-Häkchen, Mapping-Konfiguration). Schema-Versionierung über `PRAGMA user_version`, kein Migrations-Framework.
- **Frontend:** statisches HTML/CSS/Vanilla-JS, kein Build-Schritt, kein npm, keine CDN-Ressourcen (läuft offline im LAN).
- **Keine eigene Anmeldung** – Authentik Forward Auth im NPM davor. Schreibende Requests nur mit `Content-Type: application/json`, sonst 415 (CSRF-Schutz, wie sapfav/vmdash).
- `GET /healthz` für den Docker-Healthcheck (Python-Einzeiler, kein curl).
- **Backend-Abstraktion** für YouTrack und Dolibarr mit je einer echten und einer Mock-Implementierung. Tests laufen ausschließlich gegen Mocks bzw. aufgezeichnete, **anonymisierte** API-Antworten in `testdata/` – CI hat keinen Zugriff auf YouTrack/Dolibarr.

### 12.2 Repository (öffentlich)

- `README.md` (Deutsch): Zweck, Setup mit Docker Compose/Dockge, fertiges Image aus GHCR, Konfigurationstabelle der Env-Variablen, Betrieb hinter NPM/Authentik, Datensicherung.
- `CLAUDE.md`: Entscheidungen zu Stack und Aufbau (wie bei sapfav/vmdash).
- `.env.example`, `.dockerignore`, `.gitignore` (u. a. `.env`, `*.db`).
- `docker-compose.yml` mit `build: .` und auskommentierter `image: ghcr.io/maxkhl/zeitbuch:latest`-Zeile, benanntes Volume für `/data`.
- Keine echten Kunden-, Projekt- oder Personendaten in Code, Doku, Commits oder Testdaten.

### 12.3 Container

- Basis `debian:trixie-slim`, unprivilegierter Benutzer, `EXPOSE 8000`, `VOLUME /data`, `HEALTHCHECK` auf `/healthz`.
- Build-Arg `VERSION` (Short-SHA) → Env-Variable, in der UI sichtbar.
- Konfiguration nur über Env-Variablen mit App-Präfix, z. B. `ZEITBUCH_PORT`, `ZEITBUCH_DB`, `ZEITBUCH_YOUTRACK_URL`, `ZEITBUCH_YOUTRACK_TOKEN`, `ZEITBUCH_DOLIBARR_URL`, `ZEITBUCH_DOLIBARR_KEY`, dazu die Feldkonfiguration aus 8.1. Alle Variablen mit Default und Bedeutung in README und `.env.example`.

### 12.4 GitHub Actions

Eine Workflow-Datei nach dem Muster von sapfav/vmdash:

- Trigger: Push auf `main`, zusätzlich `workflow_dispatch`.
- `concurrency` je Ref mit `cancel-in-progress` (wie sapfav).
- Job **test**: `actions/setup-python` (3.13, wie Debian trixie), Abhängigkeiten aus `requirements-dev.txt`, `python -m pytest -q`.
- Job **image** (`needs: test`, `packages: write`): Buildx, Login an `ghcr.io` mit `GITHUB_TOKEN`, Build **nur für `linux/amd64`** (kein arm64, daher auch kein QEMU), Push nach `ghcr.io/maxkhl/zeitbuch` mit Tags `:latest` und `:<short-sha>`, OCI-Labels `source`/`revision`, GHA-Cache.
- Actions in den aktuellen Hauptversionen wie in sapfav (`checkout@v7`, `setup-buildx-action@v4`, `login-action@v4`, `build-push-action@v7`); für `setup-python` die aktuelle Hauptversion prüfen.
- Hinweis für die README: Neue GHCR-Pakete sind zunächst privat → Sichtbarkeit im Package auf *Public* stellen.

### 12.5 Arbeitsweise für Claude Code

- Keine Features einbauen, die nicht in diesem Konzept stehen – Ideen als Vorschlag nennen.
- Vor dem ersten Code die beiden „zu verifizieren“-Punkte (YouTrack-Work-Items-Endpunkt, Dolibarr-Zeitbuchung) gegen die echten API-Dokus der eingesetzten Versionen prüfen.
- In kleinen Schritten committen; jeder Commit muss für sich bauen und testen.
