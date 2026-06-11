// Pillar of Fire — Known Large Events layer (contextual intelligence).
//
// This module is deliberately self-contained and *additive*: it reuses the
// globals defined in app.js (map, state, api, esc, toast) and plugs into the
// existing render loop via window.renderKnownLayer / renderContextAlertHTML /
// bindContextAlert hooks. Nothing here competes with the emergency workflow —
// known events stay subtle on the map and only surface when an emergency lands
// near one.

(function () {
  "use strict";

  // --- label maps ---
  const TYPE_HE = {
    political: "פוליטי / הפגנה", cultural: "תרבות", private: "אירוע פרטי",
    religious: "דתי", sports: "ספורט", festival: "פסטיבל", other: "אחר",
  };
  const STATUS_HE = {
    scheduled: "מתוכנן", active: "פעיל כעת", ended: "הסתיים", cancelled: "בוטל",
  };
  const TIMEREL_HE = {
    active: "פעיל כעת", starting_soon: "מתחיל בקרוב",
    recently_ended: "הסתיים לאחרונה", scheduled: "מתוכנן",
  };
  const RELATION_HE = { inside: "בתוך האירוע הידוע", nearby: "בקרבת אירוע ידוע" };
  const ALERT_HE = { critical: "קריטי", important: "חשוב", info: "מידע" };
  const TYPE_EMOJI = {
    political: "📢", cultural: "🎭", private: "🎉", religious: "🕯️",
    sports: "🏟️", festival: "🎪", other: "📍",
  };

  // --- helpers ---
  const $ = (id) => document.getElementById(id);
  const eventById = (id) => (state.known_events || []).find((e) => e.id === id);

  function fmtRange(start, end) {
    const opt = { day: "2-digit", month: "2-digit", hour: "2-digit", minute: "2-digit" };
    const fmt = (s) => { try { return new Date(s).toLocaleString("he-IL", opt); } catch { return s || "—"; } };
    if (!start && !end) return "—";
    if (start && end) return `${fmt(start)} – ${fmt(end)}`;
    return fmt(start || end);
  }
  function fmtDay(s) {
    try { return new Date(s).toLocaleDateString("he-IL", { weekday: "long", day: "2-digit", month: "2-digit", year: "numeric" }); }
    catch { return s || "—"; }
  }
  function num(n) { return (n || 0).toLocaleString("he-IL"); }

  // Is the event's time window overlapping *today* (local date)? Used to keep the
  // shared situational map focused on what's happening now, not future plans.
  function happeningToday(e) {
    const now = new Date();
    const dayStart = new Date(now.getFullYear(), now.getMonth(), now.getDate());
    const dayEnd = new Date(dayStart.getTime() + 24 * 3600 * 1000);
    const s = e.start_time ? new Date(e.start_time) : null;
    const en = e.end_time ? new Date(e.end_time) : null;
    if (s && en) return s < dayEnd && en >= dayStart;   // window overlaps today
    if (s) return s >= dayStart && s < dayEnd;           // single-point: starts today
    return false;                                        // no time → not "today"
  }

  // Which known events currently have an active emergency context alert, and at
  // what (strongest) level — so the map can tint just those, calmly.
  function alertingEvents() {
    const out = {};
    (state.incidents || []).forEach((inc) => (inc.event_context || []).forEach((m) => {
      const rank = { info: 0, important: 1, critical: 2 };
      if (!(m.known_event_id in out) || rank[m.alert_level] > rank[out[m.known_event_id]])
        out[m.known_event_id] = m.alert_level;
    }));
    return out;
  }

  // ============================================================ MAP LAYER ===
  // A separate layer of clearly-visible PINS (emoji per event type) so known
  // events are noticeable on the map. A faint translucent circle behind each pin
  // still conveys the event area (radius_meters). Calm slate by default; the pin
  // and circle turn amber only when an active emergency relates to that event.
  let knownLayer = null;
  let lastKnownSig = null; // skip redraw when nothing changed (prevents popup closing)
  function ensureLayer() {
    if (!knownLayer && typeof map !== "undefined" && map) {
      knownLayer = L.layerGroup().addTo(map);
    }
    return knownLayer;
  }

  function pinIcon(emoji, lvl) {
    const cls = "ke-pin" + (lvl ? " ke-pin-alert ke-pin-" + lvl : "");
    return L.divIcon({
      className: "ke-pin-wrap",
      html: `<div class="${cls}"><span class="ke-pin-emoji">${emoji}</span></div>`,
      iconSize: [30, 38], iconAnchor: [15, 38], popupAnchor: [0, -34],
    });
  }

  function knownLayerSignature() {
    const alerts = alertingEvents();
    const evts = (state.known_events || []).map((e) =>
      [e.id, e.status, e.type, e.name, (e.location || {}).lat, (e.location || {}).lng,
       (e.location || {}).radius_meters, e.start_time, e.end_time, alerts[e.id] || ""]);
    return JSON.stringify(evts);
  }

  function drawKnownOnLayer(layer, mapObj) {
    if (!layer || !mapObj) return;
    layer.clearLayers();
    const alerts = alertingEvents();
    (state.known_events || []).forEach((e) => {
      const loc = e.location || {};
      if (loc.lat == null || loc.lng == null) return;
      if (e.status === "cancelled") return;
      // Shared map shows only events happening today — UNLESS one is tied to an
      // active emergency context alert, which we never hide.
      if (!happeningToday(e) && !alerts[e.id]) return;
      const lvl = alerts[e.id];
      const calm = "#8ea2c0";                 // neutral slate — background intel
      const hot = lvl === "critical" ? "#f0883e" : "#d6a30b"; // amber only on alert
      const color = lvl ? hot : calm;
      const emoji = TYPE_EMOJI[e.type] || "📍";

      // Faint area ring behind the pin (only when the event has a real radius).
      const r = loc.radius_meters || 0;
      if (r >= 60) {
        L.circle([loc.lat, loc.lng], {
          radius: r, color, weight: lvl ? 1.6 : 1.2,
          opacity: lvl ? 0.85 : 0.55, dashArray: lvl ? null : "5 5",
          fillColor: color, fillOpacity: lvl ? 0.14 : 0.08, interactive: false,
        }).addTo(layer);
      }

      const popup =
        `<div style="min-width:180px">
           <b>${emoji} ${esc(e.name)}</b><br>
           <span style="color:#888">${esc(TYPE_HE[e.type] || e.type)} · ${esc(STATUS_HE[e.status] || e.status)}</span><br>
           ${num(e.expected_participants)} משתתפים צפויים<br>
           <span style="color:#888;font-size:11px">${esc(fmtRange(e.start_time, e.end_time))}</span><br>
           <button onclick="window.openKnownEventDetail('${e.id}')"
             style="margin-top:6px;cursor:pointer">פרטי האירוע</button>
         </div>`;
      L.marker([loc.lat, loc.lng], { icon: pinIcon(emoji, lvl), keEventId: e.id })
        .bindPopup(popup, { autoPan: false, closeOnClick: false }).addTo(layer);
    });
  }

  window.renderKnownLayer = function () {
    const layer = ensureLayer();
    if (!layer) return;
    // Only redraw when data actually changed — prevents destroying open popups
    const sig = knownLayerSignature();
    if (sig === lastKnownSig) return;
    lastKnownSig = sig;
    drawKnownOnLayer(layer, map);
  };

  // Expose for the meshager map to also show known events
  window.renderKnownLayerOnMap = function (mapObj) {
    if (!mapObj) return;
    if (!mapObj._keLayer) {
      mapObj._keLayer = L.layerGroup().addTo(mapObj);
    }
    drawKnownOnLayer(mapObj._keLayer, mapObj);
  };

  // ================================================= INCIDENT CONTEXT ALERT ===
  // Rendered inside the incident drawer (progressive disclosure). Calm card, not
  // an emergency-red banner; escalates wording for critical matches.
  window.renderContextAlertHTML = function (inc) {
    const ctx = inc.event_context || [];
    if (!ctx.length) return "";
    const cards = ctx.map((m) => {
      const cls = `ctx-${m.alert_level}`;
      const notes = [m.risk_notes, m.police_notes].filter(Boolean)
        .map((n) => `<div class="ctx-note">• ${esc(n)}</div>`).join("");
      return `<div class="ctx-card ${cls}">
        <div class="ctx-head">
          <span class="ctx-icon">${TYPE_EMOJI[m.type] || "📍"}</span>
          <span class="ctx-headtext">אירוע חירום ${esc(RELATION_HE[m.relation] || "")}: <b>${esc(m.name)}</b></span>
          <span class="ctx-level ctx-level-${m.alert_level}">${esc(ALERT_HE[m.alert_level] || "")}</span>
        </div>
        <div class="ctx-grid">
          <div><span class="ctx-k">סוג</span>${esc(TYPE_HE[m.type] || m.type)}</div>
          <div><span class="ctx-k">משתתפים צפויים</span>${num(m.expected_participants)}</div>
          <div><span class="ctx-k">מרחק</span>${num(m.distance_meters)} מ׳</div>
          <div><span class="ctx-k">חלון זמן</span>${esc(TIMEREL_HE[m.time_relation] || "")}</div>
          <div class="ctx-span"><span class="ctx-k">מתי</span>${esc(fmtRange(m.start_time, m.end_time))}</div>
        </div>
        ${notes ? `<div class="ctx-notes">${notes}</div>` : ""}
        <div class="ctx-suggest">💡 ${esc(m.suggestion)}</div>
        <div class="ctx-actions">
          <button class="ctx-btn" data-ke-detail="${m.known_event_id}">פרטי האירוע הידוע</button>
          <button class="ctx-btn ghost" data-ke-jump="${m.known_event_id}">הצג במפה</button>
        </div>
      </div>`;
    }).join("");
    return `<div class="section">
      <div class="section-title">הקשר מודיעיני · אירוע ידוע בקרבת מקום</div>
      ${cards}
    </div>`;
  };

  window.bindContextAlert = function (root) {
    root.querySelectorAll("[data-ke-detail]").forEach((b) =>
      (b.onclick = () => window.openKnownEventDetail(b.dataset.keDetail)));
    root.querySelectorAll("[data-ke-jump]").forEach((b) =>
      (b.onclick = () => jumpToEvent(b.dataset.keJump)));
  };

  function jumpToEvent(id) {
    const e = eventById(id);
    if (!e || !e.location || e.location.lat == null) { toast("אין מיקום לאירוע"); return; }
    closeModal();
    map.setView([e.location.lat, e.location.lng], 14, { animate: true });
    // Re-open the matching pin's popup after the layer redraws.
    setTimeout(() => {
      (knownLayer.getLayers() || []).forEach((l) => {
        if (l.options && l.options.keEventId === id) l.openPopup();
      });
    }, 250);
  }

  // ============================================================= MODALS =====
  let keView = null; // 'calendar' | 'detail' | 'form' | 'import'

  let filterMap = null; // a separate Leaflet map living inside the "show on map" modal
  function destroyFilterMap() {
    if (filterMap) { filterMap.remove(); filterMap = null; }
  }
  function openModal(html, wide) {
    destroyFilterMap(); // tear down any previous in-modal map before replacing content
    const modal = $("ke-modal");
    modal.className = "modal" + (wide ? " wide" : "");
    modal.innerHTML = html;
    $("ke-scrim").classList.remove("hidden");
    modal.classList.remove("hidden");
    modal.setAttribute("aria-hidden", "false");
    modal.querySelectorAll("[data-ke-close]").forEach((b) => (b.onclick = closeModal));
  }
  function closeModal() {
    keView = null;
    destroyFilterMap();
    $("ke-modal").classList.add("hidden");
    $("ke-scrim").classList.add("hidden");
  }
  $("ke-scrim").onclick = closeModal;
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && keView) closeModal(); });

  // ----------------------------------------------------------- CALENDAR -----
  const filters = { area: "", type: "", status: "", from: "", to: "", min: "", max: "", q: "" };

  function applyFilters(events) {
    const q = filters.q.trim().toLowerCase();
    const area = filters.area.trim().toLowerCase();
    return events.filter((e) => {
      const loc = e.location || {};
      const locText = `${loc.normalized_address || ""} ${loc.raw_address || ""}`.toLowerCase();
      if (area && !locText.includes(area)) return false;
      if (filters.type && e.type !== filters.type) return false;
      if (filters.status && e.status !== filters.status) return false;
      if (filters.min !== "" && (e.expected_participants || 0) < +filters.min) return false;
      if (filters.max !== "" && (e.expected_participants || 0) > +filters.max) return false;
      if (filters.from && e.start_time && new Date(e.start_time) < new Date(filters.from)) return false;
      if (filters.to && e.start_time && new Date(e.start_time) > new Date(filters.to + "T23:59")) return false;
      if (q) {
        const hay = `${e.name} ${locText} ${e.organizer} ${e.police_notes} ${e.risk_notes} ${e.description}`.toLowerCase();
        if (!hay.includes(q)) return false;
      }
      return true;
    });
  }

  window.openCalendar = function () {
    keView = "calendar";
    const types = Object.entries(TYPE_HE).map(([k, v]) =>
      `<option value="${k}" ${filters.type === k ? "selected" : ""}>${v}</option>`).join("");
    const statuses = Object.entries(STATUS_HE).map(([k, v]) =>
      `<option value="${k}" ${filters.status === k ? "selected" : ""}>${v}</option>`).join("");

    openModal(`
      <div class="modal-head">
        <div class="modal-title">📅 לוח אירועים ידועים</div>
        <div class="modal-head-actions">
          <button class="ke-action ghost" id="ke-showmap">🗺️ הצג על מפה</button>
          <button class="ke-action" id="ke-new">＋ אירוע חדש</button>
          <button class="ke-action ghost" id="ke-import">⬆ ייבוא Excel/CSV</button>
          <button class="dr-close" data-ke-close>✕</button>
        </div>
      </div>
      <div class="ke-filters">
        <input id="f-q" class="ke-in" placeholder="חיפוש חופשי (שם, מארגן, הערות)" value="${esc(filters.q)}" />
        <input id="f-area" class="ke-in" placeholder="אזור / כתובת" value="${esc(filters.area)}" />
        <select id="f-type" class="ke-in"><option value="">כל הסוגים</option>${types}</select>
        <select id="f-status" class="ke-in"><option value="">כל הסטטוסים</option>${statuses}</select>
        <label class="ke-lbl">מ־<input id="f-from" type="date" class="ke-in sm" value="${filters.from}" /></label>
        <label class="ke-lbl">עד<input id="f-to" type="date" class="ke-in sm" value="${filters.to}" /></label>
        <input id="f-min" type="number" class="ke-in sm" placeholder="מינ׳ משתתפים" value="${filters.min}" />
        <input id="f-max" type="number" class="ke-in sm" placeholder="מקס׳ משתתפים" value="${filters.max}" />
        <button class="ke-action ghost sm" id="f-clear">נקה</button>
      </div>
      <div class="ke-list" id="ke-list"></div>
    `, true);

    const bind = (id, key) => {
      const el = $(id);
      el.oninput = el.onchange = () => { filters[key] = el.value; renderList(); };
    };
    bind("f-q", "q"); bind("f-area", "area"); bind("f-type", "type"); bind("f-status", "status");
    bind("f-from", "from"); bind("f-to", "to"); bind("f-min", "min"); bind("f-max", "max");
    $("f-clear").onclick = () => { Object.keys(filters).forEach((k) => (filters[k] = "")); window.openCalendar(); };
    $("ke-new").onclick = openForm;
    $("ke-import").onclick = openImport;
    $("ke-showmap").onclick = openFilteredMap;
    renderList();
  };

  function renderList() {
    const wrap = $("ke-list");
    if (!wrap) return;
    let events = applyFilters(state.known_events || []);
    events = events.slice().sort((a, b) => String(a.start_time).localeCompare(String(b.start_time)));
    if (!events.length) {
      wrap.innerHTML = `<div class="empty" style="grid-column:auto">לא נמצאו אירועים ידועים התואמים את הסינון.</div>`;
      return;
    }
    // Group by calendar day (list view is enough for MVP).
    const groups = {};
    events.forEach((e) => { const k = (e.start_time || "").slice(0, 10) || "ללא תאריך"; (groups[k] = groups[k] || []).push(e); });
    wrap.innerHTML = Object.keys(groups).sort().map((day) => {
      const header = day === "ללא תאריך" ? day : fmtDay(day + "T00:00");
      const rows = groups[day].map((e) => {
        const loc = e.location || {};
        return `<div class="ke-row" data-ke-open="${e.id}">
          <span class="ke-emoji">${TYPE_EMOJI[e.type] || "📍"}</span>
          <div class="ke-row-main">
            <div class="ke-row-name">${esc(e.name)}</div>
            <div class="ke-row-sub">${esc(TYPE_HE[e.type] || e.type)} · ${esc(loc.normalized_address || loc.raw_address || "מיקום לא ידוע")} · ${num(e.expected_participants)} משתתפים</div>
          </div>
          <span class="ke-time">${esc(fmtRange(e.start_time, e.end_time))}</span>
          <span class="ke-status ke-status-${e.status}">${esc(STATUS_HE[e.status] || e.status)}</span>
        </div>`;
      }).join("");
      return `<div class="ke-day"><div class="ke-day-head">${esc(header)}</div>${rows}</div>`;
    }).join("");
    wrap.querySelectorAll("[data-ke-open]").forEach((r) =>
      (r.onclick = () => window.openKnownEventDetail(r.dataset.keOpen)));
  }

  // -------------------------------------------------- FILTERED MAP VIEW -----
  // Plot exactly the events matching the current filters on a dedicated map.
  function openFilteredMap() {
    keView = "filtermap";
    const events = applyFilters(state.known_events || [])
      .filter((e) => (e.location || {}).lat != null && e.location.lng != null);
    openModal(`
      <div class="modal-head">
        <div class="modal-title">🗺️ אירועים ידועים על המפה</div>
        <div class="modal-head-actions">
          <span class="muted" style="font-size:12px">${events.length} אירועים תואמי סינון</span>
          <button class="ke-action ghost" id="ke-back-cal">↩ חזרה לרשימה</button>
          <button class="dr-close" data-ke-close>✕</button>
        </div>
      </div>
      <div id="ke-map" class="ke-map"></div>
      ${events.length ? "" : `<div class="muted" style="padding:14px 20px">אין לאירועים התואמים מיקום להצגה.</div>`}
    `, true);
    $("ke-back-cal").onclick = window.openCalendar;

    if (!events.length) return;
    // Build the map fresh (the modal owns its own Leaflet instance).
    filterMap = L.map("ke-map", { zoomControl: true, attributionControl: false })
      .setView([31.7, 35.0], 7);
    L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", { maxZoom: 18 }).addTo(filterMap);
    const pts = [];
    events.forEach((e) => {
      const loc = e.location, emoji = TYPE_EMOJI[e.type] || "📍";
      const r = loc.radius_meters || 0;
      if (r >= 60) {
        L.circle([loc.lat, loc.lng], {
          radius: r, color: "#8ea2c0", weight: 1.2, opacity: 0.6,
          dashArray: "5 5", fillColor: "#8ea2c0", fillOpacity: 0.08, interactive: false,
        }).addTo(filterMap);
      }
      L.marker([loc.lat, loc.lng], { icon: pinIcon(emoji, null) })
        .bindPopup(
          `<div style="min-width:170px">
             <b>${emoji} ${esc(e.name)}</b><br>
             <span style="color:#888">${esc(TYPE_HE[e.type] || e.type)} · ${esc(STATUS_HE[e.status] || e.status)}</span><br>
             ${num(e.expected_participants)} משתתפים · <span style="color:#888;font-size:11px">${esc(fmtRange(e.start_time, e.end_time))}</span><br>
             <button onclick="window.openKnownEventDetail('${e.id}')" style="margin-top:6px;cursor:pointer">פרטי האירוע</button>
           </div>`)
        .addTo(filterMap);
      pts.push([loc.lat, loc.lng]);
    });
    if (pts.length) filterMap.fitBounds(pts, { padding: [40, 40], maxZoom: 14 });
    // The container only gets its real size once the modal is visible.
    setTimeout(() => filterMap && filterMap.invalidateSize(), 60);
  }

  // ------------------------------------------------------------- DETAIL -----
  window.openKnownEventDetail = function (id) {
    const e = eventById(id);
    if (!e) { toast("האירוע לא נמצא"); return; }
    keView = "detail";
    const loc = e.location || {};
    const row = (k, v) => v ? `<div class="kd-row"><span class="kd-k">${k}</span><span class="kd-v">${esc(v)}</span></div>` : "";
    openModal(`
      <div class="modal-head">
        <div class="modal-title">${TYPE_EMOJI[e.type] || "📍"} ${esc(e.name)}</div>
        <button class="dr-close" data-ke-close>✕</button>
      </div>
      <div class="kd-body">
        <div class="kd-badges">
          <span class="kd-badge">${esc(TYPE_HE[e.type] || e.type)}</span>
          <span class="kd-badge st-${e.status}">${esc(STATUS_HE[e.status] || e.status)}</span>
          <span class="kd-badge src">${e.source === "excel_import" ? "יובא מקובץ" : "הוזן ידנית"}</span>
        </div>
        ${row("משתתפים צפויים", num(e.expected_participants))}
        ${row("חלון זמן", fmtRange(e.start_time, e.end_time))}
        ${row("מיקום", loc.normalized_address || loc.raw_address)}
        ${row("רדיוס אזור", loc.radius_meters ? loc.radius_meters + " מ׳" : "")}
        ${row("מארגן", e.organizer)}
        ${row("תיאור", e.description)}
        ${row("הערות משטרה", e.police_notes)}
        ${row("הערות סיכון", e.risk_notes)}
      </div>
      <div class="modal-foot">
        ${loc.lat != null ? `<button class="ke-action" id="kd-jump">📍 הצג במפה</button>` : ""}
        <button class="ke-action ghost" data-ke-close>סגור</button>
      </div>
    `);
    if ($("kd-jump")) $("kd-jump").onclick = () => jumpToEvent(id);
  };

  // --------------------------------------------------------------- FORM -----
  function openForm() {
    keView = "form";
    const types = Object.entries(TYPE_HE).map(([k, v]) => `<option value="${k}">${v}</option>`).join("");
    openModal(`
      <div class="modal-head">
        <div class="modal-title">＋ אירוע ידוע חדש</div>
        <button class="dr-close" data-ke-close>✕</button>
      </div>
      <form id="ke-form" class="ke-form">
        <label class="ke-field"><span>שם האירוע *</span><input name="name" required /></label>
        <label class="ke-field"><span>סוג</span><select name="type">${types}</select></label>
        <label class="ke-field"><span>מספר משתתפים צפוי</span><input name="expected_participants" type="number" min="0" value="0" /></label>
        <label class="ke-field"><span>תחילה</span><input name="start_time" type="datetime-local" /></label>
        <label class="ke-field"><span>סיום</span><input name="end_time" type="datetime-local" /></label>
        <label class="ke-field wide"><span>כתובת / מיקום</span><input name="address" placeholder="לדוגמה: כיכר רבין, תל אביב" /></label>
        <label class="ke-field"><span>עיר</span><input name="city" /></label>
        <label class="ke-field"><span>רדיוס (מטרים, אופציונלי)</span><input name="radius_meters" type="number" min="0" /></label>
        <label class="ke-field"><span>קו רוחב (lat, אופציונלי)</span><input name="lat" type="number" step="any" /></label>
        <label class="ke-field"><span>קו אורך (lng, אופציונלי)</span><input name="lng" type="number" step="any" /></label>
        <label class="ke-field wide"><span>מארגן (אופציונלי)</span><input name="organizer" /></label>
        <label class="ke-field wide"><span>הערות משטרה (אופציונלי)</span><textarea name="police_notes" rows="2"></textarea></label>
        <label class="ke-field wide"><span>הערות סיכון (אופציונלי)</span><textarea name="risk_notes" rows="2"></textarea></label>
        <div class="ke-hint wide">אם לא הוזנו lat/lng, המערכת תנסה לאתר את הקואורדינטות מהכתובת/העיר (גיאוקודינג מדומה).</div>
      </form>
      <div class="modal-foot">
        <button class="ke-action" id="ke-save">שמירה</button>
        <button class="ke-action ghost" data-ke-close>ביטול</button>
      </div>
    `);
    $("ke-save").onclick = saveForm;
  }

  async function saveForm() {
    const f = $("ke-form");
    if (!f.name.value.trim()) { toast("יש להזין שם אירוע"); return; }
    const v = (k) => f[k] ? f[k].value : "";
    const numOrNull = (k) => f[k].value === "" ? null : +f[k].value;
    const body = {
      name: v("name"), type: v("type"),
      expected_participants: +f.expected_participants.value || 0,
      start_time: v("start_time"), end_time: v("end_time"),
      address: v("address"), city: v("city"),
      lat: numOrNull("lat"), lng: numOrNull("lng"),
      radius_meters: +f.radius_meters.value || 0,
      organizer: v("organizer"), police_notes: v("police_notes"), risk_notes: v("risk_notes"),
    };
    const res = await api("/api/known-events", "POST", body);
    if (res && res.ok) {
      state.known_events.push(res.event); // optimistic; poll will reconcile
      toast("האירוע הידוע נשמר");
      window.openCalendar();
    } else {
      toast("שמירה נכשלה");
    }
  }

  // ------------------------------------------------------------- IMPORT -----
  let importPayloads = [];
  function openImport() {
    keView = "import";
    openModal(`
      <div class="modal-head">
        <div class="modal-title">⬆ ייבוא אירועים ידועים (Excel / CSV)</div>
        <button class="dr-close" data-ke-close>✕</button>
      </div>
      <div class="ke-import-body">
        <div class="ke-hint">העלי קובץ .xlsx או .csv. עמודות נתמכות: event_name, event_type, expected_participants,
          start_time, end_time, address, city, lat, lng, radius_meters, organizer, description, police_notes, risk_notes.</div>
        <input id="ke-file" type="file" accept=".csv,.xlsx" class="ke-in" />
        <div id="ke-preview"></div>
      </div>
    `, true);
    $("ke-file").onchange = handleImportFile;
  }

  function fileToBase64(file) {
    return new Promise((resolve, reject) => {
      const r = new FileReader();
      r.onerror = reject;
      r.onload = () => {
        const res = r.result || "";
        resolve(String(res).split(",").pop()); // strip data: prefix
      };
      r.readAsDataURL(file);
    });
  }

  async function handleImportFile(e) {
    const file = e.target.files[0];
    if (!file) return;
    $("ke-preview").innerHTML = `<div class="muted" style="padding:12px">מנתח את הקובץ…</div>`;
    let content_b64;
    try { content_b64 = await fileToBase64(file); }
    catch { $("ke-preview").innerHTML = `<div class="ctx-card ctx-critical">קריאת הקובץ נכשלה.</div>`; return; }
    const res = await api("/api/known-events/import/preview", "POST", { filename: file.name, content_b64 });
    if (!res || res.valid === undefined) {
      $("ke-preview").innerHTML = `<div class="ctx-card ctx-critical">ניתוח הקובץ נכשל. ודאי שזהו קובץ CSV/XLSX תקין.</div>`;
      return;
    }
    importPayloads = res.valid.map((v) => v.payload);
    renderImportPreview(res);
  }

  function renderImportPreview(res) {
    const validRows = res.valid.map((v) => {
      const ev = v.event, loc = ev.location || {};
      return `<tr>
        <td>${v.row}</td><td>${esc(ev.name)}</td><td>${esc(TYPE_HE[ev.type] || ev.type)}</td>
        <td>${num(ev.expected_participants)}</td>
        <td>${esc(loc.normalized_address || loc.raw_address || "")}</td>
        <td>${loc.lat != null ? loc.lat.toFixed(4) + ", " + loc.lng.toFixed(4) : "—"}</td>
      </tr>`;
    }).join("");
    const invalidRows = res.invalid.map((iv) =>
      `<tr class="bad"><td>${iv.row}</td><td colspan="5">${esc((iv.errors || []).join(" · "))}</td></tr>`).join("");

    $("ke-preview").innerHTML = `
      <div class="ke-import-summary">
        סה״כ ${res.total} שורות · <span class="ok">${res.valid_count} תקינות</span>
        ${res.invalid_count ? ` · <span class="bad">${res.invalid_count} שגויות</span>` : ""}
      </div>
      ${res.valid_count ? `<div class="ke-import-section">תצוגה מקדימה (תקינות)</div>
      <table class="ke-table"><tr><th>#</th><th>שם</th><th>סוג</th><th>משתתפים</th><th>מיקום</th><th>קואורדינטות</th></tr>${validRows}</table>` : ""}
      ${res.invalid_count ? `<div class="ke-import-section bad">שורות שגויות (לא ייובאו)</div>
      <table class="ke-table"><tr><th>#</th><th>שגיאה</th></tr>${invalidRows}</table>` : ""}
      <div class="modal-foot">
        <button class="ke-action" id="ke-confirm" ${res.valid_count ? "" : "disabled"}>אשרי ייבוא ${res.valid_count} אירועים</button>
        <button class="ke-action ghost" data-ke-close>ביטול</button>
      </div>`;
    $("ke-modal").querySelectorAll("[data-ke-close]").forEach((b) => (b.onclick = closeModal));
    if ($("ke-confirm") && res.valid_count) $("ke-confirm").onclick = confirmImport;
  }

  async function confirmImport() {
    const res = await api("/api/known-events/import/confirm", "POST", { payloads: importPayloads });
    if (res && res.ok) {
      (res.events || []).forEach((e) => state.known_events.push(e));
      toast(`יובאו ${res.imported} אירועים ידועים`);
      window.openCalendar();
    } else {
      toast("הייבוא נכשל");
    }
  }

  // --- wire the topbar button ---
  const btn = $("btn-calendar");
  if (btn) btn.onclick = window.openCalendar;
})();
