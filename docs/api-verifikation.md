# API-Verifikation (Konzept 7.2 und 8)

Geprüft am 20.10.2026 gegen die offizielle YouTrack-REST-Doku und gegen den
Quelltext von Dolibarr **23.0.2** (Tag `23.0.2`, Dateien
`htdocs/projet/class/api_tasks.class.php`, `htdocs/projet/class/task.class.php`,
`htdocs/core/class/timespent.class.php`, `htdocs/api/class/api.class.php`).

## YouTrack: Work Items je Zeitraum

`GET /api/workItems` – bestätigt.

| Parameter | Bedeutung |
|---|---|
| `startDate`, `endDate` | Zeitraum der Work-Item-Daten, Format `YYYY-MM-DD` (beide Grenzen inklusive) |
| `query` | YouTrack-Suchanfrage für die Tickets, z. B. `project: CON, XSYS` |
| `author` | Login, mehrfach angebbar (für `ZEITBUCH_YT_AUTHORS`) |
| `fields` | Pflicht, sonst kommt nur die ID |
| `$top`, `$skip` | Paging; ohne `$top` liefert der Server nur 42 Einträge |

Genutzte Felder: `id, date, duration(minutes), type(name), text, author(login), issue(idReadable), created, attributes(name,value(name))`.
`attributes` ist eine Liste von `WorkItemAttribute` (`name`, `value` vom Typ
`WorkItemAttributeValue` mit `name`, kann leer sein) – darüber kommt das
Attribut „Abrechenbar“.
`date` ist ein UTC-Zeitstempel in Millisekunden auf Mitternacht des Tages →
zeitbuch nimmt das UTC-Datum.

Tickets: `GET /api/issues?query=…&fields=idReadable,summary,project(shortName),parent(issues(idReadable)),customFields(name,value(name))`.
`Issue.parent` ist ein `IssueLink`, dessen `issues` den Parent enthält (leer =
kein Subtask). String-Felder liefern `value` als String, Enum-Felder als Objekt
mit `name`.

Konfigurationsprüfung: `GET /api/admin/projects?fields=id,shortName,…`, dann
`/api/admin/projects/{id}/customFields?fields=field(name),bundle(values(name))`
und `/api/admin/projects/{id}/timeTrackingSettings?fields=enabled,workItemTypes(name),attributes(name,values(name))`
(`attributes` = Liste von `WorkItemProjectAttribute` mit den möglichen `values`).

> **Nachtrag nach der Inbetriebnahme:** In 23.0.2 und 23.0.3 ist `addtimespent`
> über die API nicht nutzbar (HTTP 500), erst ab **23.0.4** behoben – siehe
> [unten](#dolibarr-2302-und-2303-addtimespent-endet-mit-http-500). Die
> Projektliste braucht zusätzlich ein Geschäftspartner-Recht, siehe
> [Projektliste](#dolibarr-2302-projektliste-und-geschäftspartner-rechte).

## Dolibarr 23.0.2: Aufgaben

- **Anlegen:** `POST /api/index.php/tasks` mit Pflichtfeldern `ref`, `label`,
  `fk_project`. `ref: "auto"` vergibt die nächste Nummer über das eingestellte
  Nummernmodul. Übergeordnete Aufgabe über `fk_task_parent`. Rückgabe: die neue ID.
- **Bezeichnung ändern:** `PUT /api/index.php/tasks/{id}` mit `{"label": …}`;
  nur übergebene Felder werden geändert.
- **Lesen:** `GET /api/index.php/tasks/{id}` (404, wenn gelöscht).

## Dolibarr 23.0.2: Zeitbuchungen

| Aktion | Endpunkt | Befund |
|---|---|---|
| Anlegen | `POST /tasks/{id}/addtimespent` mit `date` (`YYYY-MM-DD HH:MM:SS`, GMT), `duration` (Sekunden), `user_id` (0 = API-Benutzer), `note` | **Liefert keine ID**, nur `{"success": …}` |
| Liste | `GET /tasks/{id}/timespent` | Zeilen mit `timespent_line_id`, `timespent_line_duration`, `timespent_line_note`; **ohne** Fakturiert-Kennzeichen |
| Einzeln | `GET /tasks/{id}/getTimeSpent/{timespent_id}` | Enthält `invoice_id` (= fakturiert, wenn gesetzt), `element_duration`, Notiz in `note_private` |
| Ändern | `PUT /tasks/{id}/timespent/{timespent_id}` mit `date`, `duration`, `note` | Setzt `invoice_id`/`invoice_line_id` auf leer zurück (siehe unten) |

### Abrechenbar und fakturiert

- **Abrechenbar** ist in Dolibarr 23 eine Eigenschaft der **Aufgabe**
  (`billable`, Default 1), nicht der einzelnen Zeitbuchung. zeitbuch legt seine
  Aufgaben deshalb mit `billable: 1` an. Zusätzlich muss im Dolibarr-Projekt
  „Zeit abrechnen“ (`usage_bill_time`) aktiv sein; das stellt zeitbuch nicht
  ein, sondern setzt es voraus.
- **Fakturiert** ist `invoice_id` der Zeitbuchung (`llx_element_time`). Gesetzt
  = fakturiert. Lesbar nur über den Einzelabruf `getTimeSpent`.

### Folgen für die Umsetzung

1. **ID nach dem Anlegen:** zeitbuch liest vor und nach `addtimespent` die
   Zeilen der Aufgabe; die neue ID ist die einzige hinzugekommene. Ist das nicht
   eindeutig (z. B. paralleles Buchen von Hand auf dieselbe Aufgabe), bricht die
   Übertragung mit einer Meldung ab, statt zu raten. Die Aufgaben gehören
   zeitbuch, gleichzeitige Handbuchungen darauf sind nicht vorgesehen.
2. **Fakturiert-Status:** Vor jeder Übertragung wird jede betroffene
   Zeitbuchung einzeln per `getTimeSpent` gelesen.
3. **`PUT` löscht das Fakturiert-Kennzeichen:** `putTimeSpent` lädt die
   Buchung über `fetchTimeSpent`, das `invoice_id` nicht übernimmt; beim
   Speichern wird `invoice_id` daher auf leer gesetzt. zeitbuch ändert nur
   Buchungen, die unmittelbar vorher als nicht fakturiert gelesen wurden – damit
   ist das unkritisch. Eine fakturierte Buchung darf nie per API geändert werden.
4. **Datum:** zeitbuch sendet `YYYY-MM-DD 12:00:00` (GMT). Die Mittagszeit hält
   den Tag in jeder europäischen Zeitzone stabil. In Dolibarr erscheint die
   Buchung deshalb mit Startzeit 12:00 bzw. 13:00/14:00 Ortszeit.
5. **Benutzer:** siehe nächster Abschnitt.

## Dolibarr 23.0.2: Buchung auf einen anderen Benutzer

Nachgeprüft für `ZEITBUCH_DOLIBARR_USER_ID`.

- **`addtimespent` nimmt einen abweichenden Benutzer an.** `user_id` wird
  ohne weitere Prüfung als `fk_user` der Buchung übernommen; `0` oder leer =
  API-Benutzer. Verlangt werden nur das Recht **Projekte: anlegen/ändern**
  (`projet->creer`) und Zugriff auf das Projekt der Aufgabe
  (`_checkAccessToResource('project', …)`, d. h. Recht „alle Projekte“ oder
  API-Benutzer als Kontakt im Projekt). Ein Recht „Zeiten anderer Benutzer
  erfassen“ wird über die API **nicht** geprüft.
- **Stundensatz:** Beim Anlegen übernimmt `Task::addTimeSpent` den `thm` des
  gebuchten Benutzers (`SELECT thm FROM llx_user WHERE rowid = fk_user`), nicht
  den des API-Benutzers.
- **Keine Existenzprüfung:** `llx_element_time.fk_user` hat keinen
  Fremdschlüssel. Eine nicht existierende ID wird stillschweigend gespeichert.
  Deshalb prüft zeitbuch die ID vorher und sperrt das Übertragen, wenn es sie
  nicht gibt.
- **Prüfen, ob die ID existiert:** `GET /api/index.php/users/{id}` (404 = gibt es
  nicht). Dafür braucht der API-Benutzer das Recht **Benutzer: lesen**
  (`user->user->lire`) oder Admin; ohne das Recht kommt 403, außer für die
  eigene ID. Fehlt das Recht, meldet zeitbuch „nicht prüfbar“ und bucht trotzdem.
- **Ändern:** `PUT /tasks/{id}/timespent/{tid}` setzt `fk_user` nur, wenn
  `user_id` > 0 ist; mit 0 bleibt der Benutzer der Buchung unverändert. zeitbuch
  sendet die konfigurierte ID (bzw. 0 ohne Konfiguration). Der `thm` wird beim
  Ändern nur neu bestimmt, wenn er leer ist oder `TIMESPENT_ALWAYS_UPDATE_THM`
  gesetzt ist.

## Dolibarr 23.0.2 und 23.0.3: addtimespent endet mit HTTP 500

Beim ersten echten Lauf gefunden und am Quelltext bestätigt; die reine
Quelltextprüfung oben hatte es übersehen.

- **Fehler:** `POST /api/index.php/tasks/{id}/addtimespent` endet mit HTTP 500,
  PHP Fatal „Illegal offset type in isset or empty“ in
  `htdocs/includes/restler/framework/Luracast/Restler/Data/Validator.php:427`.
- **Ursache:** Im Docblock von `Tasks::addTimeSpent` stehen Union-Typen –
  `@param datetime|string $date` und `@param int|null $progress`. Restler
  übernimmt den Typ aus dem Docblock und verwendet ihn als Array-Schlüssel; ein
  Union-Typ ist dort kein gültiger Schlüssel.
- **Betroffen:** 23.0.2 und 23.0.3. In **23.0.4** stehen wieder einfache Typen
  (`@param datetime $date`), der Fehler ist behoben.
- **Abhilfe:** Dolibarr auf ≥ 23.0.4 aktualisieren. Alternativ in
  `htdocs/projet/class/api_tasks.class.php` die beiden Docblock-Zeilen von
  `addTimeSpent` auf `@param datetime $date` und `@param int $progress` ändern
  (wie in 23.0.4). Den Routen-Cache (`api/temp/routes.php` im
  Dokumentenverzeichnis) erzeugt Dolibarr standardmäßig bei jedem Aufruf neu;
  nur wenn `API_PRODUCTION_DO_NOT_ALWAYS_REFRESH_CACHE` gesetzt ist, die Datei
  nach dem Patch löschen.
- **Folge für zeitbuch:** keine Codeänderung; seit dieser Inbetriebnahme zeigt
  zeitbuch bei Antworten ohne JSON den gekürzten Antworttext in Meldung und Log,
  sodass ein solcher PHP-Fehler direkt sichtbar ist.

## Dolibarr 23.0.2: Projektliste und Geschäftspartner-Rechte

- `GET /api/index.php/projects` (`api_projects.class.php`, `index()`) setzt ohne
  das Recht `societe->client->voir` („Zugriff auf alle Geschäftspartner
  erweitern“) einen Filter auf den Vertriebsmitarbeiter: Es kommen nur
  Projekte von Geschäftspartnern, bei denen der API-Benutzer in
  `llx_societe_commerciaux` eingetragen ist. Projektrechte allein reichen nicht.
- **Abhilfe:** dem API-Benutzer das Recht „Zugriff auf alle Geschäftspartner
  erweitern“ geben, oder ihn bei den betroffenen Geschäftspartnern als
  Vertriebsmitarbeiter eintragen.
- zeitbuch zeigt in den Einstellungen einen Hinweis, wenn Dolibarr eine leere
  Projektliste liefert.
