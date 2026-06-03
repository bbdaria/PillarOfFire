// Pillar of Fire — dashboard frontend.
// Polls /api/state and re-renders. Keeps minimal client state (selection).

const POLL_MS = 800;
const state = { calls: [], incidents: [] };
let selectedCallId = null;
let selectedIncidentId = null;
let prevIncidentCount = 0;
const knownLinks = {}; // incident_id -> #calls, to detect a fresh merge

const EVENT_HE = {
  explosion: "פיצוץ", fire: "שריפה", traffic_accident: "תאונת דרכים",
  medical: "אירוע רפואי", hazmat: 'חומ"ס', unknown: "לא מזוהה",
};
const HAZARD_HE = {
  smoke: "עשן", gas: "גז", fire: "אש", explosion: "פיצוץ", vehicle: "כלי רכב",
};
const DISTRESS_HE = {
  calm: "רגוע", concerned: "מודאג", distressed: "במצוקה", panicked: "בפאניקה", unknown: "—",
};
const FIELD_HE = {
  summary: "תקציר", event_type: "סוג אירוע", location: "מיקום", hazards: "סכנות",
  casualties: "נפגעים", urgency_indicators: "סימני דחיפות", distress_level: "מצוקה",
  missing_information: "מידע חסר", suggested_questions: "שאלות המשך",
};

// --- API ---
async function api(path, method = "GET") {
  const r = await fetch(path, { method });
  return r.json();
}

// --- helpers ---
const esc = (s) => String(s ?? "").replace(/[&<>"]/g, (c) =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
const callColor = (id) => (state.calls.find((c) => c.call_id === id) || {}).color || "#888";

function sevBadge(sev) {
  if (!sev) return "";
  return `<span class="sev-badge sev-${sev.label}">${esc(sev.label.toUpperCase())} · ${sev.score}/10</span>`;
}

// --- demo controls ---
async function loadDemoButtons() {
  const calls = await api("/api/demo-calls");
  const wrap = document.getElementById("call-buttons");
  wrap.innerHTML = "";
  calls.forEach((c) => {
    const b = document.createElement("button");
    b.className = "call-btn";
    b.innerHTML = `▶ ${esc(c.title)}`;
    b.onclick = () => api(`/api/simulate/${c.call_id}`, "POST");
    wrap.appendChild(b);
  });
}
document.getElementById("btn-all").onclick = () => api("/api/simulate-all", "POST");
document.getElementById("btn-reset").onclick = async () => {
  await api("/api/reset", "POST");
  selectedCallId = selectedIncidentId = null;
  prevIncidentCount = 0;
};

// --- render: calls list ---
function renderCalls() {
  document.getElementById("calls-count").textContent = state.calls.length;
  const list = document.getElementById("calls-list");
  if (!state.calls.length) { list.innerHTML = `<div class="muted">אין שיחות עדיין. לחץ "הדמה את כל השיחות".</div>`; return; }
  list.innerHTML = state.calls.map((c) => {
    const last = c.transcript.split(" ").slice(-12).join(" ");
    const sel = c.call_id === selectedCallId ? "selected" : "";
    const live = c.status === "transcribing" ? "pulse" : "";
    return `<div class="call-card ${sel}" style="border-right-color:${c.color}" data-call="${c.call_id}">
      <div class="row">
        <span class="call-id"><span class="dot ${live}" style="background:${c.color}"></span>${esc(c.call_id)}</span>
        <span class="status ${c.status}">${c.status === "transcribing" ? "מתמלל…" : c.status === "analyzed" ? "נותח" : "המתנה"}</span>
      </div>
      <div class="snippet" dir="rtl">${esc(last) || "…"}</div>
    </div>`;
  }).join("");
  list.querySelectorAll(".call-card").forEach((el) =>
    (el.onclick = () => { selectedCallId = el.dataset.call; render(); }));
}

// --- render: selected call transcript + details ---
function renderSelectedCall() {
  const c = state.calls.find((x) => x.call_id === selectedCallId)
    || state.calls[state.calls.length - 1];
  const tEl = document.getElementById("transcript");
  const dEl = document.getElementById("details");
  document.getElementById("transcript-call").textContent = c ? `· ${c.call_id}` : "";
  if (!c) { tEl.textContent = "בחר שיחה כדי לראות תמלול…"; dEl.innerHTML = "—"; return; }

  tEl.innerHTML = `<span style="color:${c.color}">${esc(c.transcript) || "…"}</span>`;
  const a = c.analysis || {};
  const loc = a.location || {};
  const cas = a.casualties || {};
  const hazards = (a.hazards || []).map((h) => `<span class="tag hazard">${esc(HAZARD_HE[h] || h)}</span>`).join("") || "—";
  const urg = (a.urgency_indicators || []).map((u) => `<span class="tag">${esc(u)}</span>`).join("") || "—";
  const questions = (a.suggested_questions || []).map((q) => `<li>${esc(q)}</li>`).join("");
  const missing = (a.missing_information || []).map((m) => `<span class="tag">${esc(m)}</span>`).join("") || "—";
  const casTxt = cas.unknown ? "לא ידוע" :
    [cas.injured ? `${cas.injured} פצועים` : null, cas.dead ? `${cas.dead} הרוגים` : null].filter(Boolean).join(", ") || "ללא";

  dEl.innerHTML = `
    <div class="kv"><span class="k">תקציר</span><span dir="rtl">${esc(a.summary) || "—"}</span></div>
    <div class="kv"><span class="k">סוג אירוע</span><span>${esc(EVENT_HE[a.event_type] || a.event_type || "—")}</span></div>
    <div class="kv"><span class="k">מיקום</span><span dir="rtl">${esc(loc.normalized || loc.raw_text || "—")} <span class="muted">(${Math.round((loc.confidence||0)*100)}%)</span></span></div>
    <div class="kv"><span class="k">נפגעים</span><span>${esc(casTxt)}</span></div>
    <div class="kv"><span class="k">סכנות</span><span class="chips">${hazards}</span></div>
    <div class="kv"><span class="k">דחיפות</span><span class="chips">${urg}</span></div>
    <div class="kv"><span class="k">מצוקה</span><span>${esc(DISTRESS_HE[a.distress_level] || a.distress_level || "—")}</span></div>
    <div class="kv"><span class="k">חומרה</span><span>${sevBadge(a.severity)}</span></div>
    <div class="kv"><span class="k">מידע חסר</span><span class="chips">${missing}</span></div>
    <div><div class="section-title">שאלות המשך מוצעות</div><ul class="questions">${questions || "<li class='muted'>—</li>"}</ul></div>
  `;
}

// --- render: incidents list ---
function renderIncidents() {
  document.getElementById("incidents-count").textContent = state.incidents.length;
  const list = document.getElementById("incidents-list");
  if (!state.incidents.length) { list.innerHTML = `<div class="muted">אין אירעים פעילים.</div>`; return; }
  list.innerHTML = state.incidents.map((inc) => {
    const sel = inc.incident_id === selectedIncidentId ? "selected" : "";
    const dots = inc.call_ids.map((id) => `<span class="dot" style="background:${callColor(id)}"></span>`).join(" ");
    const merged = inc.call_ids.length > 1 ? `<span class="tag">🔗 ${inc.call_ids.length} שיחות מקושרות</span>` : "";
    return `<div class="incident-card ${sel}" data-inc="${inc.incident_id}" style="border-right-color:${inc.severity?.label === 'critical' ? 'var(--critical)' : inc.severity?.label === 'high' ? 'var(--high)' : 'var(--border)'}">
      <div class="row"><span class="inc-title">${esc(inc.title || inc.incident_id)}</span>${sevBadge(inc.severity)}</div>
      <div class="snippet">${dots} ${merged}</div>
    </div>`;
  }).join("");
  list.querySelectorAll(".incident-card").forEach((el) =>
    (el.onclick = () => { selectedIncidentId = el.dataset.inc; render(); }));
}

// --- render: incident detail with color-coded evidence ---
function renderIncidentDetail() {
  const el = document.getElementById("incident-detail");
  const inc = state.incidents.find((i) => i.incident_id === selectedIncidentId);
  if (!inc) { el.innerHTML = `<span class="muted">בחר אירוע מהרשימה כדי לראות את כל השיחות המקושרות.</span>`; return; }

  const linked = inc.call_ids.map((id) =>
    `<span class="linked-chip"><span class="dot" style="background:${callColor(id)}"></span>${esc(id)}</span>`).join("");

  // Color-coded merged evidence: each value tinted by its source call.
  const order = ["summary", "event_type", "location", "casualties", "hazards",
    "urgency_indicators", "distress_level", "missing_information"];
  const evidence = order.map((field) => {
    const items = (inc.merged && inc.merged[field]) || [];
    if (!items.length) return "";
    const rendered = items.map((it) => {
      let v = it.value;
      if (field === "event_type") v = EVENT_HE[v] || v;
      if (field === "hazards") v = HAZARD_HE[v] || v;
      if (field === "distress_level") v = DISTRESS_HE[v] || v;
      return `<span class="evidence-item" style="border-right-color:${it.color}" title="מקור: ${esc(it.call_id)}">
        <span class="dot" style="background:${it.color}"></span><span dir="rtl">${esc(v)}</span></span>`;
    }).join("");
    return `<div class="evidence-row"><div class="section-title">${esc(FIELD_HE[field] || field)}</div>${rendered}</div>`;
  }).join("");

  // Match score breakdown (why calls were linked).
  let matchTable = "";
  if ((inc.match_scores || []).length && inc.call_ids.length > 1) {
    const rows = inc.match_scores.filter((m) => m.total > 0).map((m) =>
      `<tr><td><span class="dot" style="background:${callColor(m.call_id)}"></span> ${esc(m.call_id)}</td>
        <td>${m.location}</td><td>${m.event_type}</td><td>${m.time}</td><td>${m.semantic}</td><td>${m.shared_entities}</td>
        <td><b>${m.total}</b></td></tr>`).join("");
    matchTable = `<div><div class="section-title">מדדי קישור (מדוע השיחות אוחדו)</div>
      <table class="match-table">
        <tr><th>שיחה</th><th>מיקום</th><th>סוג</th><th>זמן</th><th>סמנטי</th><th>ישויות</th><th>סה"כ</th></tr>
        ${rows}</table>
      <div class="muted" style="margin-top:4px">סף קישור: 0.55</div></div>`;
  }

  const steps = (inc.recommended_next_steps || []).map((s) => `<li>${esc(s)}</li>`).join("");
  const justMerged = knownLinks[inc.incident_id] && inc.call_ids.length > knownLinks[inc.incident_id];

  el.innerHTML = `
    <div class="${justMerged ? "merge-flash" : ""}">
      <div class="inc-head">
        <div>
          <div class="inc-title">${esc(inc.title || inc.incident_id)}</div>
          <div class="muted">${esc(EVENT_HE[inc.event_type] || inc.event_type)} · ${inc.call_ids.length} שיחות</div>
        </div>
        ${sevBadge(inc.severity)}
      </div>
      <div class="linked-calls">${linked}</div>
      ${inc.severity?.reasoning ? `<div class="muted" dir="rtl">חומרה: ${esc(inc.severity.reasoning)}</div>` : ""}
    </div>
    <div><div class="section-title">ראיות מאוחדות (צבע לפי שיחה מקורית)</div>${evidence || "<span class='muted'>—</span>"}</div>
    ${matchTable}
    <div><div class="section-title">צעדים מומלצים</div><ul class="steps">${steps || "<li class='muted'>—</li>"}</ul></div>
  `;
}

// --- map ---
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
    (inc.locations || []).forEach((loc) => {
      if (loc.lat == null) return;
      const color = inc.severity?.label === "critical" ? "#f85149"
        : inc.severity?.label === "high" ? "#f0883e" : "#d6a30b";
      const m = L.circleMarker([loc.lat, loc.lng], {
        radius: 9, color, fillColor: color, fillOpacity: 0.7, weight: 2,
      }).bindPopup(`<b>${esc(inc.title)}</b><br>${esc(loc.normalized)}<br>חומרה: ${inc.severity?.score}/10`);
      m.addTo(markerLayer);
      pts.push([loc.lat, loc.lng]);
    });
  });
  if (pts.length) map.fitBounds(pts, { padding: [40, 40], maxZoom: 14 });
}

// --- main render ---
function render() {
  renderCalls();
  renderSelectedCall();
  renderIncidents();
  renderIncidentDetail();
  renderMap();
}

async function poll() {
  try {
    const s = await api("/api/state");
    state.calls = s.calls;
    state.incidents = s.incidents;

    // Auto-select newest call while transcribing, for the "live" feel.
    const live = state.calls.find((c) => c.status === "transcribing");
    if (live && !selectedCallId) selectedCallId = live.call_id;

    // Auto-open an incident the moment a second call links into it.
    state.incidents.forEach((inc) => {
      if (inc.call_ids.length > 1 && (knownLinks[inc.incident_id] || 0) < 2) {
        selectedIncidentId = inc.incident_id;
      }
    });

    render();
    // Update link tracking AFTER render so the merge-flash fires once.
    state.incidents.forEach((inc) => { knownLinks[inc.incident_id] = inc.call_ids.length; });
    prevIncidentCount = state.incidents.length;
  } catch (e) {
    console.error(e);
  }
}

initMap();
loadDemoButtons();
poll();
setInterval(poll, POLL_MS);
