// Pillar of Fire — dispatcher workspace.
// Polls /api/state and re-renders. Client state = who I am + which incident is open.

const POLL_MS = 800;
const state = { calls: [], incidents: [], dispatchers: [], suggestions: [], known_events: [] };
let me = localStorage.getItem("dispatcher_id") || "d-daria";
let openIncidentId = null;
let lastDrawerSig = null; // skip drawer re-render (and tooltip teardown) when unchanged
const knownCalls = {}; // incident_id -> #calls, to flash a fresh merge

// --- Hebrew label maps ---
const EVENT_HE = {
  explosion: "פיצוץ", fire: "שריפה", traffic_accident: "תאונת דרכים",
  medical: "אירוע רפואי", hazmat: 'חומ"ס', unknown: "אירוע לא מזוהה",
};
const HAZARD_HE = { smoke: "עשן", gas: "גז", fire: "אש", explosion: "פיצוץ", vehicle: "כלי רכב" };
const DISTRESS_HE = { calm: "רגוע", concerned: "מודאג", distressed: "במצוקה", panicked: "בפאניקה", unknown: "—" };
const SEV_HE = { low: "נמוכה", medium: "בינונית", high: "גבוהה", critical: "קריטית" };
const FIELD_LABEL = {
  summary: "תקציר", location: "מיקום", casualties: "נפגעים", hazards: "סכנות",
  urgency_indicators: "דחיפות", distress_level: "מצוקה", missing_information: "מידע חסר",
};

// --- API + helpers ---
async function api(path, method = "GET", body) {
  const opts = { method };
  if (body !== undefined) { opts.headers = { "Content-Type": "application/json" }; opts.body = JSON.stringify(body); }
  const r = await fetch(path, opts);
  return r.json().catch(() => ({}));
}
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const callById = (id) => state.calls.find((c) => c.call_id === id);
const incById = (id) => state.incidents.find((i) => i.incident_id === id);
const dispById = (id) => state.dispatchers.find((d) => d.dispatcher_id === id);
const sevVar = (label) => `var(--${label || "low"})`;
const initials = (name) => (name || "?").trim().slice(0, 2);

function toast(msg) {
  const t = document.getElementById("toast");
  t.textContent = msg; t.classList.remove("hidden");
  clearTimeout(toast._t);
  toast._t = setTimeout(() => t.classList.add("hidden"), 2600);
}

function sevBadge(sev, small) {
  if (!sev) return "";
  return `<span class="sev-badge sev-${sev.label} ${small ? "sm" : ""}">${esc(SEV_HE[sev.label] || sev.label)} · ${sev.score}/10</span>`;
}
function avatar(disp, cls = "av") {
  if (!disp) return "";
  return `<span class="${cls}" style="background:${disp.color}" title="${esc(disp.name)}">${esc(initials(disp.name))}</span>`;
}

// suggestions where this incident participates (the "other" side included)
function suggestionsFor(incId) {
  return state.suggestions
    .filter((s) => s.incident_a === incId || s.incident_b === incId)
    .map((s) => ({ s, otherId: s.incident_a === incId ? s.incident_b : s.incident_a }))
    .filter((x) => incById(x.otherId)); // other incident still open
}
const myIncidents = () => state.incidents.filter((i) => (i.dispatcher_ids || []).includes(me));
const incidentIsLive = (inc) => (inc.call_ids || []).some((id) => (callById(id) || {}).status === "transcribing");

// --- dispatcher switcher ---
function renderDispatcherSelect() {
  const sel = document.getElementById("dispatcher-select");
  if (sel.dataset.n != state.dispatchers.length) {
    sel.innerHTML = state.dispatchers.map((d) =>
      `<option value="${d.dispatcher_id}">${esc(d.name)}</option>`).join("");
    sel.dataset.n = state.dispatchers.length;
  }
  if (!dispById(me) && state.dispatchers[0]) me = state.dispatchers[0].dispatcher_id;
  sel.value = me;
  const av = document.getElementById("who-avatar");
  const d = dispById(me);
  if (d) { av.textContent = initials(d.name); av.style.background = d.color; }
}
document.getElementById("file-input").onchange = async (e) => {
  const f = e.target.files[0];
  if (!f) return;
  toast(`מעבד הקלטה: ${f.name}`);

  const formData = new FormData();
  formData.append("file", f);
  formData.append("dispatcher_id", me);

  await fetch("/api/upload", {
    method: "POST",
    body: formData
  });

  e.target.value = "";
};

// --- incident cards ---
function renderIncidents() {
  const wrap = document.getElementById("incidents");
  const mine = myIncidents();
  document.getElementById("incidents-count").textContent = mine.length;
  if (!mine.length) {
    wrap.innerHTML = `<div class="empty">אין אירועים פעילים במרחב שלך.<br>העלי הקלטה כדי להתחיל.</div>`;
    return;
  }
  wrap.innerHTML = mine.map((inc) => {
    const sev = inc.severity || {};
    const live = incidentIsLive(inc);
    const sugg = suggestionsFor(inc.incident_id);
    const owners = (inc.dispatcher_ids || []).map((id) => avatar(dispById(id))).join("");
    const nCalls = inc.call_ids.length;
    return `<div class="card ${sugg.length ? "has-suggestion" : ""}" data-inc="${inc.incident_id}" style="--sev:${sevVar(sev.label)}">
      <div class="card-top">
        <div>
          <div class="card-title">${esc(inc.title || EVENT_HE[inc.event_type] || inc.incident_id)}</div>
          <div class="card-sub">${esc(EVENT_HE[inc.event_type] || inc.event_type)}</div>
        </div>
        ${sevBadge(sev, true)}
      </div>
      <div class="card-meta">
        ${live ? `<span class="chip live"><span class="dot pulse" style="background:var(--link)"></span>מתמלל…</span>` : ""}
        <span class="chip">🔗 ${nCalls} ${nCalls === 1 ? "שיחה" : "שיחות"}</span>
        ${sugg.length ? `<span class="chip suggestion">⚠ הצעת איחוד</span>` : ""}
        ${(inc.event_context && inc.event_context.length) ? `<span class="chip known-near">📍 אירוע ידוע בקרבת מקום</span>` : ""}
        <span class="owners">${owners}</span>
      </div>
    </div>`;
  }).join("");
  wrap.querySelectorAll(".card").forEach((el) =>
    (el.onclick = () => openDrawer(el.dataset.inc)));
}

// --- drawer (incident detail) ---
function openDrawer(incId) { openIncidentId = incId; lastDrawerSig = null; render(); }
function closeDrawer() {
  openIncidentId = null; lastDrawerSig = null; hideSegTip();
  document.getElementById("drawer").classList.add("hidden");
  document.getElementById("scrim").classList.add("hidden");
}
// A drawer only needs re-rendering when its incident's visible data changes.
function drawerSignature(inc) {
  const calls = inc.call_ids.map((id) => { const c = callById(id) || {}; return [id, c.status, c.transcript]; });
  const sug = suggestionsFor(inc.incident_id).map((x) => x.s.suggestion_id);
  return JSON.stringify([inc.incident_id, inc.title, inc.severity,
  inc.narrative, inc.dispatcher_ids, inc.recommended_next_steps, calls, sug,
  inc.event_context]);
}
document.getElementById("scrim").onclick = closeDrawer;
document.addEventListener("keydown", (e) => { if (e.key === "Escape") closeDrawer(); });

// Narrative paragraph: fact-bearing phrases become hoverable spans whose
// sources are stashed by index, then revealed in a floating tooltip on hover.
let segSources = [];
function renderNarrative(inc) {
  const segs = inc.narrative || [];
  segSources = [];
  if (!segs.length) return "<span class='muted'>טרם חולצו פרטים מהשיחה…</span>";
  const html = segs.map((s) => {
    if (!s.sources || !s.sources.length) return esc(s.text);
    const idx = segSources.push(s.sources) - 1;
    return `<span class="seg" data-idx="${idx}">${esc(s.text)}</span>`;
  }).join("");
  return `<p class="narrative" dir="rtl">${html}</p>`;
}

function bindSegHovers(root) {
  root.querySelectorAll(".seg").forEach((el) => {
    el.onmouseenter = () => showSegTip(el);
    el.onmouseleave = hideSegTip;
  });
}
function showSegTip(el) {
  const sources = segSources[+el.dataset.idx] || [];
  const tip = document.getElementById("seg-tip");
  tip.innerHTML = `<div class="tip-head">נאמר ב־${sources.length} ${sources.length === 1 ? "שיחה" : "שיחות"}</div>` +
    sources.map((s) => {
      const disp = dispById(s.dispatcher_id);
      return `<div class="tip-row"><span class="dot" style="background:${s.color || "#888"}"></span>
        <span class="tip-call">${esc(s.call_id)}${disp ? " · " + esc(disp.name) : ""}</span>
        <span class="tip-detail">${esc(s.detail)}</span></div>`;
    }).join("");
  tip.classList.remove("hidden");
  const r = el.getBoundingClientRect();
  const tr = tip.getBoundingClientRect();
  let top = r.top - tr.height - 8;
  if (top < 8) top = r.bottom + 8; // flip below if no room above
  let left = r.left + r.width / 2 - tr.width / 2;
  left = Math.max(8, Math.min(left, window.innerWidth - tr.width - 8));
  tip.style.top = `${top}px`;
  tip.style.left = `${left}px`;
}
function hideSegTip() { document.getElementById("seg-tip").classList.add("hidden"); }

function renderSuggestion({ s, otherId }) {
  const other = incById(otherId);
  const ownerNames = (other.dispatcher_ids || []).map((id) => (dispById(id) || {}).name).filter(Boolean);
  const pct = Math.round((s.score?.total || 0) * 100);
  const crossNote = ownerNames.length && !ownerNames.includes((dispById(me) || {}).name)
    ? ` (מטופל ע"י ${esc(ownerNames.join(", "))})` : "";
  return `<div class="suggest-card" data-sug="${s.suggestion_id}">
    <div class="suggest-head">⚠ הצעת איחוד אירועים</div>
    <div class="suggest-body">ייתכן שזהו אותו אירוע כמו: <b>${esc(other.title || otherId)}</b><span class="muted">${crossNote}</span></div>
    <div class="suggest-meta">התאמה ${pct}%</div>
    <div class="match-bar"><i style="width:${pct}%"></i></div>
    <div class="suggest-actions">
      <button class="btn-merge" data-merge="${s.suggestion_id}">אחד אירועים</button>
      <button class="btn-dismiss" data-reject="${s.suggestion_id}">התעלם</button>
    </div>
  </div>`;
}

function renderDrawer() {
  const drawer = document.getElementById("drawer");
  const inc = incById(openIncidentId);
  if (!inc) { closeDrawer(); return; }
  document.getElementById("scrim").classList.remove("hidden");
  drawer.classList.remove("hidden");
  drawer.setAttribute("aria-hidden", "false");

  const sev = inc.severity || {};
  const m = inc.merged || {};
  const owners = (inc.dispatcher_ids || []).map((id) => avatar(dispById(id))).join("");
  const sugg = suggestionsFor(inc.incident_id);

  // narrative summary with hover-to-source provenance
  const narrative = renderNarrative(inc);

  // live transcripts, one block per linked call
  const transcripts = inc.call_ids.map((id) => {
    const c = callById(id); if (!c) return "";
    const disp = dispById(c.dispatcher_id);
    const liveTag = c.status === "transcribing" ? `<span class="dot pulse" style="background:var(--link)"></span>מתמלל`
      : c.status === "error" ? `<span style="color:#e35d6a">⚠ שגיאת תמלול</span>` : "נותח";
    return `<div class="tr-block">
      <div class="tr-head"><span class="dot" style="background:${c.color}"></span>${esc(c.call_id)}${disp ? " · " + esc(disp.name) : ""} <span class="muted" style="margin-inline-start:auto">${liveTag}</span></div>
      <div class="tr-text" dir="rtl">${esc(c.transcript) || "…"}</div>
    </div>`;
  }).join("");

  const linked = inc.call_ids.map((id) => {
    const c = callById(id) || {}; const disp = dispById(c.dispatcher_id);
    return `<span class="prov"><span class="dot" style="background:${c.color || "#888"}"></span>${esc(id)}${disp ? " · " + esc(disp.name) : ""}</span>`;
  }).join("");

  const steps = (inc.recommended_next_steps || []).map((s) => `<li>${esc(s)}</li>`).join("");

  // match-score detail (why a merge was suggested / done)
  let matchDetail = "";
  const scored = (inc.match_scores || []).filter((s) => s.total > 0);
  if (scored.length) {
    const rows = scored.map((s) =>
      `<tr><td>${esc(s.call_id)}</td><td>${s.location}</td><td>${s.event_type}</td><td>${s.time}</td><td>${s.semantic}</td><td>${s.shared_entities}</td><td><b>${s.total}</b></td></tr>`).join("");
    matchDetail = `<details class="match"><summary>מדדי קישור (מדוע אוחדו)</summary>
      <table class="match-table">
        <tr><th>שיחה</th><th>מיקום</th><th>סוג</th><th>זמן</th><th>סמנטי</th><th>ישויות</th><th>סה"כ</th></tr>${rows}
      </table></details>`;
  }

  const justMerged = (knownCalls[inc.incident_id] || 0) < inc.call_ids.length && inc.call_ids.length > 1;

  drawer.innerHTML = `
    <div class="dr-head">
      <div class="dr-head-top">
        <div>
          <div class="dr-title">${esc(inc.title || inc.incident_id)}</div>
          <div class="dr-sub">${sevBadge(sev)} <span>${esc(EVENT_HE[inc.event_type] || inc.event_type)}</span> · <span>${inc.call_ids.length} שיחות</span> <span class="owners">${owners}</span></div>
        </div>
        <button class="dr-close" id="dr-close">✕</button>
      </div>
      ${sev.reasoning ? `<div class="muted" dir="rtl" style="margin-top:8px;font-size:12px">${esc(sev.reasoning)}</div>` : ""}
    </div>
    <div class="dr-body ${justMerged ? "merge-flash" : ""}">
      ${sugg.length ? `<div class="section">${sugg.map(renderSuggestion).join("")}</div>` : ""}
      ${window.renderContextAlertHTML ? window.renderContextAlertHTML(inc) : ""}
      <div class="section">
        <div class="section-title">תמונת מצב · רחפי על משפט לצפייה במקורות</div>
        ${narrative}
      </div>
      <div class="section">
        <div class="section-title">תמלול חי</div>
        <div class="transcripts">${transcripts}</div>
      </div>
      <div class="section">
        <div class="section-title">שיחות מקושרות</div>
        <div class="linked">${linked}</div>
      </div>
      <div class="section">
        <div class="section-title">צעדים מומלצים</div>
        <ul class="steps">${steps || "<li class='muted'>—</li>"}</ul>
      </div>
      ${matchDetail}
    </div>`;

  document.getElementById("dr-close").onclick = closeDrawer;
  drawer.querySelectorAll("[data-merge]").forEach((b) => (b.onclick = () => doMerge(b.dataset.merge)));
  drawer.querySelectorAll("[data-reject]").forEach((b) => (b.onclick = () => doReject(b.dataset.reject)));
  if (window.bindContextAlert) window.bindContextAlert(drawer);
  bindSegHovers(drawer);
}

async function doMerge(suggestionId) {
  const res = await api("/api/merge", "POST", { suggestion_id: suggestionId });
  if (res && res.incident_id) { openIncidentId = res.incident_id; toast("האירועים אוחדו"); }
  await poll();
}
async function doReject(suggestionId) {
  await api(`/api/suggestion/${suggestionId}/reject`, "POST");
  toast("ההצעה נדחתה"); await poll();
}

// --- map (shared, global) ---
let map, markerLayer;
function initMap() {
  map = L.map("map", { zoomControl: true, attributionControl: false }).setView([32.08, 34.8], 9);
  L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(map);
  markerLayer = L.layerGroup().addTo(map);
}
function renderMap() {
  if (!map) return;
  markerLayer.clearLayers();
  const pts = [];
  state.incidents.forEach((inc) => {
    const loc = (inc.locations || []).find((l) => l.lat != null);
    if (!loc) return;
    const color = sevColor(inc.severity?.label);
    const radius = Math.min(26, 8 + (inc.call_ids.length - 1) * 5); // size = #calls
    const mine = (inc.dispatcher_ids || []).includes(me);
    const owners = (inc.dispatcher_ids || []).map((id) => (dispById(id) || {}).name).filter(Boolean).join(", ");
    const mk = L.circleMarker([loc.lat, loc.lng], {
      radius, color: mine ? "#fff" : color, weight: mine ? 2 : 1.5,
      fillColor: color, fillOpacity: 0.55,
    }).bindPopup(`<b>${esc(inc.title)}</b><br>חומרה: ${SEV_HE[inc.severity?.label] || ""} ${inc.severity?.score}/10<br>שיחות מאוחדות: ${inc.call_ids.length}<br>מטופל ע"י: ${esc(owners)}`);
    mk.on("click", () => { if (mine) openDrawer(inc.incident_id); });
    mk.addTo(markerLayer);
    pts.push([loc.lat, loc.lng]);
  });
  if (pts.length && !map._fitOnce) { map.fitBounds(pts, { padding: [40, 40], maxZoom: 13 }); map._fitOnce = true; }
}
function sevColor(label) {
  return label === "critical" ? "#f85149" : label === "high" ? "#f0883e"
    : label === "medium" ? "#d6a30b" : "#3fb950";
}

// --- theme (light / dark) ---
function applyTheme(t) {
  document.body.classList.toggle("light", t === "light");
  const btn = document.getElementById("btn-theme");
  if (btn) btn.textContent = t === "light" ? "☀️" : "🌙";
}
let theme = localStorage.getItem("theme") || "dark";
applyTheme(theme);
document.getElementById("btn-theme").onclick = () => {
  theme = theme === "light" ? "dark" : "light";
  localStorage.setItem("theme", theme);
  applyTheme(theme);
};

// --- controls ---
document.getElementById("btn-reset").onclick = async () => {
  await api("/api/reset", "POST"); openIncidentId = null;
  for (const k in knownCalls) delete knownCalls[k];
  if (map) map._fitOnce = false;
};
document.getElementById("btn-upload").onclick = () => document.getElementById("file-input").click();

// --- main render + poll ---
function render() {
  renderDispatcherSelect();
  renderIncidents();
  renderMap();
  if (window.renderKnownLayer) window.renderKnownLayer(); // subtle known-events map layer
  if (openIncidentId) {
    const inc = incById(openIncidentId);
    if (!inc) { closeDrawer(); return; }
    const sig = drawerSignature(inc);
    if (sig !== lastDrawerSig) { renderDrawer(); lastDrawerSig = sig; }
  }
}

async function poll() {
  try {
    const s = await api("/api/state");
    state.calls = s.calls || [];
    state.incidents = s.incidents || [];
    state.dispatchers = s.dispatchers || [];
    state.suggestions = s.suggestions || [];
    state.known_events = s.known_events || [];
    render();
    // track call counts AFTER render so the merge-flash fires once
    state.incidents.forEach((inc) => { knownCalls[inc.incident_id] = inc.call_ids.length; });
  } catch (e) { console.error(e); }
}

initMap();
poll();
setInterval(poll, POLL_MS);
