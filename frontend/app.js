// Pillar of Fire — dispatcher workspace.
// Polls /api/state and re-renders. Client state = who I am + which incident is open.

const POLL_MS = 800;
const state = { calls: [], incidents: [], dispatchers: [], suggestions: [], known_events: [] };

// --- role + identity (no real auth; demo) -------------------------------
const ROLES = ["moked", "meshager", "hamal"];
const ROLE_HE = { moked: "מוקדנית", meshager: "משגר", hamal: 'מצודה' };
const ROLE_TAGLINE = {
  moked: "מרחב עבודה אישי למוקדנית · איחוד שיחות חכם",
  meshager: "ניהול אירועים שהועברו · שליחת כוחות וקבלת החלטות",
  hamal: 'תמונת מצב כלל־מערכתית · מרכז שליטה (מצודה)',
};
const ROLE_DEFAULT_USER = { moked: "d-daria", meshager: "m-shahar", hamal: "h-mefaked" };
let role = localStorage.getItem("role") || "moked";
// Remembered person per role, so switching roles always lands on a valid id.
const meByRole = {
  moked: localStorage.getItem("me_moked") || localStorage.getItem("dispatcher_id") || ROLE_DEFAULT_USER.moked,
  meshager: localStorage.getItem("me_meshager") || ROLE_DEFAULT_USER.meshager,
  hamal: ROLE_DEFAULT_USER.hamal,
};
let me = meByRole[role];
let openIncidentId = null;
let lastDrawerSig = null; // skip drawer re-render (and tooltip teardown) when unchanged
let lastHamalSig = null;  // same idea for the dashboard
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
const WF_HE = { new: "חדש", forwarded: "הועבר למשגר", in_progress: "בטיפול", resolved: "טופל", escalated: 'הועבר למצודה' };
const RES_HE = { ambulance: "אמבולנס", fire: "כבאית", police: "משטרה" };
const RES_ICON = { ambulance: "🚑", fire: "🚒", police: "🚓" };
const PRIORITIES = ["low", "medium", "high", "critical"];
// 4-level urgency scale shown to users (1/4 low … 4/4 critical), decoupled from
// the analyzer's internal 1–10 score.
const SEV_N = { low: 1, medium: 2, high: 3, critical: 4 };
const sev4 = (sev) => SEV_N[sev && sev.label] || 1;

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

function sevBadge(sev, small, noNum) {
  if (!sev) return "";
  const num = noNum ? "" : ` · ${sev4(sev)}/4`;
  return `<span class="sev-badge sev-${sev.label} ${small ? "sm" : ""}">${esc(SEV_HE[sev.label] || sev.label)}${num}</span>`;
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

// --- role helpers ---
const usersByRole = (r) => state.dispatchers.filter((d) => d.role === r);
function firstUserOfRole(r) { const u = usersByRole(r)[0]; return u ? u.dispatcher_id : ROLE_DEFAULT_USER[r]; }
const meshagerUsers = () => usersByRole("meshager");
// Effective severity: a manual override wins over the computed score.
const effSev = (inc) => (inc && inc.priority_override) ? inc.priority_override : (inc ? inc.severity : null);
// Events forwarded to the currently-acting משגר.
const myForwarded = () => state.incidents.filter((i) => i.assigned_meshager_id === me);
// Per-incident casualty estimate = MAX across its calls, ignoring nulls
// (null = not reported). Returns null when no call reported a number.
function incidentCasualty(inc, field) {
  let best = null;
  (inc.call_ids || []).forEach((id) => {
    const c = callById(id);
    const v = c && c.analysis && c.analysis.casualties ? c.analysis.casualties[field] : null;
    if (v != null) best = best == null ? v : Math.max(best, v);
  });
  return best;
}
// LLM-extracted facts aggregated across an incident's calls.
function incidentFacts(inc) {
  const a = (inc.call_ids || []).map(callById).filter(Boolean).map((c) => c.analysis || {});
  const first = a.find((x) => x.date || x.time) || {};
  return {
    date: first.date || "", time: first.time || "",
    callers: [...new Set(a.map((x) => x.caller).filter(Boolean))],
    tags: [...new Set(a.flatMap((x) => x.tags || []))],
    ambulance: a.some((x) => x.ambulance_needed),
    injured: incidentCasualty(inc, "injured"),
    dead: incidentCasualty(inc, "dead"),
  };
}

function setRole(r) {
  if (!ROLES.includes(r)) return;
  role = r; localStorage.setItem("role", r);
  me = meByRole[r] || firstUserOfRole(r);
  lastDrawerSig = null; lastHamalSig = null;
  openIncidentId = null; hideSegTip();
  document.getElementById("drawer").classList.add("hidden");
  document.getElementById("scrim").classList.add("hidden");
  render();
}
// --- top bar: role switcher ---
function renderTopbar() {
  // role switch active state + tagline
  document.querySelectorAll("#role-switch .role-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.role === role));
  const tag = document.getElementById("tagline");
  if (tag) tag.textContent = ROLE_TAGLINE[role] || "";
  // gate moked-only controls (upload / calendar)
  document.querySelectorAll(".moked-only").forEach((el) =>
    (el.style.display = role === "moked" ? "" : "none"));
  document.body.dataset.role = role;
  ensureValidMe();
}

// Single-user demo: no person picker. Just keep `me` pointing at a valid
// identity for the current role (the sole seeded user of that role).
function ensureValidMe() {
  const users = usersByRole(role);
  if (!users.find((d) => d.dispatcher_id === me)) { me = firstUserOfRole(role); meByRole[role] = me; }
}
// Upload one or more recordings at once. Each file becomes its own call/incident,
// uploaded in parallel so a batch of recordings streams in together.
document.getElementById("file-input").onchange = async (e) => {
  const files = [...e.target.files];
  if (!files.length) return;
  toast(files.length === 1
    ? `מעבד הקלטה: ${files[0].name}`
    : `מעבד ${files.length} הקלטות…`);

  await Promise.all(files.map((f) => {
    const formData = new FormData();
    formData.append("file", f);
    formData.append("dispatcher_id", meByRole.moked || me);
    return fetch("/api/upload", { method: "POST", body: formData }).catch((err) =>
      console.error("upload failed for", f.name, err));
  }));

  e.target.value = "";
};

// --- incident cards (KANBAN layout by severity) ---
function renderIncidents() {
  const wrap = document.getElementById("incidents");
  const mine = myIncidents();
  document.getElementById("incidents-count").textContent = mine.length;
  if (!mine.length) {
    wrap.innerHTML = `<div class="empty">אין אירועים פעילים במרחב שלך.<br>העלי הקלטה כדי להתחיל.</div>`;
    return;
  }
  // Group by severity into 4 columns
  const buckets = { critical: [], high: [], medium: [], low: [] };
  mine.forEach((inc) => {
    const sev = effSev(inc) || {};
    const label = sev.label || "low";
    if (buckets[label]) buckets[label].push(inc);
    else buckets.low.push(inc);
  });

  const colOrder = ["critical", "high", "medium", "low"];
  wrap.innerHTML = `<div class="kanban">${colOrder.map((level) => {
    const items = buckets[level];
    return `<div class="kanban-col">
      <div class="kanban-header" style="--sev:var(--${level})">
        <span class="kanban-dot" style="background:var(--${level})"></span>
        ${esc(SEV_HE[level])} <span class="count">${items.length}</span>
      </div>
      <div class="kanban-cards">
        ${items.length ? items.map((inc) => renderIncidentCard(inc)).join("") : `<div class="kanban-empty">—</div>`}
      </div>
    </div>`;
  }).join("")}</div>`;
  wrap.querySelectorAll(".card").forEach((el) =>
    (el.onclick = () => openDrawer(el.dataset.inc)));
}

function renderIncidentCard(inc) {
  const sev = effSev(inc) || {};
  const live = incidentIsLive(inc);
  const sugg = suggestionsFor(inc.incident_id);
  const wf = inc.workflow_status || "new";
  // Severity is conveyed by the colored edge bar (--sev) — no explicit text needed.
  return `<div class="card ${sugg.length ? "has-suggestion" : ""}" data-inc="${inc.incident_id}" style="--sev:${sevVar(sev.label)}">
    <div class="card-top">
      <div>
        <div class="card-title">${esc(inc.title || EVENT_HE[inc.event_type] || inc.incident_id)}</div>
        <div class="card-sub">${esc(EVENT_HE[inc.event_type] || inc.event_type)}</div>
      </div>
    </div>
    <div class="card-meta">
      ${live ? `<span class="chip live"><span class="dot pulse" style="background:var(--link)"></span>מתמלל…</span>` : ""}
      <span class="chip">🔗 ${inc.call_ids.length} ${inc.call_ids.length === 1 ? "שיחה" : "שיחות"}</span>
      ${sugg.length ? `<span class="chip suggestion">⚠ הצעת איחוד</span>` : ""}
      ${wf !== "new" ? `<span class="chip wf-chip wf-${wf}">${WF_HE[wf]}</span>` : ""}
      ${(inc.event_context && inc.event_context.length) ? `<span class="chip known-near">📍 אירוע ידוע בקרבת מקום</span>` : ""}
    </div>
  </div>`;
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
  inc.event_context, role,
  inc.workflow_status, inc.assigned_meshager_id, inc.dispatched,
  inc.priority_override, inc.forwarded_by, inc.escalated_to_c2,
  inc.review_flag, state.incidents.length]);
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

  const sev = effSev(inc) || {};
  const m = inc.merged || {};
  const owners = (inc.dispatcher_ids || []).map((id) => avatar(dispById(id))).join("");
  const sugg = suggestionsFor(inc.incident_id);

  // narrative summary with hover-to-source provenance
  const narrative = renderNarrative(inc);

  // LLM key facts row
  const f = incidentFacts(inc);
  const factsHtml = `<div class="dr-facts">
    ${f.date || f.time ? `<span class="fact">🕒 ${esc(f.date)} ${esc(f.time)}</span>` : ""}
    ${f.callers.length ? `<span class="fact">📞 ${esc(f.callers.join(", "))}</span>` : ""}
    ${f.ambulance ? `<span class="fact amb">🚑 נדרש אמבולנס${f.injured != null ? ` · ${f.injured} פצועים` : ""}</span>`
      : (f.injured != null ? `<span class="fact">${f.injured} פצועים</span>` : "")}
    ${f.dead ? `<span class="fact crit">☠ ${f.dead} הרוגים</span>` : ""}
    ${f.tags.length ? `<span class="fact">🏷 ${f.tags.map(esc).join(" · ")}</span>` : ""}
  </div>`;

  // Editing is allowed for the call-taker / dispatcher, not the read-only C2 view.
  const canEdit = role !== "hamal";

  // live transcripts, one block per linked call (editable)
  const transcripts = inc.call_ids.map((id) => {
    const c = callById(id); if (!c) return "";
    const liveTag = c.status === "transcribing" ? `<span class="dot pulse" style="background:var(--link)"></span>מתמלל`
      : c.status === "error" ? `<span style="color:#e35d6a">⚠ שגיאת תמלול</span>` : "נותח";
    return `<div class="tr-block">
      <div class="tr-head"><span class="dot" style="background:${c.color}"></span>שיחה ${esc(c.call_number || c.call_id)} <span class="muted" style="margin-inline-start:auto">${liveTag}</span>${canEdit ? `<button class="edit-link" data-edit-call="${esc(id)}">עריכה</button>` : ""}</div>
      <div class="tr-text" id="tr-${esc(id)}" dir="rtl">${esc(c.transcript) || "…"}</div>
    </div>`;
  }).join("");

  const linked = inc.call_ids.map((id) => {
    const c = callById(id) || {};
    return `<span class="prov"><span class="dot" style="background:${c.color || "#888"}"></span>שיחה ${esc(c.call_number || id)}</span>`;
  }).join("");

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
        <div class="section-title">תמונת מצב · רחפי על משפט לצפייה במקורות${canEdit ? `<button class="edit-link" id="edit-summary">עריכת הסיכום</button>` : ""}</div>
        ${factsHtml}
        <div id="dr-summary">${narrative}</div>
      </div>
      <div class="section">
        <div class="section-title">תמלול חי</div>
        <div class="transcripts">${transcripts}</div>
      </div>
      <div class="section">
        <div class="section-title">שיחות מקושרות</div>
        <div class="linked">${linked}</div>
      </div>
      ${matchDetail}
      ${(role === "moked" || role === "meshager") ? renderActionFooter(inc) : ""}
    </div>`;

  document.getElementById("dr-close").onclick = closeDrawer;
  drawer.querySelectorAll("[data-merge]").forEach((b) => (b.onclick = () => doMerge(b.dataset.merge)));
  drawer.querySelectorAll("[data-reject]").forEach((b) => (b.onclick = () => doReject(b.dataset.reject)));
  // Inline editing: summary + each call transcript.
  const esBtn = drawer.querySelector("#edit-summary");
  if (esBtn) esBtn.onclick = () => editSummary(inc.incident_id);
  drawer.querySelectorAll("[data-edit-call]").forEach((b) =>
    (b.onclick = () => editTranscript(b.dataset.editCall)));
  if (window.bindContextAlert) window.bindContextAlert(drawer);
  bindSegHovers(drawer);
  bindActionFooter(drawer, inc);
}

// While an inline editor is open, suppress the polling re-render so typing
// isn't wiped. Saving/cancelling clears it and forces a fresh render.
let drawerEditing = false;
function editorHTML(value) {
  return `<textarea class="edit-area" dir="rtl">${esc(value)}</textarea>
    <div class="edit-actions">
      <button class="act-btn primary" data-save>שמירה</button>
      <button class="act-btn" data-cancel>ביטול</button>
    </div>`;
}
// POST JSON and report the HTTP status, so a missing endpoint (e.g. server not
// restarted -> 404/405) gives an actionable message instead of failing silently.
async function saveEdit(path, body, okMsg) {
  try {
    const r = await fetch(path, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    if (r.ok) { toast(okMsg); return true; }
    if (r.status === 404 || r.status === 405) {
      toast(`שמירה נכשלה (${r.status}) — יש להפעיל מחדש את השרת (run.ps1)`);
    } else {
      toast(`שמירה נכשלה (${r.status})`);
    }
  } catch (e) {
    toast("שמירה נכשלה — אין חיבור לשרת");
  }
  return false;
}
function editSummary(incId) {
  const inc = incById(incId); if (!inc) return;
  const host = document.getElementById("dr-summary"); if (!host) return;
  drawerEditing = true;
  const cur = (inc.narrative || []).map((s) => s.text).join(" ");
  host.innerHTML = editorHTML(cur);
  host.querySelector("[data-save]").onclick = async () => {
    const val = host.querySelector("textarea").value.trim();
    await saveEdit(`/api/incident/${incId}/summary`, { summary: val }, "הסיכום עודכן");
    drawerEditing = false; lastDrawerSig = null; await poll();
  };
  host.querySelector("[data-cancel]").onclick = () => {
    drawerEditing = false; lastDrawerSig = null; renderDrawer();
  };
  host.querySelector("textarea").focus();
}
function editTranscript(callId) {
  const c = callById(callId); if (!c) return;
  const host = document.getElementById(`tr-${callId}`); if (!host) return;
  drawerEditing = true;
  host.innerHTML = editorHTML(c.transcript || "");
  host.querySelector("[data-save]").onclick = async () => {
    const val = host.querySelector("textarea").value;
    await saveEdit(`/api/call/${callId}/transcript`, { transcript: val }, "התמלול עודכן");
    drawerEditing = false; lastDrawerSig = null; await poll();
  };
  host.querySelector("[data-cancel]").onclick = () => {
    drawerEditing = false; lastDrawerSig = null; renderDrawer();
  };
  host.querySelector("textarea").focus();
}

// --- role action footer in the drawer (forward / dispatch / status / priority) ---
function renderActionFooter(inc) {
  const wf = inc.workflow_status || "new";
  const statusChip = `<span class="wf-chip wf-${wf}">${WF_HE[wf] || wf}</span>`;
  const dispatched = (inc.dispatched || []).map((d) =>
    `<span class="res-chip">${RES_HE[d.resource] || d.resource} ✓</span>`).join("");
  const prio = effSev(inc) || {};
  const prioBtns = PRIORITIES.map((p) =>
    `<button class="prio-btn sev-${p} ${prio.label === p ? "active" : ""}" data-prio="${p}">${SEV_HE[p]}</button>`).join("");

  // Manual link: combine this incident with another so the LLM re-summarises
  // their merged information. Available to BOTH the call-taker and the dispatcher.
  function linkSectionHTML() {
    const others = state.incidents.filter((i) =>
      i.status === "open" && i.incident_id !== inc.incident_id);
    const linkOpts = others.length
      ? others.map((o) => `<button class="link-opt" data-link="${o.incident_id}">
          <span class="dot" style="background:${sevVar((effSev(o) || {}).label)}"></span>
          <span class="link-opt-title">${esc(o.title || o.incident_id)}</span>
          <span class="muted">${o.call_ids.length} שיחות</span>
        </button>`).join("")
      : `<div class="muted" style="padding:8px 4px">אין אירועים אחרים לקישור כרגע</div>`;
    return `
      <div class="act-label">קישור לאירוע אחר</div>
      <div class="act-row"><button class="act-btn" id="link-btn">🔗 קשר לאירוע אחר</button></div>
      <div id="link-list" class="link-list hidden">${linkOpts}</div>`;
  }

  let roleActions = "";
  if (role === "moked") {
    // Forward (only if not already forwarded — "העבר מחדש" removed).
    const fwd = inc.assigned_meshager_id ? "" : `
        <div class="act-label">העברה למשגר</div>
        <div class="act-row">
          <button class="act-btn primary" id="fwd-btn">העבר למשגר הפנוי ביותר ▸</button>
        </div>`;
    roleActions = `${fwd}${linkSectionHTML()}`;
  } else if (role === "meshager") {
    const active = new Set((inc.dispatched || []).map((d) => d.resource));
    // Resource buttons WITHOUT emojis, with green checkmark when active
    const resBtns = Object.keys(RES_HE).map((r) =>
      `<button class="res-btn ${active.has(r) ? "active" : ""}" data-res="${r}">${RES_HE[r]}${active.has(r) ? " ✓" : ""}</button>`).join("");
    // C2 escalation button
    const isEscalated = inc.escalated_to_c2;
    const c2Btn = `<button class="res-btn ${isEscalated ? "active" : ""}" id="c2-btn">${isEscalated ? 'מצודה ✓' : 'העבר למצודה'}</button>`;
    const steps = ["in_progress", "resolved"].map((s) =>
      `<button class="wf-btn ${wf === s ? "active" : ""}" data-wf="${s}">${WF_HE[s]}</button>`).join("");
    roleActions = `
      <div class="act-label">שליחת כוחות / העברה (לחיצה נוספת מבטלת)</div>
      <div class="act-row">${resBtns}${c2Btn}</div>
      <div class="act-label">סטטוס טיפול</div>
      <div class="act-row">${steps}</div>
      ${linkSectionHTML()}`;
  }

  // Post-merge review banner (shown to the משגר who already holds the event).
  const reviewBanner = (role === "meshager" && inc.review_flag) ? `
      <div class="review-banner">
        <span>⚠ ${esc(inc.review_reason || "האירוע אוחד — נא לבדוק מחדש")}</span>
        <button class="act-btn" id="ack-review-btn">סמן כנבדק</button>
      </div>` : "";

  return `
    <div class="section dr-actions">
      ${reviewBanner}
      <div class="act-head">${statusChip}</div>
      ${dispatched ? `<div class="act-row res-list">${dispatched}</div>` : ""}
      <div class="act-label">עדיפות</div>
      <div class="act-row prio-row">${prioBtns}</div>
      ${roleActions}
    </div>`;
}

function bindActionFooter(drawer, inc) {
  drawer.querySelectorAll("[data-prio]").forEach((b) =>
    (b.onclick = () => doPriority(inc.incident_id, b.dataset.prio)));
  const fwdBtn = drawer.querySelector("#fwd-btn");
  if (fwdBtn) fwdBtn.onclick = () => doForward(inc.incident_id);
  drawer.querySelectorAll("[data-res]").forEach((b) =>
    (b.onclick = () => doDispatch(inc.incident_id, b.dataset.res)));
  drawer.querySelectorAll("[data-wf]").forEach((b) =>
    (b.onclick = () => doStatus(inc.incident_id, b.dataset.wf)));
  const c2Btn = drawer.querySelector("#c2-btn");
  if (c2Btn) c2Btn.onclick = () => doEscalate(inc.incident_id);
  // Manual incident linking (moked)
  const linkBtn = drawer.querySelector("#link-btn");
  const linkList = drawer.querySelector("#link-list");
  if (linkBtn && linkList) linkBtn.onclick = () => linkList.classList.toggle("hidden");
  drawer.querySelectorAll("[data-link]").forEach((b) =>
    (b.onclick = () => doManualLink(inc.incident_id, b.dataset.link)));
  // Acknowledge post-merge review (meshager)
  const ackBtn = drawer.querySelector("#ack-review-btn");
  if (ackBtn) ackBtn.onclick = () => doAckReview(inc.incident_id);
}

async function doManualLink(aId, bId) {
  const res = await api("/api/merge", "POST", { incident_a: aId, incident_b: bId });
  if (res && res.incident_id) {
    openIncidentId = res.incident_id; lastDrawerSig = null;
    toast("האירועים קושרו ואוחדו — תמונת המצב חודשה");
  } else { toast("הקישור נכשל"); }
  await poll();
}
async function doAckReview(id) {
  await api(`/api/incident/${id}/ack_review`, "POST");
  toast("האירוע סומן כנבדק"); await poll();
}

async function doForward(id) {
  const res = await api(`/api/incident/${id}/forward`, "POST", { by: me });
  const name = res && res.assigned_meshager_id ? (dispById(res.assigned_meshager_id) || {}).name : "משגר";
  toast(`האירוע הועבר ל${name || "משגר"} (הפנוי ביותר)`); await poll();
}
async function doStatus(id, status) {
  await api(`/api/incident/${id}/status`, "POST", { status });
  toast(`סטטוס עודכן: ${WF_HE[status] || status}`); await poll();
}
async function doDispatch(id, resource) {
  // Toggle: was it already dispatched before this click?
  const inc = incById(id) || {};
  const wasActive = (inc.dispatched || []).some((d) => d.resource === resource);
  await api(`/api/incident/${id}/dispatch`, "POST", { resource, by: me });
  toast(`${wasActive ? "בוטל" : "נשלח"} ${RES_HE[resource] || resource}`); await poll();
}
async function doPriority(id, label) {
  await api(`/api/incident/${id}/priority`, "POST", { label, by: me });
  toast(`עדיפות עודכנה: ${SEV_HE[label] || label}`); await poll();
}
async function doEscalate(id) {
  await api(`/api/incident/${id}/escalate`, "POST");
  toast('האירוע הועבר למצודה'); await poll();
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

// --- maps (moked shared map + meshager map + hamal all-events map) ---
let map, markerLayer, meshagerMap, meshagerLayer, hamalMap, hamalLayer;
// Clean, high-resolution basemap (CartoDB Positron): light, minimal labels,
// retina tiles. Far less cluttered than the default OSM raster.
const BASE_TILE_URL = "https://{s}.basemaps.cartocdn.com/light_all/{z}/{x}/{y}{r}.png";
const BASE_TILE_OPTS = { maxZoom: 20, detectRetina: true, subdomains: "abcd" };
window.addBaseTiles = (m) => L.tileLayer(BASE_TILE_URL, BASE_TILE_OPTS).addTo(m);
function initMap() {
  map = L.map("map", { zoomControl: true, attributionControl: false }).setView([32.08, 34.8], 9);
  window.addBaseTiles(map);
  markerLayer = L.layerGroup().addTo(map);
}
function initMeshagerMap() {
  if (meshagerMap) return;
  const el = document.getElementById("meshager-map");
  if (!el) return;
  meshagerMap = L.map("meshager-map", { zoomControl: true, attributionControl: false }).setView([32.08, 34.8], 9);
  window.addBaseTiles(meshagerMap);
  meshagerLayer = L.layerGroup().addTo(meshagerMap);
}
function initHamalMap() {
  if (hamalMap) return;
  hamalMap = L.map("hamal-map", { zoomControl: true, attributionControl: false }).setView([32.08, 34.8], 9);
  window.addBaseTiles(hamalMap);
  hamalLayer = L.layerGroup().addTo(hamalMap);
}
// Draw incident markers onto a given map/layer. opts.highlightMine outlines the
// acting user's incidents; opts.openAny lets any marker open its drawer.
// opts.filterFn can restrict which incidents are drawn.
function drawMarkers(mapObj, layer, opts) {
  if (!mapObj) return;
  layer.clearLayers();
  const pts = [];
  const incs = opts.filterFn ? state.incidents.filter(opts.filterFn) : state.incidents;
  incs.forEach((inc) => {
    const loc = (inc.locations || []).find((l) => l.lat != null);
    if (!loc) return;
    const sev = effSev(inc) || {};
    const color = sevColor(sev.label);
    const radius = Math.min(26, 8 + (inc.call_ids.length - 1) * 5); // size = #calls
    const mine = (inc.dispatcher_ids || []).includes(me);
    const highlight = opts.highlightMine && mine;
    const owners = (inc.dispatcher_ids || []).map((id) => (dispById(id) || {}).name).filter(Boolean).join(", ");
    // C2 (מצודה) doesn't show the handling operator — it's an overview, not a workspace.
    const ownerLine = (role === "hamal" || !owners) ? "" : `<br>מטופל ע"י: ${esc(owners)}`;
    const mk = L.circleMarker([loc.lat, loc.lng], {
      radius, color: highlight ? "#fff" : color, weight: highlight ? 2 : 1.5,
      fillColor: color, fillOpacity: 0.55,
    }).bindPopup(`<b>${esc(inc.title)}</b><br>חומרה: ${SEV_HE[sev.label] || ""} ${sev4(sev)}/4<br>שיחות מאוחדות: ${inc.call_ids.length}${ownerLine}`);
    mk.on("click", () => { if (opts.openAny || mine) openDrawer(inc.incident_id); });
    mk.addTo(layer);
    pts.push([loc.lat, loc.lng]);
  });
  if (pts.length && !mapObj._fitOnce) { mapObj.fitBounds(pts, { padding: [40, 40], maxZoom: 13 }); mapObj._fitOnce = true; }
}
function renderMap() { drawMarkers(map, markerLayer, { highlightMine: true, openAny: false }); }
// Softer, pastel-leaning severity colors that read well on the light basemap.
function sevColor(label) {
  return label === "critical" ? "#e0655f" : label === "high" ? "#e8965a"
    : label === "medium" ? "#dcb84e" : "#5fb985";
}

// --- theme (light / dark) ---
// Monochrome SVG icons (currentColor = gray) instead of colorful emoji.
const ICON_MOON = `<svg class="icn" viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M12.7 2.3a1 1 0 0 0-1.1 1.4 7 7 0 0 1-9.3 9.3 1 1 0 0 0-1.4 1.1A9 9 0 1 0 12.7 2.3z"/></svg>`;
const ICON_SUN = `<svg class="icn" viewBox="0 0 24 24" width="16" height="16" fill="currentColor" aria-hidden="true"><path d="M12 7a5 5 0 1 0 0 10 5 5 0 0 0 0-10zm0-6a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0V2a1 1 0 0 1 1-1zm0 19a1 1 0 0 1 1 1v1a1 1 0 1 1-2 0v-1a1 1 0 0 1 1-1zM3 11a1 1 0 1 1 0 2H2a1 1 0 1 1 0-2h1zm19 0a1 1 0 1 1 0 2h-1a1 1 0 1 1 0-2h1zM5.6 4.2l.7.7A1 1 0 1 1 4.9 6.3l-.7-.7a1 1 0 0 1 1.4-1.4zm12.8 12.8l.7.7a1 1 0 0 1-1.4 1.4l-.7-.7a1 1 0 0 1 1.4-1.4zM4.9 17.7a1 1 0 0 1 1.4 1.4l-.7.7a1 1 0 1 1-1.4-1.4l.7-.7zM17.7 4.9a1 1 0 0 1 1.4 1.4l-.7.7A1 1 0 0 1 17 5.6l.7-.7z"/></svg>`;
function applyTheme(t) {
  document.body.classList.toggle("light", t === "light");
  const btn = document.getElementById("btn-theme");
  if (btn) btn.innerHTML = t === "light" ? ICON_SUN : ICON_MOON;
}
let theme = localStorage.getItem("theme") || "light";
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
  if (meshagerMap) meshagerMap._fitOnce = false;
  if (hamalMap) hamalMap._fitOnce = false;
  lastHamalSig = null;
};
document.getElementById("btn-upload").onclick = () => document.getElementById("file-input").click();

// role switcher (single-user demo — no person picker)
document.querySelectorAll("#role-switch .role-btn").forEach((b) =>
  (b.onclick = () => setRole(b.dataset.role)));

// hamal table filters
document.querySelectorAll("#hamal-filters .filter-btn").forEach((b) =>
  (b.onclick = () => setHamalFilter(b.dataset.filter)));
// hamal severity dropdown
const hamalSevSel = document.getElementById("hamal-sev-filter");
if (hamalSevSel) hamalSevSel.onchange = () => { hamalSevFilter = hamalSevSel.value; lastHamalSig = null; render(); };

// --- per-role views ---------------------------------------------------------
function applyRoleVisibility() {
  document.getElementById("view-moked").hidden = role !== "moked";
  document.getElementById("view-meshager").hidden = role !== "meshager";
  document.getElementById("view-hamal").hidden = role !== "hamal";
}

function renderMoked() {
  renderIncidents();
  renderMap();
  if (map) requestAnimationFrame(() => map.invalidateSize()); // container may have been hidden
  if (window.renderKnownLayer) window.renderKnownLayer(); // subtle known-events map layer (moked only)
}

function renderMeshager() {
  const wrap = document.getElementById("meshager-queue");
  const mine = myForwarded();
  document.getElementById("meshager-count").textContent = mine.length;
  if (!mine.length) {
    wrap.innerHTML = `<div class="empty">אין אירועים שהועברו אליך כרגע.<br>אירועים שמוקדנית תעביר אליך יופיעו כאן.</div>`;
  } else {
    // Sort by severity descending (ordered list)
    mine.sort((a, b) => ((effSev(b)?.score || 0) - (effSev(a)?.score || 0)));
    wrap.innerHTML = mine.map(renderMeshagerCard).join("");
    wrap.querySelectorAll(".m-card").forEach((el) => (el.onclick = () => openDrawer(el.dataset.inc)));
  }

  // Initialize and render meshager map
  initMeshagerMap();
  if (meshagerMap) {
    requestAnimationFrame(() => meshagerMap.invalidateSize());
    drawMarkers(meshagerMap, meshagerLayer, {
      highlightMine: false, openAny: true,
      filterFn: (inc) => inc.assigned_meshager_id === me
    });
    // Also render known events layer on meshager map
    if (window.renderKnownLayerOnMap) window.renderKnownLayerOnMap(meshagerMap);
  }
}

function renderMeshagerCard(inc) {
  const sev = effSev(inc) || {};
  const wf = inc.workflow_status || "forwarded";
  const loc = (inc.locations || []).find((l) => l.normalized || l.raw_text) || {};
  const inj = incidentCasualty(inc, "injured");
  const dead = incidentCasualty(inc, "dead");
  const amb = incidentFacts(inc).ambulance;
  const summary = (inc.narrative || []).map((s) => s.text).join(" ");
  // Show green checkmarks for sent resources and C2 escalation
  const active = new Set((inc.dispatched || []).map((d) => d.resource));
  const resChecks = Object.keys(RES_HE).filter((r) => active.has(r)).map((r) =>
    `<span class="res-chip sm">${RES_HE[r]} ✓</span>`).join("");
  const c2Check = inc.escalated_to_c2 ? `<span class="res-chip sm">מצודה ✓</span>` : "";
  const n = inc.call_ids.length;
  return `<div class="card m-card ${inc.review_flag ? "needs-review" : ""}" data-inc="${inc.incident_id}" style="--sev:${sevVar(sev.label)}">
    ${inc.review_flag ? `<div class="m-review">⚠ ${esc(inc.review_reason || "האירוע אוחד — נא לבדוק מחדש")}</div>` : ""}
    <div class="card-top">
      <div>
        <div class="card-title">${esc(inc.title || EVENT_HE[inc.event_type] || inc.incident_id)}</div>
        <div class="card-sub">${esc(EVENT_HE[inc.event_type] || inc.event_type)}${loc.normalized ? ` · ${esc(loc.normalized)}` : ""}</div>
      </div>
      <div class="m-sev-col">
        ${sevBadge(sev, true, true)}
        <span class="m-unified">🔗 ${n} ${n === 1 ? "שיחה" : "שיחות מאוחדות"}</span>
      </div>
    </div>
    <div class="m-summary" dir="rtl">${esc(summary) || "<span class='muted'>טרם חולץ תקציר…</span>"}</div>
    <div class="card-meta">
      <span class="chip wf-chip wf-${wf}">${WF_HE[wf]}</span>
      ${amb ? `<span class="chip amb-chip">דרוש אמבולנס</span>` : ""}
      <span class="chip">נפגעים: ${inj == null ? "—" : inj}${dead ? ` · ☠ ${dead}` : ""}</span>
      ${resChecks}${c2Check}
    </div>
  </div>`;
}

// --- מצודה dashboard ---
function hamalMetrics() {
  // Only count escalated events for C2 dashboard
  const incs = state.incidents.filter((i) => i.escalated_to_c2);
  const total = incs.length;
  const handled = incs.filter((i) => i.workflow_status === "resolved").length;
  const sevCount = { low: 0, medium: 0, high: 0, critical: 0 };
  const typeCount = {};
  incs.forEach((inc) => {
    const lab = (effSev(inc) || {}).label || "low"; if (sevCount[lab] != null) sevCount[lab]++;
    const t = inc.event_type || "unknown"; typeCount[t] = (typeCount[t] || 0) + 1;
  });
  return { total, active: total - handled, handled, sevCount, typeCount };
}
// table-first overview state
let hamalFilter = "all";              // all | open | resolved | critical
let hamalSevFilter = "";              // "" | low | medium | high | critical
let hamalSort = { key: "severity", dir: -1 }; // -1 desc, 1 asc

const WF_ORDER = { new: 0, forwarded: 1, in_progress: 2, escalated: 3, resolved: 4 };
// Remove status column from hamal table
const HAMAL_COLS = [
  { key: "title", label: "אירוע" },
  { key: "type", label: "סוג" },
  { key: "severity", label: "חומרה" },
  { key: "location", label: "מיקום" },
  { key: "calls", label: "שיחות" },
  { key: "injured", label: "נפגעים" },
];
const SORT_VAL = {
  title: (i) => i.title || i.incident_id,
  type: (i) => EVENT_HE[i.event_type] || i.event_type,
  severity: (i) => (effSev(i) || {}).score || 0,
  status: (i) => WF_ORDER[i.workflow_status] ?? 0,
  location: (i) => { const l = (i.locations || []).find((x) => x.normalized || x.raw_text) || {}; return l.normalized || l.raw_text || ""; },
  calls: (i) => i.call_ids.length,
  injured: (i) => { const v = incidentCasualty(i, "injured"); return v == null ? -1 : v; },
};
function incFilterPass(inc) {
  // C2 only sees escalated events
  if (!inc.escalated_to_c2) return false;
  if (hamalFilter === "open") return inc.workflow_status !== "resolved";
  if (hamalFilter === "resolved") return inc.workflow_status === "resolved";
  if (hamalFilter === "critical") return (effSev(inc) || {}).label === "critical";
  // Severity dropdown filter
  if (hamalSevFilter && (effSev(inc) || {}).label !== hamalSevFilter) return false;
  return true;
}

function hamalSignature() {
  const incSig = state.incidents.map((i) => [i.incident_id, i.title, i.event_type, effSev(i),
  i.workflow_status, i.call_ids.length, i.assigned_meshager_id, i.dispatcher_ids,
  incidentCasualty(i, "injured"), incidentCasualty(i, "dead"),
  (i.locations || []).map((l) => [l.lat, l.lng, l.normalized]), i.escalated_to_c2]);
  return JSON.stringify([hamalFilter, hamalSevFilter, hamalSort, incSig]);
}

function renderHamal() {
  initHamalMap();
  requestAnimationFrame(() => { if (hamalMap) hamalMap.invalidateSize(); }); // un-hidden container
  document.querySelectorAll("#hamal-filters .filter-btn").forEach((b) =>
    b.classList.toggle("active", b.dataset.filter === hamalFilter));
  const sig = hamalSignature();
  if (sig === lastHamalSig) return;
  lastHamalSig = sig;
  drawMarkers(hamalMap, hamalLayer, {
    highlightMine: false, openAny: true,
    filterFn: (inc) => inc.escalated_to_c2
  });
  // Known large events on the C2 map too (contextual intelligence everywhere).
  if (window.renderKnownLayerOnMap) window.renderKnownLayerOnMap(hamalMap);
  renderHamalTiles();
  renderHamalCharts();
  renderHamalTable();
}

function renderHamalTiles() {
  const m = hamalMetrics();
  const tile = (label, value, cls = "") =>
    `<div class="tile ${cls}"><div class="tile-val">${value}</div><div class="tile-lbl">${label}</div></div>`;
  document.getElementById("hamal-tiles").innerHTML =
    tile('סה"כ אירועים', m.total) +
    tile("פעילים", m.active, "warn") +
    tile("טופלו", m.handled, "ok");
  // Removed: "נפגעים" and "הרוגים" tiles
}

function barChart(elId, rows) {
  const el = document.getElementById(elId);
  const max = Math.max(1, ...rows.map((r) => r.value));
  el.innerHTML = rows.map((r) => `
    <div class="bar-row">
      <span class="bar-lbl">${esc(r.label)}</span>
      <span class="bar-track"><i style="width:${Math.round(r.value / max * 100)}%;background:${r.color || "var(--link)"}"></i></span>
      <span class="bar-val">${r.value}</span>
    </div>`).join("") || "<div class='muted'>אין נתונים</div>";
}
function renderHamalCharts() {
  const m = hamalMetrics();
  barChart("chart-sev", [
    { label: SEV_HE.critical, value: m.sevCount.critical, color: sevColor("critical") },
    { label: SEV_HE.high, value: m.sevCount.high, color: sevColor("high") },
    { label: SEV_HE.medium, value: m.sevCount.medium, color: sevColor("medium") },
    { label: SEV_HE.low, value: m.sevCount.low, color: sevColor("low") },
  ]);
  // "by type" chart removed per dashboard spec — the map takes that space.
}

function renderHamalTable() {
  const el = document.getElementById("hamal-table");
  const rows = state.incidents.filter(incFilterPass).sort((a, b) => {
    const va = SORT_VAL[hamalSort.key](a), vb = SORT_VAL[hamalSort.key](b);
    if (va < vb) return -hamalSort.dir; if (va > vb) return hamalSort.dir; return 0;
  });
  document.getElementById("hamal-table-count").textContent = `${rows.length} אירועים`;
  const arrow = (k) => hamalSort.key === k ? (hamalSort.dir === 1 ? " ▲" : " ▼") : "";
  const head = HAMAL_COLS.map((c) => `<th data-sort="${c.key}" class="${hamalSort.key === c.key ? "sorted" : ""}">${c.label}${arrow(c.key)}</th>`).join("");
  const body = rows.map((inc) => {
    const sev = effSev(inc) || {};
    const loc = (inc.locations || []).find((l) => l.normalized || l.raw_text) || {};
    const inj = incidentCasualty(inc, "injured");
    return `<tr data-inc="${inc.incident_id}">
      <td>${esc(inc.title || inc.incident_id)}</td>
      <td>${esc(EVENT_HE[inc.event_type] || inc.event_type)}</td>
      <td><span class="sev-badge sev-${sev.label} sm">${SEV_HE[sev.label] || ""} ${sev4(sev)}/4</span></td>
      <td>${esc(loc.normalized || loc.raw_text || "—")}</td>
      <td>${inc.call_ids.length}</td>
      <td>${inj == null ? "—" : inj}</td>
    </tr>`;
  }).join("");
  el.innerHTML = `<table class="data-table">
    <thead><tr>${head}</tr></thead>
    <tbody>${body || `<tr><td colspan="${HAMAL_COLS.length}" class="muted">אין אירועים</td></tr>`}</tbody></table>`;
  el.querySelectorAll("tr[data-inc]").forEach((tr) => (tr.onclick = () => openDrawer(tr.dataset.inc)));
  el.querySelectorAll("th[data-sort]").forEach((th) => (th.onclick = () => setHamalSort(th.dataset.sort)));
}

function setHamalSort(key) {
  if (hamalSort.key === key) hamalSort.dir *= -1;
  else hamalSort = { key, dir: (key === "title" || key === "type" || key === "location" || key === "moked" || key === "meshager") ? 1 : -1 };
  lastHamalSig = null; render();
}
function setHamalFilter(f) { hamalFilter = f; lastHamalSig = null; render(); }

// --- main render + poll ---
function render() {
  renderTopbar();
  applyRoleVisibility();
  if (role === "moked") renderMoked();
  else if (role === "meshager") renderMeshager();
  else if (role === "hamal") renderHamal();
  // shared incident drawer (used by moked/meshager actions and hamal viewing)
  if (openIncidentId) {
    const inc = incById(openIncidentId);
    if (!inc) { closeDrawer(); return; }
    if (drawerEditing) return; // don't clobber an open inline editor
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
