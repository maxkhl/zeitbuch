"use strict";

const TABS = ["overview", "external", "problems", "dolibarr", "settings"];
const WEEKDAYS = ["So", "Mo", "Di", "Mi", "Do", "Fr", "Sa"];

const ACTIONS = {
  create: ["wird angelegt", "info"],
  update: ["wird geändert", "warn"],
  unchanged: ["unverändert", ""],
  locked: ["fakturiert", "ok"],
  locked_diff: ["fakturiert, weicht ab", "bad"],
  conflict: ["Konflikt", "bad"],
};

const state = {
  info: null,
  month: "",
  tab: "overview",
  data: null, // /api/month
  loading: false,
  plan: null, // /api/dolibarr/preview
  planMonth: "",
  planBusy: false,
  applyResult: null,
  settings: null,
  check: null,
  filterDay: "",
  filterProject: "",
};

// ------------------------------------------------------------------ Helfer

const $ = (sel, root = document) => root.querySelector(sel);

function esc(s) {
  return String(s ?? "").replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[c]);
}

function fmtDate(iso) {
  const [y, m, d] = iso.split("-").map(Number);
  const wd = WEEKDAYS[new Date(Date.UTC(y, m - 1, d)).getUTCDay()];
  return `${wd} ${String(d).padStart(2, "0")}.${String(m).padStart(2, "0")}.${y}`;
}

function shortDate(iso) {
  const [y, m, d] = iso.split("-");
  return `${d}.${m}.${y}`;
}

function link(url, text) {
  return url ? `<a href="${esc(url)}" target="_blank" rel="noopener noreferrer">${esc(text)}</a>` : esc(text);
}

function copyBtn(value, title = "Kopieren") {
  if (value === null || value === undefined || value === "") return "";
  return `<button class="copy" type="button" data-copy="${esc(value)}" title="${esc(title)}" aria-label="${esc(title)}">⧉</button>`;
}

function copyFallback(text) {
  const ta = document.createElement("textarea");
  ta.value = text;
  ta.setAttribute("readonly", "");
  ta.className = "offscreen";
  document.body.appendChild(ta);
  ta.select();
  const ok = document.execCommand("copy");
  ta.remove();
  if (!ok) throw new Error("execCommand");
}

async function copyText(text, btn) {
  try {
    try {
      await navigator.clipboard.writeText(text);
    } catch (e) {
      // Ohne HTTPS oder ohne Berechtigung: klassischer Weg
      copyFallback(text);
    }
    btn.classList.add("done");
    btn.textContent = "✓";
    setTimeout(() => { btn.classList.remove("done"); btn.textContent = "⧉"; }, 1200);
  } catch (e) {
    showError("Kopieren fehlgeschlagen");
  }
}

async function api(method, url, body) {
  const opts = { method, headers: {} };
  if (body !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(body);
  }
  const r = await fetch(url, opts);
  let data = null;
  try { data = await r.json(); } catch (e) { /* kein JSON */ }
  if (!r.ok) {
    const err = new Error((data && data.error) || `HTTP ${r.status}`);
    err.status = r.status;
    err.data = data;
    throw err;
  }
  return data;
}

function showError(msg) {
  const el = $("#error");
  el.textContent = msg || "";
  el.hidden = !msg;
}

function currentMonth() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function shiftMonth(month, delta) {
  const [y, m] = month.split("-").map(Number);
  const d = new Date(y, m - 1 + delta, 1);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}`;
}

function duration(d) {
  return `${esc(d.hm)} <span class="muted">(${esc(d.decimal)})</span>`;
}

// ------------------------------------------------------------------ Laden

async function loadInfo() {
  try {
    state.info = await api("GET", "/api/info");
  } catch (e) {
    showError(`Info nicht ladbar: ${e.message}`);
    return;
  }
  $("#version").textContent = state.info.version;
  const mocks = [];
  if (state.info.mock.youtrack) mocks.push("YouTrack");
  if (state.info.mock.dolibarr) mocks.push("Dolibarr");
  const banner = $("#mock-banner");
  banner.hidden = !mocks.length;
  banner.textContent = `MOCK-MODUS – ${mocks.join(" und ")} simuliert, es werden keine echten Daten gelesen oder gebucht`;
  renderConfigBanner(state.info.check);
  // Die Startprüfung läuft im Hintergrund; bis sie fertig ist, nachfragen
  if (!state.info.check.done) setTimeout(loadInfo, 2000);
}

function renderConfigBanner(check) {
  const el = $("#config-banner");
  const msgs = [];
  if (check && check.done) {
    for (const [name, label] of [["youtrack", "YouTrack"], ["dolibarr", "Dolibarr"]]) {
      if (check[name] && !check[name].ok) msgs.push(`${label}: ${check[name].error}`);
    }
    msgs.push(...(check.config || []));
  }
  el.hidden = !msgs.length;
  el.innerHTML = msgs.length
    ? `<strong>Konfiguration prüfen:</strong><ul>${msgs.map((m) => `<li>${esc(m)}</li>`).join("")}</ul>`
    : "";
  updateLight();
}

async function loadMonth() {
  state.loading = true;
  state.data = null;
  render();
  try {
    state.data = await api("GET", `/api/month?month=${encodeURIComponent(state.month)}`);
    showError("");
  } catch (e) {
    showError(`YouTrack-Daten nicht ladbar: ${e.message}`);
  } finally {
    state.loading = false;
  }
  render();
}

async function loadPlan() {
  state.planBusy = true;
  render();
  try {
    state.plan = await api("GET", `/api/dolibarr/preview?month=${encodeURIComponent(state.month)}`);
    state.planMonth = state.month;
    showError("");
  } catch (e) {
    state.plan = null;
    showError(`Dolibarr-Vorschau nicht ladbar: ${e.message}`);
  } finally {
    state.planBusy = false;
  }
  render();
}

async function loadSettings() {
  try {
    state.settings = await api("GET", "/api/settings");
  } catch (e) {
    showError(`Einstellungen nicht ladbar: ${e.message}`);
  }
  render();
}

// ------------------------------------------------------------------ Darstellung

function render() {
  for (const t of TABS) {
    $(`#tab-${t}`).hidden = t !== state.tab;
    document.querySelector(`.tab[data-tab="${t}"]`).classList.toggle("active", t === state.tab);
  }
  updateBadges();
  const fn = { overview: renderOverview, external: renderExternal, problems: renderProblems,
    dolibarr: renderDolibarr, settings: renderSettings }[state.tab];
  fn($(`#tab-${state.tab}`));
}

function updateBadges() {
  const d = state.data;
  const ext = $("#badge-external");
  const prob = $("#badge-problems");
  const open = d ? d.totals.open + d.totals.changed : 0;
  ext.hidden = !open;
  ext.textContent = open;
  ext.title = d ? `${d.totals.open} offen, ${d.totals.changed} geändert` : "";
  prob.hidden = !(d && d.totals.problems);
  prob.textContent = d ? d.totals.problems : "";
  updateLight();
}

function updateLight() {
  const el = $("#light");
  const d = state.data;
  const check = state.info && state.info.check;
  const configBad = check && check.done && ((check.config || []).length
    || (check.youtrack && !check.youtrack.ok) || (check.dolibarr && !check.dolibarr.ok));
  el.className = "light";
  if (!d) return;
  let color = "green";
  let title = "Alles zugeordnet";
  if (d.totals.problems || configBad) {
    color = "red";
    title = d.totals.problems ? `${d.totals.problems} nicht zuordenbar` : "Konfiguration prüfen";
  } else if (d.totals.changed) {
    color = "yellow";
    title = `${d.totals.changed} übertragene Zeile(n) geändert`;
  }
  el.classList.add(color);
  el.title = title;
}

function loadingOr(el) {
  if (state.loading) { el.innerHTML = `<p class="muted">Lade aus YouTrack …</p>`; return true; }
  if (!state.data) { el.innerHTML = ""; return true; }
  if (!state.data.projects.length) {
    el.innerHTML = `<div class="notice info">Noch kein YouTrack-Projekt eingerichtet – unter
      <a href="#settings" data-goto="settings">Einstellungen</a> hinzufügen.</div>`;
    return true;
  }
  return false;
}

function renderOverview(el) {
  if (loadingOr(el)) return;
  const d = state.data;
  const t = d.totals;
  let html = `<div class="cards">
    <div class="card"><div class="label">Gesamt (gerundet)</div><div class="value">${esc(t.hm)} <small>${esc(t.decimal)}</small></div></div>
    <div class="card"><div class="label">davon für externe Systeme</div><div class="value">${esc(t.external.hm)} <small>${esc(t.external.decimal)}</small></div></div>
    <div class="card ${t.problems ? "bad" : ""}"><div class="label">Nicht zuordenbar</div><div class="value">${t.problems}</div></div>
    <div class="card ${t.changed ? "warn" : ""}"><div class="label">Übertragen, aber geändert</div><div class="value">${t.changed}</div></div>
    <div class="card"><div class="label">Nicht abrechenbar (roh)</div><div class="value">${esc(t.nonbillable.hm)}</div></div>
  </div>`;

  if (t.problems) {
    html += `<div class="notice bad">${t.problems} Work Item(s) nicht zuordenbar – sie fehlen in allen Ausgaben.
      <a href="#problems" data-goto="problems">Anzeigen</a></div>`;
  }

  html += `<h2>Summen je Abrechnungsprojekt und Position</h2>`;
  if (!d.overview.length) {
    html += `<div class="table-wrap"><p class="empty">Keine abrechenbaren Zeiten in diesem Monat.</p></div>`;
  } else {
    html += `<div class="table-wrap"><table><thead><tr>
      <th>Abrechnungsprojekt / Position</th><th>Externe Kennung</th><th class="num">Stunden</th><th class="num">Dezimal</th>
      </tr></thead><tbody>`;
    for (const p of d.overview) {
      html += `<tr class="group"><td>${link(p.url, p.billing_project)} ${esc(p.label)} <span class="muted">· ${esc(p.yt_project)}</span></td>
        <td></td><td class="num">${esc(p.hm)}</td><td class="num">${esc(p.decimal)}</td></tr>`;
      for (const x of p.targets) {
        const label = x.target_kind === "project" ? `<em>Projektebene</em>` : `${esc(x.target)} ${esc(x.label)}`;
        html += `<tr class="sub"><td>${label}</td><td class="mono">${esc(x.ext_key || "")}</td>
          <td class="num">${esc(x.hm)}</td><td class="num">${esc(x.decimal)}</td></tr>`;
      }
    }
    html += `</tbody></table></div>`;
  }

  if (d.nonbillable.length) {
    html += `<h2>Nicht abrechenbar (nur informativ)</h2><div class="table-wrap"><table><thead><tr>
      <th>Datum</th><th>Ticket</th><th>Abrechnungsprojekt</th><th>Kommentar</th><th class="num">Dauer</th>
      </tr></thead><tbody>`;
    for (const n of d.nonbillable) {
      html += `<tr><td class="nowrap">${fmtDate(n.work_item.date)}</td>
        <td>${link(n.url, n.work_item.issue)} ${esc(n.issue_summary)}</td>
        <td>${esc(n.billing_project ? `${n.billing_project} ${n.billing_project_label}` : "–")}</td>
        <td>${esc(n.work_item.text)}</td><td class="num">${esc(n.work_item.hm)}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }
  el.innerHTML = html;
}

function renderExternal(el) {
  if (loadingOr(el)) return;
  const d = state.data;
  const rows = d.rows.filter((r) => r.external);
  const days = [...new Set(rows.map((r) => r.date))];
  const projects = [...new Map(rows.map((r) => [r.billing_project, r.billing_project_label])).entries()];
  if (state.filterDay && !days.includes(state.filterDay)) state.filterDay = "";
  if (state.filterProject && !projects.some(([id]) => id === state.filterProject)) state.filterProject = "";
  const shown = rows.filter((r) => (!state.filterDay || r.date === state.filterDay)
    && (!state.filterProject || r.billing_project === state.filterProject));

  let html = `<div class="filters">
    <label>Tag <select id="filter-day"><option value="">alle</option>
      ${days.map((x) => `<option value="${x}" ${x === state.filterDay ? "selected" : ""}>${fmtDate(x)}</option>`).join("")}
    </select></label>
    <label>Abrechnungsprojekt <select id="filter-project"><option value="">alle</option>
      ${projects.map(([id, label]) => `<option value="${esc(id)}" ${id === state.filterProject ? "selected" : ""}>${esc(id)} ${esc(label)}</option>`).join("")}
    </select></label>
    <span class="muted">${shown.length} Zeile(n), ${d.totals.open} offen</span>
  </div>`;

  if (d.removed_transfers.length) {
    html += `<div class="notice warn"><strong>Entfallen:</strong> Diese Zeilen waren als übertragen markiert, gibt es in YouTrack aber nicht mehr.
      Im externen System entfernen und dann bestätigen.<ul>
      ${d.removed_transfers.map((r) => `<li>${fmtDate(r.date)} · ${esc(r.hm)} (${esc(r.decimal)}) · <span class="mono">${esc(r.ext_key || "")}</span> · ${esc(r.label)} – ${esc(r.description)}
        <button class="btn small" type="button" data-unmark="${esc(r.key)}">Entfernt</button></li>`).join("")}
      </ul></div>`;
  }

  if (!shown.length) {
    html += `<div class="table-wrap"><p class="empty">Keine Zeilen für externe Systeme.</p></div>`;
    el.innerHTML = html;
    return;
  }

  html += `<div class="table-wrap"><table><thead><tr>
    <th>Datum</th><th class="num">Dauer</th><th class="num">Dezimal</th><th>Externe Kennung</th><th>Bezeichnung</th><th>Beschreibung</th><th>übertragen</th>
    </tr></thead><tbody>`;
  for (const r of shown) {
    const tr = r.transfer;
    const changed = tr && tr.state === "changed";
    const cls = changed ? "changed" : tr ? "done" : "";
    const old = (field, fmt = (v) => esc(v)) => (changed && tr.old[field] !== r[field]
      ? `<span class="old">alt: <s>${fmt(tr.old[field] || "–")}</s></span>` : "");
    html += `<tr class="${cls}">
      <td class="nowrap">${fmtDate(r.date)} ${copyBtn(shortDate(r.date), "Datum kopieren")}</td>
      <td class="num">${esc(r.hm)} ${copyBtn(r.hm, "Dauer h:mm kopieren")}${old("hm")}</td>
      <td class="num">${esc(r.decimal)} ${copyBtn(r.decimal, "Dauer dezimal kopieren")}${old("decimal")}</td>
      <td class="mono nowrap">${esc(r.ext_key)} ${copyBtn(r.ext_key, "Kennung kopieren")}${old("ext_key")}</td>
      <td>${link(r.url, r.label)}</td>
      <td class="desc">${esc(r.description)} ${copyBtn(r.description, "Beschreibung kopieren")}${old("description")}</td>
      <td class="nowrap">
        <input type="checkbox" data-transfer="${esc(r.key)}" ${tr ? "checked" : ""} aria-label="übertragen">
        ${changed ? `<span class="tag warn">geändert</span> <button class="btn small" type="button" data-confirm="${esc(r.key)}">Neu bestätigen</button>` : ""}
      </td></tr>`;
  }
  html += `</tbody></table></div>`;
  el.innerHTML = html;
}

function renderProblems(el) {
  if (loadingOr(el)) return;
  const p = state.data.problems;
  if (!p.length) {
    el.innerHTML = `<div class="notice ok">Alle Work Items sind zugeordnet.</div>`;
    return;
  }
  let html = `<p class="muted">Diese Work Items gehen in keine Ausgabe, bis sie in YouTrack korrigiert sind.</p>
    <div class="table-wrap"><table><thead><tr>
    <th>Datum</th><th>Ticket</th><th>Arbeitstyp</th><th>Kommentar</th><th class="num">Dauer</th><th>Grund</th>
    </tr></thead><tbody>`;
  for (const x of p) {
    const wi = x.work_item;
    html += `<tr><td class="nowrap">${fmtDate(wi.date)}</td>
      <td>${link(x.url, wi.issue)} ${esc(x.issue_summary)}</td>
      <td>${wi.type ? esc(wi.type) : `<span class="tag bad">leer</span>`}</td>
      <td>${esc(wi.text)}</td><td class="num">${esc(wi.hm)}</td>
      <td>${esc(x.reason)}</td></tr>`;
  }
  html += `</tbody></table></div>`;
  el.innerHTML = html;
}

function renderDolibarr(el) {
  if (state.planBusy) { el.innerHTML = `<p class="muted">Lese YouTrack und Dolibarr …</p>`; return; }
  if (!state.plan || state.planMonth !== state.month) {
    el.innerHTML = `<div class="actions"><button class="btn primary" type="button" data-action="preview">Vorschau laden</button>
      <span class="muted">Liest YouTrack und den Status der Buchungen in Dolibarr. Es wird nichts geschrieben.</span></div>`;
    return;
  }
  const p = state.plan;
  const c = p.counts;
  let html = "";

  if (state.applyResult) {
    const r = state.applyResult;
    const parts = [`${r.tasks_created} Aufgabe(n) angelegt`, `${r.tasks_renamed} umbenannt`,
      `${r.times_created} Buchung(en) angelegt`, `${r.times_updated} geändert`];
    html += `<div class="notice ${r.error ? "bad" : "ok"}">${r.error ? `<strong>Abgebrochen:</strong> ${esc(r.error)}<br>` : ""}
      ${esc(parts.join(", "))}.
      ${r.skipped.length ? `<ul>${r.skipped.map((s) => `<li>${esc(s)}</li>`).join("")}</ul>` : ""}</div>`;
  }

  html += `<div class="actions">
    <button class="btn primary" type="button" data-action="apply" ${p.pending ? "" : "disabled"}>Nach Dolibarr übertragen</button>
    <button class="btn" type="button" data-action="preview">Vorschau neu laden</button>
    <span class="muted">${p.booking_user.id ? `Buchung auf ${esc(p.booking_user.login || `Benutzer ${p.booking_user.id}`)} · ` : "Buchung auf den API-Benutzer · "}${c.create_tasks} Aufgabe(n) neu, ${c.rename_tasks} Umbenennung(en), ${c.create} Buchung(en) neu,
      ${c.update} geändert, ${c.unchanged} unverändert, ${c.locked + c.locked_diff} fakturiert</span>
  </div>`;

  if (p.warnings.length) {
    html += `<h2>Warnungen</h2><ul class="warnings">${p.warnings.map((w) => `<li class="${esc(w.level)}">${esc(w.text)}${w.url ? link(w.url, "öffnen") : ""}</li>`).join("")}</ul>`;
  }

  if (p.tasks_create.length || p.tasks_rename.length) {
    html += `<h2>Aufgabenstruktur</h2><div class="table-wrap"><table><thead><tr>
      <th>Aktion</th><th>Aufgabe</th><th>Übergeordnet</th><th>Dolibarr-Projekt</th></tr></thead><tbody>`;
    for (const t of p.tasks_create) {
      html += `<tr><td><span class="tag info">wird angelegt</span></td><td>${esc(t.label)}</td>
        <td>${esc(t.parent_yt_issue || "–")}</td><td>${esc(t.dolibarr_project)}</td></tr>`;
    }
    for (const t of p.tasks_rename) {
      html += `<tr><td><span class="tag warn">wird umbenannt</span></td><td><s class="muted">${esc(t.old)}</s><br>${esc(t.new)}</td>
        <td></td><td>${link(t.url, "Aufgabe öffnen")}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }

  html += `<h2>Zeitbuchungen</h2>`;
  if (!p.times.length) {
    html += `<div class="table-wrap"><p class="empty">Keine Buchungen für diesen Monat.</p></div>`;
  } else {
    html += `<div class="table-wrap"><table><thead><tr>
      <th>Status</th><th>Datum</th><th class="num">Dauer</th><th>Zielaufgabe</th><th>Notiz</th></tr></thead><tbody>`;
    for (const t of p.times) {
      const [label, cls] = ACTIONS[t.action] || [t.action, ""];
      const showOld = t.old && (t.action === "update" || t.action === "locked_diff");
      html += `<tr><td class="nowrap"><span class="tag ${cls}">${esc(label)}</span>${t.hint ? `<span class="old">${esc(t.hint)}</span>` : ""}</td>
        <td class="nowrap">${fmtDate(t.date)}</td>
        <td class="num">${duration(t)}${showOld && t.old.hm !== t.hm ? `<span class="old">Dolibarr: ${esc(t.old.hm)}</span>` : ""}</td>
        <td>${t.task_id ? link(t.url, t.target_label) : esc(t.target_label)}</td>
        <td class="desc">${esc(t.note)}${showOld && t.old.note !== t.note ? `<span class="old">Dolibarr: ${esc(t.old.note)}</span>` : ""}</td></tr>`;
    }
    html += `</tbody></table></div>`;
  }

  if (p.orphans.length) {
    html += `<h2>Verwaiste Buchungen</h2><p class="muted">Von zeitbuch angelegt, die Zeile gibt es in YouTrack nicht mehr.
      zeitbuch löscht nichts – bitte in Dolibarr von Hand entfernen.</p>
      <div class="table-wrap"><table><thead><tr><th>Datum</th><th>Ziel</th><th class="num">Dauer</th><th>Notiz</th><th>Status</th><th></th></tr></thead><tbody>
      ${p.orphans.map((o) => `<tr><td>${fmtDate(o.date)}</td><td>${esc(o.target)}</td><td class="num">${esc(o.hm)}</td>
        <td>${esc(o.note)}</td><td>${o.invoiced ? `<span class="tag ok">fakturiert</span>` : ""}</td><td>${link(o.url, "in Dolibarr öffnen")}</td></tr>`).join("")}
      </tbody></table></div>`;
  }
  el.innerHTML = html;
}

function renderSettings(el) {
  const s = state.settings;
  if (!s) { el.innerHTML = `<p class="muted">Lade …</p>`; return; }
  const mapped = new Set(s.projects.map((p) => p.short_name));
  const ytName = new Map(s.youtrack_projects.map((p) => [p.short_name, p.name]));
  const doliOptions = (selected) => `<option value="">– kein Dolibarr-Projekt –</option>`
    + s.dolibarr_projects.map((p) => `<option value="${p.id}" ${p.id === selected ? "selected" : ""}>${esc(p.ref)} ${esc(p.title)}</option>`).join("")
    + (selected && !s.dolibarr_projects.some((p) => p.id === selected) ? `<option value="${selected}" selected>ID ${selected} (nicht gefunden)</option>` : "");

  let html = `<h2>YouTrack-Projekt → Dolibarr-Projekt</h2>`;
  if (s.youtrack_error) html += `<div class="notice bad">YouTrack: ${esc(s.youtrack_error)}</div>`;
  if (s.dolibarr_error) html += `<div class="notice bad">Dolibarr: ${esc(s.dolibarr_error)}</div>`;
  html += `<div class="table-wrap"><table><thead><tr><th>YouTrack-Projekt</th><th>Dolibarr-Projekt</th><th></th></tr></thead><tbody>`;
  if (!s.projects.length) html += `<tr><td colspan="3" class="muted">Noch keine Projekte.</td></tr>`;
  for (const p of s.projects) {
    html += `<tr><td><strong>${esc(p.short_name)}</strong> <span class="muted">${esc(ytName.get(p.short_name) || "")}</span></td>
      <td><select data-map="${esc(p.short_name)}">${doliOptions(p.dolibarr_project_id)}</select></td>
      <td><button class="btn small" type="button" data-remove="${esc(p.short_name)}">Entfernen</button></td></tr>`;
  }
  const free = s.youtrack_projects.filter((p) => !mapped.has(p.short_name));
  html += `</tbody></table></div>
    <div class="actions"><select id="add-project" ${free.length ? "" : "disabled"}>
      ${free.map((p) => `<option value="${esc(p.short_name)}">${esc(p.short_name)} – ${esc(p.name)}</option>`).join("")}
    </select><button class="btn" type="button" data-action="add-project" ${free.length ? "" : "disabled"}>Projekt hinzufügen</button>
    <span class="muted">Ohne Dolibarr-Projekt erscheinen die Zeiten nur in der Anzeige für externe Systeme.</span></div>`;

  html += `<h2>Verbindungstest</h2><div class="actions"><button class="btn" type="button" data-action="check">Verbindungen und Konfiguration prüfen</button></div>`;
  const c = state.check;
  if (c) {
    const sys = (name, label) => c[name].ok
      ? `<li>${label}: <span class="tag ok">verbunden</span> als ${esc(c[name].user)}${
        c[name].booking_user ? `, Zeiten werden gebucht auf <strong>${esc(c[name].booking_user)}</strong>` : ""}</li>`
      : `<li>${label}: <span class="tag bad">Fehler</span> ${esc(c[name].error)}</li>`;
    html += `<ul>${sys("youtrack", "YouTrack")}${sys("dolibarr", "Dolibarr")}</ul>`;
    html += c.config.length
      ? `<div class="notice warn"><strong>Konfiguration:</strong><ul>${c.config.map((m) => `<li>${esc(m)}</li>`).join("")}</ul></div>`
      : `<div class="notice ok">Alle Felder, Werte und Arbeitstypen gefunden.</div>`;
  }

  html += `<h2>Aktive Feldkonfiguration</h2><p class="muted">Nur lesend – Änderung über die Env-Variablen im Dockge-Stack.</p>
    <div class="table-wrap"><table><thead><tr><th>Variable</th><th>Wert</th></tr></thead><tbody>
    ${s.field_config.map(([k, v]) => `<tr><td class="mono">${esc(k)}</td><td>${esc(v)}</td></tr>`).join("")}
    </tbody></table></div>`;
  el.innerHTML = html;
}

// ------------------------------------------------------------------ Aktionen

function findRow(key) {
  return state.data && state.data.rows.find((r) => r.key === key);
}

function recountTransfers() {
  const d = state.data;
  const ext = d.rows.filter((r) => r.external);
  d.totals.open = ext.filter((r) => !r.transfer).length;
  d.totals.changed = ext.filter((r) => r.transfer && r.transfer.state === "changed").length
    + d.removed_transfers.length;
}

async function setTransfer(key, done) {
  const row = findRow(key) || { key };
  try {
    await api("POST", "/api/transfer", { row, done });
    // Lokal nachziehen statt den Monat neu aus YouTrack zu laden
    if (row.date) {
      row.transfer = done ? { state: "ok", old: { hm: row.hm, decimal: row.decimal, ext_key: row.ext_key,
        description: row.description, label: row.label } } : null;
    }
    state.data.removed_transfers = state.data.removed_transfers.filter((r) => r.key !== key || done);
    recountTransfers();
    showError("");
  } catch (e) {
    showError(`Speichern fehlgeschlagen: ${e.message}`);
  }
  render();
}

async function applyPlan() {
  const p = state.plan;
  const c = p.counts;
  const text = `Nach Dolibarr übertragen?\n\n${c.create_tasks} Aufgabe(n) anlegen\n${c.rename_tasks} Aufgabe(n) umbenennen\n`
    + `${c.create} Buchung(en) anlegen\n${c.update} Buchung(en) ändern\n\nEs wird nichts gelöscht.`;
  if (!window.confirm(text)) return;
  state.planBusy = true;
  render();
  try {
    const r = await api("POST", "/api/dolibarr/apply", { month: state.month, fingerprint: p.fingerprint });
    state.applyResult = r.done;
    state.plan = r.plan;
    showError("");
  } catch (e) {
    if (e.status === 409 && e.data && e.data.plan) state.plan = e.data.plan;
    state.applyResult = null;
    showError(e.message);
  } finally {
    state.planBusy = false;
  }
  render();
}

async function saveMapping(short, dolibarrId) {
  try {
    const r = await api("POST", "/api/settings/projects", { short_name: short, dolibarr_project_id: dolibarrId || null });
    state.settings.projects = r.projects;
    state.plan = null;
    showError("");
    await loadMonth();
  } catch (e) {
    showError(`Speichern fehlgeschlagen: ${e.message}`);
  }
  render();
}

async function removeMapping(short) {
  if (!window.confirm(`${short} entfernen? Zeiten aus diesem Projekt werden dann nicht mehr ausgewertet.`)) return;
  try {
    const r = await api("DELETE", `/api/settings/projects/${encodeURIComponent(short)}`, {});
    state.settings.projects = r.projects;
    state.plan = null;
    await loadMonth();
  } catch (e) {
    showError(`Entfernen fehlgeschlagen: ${e.message}`);
  }
  render();
}

async function runCheck() {
  state.check = null;
  render();
  try {
    state.check = await api("POST", "/api/check", {});
    if (state.info) state.info.check = state.check;
    renderConfigBanner(state.check);
  } catch (e) {
    showError(`Verbindungstest fehlgeschlagen: ${e.message}`);
  }
  render();
}

function setTab(tab) {
  if (!TABS.includes(tab)) tab = "overview";
  state.tab = tab;
  if (location.hash !== `#${tab}`) history.replaceState(null, "", `#${tab}`);
  if (tab === "settings" && !state.settings) loadSettings();
  render();
}

function setMonth(month) {
  if (!/^\d{4}-\d{2}$/.test(month)) return;
  state.month = month;
  $("#month").value = month;
  state.applyResult = null;
  loadMonth();
}

document.addEventListener("click", (ev) => {
  const t = ev.target.closest("button, a[data-goto]");
  if (!t) return;
  if (t.dataset.tab) return setTab(t.dataset.tab);
  if (t.dataset.goto) { ev.preventDefault(); return setTab(t.dataset.goto); }
  if (t.dataset.copy !== undefined) return copyText(t.dataset.copy, t);
  if (t.dataset.confirm) return setTransfer(t.dataset.confirm, true);
  if (t.dataset.unmark) return setTransfer(t.dataset.unmark, false);
  if (t.dataset.remove) return removeMapping(t.dataset.remove);
  switch (t.dataset.action) {
    case "preview": state.applyResult = null; return loadPlan();
    case "apply": return applyPlan();
    case "check": return runCheck();
    case "add-project": {
      const sel = $("#add-project");
      if (sel && sel.value) saveMapping(sel.value, null);
      return;
    }
    default:
  }
  if (t.id === "month-prev") return setMonth(shiftMonth(state.month, -1));
  if (t.id === "month-next") return setMonth(shiftMonth(state.month, 1));
  if (t.id === "reload") { state.plan = null; return loadMonth(); }
});

document.addEventListener("change", (ev) => {
  const t = ev.target;
  if (t.dataset.transfer) return setTransfer(t.dataset.transfer, t.checked);
  if (t.dataset.map) return saveMapping(t.dataset.map, t.value);
  if (t.id === "filter-day") { state.filterDay = t.value; return render(); }
  if (t.id === "filter-project") { state.filterProject = t.value; return render(); }
  if (t.id === "month" && t.value) return setMonth(t.value);
});

window.addEventListener("hashchange", () => setTab(location.hash.slice(1)));

state.month = currentMonth();
$("#month").value = state.month;
setTab(location.hash.slice(1) || "overview");
loadInfo();
loadMonth();
