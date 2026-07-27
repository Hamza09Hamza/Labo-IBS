// Labo Bridge Admin — vanilla JS, no build step, single-operator local tool.

// Toggles body.modal-open whenever ANY .modal-scrim gains/loses its "open"
// class - pauses the decorative background blob animations while a modal is
// up (see style.css), since a moving blurred blob under a blurred modal
// scrim is expensive to recomposite every frame and was the real cause of
// laggy scrolling inside modals. A MutationObserver means every existing
// (and future) modal's open/close code needs zero changes - it just works
// off whatever class state is already there.
(function watchModalState() {
  const sync = () => {
    const anyOpen = !!document.querySelector(".modal-scrim.open");
    document.body.classList.toggle("modal-open", anyOpen);
  };
  document.querySelectorAll(".modal-scrim").forEach((scrim) => {
    new MutationObserver(sync).observe(scrim, { attributes: true, attributeFilter: ["class"] });
  });
  sync();
})();

const state = {
  machines: [],
  activeSection: "machines", // "machines" | "mappings" | "api-settings"
  mappingsMachine: null,     // machine whose Mapped/Pending/Samples tabs are shown
  mappings: [],
  pending: [],
  samples: [],
  samplesTotal: 0,
  samplesPage: 1,
  samplesDateFrom: "",
  samplesDateTo: "",
  editingCode: null, // null => "add new", string => "editing existing"
};
const SAMPLES_PAGE_SIZE = 25;

// ---------------------------------------------------------------------------
// Fetch helpers
// ---------------------------------------------------------------------------

async function apiGet(url) {
  const res = await fetch(url);
  if (!res.ok) throw new Error(`GET ${url} -> ${res.status}`);
  return res.json();
}

async function apiPut(url, body) {
  const res = await fetch(url, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `PUT ${url} -> ${res.status}`);
  return data;
}

async function apiDelete(url) {
  const res = await fetch(url, { method: "DELETE" });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `DELETE ${url} -> ${res.status}`);
  return data;
}

async function apiPutForm(url, formData) {
  const res = await fetch(url, { method: "PUT", body: formData });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `PUT ${url} -> ${res.status}`);
  return data;
}

// Fill a table body with shimmering placeholder rows while its real data is
// loading - only meant for a genuine first load (see call site), never
// during a background poll refresh, or it would flicker every few seconds.
function skeletonizeTable(tbodyId, cols, rows = 5) {
  const tbody = document.getElementById(tbodyId);
  if (!tbody) return;
  tbody.innerHTML = Array.from({ length: rows }, () =>
    `<tr class="skel-table-row">${
      Array.from({ length: cols }, () => `<td><div class="skel"></div></td>`).join("")
    }</tr>`
  ).join("");
}

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

let toastTimer = null;
// accentColor is optional (e.g. a machine's own --m-color) - when given,
// the toast's dot uses that color instead of the generic kind-based color,
// so a "new sample" toast reads as "which machine" at a glance without
// having to read the text first. Every other toast call site (mapping
// saved, API settings, etc.) doesn't pass one and looks exactly as before.
function toast(message, kind = "success", accentColor = null) {
  const el = document.getElementById("toast");
  el.textContent = "";
  const dot = document.createElement("span");
  dot.className = "toast-dot";
  if (accentColor) dot.style.background = accentColor;
  el.appendChild(dot);
  const text = document.createElement("span");
  text.textContent = message;
  el.appendChild(text);
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.classList.remove("show"); }, 3600);
}

async function pingMachine(machine, label) {
  toast(`Pinging ${label}...`, "success");
  try {
    const response = await fetch(`/api/machines/${machine}/ping`);
    const res = await response.json();
    if (!res.ok) {
      toast(res.error || `Couldn't ping ${label}`, "error");
      return;
    }
    const caveat = res.is_configured ? "" : " — shared/last-seen IP, not machine-specific";
    toast(
      res.reachable ? `${label} is reachable (${res.ip})${caveat}` : `${label} did NOT respond (${res.ip})${caveat}`,
      res.reachable ? "success" : "error"
    );
  } catch (e) {
    toast(`Ping failed: ${e.message}`, "error");
  }
}

async function pingAllMachines() {
  toast("Pinging all machines...", "success");
  try {
    const results = await apiGet("/api/machines/ping-all");
    const byMachine = Object.fromEntries(state.machines.map((m) => [m.machine, m.label || m.machine]));
    const lines = results.map((r) => {
      const label = byMachine[r.machine] || r.machine;
      if (!r.ip) return `${label}: no known IP yet`;
      const caveat = r.is_configured ? "" : " (shared/last-seen IP, not machine-specific)";
      return `${label}: ${r.reachable ? "reachable" : "NOT responding"} — ${r.ip}${caveat}`;
    });
    showPingAllResults(lines);
  } catch (e) {
    toast(`Ping All failed: ${e.message}`, "error");
  }
}

function showPingAllResults(lines) {
  const existing = document.getElementById("pingAllResults");
  if (existing) existing.remove();
  const box = document.createElement("div");
  box.id = "pingAllResults";
  box.className = "ping-all-results";
  box.innerHTML = `
    <div class="ping-all-results-header">
      <span>Ping All results</span>
      <button type="button" class="icon-btn ping-all-close" title="Close">×</button>
    </div>
    <ul>${lines.map((l) => `<li>${escapeHtml(l)}</li>`).join("")}</ul>
  `;
  document.body.appendChild(box);
  box.querySelector(".ping-all-close").addEventListener("click", () => box.remove());
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

function timeAgo(iso) {
  if (!iso) return "never";
  const then = new Date(iso.replace(" ", "T"));
  const diffMs = Date.now() - then.getTime();
  const mins = Math.floor(diffMs / 60000);
  if (mins < 1) return "just now";
  return fullTimestamp(iso);
}

// Exact date/time - "18h ago" alone gets ambiguous fast once you're
// checking back later or across a day boundary, so timeAgo() shows this
// for everything past "just now" instead of a relative m/h/d count.
// Fixed dd/mm/yyyy HH:mm format on purpose (not toLocaleString) - a
// locale-dependent format could silently swap day/month depending on the
// browser/OS, which is exactly the kind of ambiguity this replaced.
function fullTimestamp(iso) {
  if (!iso) return "never recorded";
  const then = new Date(iso.replace(" ", "T"));
  const pad = (n) => String(n).padStart(2, "0");
  const dd = pad(then.getDate());
  const mm = pad(then.getMonth() + 1);
  const yyyy = then.getFullYear();
  const hh = pad(then.getHours());
  const min = pad(then.getMinutes());
  return `${dd}/${mm}/${yyyy} ${hh}:${min}`;
}

function initials(label) {
  const cleaned = label.replace(/[^A-Za-z0-9 ]/g, "");
  const parts = cleaned.split(" ").filter(Boolean);
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + (parts[1] ? parts[1][0] : "")).toUpperCase();
}

function avatarHtml(m, sizeClass) {
  if (m.photo) {
    return `<img class="${sizeClass}-photo" src="/${m.photo}" alt="${escapeHtml(m.label)}" loading="lazy">`;
  }
  return escapeHtml(initials(m.label));
}

function isRecent(iso, minutes = 10) {
  if (!iso) return false;
  const then = new Date(iso.replace(" ", "T"));
  return (Date.now() - then.getTime()) < minutes * 60000;
}

function escapeHtml(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ---------------------------------------------------------------------------
// Status pills
// ---------------------------------------------------------------------------

// Tracks the last known state of each status pill so loadStatus can pulse
// ONLY when something actually flips (DB drops/recovers, API mode toggled),
// never on every 15s poll tick regardless of change.
let prevStatus = { postgres_ok: null, use_machine_result_api: null };

function _applyPillPulse(el, changedFlag) {
  if (!changedFlag) return;
  el.classList.remove("pill-pulse"); // restart the animation if it's re-triggered quickly
  void el.offsetWidth; // force reflow so removing+re-adding the class actually replays it
  el.classList.add("pill-pulse");
}

async function loadStatus() {
  try {
    const s = await apiGet("/api/status");
    const pgPill = document.getElementById("pgStatusPill");
    pgPill.className = `status-pill ${s.postgres_ok ? "ok" : "err"}`;
    pgPill.querySelector(".status-text").textContent =
      s.postgres_ok ? "Clinic DB connected" : "Clinic DB unreachable";
    _applyPillPulse(pgPill, prevStatus.postgres_ok !== null && prevStatus.postgres_ok !== s.postgres_ok);

    const apiPill = document.getElementById("apiStatusPill");
    apiPill.className = `status-pill ${s.use_machine_result_api ? "warn" : "ok"}`;
    apiPill.querySelector(".status-text").textContent =
      s.use_machine_result_api ? "API mode: live push" : "API mode: staging only";
    _applyPillPulse(apiPill, prevStatus.use_machine_result_api !== null
      && prevStatus.use_machine_result_api !== s.use_machine_result_api);

    prevStatus = { postgres_ok: s.postgres_ok, use_machine_result_api: s.use_machine_result_api };
  } catch (e) {
    console.error(e);
  }
}

// ---------------------------------------------------------------------------
// Section navigation (Machines / Mappings / API Settings)
// ---------------------------------------------------------------------------

// Mobile nav drawer (hamburger toggle) - the vertical Machines/Mappings/
// API Settings nav lives off-canvas below 900px instead of squeezed into
// the top bar (see style.css's mobile block for why the inline-row
// attempt didn't work). closeNavDrawer is also called from showSection
// so picking a section always closes the drawer behind it.
function openNavDrawer() {
  document.querySelector(".app").classList.add("nav-open");
  document.getElementById("drawerScrim").classList.add("open");
  document.getElementById("navToggle").setAttribute("aria-expanded", "true");
}
function closeNavDrawer() {
  document.querySelector(".app").classList.remove("nav-open");
  document.getElementById("drawerScrim").classList.remove("open");
  document.getElementById("navToggle").setAttribute("aria-expanded", "false");
}
document.getElementById("navToggle").addEventListener("click", () => {
  const isOpen = document.querySelector(".app").classList.contains("nav-open");
  if (isOpen) closeNavDrawer(); else openNavDrawer();
});
document.getElementById("drawerScrim").addEventListener("click", closeNavDrawer);
document.getElementById("drawerClose").addEventListener("click", closeNavDrawer);

function showSection(section) {
  state.activeSection = section;
  closeNavDrawer();
  document.querySelectorAll(".section-nav-item").forEach((el) => {
    el.classList.toggle("active", el.dataset.section === section);
  });

  document.getElementById("view-overview").classList.remove("active");
  document.getElementById("view-mappings").classList.remove("active");
  document.getElementById("view-api-settings").classList.remove("active");

  if (section === "machines") {
    document.getElementById("view-overview").classList.add("active");
    renderOverview();
  } else if (section === "mappings") {
    document.getElementById("view-mappings").classList.add("active");
    renderMappingsMachinePicker();
    if (!state.mappingsMachine && state.machines.length) {
      selectMappingsMachine(state.machines[0].machine);
    } else if (state.mappingsMachine) {
      selectMappingsMachine(state.mappingsMachine);
    }
  } else if (section === "api-settings") {
    document.getElementById("view-api-settings").classList.add("active");
    loadApiSettings();
  }
}

document.querySelectorAll(".section-nav-item").forEach((el) => {
  el.addEventListener("click", () => showSection(el.dataset.section));
});

// ---------------------------------------------------------------------------
// Machines: overview grid (the only place machines are browsed - no sidebar
// machine list; clicking a card jumps straight into Mappings for it)
// ---------------------------------------------------------------------------

// machine -> {mapped_codes, pending_count, sample_count} from the PREVIOUS
// poll, so renderOverview can flash exactly the numbers that actually
// changed since last time (not the whole card) - a live-updating dashboard
// should visibly say "this changed", not just silently swap the digits.
let prevMachineStats = {};
// True once the very first /api/machines response has landed - guards the
// new-sample toast below so it only ever fires for a REAL arrival after
// that point, never on page load (when every machine's count is "new"
// compared to the empty {} prevMachineStats starts as).
let machinesLoadedOnce = false;

async function loadMachines() {
  const fresh = await apiGet("/api/machines");
  const prevForToast = prevMachineStats;
  prevMachineStats = Object.fromEntries(state.machines.map((m) =>
    [m.machine, { mapped_codes: m.mapped_codes, pending_count: m.pending_count, sample_count: m.sample_count }]));

  // New-sample toast - fires from wherever you're looking (Machines page,
  // Mappings for a different machine, anywhere), not just while already
  // viewing the specific machine's Samples tab, since that's exactly the
  // case where you'd otherwise have no idea something just arrived.
  // #toast is a single element (not a stack) - calling toast() more than
  // once per tick would silently overwrite the earlier call before its
  // 3.6s timer even shows it, so multiple arrivals in the same poll (e.g.
  // two machines both finishing a run around the same moment) are combined
  // into ONE message instead of only ever showing the last one.
  if (machinesLoadedOnce) {
    const arrivals = [];
    fresh.forEach((m) => {
      const prev = prevForToast[m.machine];
      if (prev && m.sample_count > prev.sample_count) {
        arrivals.push({ label: m.label || m.machine, color: m.color, delta: m.sample_count - prev.sample_count });
      }
    });
    // Cap how many machine names get spelled out in one toast - past 3, a
    // full comma-joined list (e.g. all 6 machines finishing runs around the
    // same moment) stops being readable in a single toast line, so fall
    // back to a plain count instead of an ever-growing name list. The
    // per-machine accent color on the toast dot only makes sense for the
    // single-machine case - a multi-machine toast has no one color to show.
    if (arrivals.length === 1) {
      const a = arrivals[0];
      toast(a.delta > 1 ? `${a.delta} new samples from ${a.label}` : `New sample from ${a.label}`, "success", a.color);
    } else if (arrivals.length > 1 && arrivals.length <= 3) {
      toast(`New samples from ${arrivals.map((a) => a.label).join(", ")}`, "success");
    } else if (arrivals.length > 3) {
      toast(`New samples from ${arrivals.length} machines`, "success");
    }
  }
  machinesLoadedOnce = true;

  state.machines = fresh;
  if (state.activeSection === "machines") {
    renderOverview();
  }
  if (state.activeSection === "mappings") {
    renderMappingsMachinePicker();
  }
}

// Returns " flash-update" if this field genuinely changed since the last
// poll (never on first render, when prev is undefined - a brand new card
// shouldn't flash, only a value that's actually different from before).
function changed(prev, key, next) {
  return (prev && prev[key] !== next) ? " flash-update" : "";
}

// Look up the current record for a machine by its key. Card click handlers
// are attached once (see renderOverview) and would otherwise close over the
// `m` from the render that created the card - stale label/color/port by the
// time someone actually clicks. This always reads today's data instead.
function machineByKey(key) {
  return state.machines.find((m) => m.machine === key) || null;
}

function renderOverview() {
  const totalSamples = state.machines.reduce((a, m) => a + m.sample_count, 0);
  // "Matched" = count of curated mappings (mapped_codes), same number shown on
  // the Mappings screen - NOT the raw count of result rows. "Pending" = codes
  // seen with no mapping yet (pending_params), same as the Mappings Pending tab.
  const totalMapped = state.machines.reduce((a, m) => a + m.mapped_codes, 0);
  const totalPending = state.machines.reduce((a, m) => a + m.pending_count, 0);
  const connectedNow = state.machines.filter((m) => m.live_state === "connected").length;

  document.getElementById("overviewStats").innerHTML = `
    <div class="stat-chip"><div class="stat-value">${connectedNow}/${state.machines.length}</div><div class="stat-label">Connected</div></div>
    <div class="stat-chip"><div class="stat-value">${totalSamples}</div><div class="stat-label">Samples</div></div>
    <div class="stat-chip"><div class="stat-value">${totalMapped}</div><div class="stat-label">Mapped</div></div>
    <div class="stat-chip"><div class="stat-value">${totalPending}</div><div class="stat-label">Pending</div></div>
  `;

  const grid = document.getElementById("machineGrid");
  // The static skeleton placeholders in index.html (shown before the first
  // /api/machines response) have no data-machine attribute, so the reuse
  // logic below would only ever "see" one of them (all sharing the same
  // undefined key) - the other 2 would silently orphan in the DOM forever,
  // still shimmering next to the real cards. Clear them out explicitly the
  // first time real data arrives, before doing anything else.
  grid.querySelectorAll(".skeleton-card").forEach((el) => el.remove());
  // Reuse existing card DOM nodes across polls instead of wiping and
  // rebuilding the whole grid every ~2-3s (setInterval-driven) - destroying
  // and recreating the element the mouse is resting on made the browser
  // treat it as a brand-new, never-hovered element on every single poll
  // tick, restarting the :hover transition from scratch each time (looked
  // like the hover "animation kept redoing itself, like it has a time
  // limit" - the "time limit" was literally the poll interval). Keyed by
  // machine so a card's identity (and thus its live :hover state) survives
  // a data refresh; only genuinely added/removed machines touch the DOM.
  const existingCards = new Map();
  grid.querySelectorAll(".machine-card").forEach((el) => existingCards.set(el.dataset.machine, el));
  const seenMachines = new Set();

  state.machines.forEach((m) => {
    seenMachines.add(m.machine);
    // live_state comes straight from the listener thread itself:
    // "connected" = an analyzer is connected to this port right now,
    // "listening" = the port is open and waiting, "unknown" = the listener
    // isn't running in this process (e.g. admin UI run standalone).
    const liveClass = m.live_state === "connected" ? "connected"
                     : m.live_state === "listening" ? "listening" : "unknown";
    const liveLabel = m.live_state === "connected" ? "Connected"
                     : m.live_state === "listening" ? "Listening" : "Unknown";
    let card = existingCards.get(m.machine);
    const isNew = !card;
    if (isNew) {
      card = document.createElement("div");
      card.dataset.machine = m.machine;
      // Set once. Assigning className on every poll would silently wipe any
      // class added to the card elsewhere (a transient highlight, a future
      // state class) every 2-3s, which is a nasty thing to debug later.
      card.className = "machine-card";
    }
    // Guarded: setProperty is a style mutation even when the value is
    // identical, and this runs once per card per poll.
    const wantColor = m.color || "";
    if (card.style.getPropertyValue("--m-color") !== wantColor) {
      card.style.setProperty("--m-color", wantColor);
    }

    // Build the card's DOM ONCE, on first creation only. Re-running
    // innerHTML on every poll tick (~2-3s) destroys and recreates the
    // <img> element each time, which makes the browser drop and re-decode
    // the photo - invisible on a warm desktop cache, but on mobile it
    // reads as the images "blinking"/reloading forever. Subsequent polls
    // patch just the handful of values that actually change (below), so
    // the <img> node itself is never touched.
    if (isNew) {
      const cardBgClass = m.photo_bg === "card" ? " has-photo-card" : "";
      card.innerHTML = `
        <div class="card-photo-frame${cardBgClass}">
          <div class="card-photo-frame-top">
            <div class="card-live-badge ${liveClass}">
              <span class="dot"></span><span class="live-label">${liveLabel}</span>
            </div>
            <button class="icon-btn card-ping-btn" title="Ping this machine's last known IP" type="button">
              <svg viewBox="0 0 24 24" fill="none"><path d="M12 22s8-7.2 8-13a8 8 0 10-16 0c0 5.8 8 13 8 13z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/><circle cx="12" cy="9" r="2.5" stroke="currentColor" stroke-width="1.8"/></svg>
            </button>
            <button class="icon-btn card-config-btn" title="Edit machine" type="button">
              <svg viewBox="0 0 24 24" fill="none"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4L16.5 3.5z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </button>
          </div>
          <div class="card-photo-frame-body">
            ${m.photo
              ? `<img class="card-photo" src="/${m.photo}" alt="${escapeHtml(m.label)}" loading="lazy">`
              : `<div class="card-avatar">${escapeHtml(initials(m.label))}</div>`}
          </div>
        </div>
        <h3 class="card-title">${escapeHtml(m.label)}</h3>
        <p class="card-kind">${escapeHtml(m.kind)}</p>
        <div class="card-metrics">
          <div class="card-metric" title="Test codes from this analyzer that have a curated mapping to a clinic parameter/exam — the same count shown on the Mappings screen">
            <div class="card-metric-value matched" data-metric="mapped">${m.mapped_codes}</div>
            <div class="card-metric-label">Mapped</div>
          </div>
          <div class="card-metric" title="Distinct test codes seen from this analyzer with no curated mapping yet — the same list shown on the Mappings 'Pending Codes' tab">
            <div class="card-metric-value pending" data-metric="pending">${m.pending_count}</div>
            <div class="card-metric-label">Pending</div>
          </div>
          <div class="card-metric" title="Distinct samples/orders received from this analyzer">
            <div class="card-metric-value" data-metric="samples">${m.sample_count}</div>
            <div class="card-metric-label">Samples</div>
          </div>
        </div>
        <div class="card-footer">
          <span class="protocol-tag">${escapeHtml(m.protocol)}</span>
          <span class="card-port">port ${m.port}</span>
        </div>
      `;
      // Attached once, on first creation only - the card node is reused
      // across polls, so re-adding these every refresh would stack
      // duplicate handlers.
      card.addEventListener("click", () => {
        state.mappingsMachine = card.dataset.machine;
        showSection("mappings");
      });
      card.querySelector(".card-config-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        openConfigModal(machineByKey(card.dataset.machine) || m);
      });
      card.querySelector(".card-ping-btn").addEventListener("click", (e) => {
        e.stopPropagation();
        const cur = machineByKey(card.dataset.machine) || m;
        pingMachine(card.dataset.machine, cur.label);
      });
      grid.appendChild(card);
    }

    // Patch only what can actually change between polls. Every write below
    // is guarded by a read-compare first: assigning the same string to
    // textContent still counts as a DOM mutation, and doing that to a dozen
    // nodes 6 times per card per tick is exactly the kind of pointless
    // churn that shows up as jank on a phone.
    const setText = (el, val) => { if (el && el.textContent !== val) el.textContent = val; };

    const badge = card.querySelector(".card-live-badge");
    const badgeClass = `card-live-badge ${liveClass}`;
    if (badge.className !== badgeClass) badge.className = badgeClass;
    setText(badge.querySelector(".live-label"), liveLabel);

    setText(card.querySelector(".card-title"), m.label);
    setText(card.querySelector(".card-kind"), m.kind);
    setText(card.querySelector(".protocol-tag"), m.protocol);
    setText(card.querySelector(".card-port"), `port ${m.port}`);

    const prev = prevMachineStats[m.machine];
    const metric = (key, statKey, value) => {
      const el = card.querySelector(`[data-metric="${key}"]`);
      if (!el) return;
      setText(el, String(value));
      // Same highlight-on-change cue as before. .flash-update is a CSS
      // animation, and the element now persists across polls instead of
      // being regenerated - so simply re-adding the class on a second
      // consecutive change would NOT replay it. Remove it, force a reflow,
      // then re-add so the animation restarts from 0 each time the number
      // actually moves.
      if (changed(prev, statKey, value) !== "") {
        el.classList.remove("flash-update");
        void el.offsetWidth;
        el.classList.add("flash-update");
      }
    };
    metric("mapped", "mapped_codes", m.mapped_codes);
    metric("pending", "pending_count", m.pending_count);
    metric("samples", "sample_count", m.sample_count);

    // Photo/background can change via the config modal - swap only when it
    // genuinely differs, so a normal poll never touches the <img>.
    const frame = card.querySelector(".card-photo-frame");
    const wantBgClass = m.photo_bg === "card";
    if (frame.classList.contains("has-photo-card") !== wantBgClass) {
      frame.classList.toggle("has-photo-card", wantBgClass);
    }
    const img = card.querySelector(".card-photo");
    if (m.photo) {
      const wantSrc = `/${m.photo}`;
      if (!img) {
        card.querySelector(".card-photo-frame-body").innerHTML =
          `<img class="card-photo" src="${wantSrc}" alt="${escapeHtml(m.label)}" loading="lazy">`;
      } else if (!img.getAttribute("src").startsWith(wantSrc)) {
        img.src = wantSrc;
        img.alt = m.label;
      }
    } else if (img) {
      card.querySelector(".card-photo-frame-body").innerHTML =
        `<div class="card-avatar">${escapeHtml(initials(m.label))}</div>`;
    }
  });

  // Remove cards for machines that no longer exist (rare - only if a
  // machine were ever removed at runtime; harmless no-op otherwise).
  existingCards.forEach((el, machine) => {
    if (!seenMachines.has(machine)) el.remove();
  });
}

// ---------------------------------------------------------------------------
// Mappings section - machine picker + Mapped/Pending/Samples tabs (this is
// now the ONLY place per-machine detail lives; there is no separate machine
// detail page)
// ---------------------------------------------------------------------------

function renderMappingsMachinePicker() {
  const picker = document.getElementById("mappingsMachinePicker");
  picker.innerHTML = "";
  state.machines.forEach((m) => {
    const pill = document.createElement("button");
    pill.className = "machine-pill" + (state.mappingsMachine === m.machine ? " active" : "");
    pill.style.setProperty("--m-color", m.color || "");
    pill.innerHTML = `${avatarHtml(m, "machine-pill-icon")}<span>${escapeHtml(m.label)}</span>
      <span class="machine-pill-count" title="${m.mapped_codes} test code(s) have a curated mapping to a clinic parameter/exam — this is NOT how many results have been received">${m.mapped_codes} mapped</span>`;
    pill.addEventListener("click", () => selectMappingsMachine(m.machine));
    picker.appendChild(pill);
  });
}

// ---- tabs (Mapped / Pending / Samples, inside the Mappings section) ----
document.querySelectorAll(".tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".tab-panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`panel-${tab.dataset.tab}`).classList.add("active");
  });
});

document.getElementById("backToMachines").addEventListener("click", () => showSection("machines"));

// Guards against clicking machine A then machine B before A's data finishes
// loading - without this, whichever slower request resolved last would
// silently overwrite the table with the WRONG machine's (or stale/empty)
// data, which read as "there's clearly a sample but it says none".
let mappingsLoadSeq = 0;

async function selectMappingsMachine(machine) {
  // Only reset paging/date-filter/search when actually switching to a
  // DIFFERENT machine - showSection("mappings") re-calls this for the
  // already-selected machine every time you navigate back to the tab, and
  // resetting on that would throw away where you were for no reason.
  if (state.mappingsMachine !== machine) {
    state.samplesPage = 1;
    state.samplesDateFrom = "";
    state.samplesDateTo = "";
    const fromEl = document.getElementById("samplesDateFrom");
    const toEl = document.getElementById("samplesDateTo");
    if (fromEl) fromEl.value = "";
    if (toEl) toEl.value = "";
    const searchEl = document.getElementById("samplesSearch");
    if (searchEl) searchEl.value = "";
  }
  state.mappingsMachine = machine;
  renderMappingsMachinePicker();
  const mySeq = ++mappingsLoadSeq;

  const meta = state.machines.find((m) => m.machine === machine) || {};
  const avatarEl = document.getElementById("mappingsAvatar");
  avatarEl.style.setProperty("--m-color", meta.color || "");
  avatarEl.classList.toggle("machine-avatar-bare", !!meta.photo);
  avatarEl.innerHTML = avatarHtml(meta, "machine-avatar");
  document.getElementById("mappingsTitle").textContent = meta.label || machine;
  document.getElementById("mappingsSub").textContent =
    "Search the clinic database and match machine test codes to a parameter or exam.";
  document.getElementById("mappingsBadges").innerHTML = `
    <span class="badge">${escapeHtml(meta.protocol || "")}</span>
    <span class="badge">port ${meta.port}</span>
    <span class="badge">${meta.editable ? "editable map" : "aliased map (read-only)"}</span>
  `;

  // Skeleton rows only here (switching to a machine), never during the
  // background live-poll refresh (which calls loadMapped/loadPending/
  // loadSamples directly) - otherwise the tables would shimmer/flicker
  // every few seconds instead of just updating quietly in place.
  skeletonizeTable("mappedTableBody", 6, 5);
  skeletonizeTable("pendingTableBody", 5, 5);
  skeletonizeTable("samplesTableBody", 4, 6);

  // Promise.allSettled (not .all): one panel failing to load must not blank
  // out the other two - each of the 3 tables loads and reports independently.
  const results = await Promise.allSettled([
    loadMapped(machine), loadPending(machine), loadSamples(machine),
  ]);
  if (mySeq !== mappingsLoadSeq) return; // a newer machine selection superseded this one

  const labels = ["Mapped Parameters", "Pending Codes", "Recent Samples"];
  results.forEach((r, i) => {
    if (r.status === "rejected") {
      console.error(`Failed to load ${labels[i]} for ${machine}:`, r.reason);
      toast(`Couldn't load ${labels[i]} — ${r.reason.message || "request failed"}.`, "error");
    }
  });
}

async function loadMapped(machine) {
  const data = await apiGet(`/api/machines/${machine}/mappings`);
  if (machine !== state.mappingsMachine) return; // superseded by a newer selection
  state.mappings = data.entries;
  state.editable = data.editable;
  document.getElementById("addMappingBtn").style.display = data.editable ? "inline-flex" : "none";
  renderMappedTable(document.getElementById("mappedSearch").value);
}

// ---- pending codes table ----
async function loadPending(machine) {
  const rows = await apiGet(`/api/machines/${machine}/pending`);
  if (machine !== state.mappingsMachine) return; // superseded by a newer selection
  state.pending = rows;
  const tbody = document.getElementById("pendingTableBody");
  document.getElementById("pendingEmpty").hidden = state.pending.length > 0;
  tbody.innerHTML = "";
  state.pending.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="Test Code"><span class="code-pill">${escapeHtml(r.test_code)}</span></td>
      <td data-label="Sample Value"><span class="value-mono">${escapeHtml(r.sample_value)} ${escapeHtml(r.sample_unit || "")}</span></td>
      <td data-label="Times Seen">${r.seen_count}×</td>
      <td data-label="Last Seen" class="timestamp-cell">${timeAgo(r.last_seen)}</td>
      <td class="col-actions">
        <button class="btn btn-ghost map-pending-btn" ${state.editable ? "" : "disabled"}>Map it</button>
      </td>
    `;
    tr.querySelector(".map-pending-btn").addEventListener("click", () => openEditModal({
      code: r.test_code, param_id: null, service_tarification_id: null,
      service_tarification_name: "", abbrev: "", name: "",
    }, true));
    tbody.appendChild(tr);
  });
}

// ---- samples table ----
// Filtering/paging moved server-side (see api_machine_samples) once
// pagination was added - the old client-side .filter() only ever searched
// whatever page happened to already be fetched (at most 25 rows), silently
// missing every older match. loadSamples now sends the current page/date
// range/search text as query params on every call, including the ~3s
// live-poll refresh - so paging/filters survive a poll instead of
// snapping back to page 1.
function samplesQueryParams() {
  const p = new URLSearchParams();
  p.set("page", String(state.samplesPage));
  p.set("page_size", String(SAMPLES_PAGE_SIZE));
  if (state.samplesDateFrom) p.set("date_from", state.samplesDateFrom);
  if (state.samplesDateTo) p.set("date_to", state.samplesDateTo);
  const search = document.getElementById("samplesSearch").value.trim();
  if (search) p.set("search", search);
  return p;
}

async function loadSamples(machine) {
  const data = await apiGet(`/api/machines/${machine}/samples?${samplesQueryParams()}`);
  if (machine !== state.mappingsMachine) return; // superseded by a newer selection
  state.samples = data.rows;
  state.samplesTotal = data.total;
  // The live poll can't know in advance that a filter/page emptied out
  // from under it (e.g. the last item on page 2 got reassigned) - clamp
  // back onto the last real page rather than showing a blank page forever.
  const lastPage = Math.max(1, Math.ceil(state.samplesTotal / SAMPLES_PAGE_SIZE));
  if (state.samplesPage > lastPage) {
    state.samplesPage = lastPage;
    return loadSamples(machine);
  }
  renderSamplesTable();
}

// Sample IDs already rendered at least once, per machine - lets
// renderSamplesTable tell a genuinely NEW row (just arrived via live poll)
// apart from one that was already showing, so only actual arrivals slide in.
// Only meaningful on page 1 (a new arrival is always the newest row, which
// only ever lands on page 1 - a slide-in on page 3 would be misleading).
const seenSampleIds = {};

function renderSamplesTable() {
  const tbody = document.getElementById("samplesTableBody");
  const rows = state.samples;
  document.getElementById("samplesEmpty").hidden = rows.length > 0 || state.samplesTotal > 0;

  const machine = state.mappingsMachine;
  const seen = seenSampleIds[machine] || (seenSampleIds[machine] = new Set());
  const isFirstRenderForMachine = seen.size === 0;
  const onPageOne = state.samplesPage === 1;

  tbody.innerHTML = "";
  rows.forEach((s) => {
    const id = s.sample_id.trim();
    const isNew = onPageOne && !isFirstRenderForMachine && !seen.has(id);
    if (onPageOne) seen.add(id);

    const tr = document.createElement("tr");
    if (isNew) tr.className = "row-enter";
    tr.innerHTML = `
      <td data-label="Sample ID"><span class="code-pill">${escapeHtml(id)}</span></td>
      <td data-label="Patient">${escapeHtml((s.patient_name || "").trim() || "—")}</td>
      <td data-label="Received" class="timestamp-cell">${timeAgo(s.received_at)}</td>
      <td class="col-actions">
        <button class="btn btn-ghost view-sample-btn">View</button>
      </td>
    `;
    tr.querySelector(".view-sample-btn").addEventListener("click", () =>
      openSampleModal(state.mappingsMachine, id));
    tbody.appendChild(tr);
  });

  renderSamplesPager();
}

// Which page numbers to actually show as buttons: always first, last, the
// current page, and one neighbor on each side - everything else collapses
// into a single "…" so a 40-page result set doesn't render 40 buttons.
// Returns a mix of numbers and the literal string "…".
function pagerPageList(current, last) {
  if (last <= 7) {
    return Array.from({ length: last }, (_, i) => i + 1);
  }
  const pages = new Set([1, last, current, current - 1, current + 1]);
  const sorted = [...pages].filter((p) => p >= 1 && p <= last).sort((a, b) => a - b);
  const out = [];
  for (let i = 0; i < sorted.length; i++) {
    if (i > 0 && sorted[i] - sorted[i - 1] > 1) out.push("…");
    out.push(sorted[i]);
  }
  return out;
}

function goToSamplesPage(page) {
  const lastPage = Math.max(1, Math.ceil(state.samplesTotal / SAMPLES_PAGE_SIZE));
  const clamped = Math.min(Math.max(1, page), lastPage);
  if (clamped === state.samplesPage) return;
  state.samplesPage = clamped;
  loadSamples(state.mappingsMachine);
}

function renderSamplesPager() {
  const el = document.getElementById("samplesPager");
  if (!el) return;
  const lastPage = Math.max(1, Math.ceil(state.samplesTotal / SAMPLES_PAGE_SIZE));
  if (state.samplesTotal === 0) { el.innerHTML = ""; return; }
  const startN = (state.samplesPage - 1) * SAMPLES_PAGE_SIZE + 1;
  const endN = Math.min(state.samplesPage * SAMPLES_PAGE_SIZE, state.samplesTotal);

  const pageButtons = pagerPageList(state.samplesPage, lastPage).map((p) => {
    if (p === "…") return `<span class="pager-ellipsis">…</span>`;
    const active = p === state.samplesPage ? " active" : "";
    return `<button class="pager-page-btn${active}" data-page="${p}" ${active ? 'aria-current="page"' : ""}>${p}</button>`;
  }).join("");

  el.innerHTML = `
    <span class="pager-summary">${startN}–${endN} of ${state.samplesTotal}</span>
    <div class="pager-controls">
      <button class="icon-btn pager-prev" ${state.samplesPage <= 1 ? "disabled" : ""} aria-label="Previous page">
        <svg viewBox="0 0 24 24" fill="none"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
      <div class="pager-pages">${pageButtons}</div>
      <button class="icon-btn pager-next" ${state.samplesPage >= lastPage ? "disabled" : ""} aria-label="Next page">
        <svg viewBox="0 0 24 24" fill="none"><path d="M9 6l6 6-6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
      </button>
    </div>
  `;
  el.querySelector(".pager-prev").addEventListener("click", () => goToSamplesPage(state.samplesPage - 1));
  el.querySelector(".pager-next").addEventListener("click", () => goToSamplesPage(state.samplesPage + 1));
  el.querySelectorAll(".pager-page-btn").forEach((btn) => {
    btn.addEventListener("click", () => goToSamplesPage(parseInt(btn.dataset.page, 10)));
  });
}

// Native <input type="date"> - a custom popover calendar was tried twice
// and broke twice in ways only visible in a real browser (collapsed to
// 0x0 inside a hidden tab panel, then rendered cut off past the right
// edge of the screen for a trigger sitting on the right side of a wide
// viewport). The native picker handles its own popup placement/sizing
// with no positioning code to get wrong - see style.css for how the
// field itself (not the OS popup) is styled to match the rest of the app.
document.getElementById("samplesDateFrom").addEventListener("change", (e) => {
  state.samplesDateFrom = e.target.value;
  state.samplesPage = 1; // a changed filter always restarts at page 1
  loadSamples(state.mappingsMachine);
});
document.getElementById("samplesDateTo").addEventListener("change", (e) => {
  state.samplesDateTo = e.target.value;
  state.samplesPage = 1;
  loadSamples(state.mappingsMachine);
});
document.getElementById("samplesDateReset").addEventListener("click", () => {
  state.samplesDateFrom = "";
  state.samplesDateTo = "";
  document.getElementById("samplesDateFrom").value = "";
  document.getElementById("samplesDateTo").value = "";
  state.samplesPage = 1;
  loadSamples(state.mappingsMachine);
});

// Search now hits the server (see samplesQueryParams), so it's debounced
// rather than firing a request on every keystroke.
let samplesSearchDebounce = null;
document.getElementById("samplesSearch").addEventListener("input", () => {
  clearTimeout(samplesSearchDebounce);
  samplesSearchDebounce = setTimeout(() => {
    state.samplesPage = 1; // a changed search always restarts at page 1
    loadSamples(state.mappingsMachine);
  }, 350);
});

function renderMappedTable(filter = "") {
  const tbody = document.getElementById("mappedTableBody");
  const f = filter.trim().toLowerCase();
  const rows = state.mappings.filter((r) =>
    !f || r.code.toLowerCase().includes(f) ||
    (r.name || "").toLowerCase().includes(f) ||
    (r.service_tarification_name || "").toLowerCase().includes(f) ||
    (r.param_id !== null && String(r.param_id).includes(f)) ||
    (r.service_tarification_id !== null && String(r.service_tarification_id).includes(f))
  );
  document.getElementById("mappedEmpty").hidden = rows.length > 0;
  tbody.innerHTML = "";
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td data-label="Machine Code"><span class="code-pill">${escapeHtml(r.code)}</span></td>
      <td data-label="Matched To">
        <div class="param-id-row">${r.param_id !== null
          ? `<span class="match-kind-badge match-kind-param" title="Matched by param_id">param_id</span><span class="param-id">#${r.param_id}</span>`
          : `<span class="match-kind-badge match-kind-exam" title="No single param - matched by exam (service_tarification_id) instead">service_tarification_id</span><span class="param-id">#${r.service_tarification_id}</span>`}</div>
        <div class="param-name">${escapeHtml(r.abbrev || "")} ${r.name ? "· " + escapeHtml(r.name) : ""}</div>
      </td>
      <td data-label="Exam"><span class="exam-tag">${escapeHtml(r.service_tarification_name || "—")}</span></td>
      <td data-label="Last Value">${r.last_value !== null
        ? `<span class="value-mono">${escapeHtml(r.last_value)} ${escapeHtml(r.last_unit || "")}</span>`
        : '<span class="muted-cell">no data yet</span>'}</td>
      <td data-label="Last Seen" class="timestamp-cell">${timeAgo(r.last_seen)}</td>
      <td class="col-actions">
        <div class="row-actions">
          <button class="icon-btn edit-btn" title="Edit" ${state.editable ? "" : "disabled"}>
            <svg viewBox="0 0 24 24" fill="none"><path d="M12 20h9M16.5 3.5a2.1 2.1 0 013 3L7 19l-4 1 1-4L16.5 3.5z" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>
      </td>
    `;
    tr.querySelector(".edit-btn").addEventListener("click", () => openEditModal(r));
    tbody.appendChild(tr);
  });
}

document.getElementById("mappedSearch").addEventListener("input", (e) => {
  renderMappedTable(e.target.value);
});

// ---------------------------------------------------------------------------
// Edit / Add mapping modal
// ---------------------------------------------------------------------------

const scrim = document.getElementById("modalScrim");
const fCode = document.getElementById("fCode");
const codeResults = document.getElementById("codeResults");
const fSearch = document.getElementById("fSearch");
const searchResults = document.getElementById("searchResults");
const matchPicked = document.getElementById("matchPicked");
const matchPickedName = document.getElementById("matchPickedName");
const matchPickedMeta = document.getElementById("matchPickedMeta");
const modalAlert = document.getElementById("modalAlert");
const deleteBtn = document.getElementById("deleteMappingBtn");

// The one thing that actually gets saved - everything else is UI sugar
// around picking this.
let pickedMatch = null; // { param_id, service_tarification_id, service_tarification_name, abbrev, name }

function showPickedMatch(match) {
  pickedMatch = match;
  if (!match) {
    matchPicked.hidden = true;
    fSearch.value = "";
    return;
  }
  matchPicked.hidden = false;
  if (match.param_id) {
    matchPickedName.textContent = match.name || match.abbrev || `Param #${match.param_id}`;
    matchPickedMeta.textContent = `Lab parameter #${match.param_id}` +
      (match.service_tarification_name ? ` · part of ${match.service_tarification_name}` : "");
    matchKindBadge.textContent = "param_id";
    matchKindBadge.className = "match-kind-badge match-kind-param";
  } else {
    matchPickedName.textContent = match.service_tarification_name || "Exam";
    matchPickedMeta.textContent = `Exam #${match.service_tarification_id} · no single-value breakdown`;
    matchKindBadge.textContent = "service_tarification_id";
    matchKindBadge.className = "match-kind-badge match-kind-exam";
  }
  fSearch.value = "";
  searchResults.classList.remove("open");
}

document.getElementById("matchPickedClear").addEventListener("click", () => showPickedMatch(null));

// ---- machine-code picker: same styled dropdown as the clinic search below,
// instead of a native <datalist> (unstyled, browser-default look) - just a
// client-side filter over state.pending, already loaded, no API call needed. ----
function renderCodeResults(filter) {
  const f = filter.trim().toLowerCase();
  const rows = (state.pending || []).filter((p) => !f || p.test_code.toLowerCase().includes(f));

  if (rows.length === 0) {
    codeResults.innerHTML = `<div class="combo-result combo-result-message">${
      f ? `No pending codes match "${escapeHtml(filter)}".` : "No pending codes for this analyzer."
    }</div>`;
    codeResults.classList.add("open");
    return;
  }

  codeResults.innerHTML = "";
  rows.forEach((p) => {
    const div = document.createElement("div");
    div.className = "combo-result";
    div.innerHTML = `<div class="cr-name">${escapeHtml(p.test_code)}</div>
      <div class="cr-meta">${escapeHtml(p.sample_value || "")} ${escapeHtml(p.sample_unit || "")} · seen ${p.seen_count}×</div>`;
    div.addEventListener("click", () => {
      fCode.value = p.test_code;
      updateModalTitle();
      codeResults.classList.remove("open");
    });
    codeResults.appendChild(div);
  });
  codeResults.classList.add("open");
}

// Only on an actual click/tap into the field, or while typing - NOT on
// focus(), since the modal programmatically focuses this field when it
// opens (see openEditModal's setTimeout), which would otherwise pop the
// dropdown open immediately before the user did anything.
fCode.addEventListener("click", () => { if (!fCode.disabled) renderCodeResults(fCode.value); });
fCode.addEventListener("input", () => { renderCodeResults(fCode.value); updateModalTitle(); });
document.addEventListener("click", (e) => {
  if (!e.target.closest("#fCode") && !e.target.closest("#codeResults")) {
    codeResults.classList.remove("open");
  }
});

// "Map <code>" once a code is picked/typed, a plain "Add mapping" while the
// field is still empty (e.g. brand-new mapping via "Add mapping", before
// anything is typed) - avoids the ugly `Map ""` title that showed for that
// case previously.
function updateModalTitle() {
  const code = fCode.value.trim();
  document.getElementById("modalTitle").textContent = state.editingCode
    ? `Edit "${state.editingCode}"`
    : (code ? `Map "${code}"` : "Add mapping");
}

function openEditModal(entry, isNewFromPending = false) {
  // Unconditionally clear any match picked in a PREVIOUS modal session first -
  // showPickedMatch(null) below only runs when this entry itself has no
  // match, but that's still one extra branch of trust; resetting here up
  // front guarantees the green "matched" indicator can never carry over from
  // whatever code was last edited, regardless of call site or timing.
  showPickedMatch(null);

  state.editingCode = isNewFromPending ? null : entry.code;
  // fCode.value must be set BEFORE updateModalTitle() runs - it reads
  // fCode.value directly, so calling it first read whatever was left over
  // from the PREVIOUS modal session (e.g. still showed "Map BASO%" right
  // after opening a fresh pending code, even though the field itself
  // already showed the new code correctly underneath).
  fCode.value = entry.code || "";
  updateModalTitle();
  fCode.disabled = !!state.editingCode; // code is the key; don't rename in place
  codeResults.classList.remove("open");

  if (entry.param_id || entry.service_tarification_id) {
    showPickedMatch({
      param_id: entry.param_id, service_tarification_id: entry.service_tarification_id,
      service_tarification_name: entry.service_tarification_name,
      abbrev: entry.abbrev, name: entry.name,
    });
  } else {
    showPickedMatch(null);
  }

  modalAlert.hidden = true;
  deleteBtn.hidden = !state.editingCode;
  scrim.classList.add("open");
  setTimeout(() => fCode.disabled ? fSearch.focus() : fCode.focus(), 80);
}

function closeModal() {
  scrim.classList.remove("open");
}

document.getElementById("modalClose").addEventListener("click", closeModal);
document.getElementById("modalCancel").addEventListener("click", closeModal);
scrim.addEventListener("click", (e) => { if (e.target === scrim) closeModal(); });
document.addEventListener("keydown", (e) => { if (e.key === "Escape" && scrim.classList.contains("open")) closeModal(); });

document.getElementById("addMappingBtn").addEventListener("click", () => {
  openEditModal({ code: "", param_id: null, service_tarification_id: null,
                  service_tarification_name: "", abbrev: "", name: "" });
});

// ---- unified search: queries params + exams together, tags each result ----
// Debounced, sequence-guarded (a slow stale request can't clobber a newer
// one's results), and failures show a real message in the dropdown instead
// of only logging to the console - the clinic Postgres DB being temporarily
// unreachable is common enough that this needs to be visible, not silent.
let searchTimer = null;
let searchSeq = 0;

function renderSearchMessage(text) {
  searchResults.innerHTML = `<div class="combo-result combo-result-message">${escapeHtml(text)}</div>`;
  searchResults.classList.add("open");
}

function runClinicSearch() {
  clearTimeout(searchTimer);
  const q = fSearch.value.trim();
  if (q.length < 1) {
    renderSearchMessage("Start typing a parameter or exam name…");
    return;
  }

  renderSearchMessage("Searching…");
  const mySeq = ++searchSeq;

  // Short debounce so results feel instant as you type (fires from the very
  // first character now, not the second).
  searchTimer = setTimeout(async () => {
    const [paramsResult, examsResult] = await Promise.allSettled([
      apiGet(`/api/param-search?q=${encodeURIComponent(q)}`),
      apiGet(`/api/exam-search?q=${encodeURIComponent(q)}`),
    ]);
    if (mySeq !== searchSeq) return; // a newer keystroke already superseded this search

    if (paramsResult.status === "rejected" && examsResult.status === "rejected") {
      renderSearchMessage("Search unavailable — the clinic database isn't reachable right now.");
      return;
    }

    const paramRows = paramsResult.status === "fulfilled" && Array.isArray(paramsResult.value)
      ? paramsResult.value : [];
    const examRows = examsResult.status === "fulfilled" && Array.isArray(examsResult.value)
      ? examsResult.value : [];

    if (paramRows.length + examRows.length === 0) {
      renderSearchMessage(`No matches for "${q}".`);
      return;
    }

    searchResults.innerHTML = "";
    paramRows.forEach((r) => {
      const div = document.createElement("div");
      div.className = "combo-result";
      div.innerHTML = `<span class="combo-result-tag param">Parameter</span>
        <span class="match-kind-badge match-kind-param cr-id-badge" title="param_id">param_id #${r.id}</span>
        <div class="cr-name">${escapeHtml(r.name)}</div>
        <div class="cr-meta">${escapeHtml(r.abbreviation || "")} ${r.um ? "· " + escapeHtml(r.um) : ""} ${r.service_tarification_name ? "· " + escapeHtml(r.service_tarification_name) : ""}</div>`;
      div.addEventListener("click", () => showPickedMatch({
        param_id: r.id,
        service_tarification_id: r.service_tarification_id || null,
        service_tarification_name: r.service_tarification_name || "",
        abbrev: r.abbreviation || "",
        name: r.name || "",
      }));
      searchResults.appendChild(div);
    });

    examRows.forEach((r) => {
      const div = document.createElement("div");
      div.className = "combo-result";
      div.innerHTML = `<span class="combo-result-tag exam">Exam</span>
        <span class="match-kind-badge match-kind-exam cr-id-badge" title="service_tarification_id">service_tarification_id #${r.id}</span>
        <div class="cr-name">${escapeHtml(r.name)}</div>
        <div class="cr-meta">${r.is_composed ? "Has individual parameters — search for the specific one above instead" : "Single result, no parameter breakdown"}</div>`;
      div.addEventListener("click", () => showPickedMatch({
        param_id: null,
        service_tarification_id: r.id,
        service_tarification_name: r.name,
        abbrev: "", name: r.name,
      }));
      searchResults.appendChild(div);
    });

    searchResults.classList.add("open");
  }, 120);
}

fSearch.addEventListener("input", runClinicSearch);
// Show the dropdown on an actual click into the field (re-showing existing
// results if there's already a query typed, or a hint if empty) - NOT on
// focus(), since the modal programmatically focuses this field when it
// opens for editing (see openEditModal), which would otherwise pop it open
// before the user did anything.
fSearch.addEventListener("click", runClinicSearch);

document.addEventListener("click", (e) => {
  if (!e.target.closest(".combo")) {
    searchResults.classList.remove("open");
  }
});

// ---- save / delete ----
document.getElementById("modalSave").addEventListener("click", async () => {
  const code = fCode.value.trim();
  if (!code) {
    modalAlert.hidden = false;
    modalAlert.textContent = "A machine test code is required.";
    return;
  }
  if (!pickedMatch) {
    modalAlert.hidden = false;
    modalAlert.textContent = "Search and pick a parameter or exam to match this code to.";
    return;
  }

  // Warn (don't hard-block) if this exact param/exam is already mapped to a
  // DIFFERENT code on this same machine - legitimate cross-machine reuse
  // (e.g. XN-330 and XS-500i both mapping their own "WBC" to the same
  // param) is common and fine, but two DIFFERENT codes on the SAME machine
  // both pointing at one param is almost always a mistake (both would then
  // silently write into the same clinic param slot) - confirm() matches
  // the same pattern already used for Delete mapping's destructive action.
  const duplicate = (state.mappings || []).find((r) => {
    if (r.code === code) return false; // editing the same entry - not a conflict with itself
    if (pickedMatch.param_id) return r.param_id === pickedMatch.param_id;
    return !r.param_id && r.service_tarification_id === pickedMatch.service_tarification_id;
  });
  if (duplicate) {
    const target = pickedMatch.param_id
      ? `param_id #${pickedMatch.param_id}`
      : `service_tarification_id #${pickedMatch.service_tarification_id}`;
    const proceed = confirm(
      `"${duplicate.code}" is already mapped to this same ${target} on this machine.\n\n` +
      `Mapping "${code}" to it too means both codes will write into the same clinic slot - ` +
      `only do this if they're genuinely the same measurement.\n\nSave anyway?`
    );
    if (!proceed) return;
  }

  try {
    await apiPut(`/api/machines/${state.mappingsMachine}/mappings/${encodeURIComponent(code)}`, {
      param_id: pickedMatch.param_id,
      service_tarification_id: pickedMatch.service_tarification_id,
      service_tarification_name: pickedMatch.service_tarification_name,
      abbrev: pickedMatch.abbrev,
      name: pickedMatch.name,
    });
    closeModal();
    toast(`Mapping saved for "${code}".`, "success");
    await Promise.all([loadMapped(state.mappingsMachine), loadPending(state.mappingsMachine), loadMachines()]);
  } catch (e) {
    modalAlert.hidden = false;
    modalAlert.textContent = e.message || "Failed to save mapping.";
  }
});

deleteBtn.addEventListener("click", async () => {
  if (!state.editingCode) return;
  if (!confirm(`Remove the mapping for "${state.editingCode}"? This cannot be undone.`)) return;
  try {
    await apiDelete(`/api/machines/${state.mappingsMachine}/mappings/${encodeURIComponent(state.editingCode)}`);
    closeModal();
    toast(`Mapping removed for "${state.editingCode}".`, "success");
    await Promise.all([loadMapped(state.mappingsMachine), loadPending(state.mappingsMachine), loadMachines()]);
  } catch (e) {
    modalAlert.hidden = false;
    modalAlert.textContent = e.message || "Failed to delete mapping.";
  }
});

// ---------------------------------------------------------------------------
// API Settings
// ---------------------------------------------------------------------------

const setEndpoint = document.getElementById("setEndpoint");
const setToken = document.getElementById("setToken");
const setUseApiToggle = document.getElementById("setUseApiToggle");
const settingsAlert = document.getElementById("settingsAlert");
let useApiValue = false;

async function loadApiSettings() {
  try {
    const s = await apiGet("/api/settings/api");
    setEndpoint.value = s.endpoint || "";
    setToken.value = s.api_token || "";
    useApiValue = !!s.use_machine_result_api;
    setUseApiToggle.classList.toggle("on", useApiValue);
    setUseApiToggle.setAttribute("aria-checked", String(useApiValue));
    settingsAlert.hidden = true;
  } catch (e) {
    settingsAlert.hidden = false;
    settingsAlert.textContent = e.message || "Failed to load API settings.";
  }
}

setUseApiToggle.addEventListener("click", () => {
  useApiValue = !useApiValue;
  setUseApiToggle.classList.toggle("on", useApiValue);
  setUseApiToggle.setAttribute("aria-checked", String(useApiValue));
});

document.getElementById("saveSettingsBtn").addEventListener("click", async () => {
  const endpoint = setEndpoint.value.trim();
  if (!/^https?:\/\//.test(endpoint)) {
    settingsAlert.hidden = false;
    settingsAlert.textContent = "Endpoint must start with http:// or https://";
    return;
  }
  try {
    await apiPut("/api/settings/api", {
      endpoint,
      api_token: setToken.value,
      use_machine_result_api: useApiValue,
    });
    settingsAlert.hidden = true;
    toast("API settings saved.", "success");
    await loadStatus();
  } catch (e) {
    settingsAlert.hidden = false;
    settingsAlert.textContent = e.message || "Failed to save settings.";
  }
});

// ---------------------------------------------------------------------------
// Machine config modal (display name + listen port)
// ---------------------------------------------------------------------------

const configScrim = document.getElementById("configModalScrim");
const cfgLabel = document.getElementById("cfgLabel");
const cfgPort = document.getElementById("cfgPort");
const cfgMachineId = document.getElementById("cfgMachineId");
const cfgIpAddress = document.getElementById("cfgIpAddress");
const cfgKind = document.getElementById("cfgKind");
const cfgColor = document.getElementById("cfgColor");
const cfgColorHex = document.getElementById("cfgColorHex");
const cfgPhoto = document.getElementById("cfgPhoto");
const cfgPhotoPreview = document.getElementById("cfgPhotoPreview");
const cfgPhotoPreviewImg = document.getElementById("cfgPhotoPreviewImg");
const configModalAlert = document.getElementById("configModalAlert");
let configEditingMachine = null;
// Snapshot of the values the modal opened with, so save can send ONLY what
// actually changed. Critical for port: resending an unchanged port makes the
// backend re-apply a runtime override and rebind the listener's socket, which
// drops a live analyzer connection - so a machine-id-only save must NOT touch
// the port.
let configOriginal = { label: "", port: null, machineId: null, kind: "", color: "", ipAddress: "" };

function openConfigModal(m) {
  configEditingMachine = m.machine;
  document.getElementById("configModalTitle").textContent = `${m.label} Settings`;
  cfgLabel.value = m.label || "";
  cfgPort.value = m.port || "";
  cfgMachineId.value = m.machine_id ?? "";
  cfgIpAddress.value = m.ip_address || "";
  cfgKind.value = m.kind || "";
  cfgColor.value = m.color || "#0C8599";
  cfgColorHex.value = m.color || "#0C8599";
  cfgPhoto.value = "";
  if (m.photo) {
    cfgPhotoPreviewImg.src = `/${m.photo}`;
    cfgPhotoPreview.hidden = false;
  } else {
    cfgPhotoPreview.hidden = true;
  }
  configOriginal = {
    label: m.label || "",
    port: m.port ?? null,
    machineId: m.machine_id ?? null,
    kind: m.kind || "",
    color: m.color || "#0C8599",
    ipAddress: m.ip_address || "",
  };
  configModalAlert.hidden = true;
  configScrim.classList.add("open");
  setTimeout(() => cfgLabel.focus(), 80);
}

function closeConfigModal() {
  configScrim.classList.remove("open");
}

document.getElementById("configModalClose").addEventListener("click", closeConfigModal);
document.getElementById("configModalCancel").addEventListener("click", closeConfigModal);
configScrim.addEventListener("click", (e) => { if (e.target === configScrim) closeConfigModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && configScrim.classList.contains("open")) closeConfigModal();
});

cfgColor.addEventListener("input", () => { cfgColorHex.value = cfgColor.value; });
cfgColorHex.addEventListener("input", () => {
  if (/^#[0-9a-fA-F]{6}$/.test(cfgColorHex.value)) cfgColor.value = cfgColorHex.value;
});
cfgPhoto.addEventListener("change", () => {
  const file = cfgPhoto.files[0];
  if (!file) return; // keep showing the existing photo preview
  const reader = new FileReader();
  reader.onload = () => {
    cfgPhotoPreviewImg.src = reader.result;
    cfgPhotoPreview.hidden = false;
  };
  reader.readAsDataURL(file);
});

document.getElementById("configModalSave").addEventListener("click", async () => {
  const label = cfgLabel.value.trim();
  const portValue = cfgPort.value.trim();
  const machineIdValue = cfgMachineId.value.trim();
  const kind = cfgKind.value.trim();
  const color = cfgColorHex.value.trim();
  if (!label) {
    configModalAlert.hidden = false;
    configModalAlert.textContent = "Display name cannot be empty.";
    return;
  }
  const port = portValue ? parseInt(portValue, 10) : null;
  if (port !== null && (Number.isNaN(port) || port < 1024 || port > 65535)) {
    configModalAlert.hidden = false;
    configModalAlert.textContent = "Port must be a number between 1024 and 65535.";
    return;
  }
  const machineId = machineIdValue ? parseInt(machineIdValue, 10) : null;
  if (machineId !== null && Number.isNaN(machineId)) {
    configModalAlert.hidden = false;
    configModalAlert.textContent = "Machine ID must be a number.";
    return;
  }
  const ipAddress = cfgIpAddress.value.trim();

  // Send ONLY what changed. Especially: don't resend an unchanged port, or
  // the backend re-applies the runtime override and rebinds the listener
  // socket, dropping any live analyzer connection - which is exactly what
  // made saving a machine id "break everything" while a machine was connected.
  const form = new FormData();
  let hasChange = false;
  if (label !== configOriginal.label) { form.append("label", label); hasChange = true; }
  if (port !== configOriginal.port) { form.append("port", port === null ? "" : String(port)); hasChange = true; }
  if (machineId !== configOriginal.machineId) { form.append("machine_id", machineId === null ? "" : String(machineId)); hasChange = true; }
  if (ipAddress !== configOriginal.ipAddress) { form.append("ip_address", ipAddress); hasChange = true; }
  if (kind !== configOriginal.kind) { form.append("kind", kind); hasChange = true; }
  if (color !== configOriginal.color) { form.append("color", color); hasChange = true; }
  if (cfgPhoto.files[0]) { form.append("photo", cfgPhoto.files[0]); hasChange = true; }

  if (!hasChange) {
    closeConfigModal();
    toast("No changes to save.", "success");
    return;
  }

  if (port !== configOriginal.port) {
    const proceed = confirm(
      `Changing the listen port takes effect immediately and drops any ` +
      `connection this machine currently has open - if it's mid-transmission ` +
      `right now, that transmission is lost.\n\n` +
      `Change port to ${port ?? "(none)"}?`
    );
    if (!proceed) return;
  }

  try {
    const savedMachine = configEditingMachine;
    const photoChanged = form.has("photo");
    await apiPutForm(`/api/machines/${savedMachine}/config`, form);
    closeConfigModal();
    const portChanged = form.has("port");
    toast(portChanged
      ? `Settings saved for "${label}". Port change applied immediately.`
      : `Settings saved for "${label}".`, "success");
    await loadMachines();
    // An uploaded photo always overwrites machines/<machine>.png, so the URL
    // is byte-identical before and after - the browser would keep serving
    // the OLD cached image, and renderOverview's "src already matches, skip"
    // guard wouldn't catch it either. Force this one card's <img> to refetch
    // with a cache-busting param. Only runs on an actual photo upload, so
    // normal polls still never touch the image.
    if (photoChanged) {
      const img = document.querySelector(
        `.machine-card[data-machine="${savedMachine}"] .card-photo`);
      if (img) img.src = `/machines/${savedMachine}.png?v=${Date.now()}`;
    }
    if (state.activeSection === "mappings" && state.mappingsMachine === configEditingMachine) {
      selectMappingsMachine(configEditingMachine);
    }
  } catch (e) {
    configModalAlert.hidden = false;
    configModalAlert.textContent = e.message || "Failed to save machine settings.";
  }
});

// ---------------------------------------------------------------------------
// Add Analyzer modal
// ---------------------------------------------------------------------------

const addAnalyzerScrim = document.getElementById("addAnalyzerScrim");
const naLabel = document.getElementById("naLabel");
const naKey = document.getElementById("naKey");
const naKind = document.getElementById("naKind");
const naMachineId = document.getElementById("naMachineId");
const naDecoder = document.getElementById("naDecoder");
const naPort = document.getElementById("naPort");
const naColor = document.getElementById("naColor");
const naColorHex = document.getElementById("naColorHex");
const naPhoto = document.getElementById("naPhoto");
const naPhotoPreview = document.getElementById("naPhotoPreview");
const naPhotoPreviewImg = document.getElementById("naPhotoPreviewImg");
const addAnalyzerAlert = document.getElementById("addAnalyzerAlert");

let decoderChoicesLoaded = false;

async function ensureDecoderChoices() {
  if (decoderChoicesLoaded) return;
  try {
    const choices = await apiGet("/api/decoders");
    naDecoder.innerHTML = choices.map((c) =>
      `<option value="${escapeHtml(c.machine)}">${escapeHtml(c.label)} — ${escapeHtml(c.protocol.toUpperCase())}</option>`
    ).join("");
    decoderChoicesLoaded = true;
  } catch (e) {
    naDecoder.innerHTML = `<option value="">Failed to load decoder list</option>`;
  }
}

function openAddAnalyzerModal() {
  naLabel.value = "";
  naKey.value = "";
  naKind.value = "";
  naMachineId.value = "";
  naPort.value = "";
  naColor.value = "#0C8599";
  naColorHex.value = "#0C8599";
  naPhoto.value = "";
  naPhotoPreview.hidden = true;
  addAnalyzerAlert.hidden = true;
  ensureDecoderChoices();
  addAnalyzerScrim.classList.add("open");
  setTimeout(() => naLabel.focus(), 80);
}

function closeAddAnalyzerModal() {
  addAnalyzerScrim.classList.remove("open");
}

document.getElementById("addAnalyzerBtn").addEventListener("click", openAddAnalyzerModal);
document.getElementById("pingAllBtn").addEventListener("click", pingAllMachines);
document.getElementById("addAnalyzerClose").addEventListener("click", closeAddAnalyzerModal);
document.getElementById("addAnalyzerCancel").addEventListener("click", closeAddAnalyzerModal);
addAnalyzerScrim.addEventListener("click", (e) => { if (e.target === addAnalyzerScrim) closeAddAnalyzerModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && addAnalyzerScrim.classList.contains("open")) closeAddAnalyzerModal();
});

// auto-derive a machine key from the display name, but only until the user
// types into the key field themselves (then it's their choice, not ours)
let naKeyTouched = false;
naKey.addEventListener("input", () => { naKeyTouched = true; });
naLabel.addEventListener("input", () => {
  if (naKeyTouched) return;
  naKey.value = naLabel.value.trim().toLowerCase()
    .replace(/[^a-z0-9]+/g, "_").replace(/^_+|_+$/g, "");
});

naColor.addEventListener("input", () => { naColorHex.value = naColor.value; });
naColorHex.addEventListener("input", () => {
  if (/^#[0-9a-fA-F]{6}$/.test(naColorHex.value)) naColor.value = naColorHex.value;
});

naPhoto.addEventListener("change", () => {
  const file = naPhoto.files[0];
  if (!file) { naPhotoPreview.hidden = true; return; }
  const reader = new FileReader();
  reader.onload = () => {
    naPhotoPreviewImg.src = reader.result;
    naPhotoPreview.hidden = false;
  };
  reader.readAsDataURL(file);
});

document.getElementById("addAnalyzerSave").addEventListener("click", async () => {
  const label = naLabel.value.trim();
  const machine = naKey.value.trim().toLowerCase();
  const port = naPort.value.trim();

  if (!label) {
    addAnalyzerAlert.hidden = false;
    addAnalyzerAlert.textContent = "Display name is required.";
    return;
  }
  if (!/^[a-z][a-z0-9_]*$/.test(machine)) {
    addAnalyzerAlert.hidden = false;
    addAnalyzerAlert.textContent = "Machine key must be lowercase letters/numbers/underscore, starting with a letter.";
    return;
  }
  if (!naDecoder.value) {
    addAnalyzerAlert.hidden = false;
    addAnalyzerAlert.textContent = "Pick a protocol/decoder to reuse.";
    return;
  }
  const portNum = parseInt(port, 10);
  if (!port || Number.isNaN(portNum) || portNum < 1024 || portNum > 65535) {
    addAnalyzerAlert.hidden = false;
    addAnalyzerAlert.textContent = "Port must be a number between 1024 and 65535.";
    return;
  }
  const machineIdRaw = naMachineId.value.trim();
  if (machineIdRaw && Number.isNaN(parseInt(machineIdRaw, 10))) {
    addAnalyzerAlert.hidden = false;
    addAnalyzerAlert.textContent = "Clinic machine ID must be a number, or left blank.";
    return;
  }

  const form = new FormData();
  form.append("machine", machine);
  form.append("label", label);
  form.append("kind", naKind.value.trim());
  form.append("reuse_decoder_from", naDecoder.value);
  form.append("port", String(portNum));
  form.append("color", naColorHex.value);
  if (machineIdRaw) form.append("machine_id", String(parseInt(machineIdRaw, 10)));
  if (naPhoto.files[0]) form.append("photo", naPhoto.files[0]);

  const saveBtn = document.getElementById("addAnalyzerSave");
  saveBtn.disabled = true;
  try {
    const res = await fetch("/api/machines", { method: "POST", body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.error || `POST /api/machines -> ${res.status}`);
    closeAddAnalyzerModal();
    toast(`"${label}" added and listening on port ${portNum}.`, "success");
    await loadMachines();
  } catch (e) {
    addAnalyzerAlert.hidden = false;
    addAnalyzerAlert.textContent = e.message || "Failed to add analyzer.";
  } finally {
    saveBtn.disabled = false;
  }
});

// ---------------------------------------------------------------------------
// Sample detail modal (drill-down: matched values + the clinic API JSON)
// ---------------------------------------------------------------------------

const sampleScrim = document.getElementById("sampleModalScrim");

async function openSampleModal(machine, sampleId) {
  document.getElementById("sampleModalTitle").textContent = `Sample ${sampleId}`;
  document.getElementById("sampleMeta").innerHTML = `<span class="muted-cell">Loading…</span>`;
  document.getElementById("sampleResultsBody").innerHTML = "";
  document.getElementById("sampleJson").textContent = "—";
  sampleScrim.classList.add("open");
  // reset to first tab each open
  document.querySelectorAll(".sample-tab").forEach((t) => t.classList.toggle("active", t.dataset.stab === "results"));
  document.querySelectorAll(".sample-tab-panel").forEach((p) => p.classList.toggle("active", p.id === "stab-results"));

  try {
    const data = await apiGet(`/api/samples/${machine}/${encodeURIComponent(sampleId)}`);
    const s = data.sample || {};
    document.getElementById("sampleMeta").innerHTML = `
      <div class="meta-item"><div class="meta-label">Sample ID</div><div class="meta-value">${escapeHtml(sampleId)}</div></div>
      <div class="meta-item"><div class="meta-label">Paillasse</div><div class="meta-value">${escapeHtml(s.paillasse || "—")}</div></div>
      <div class="meta-item"><div class="meta-label">Patient</div><div class="meta-value">${escapeHtml((s.patient_name || "").trim() || "—")}</div></div>
      <div class="meta-item"><div class="meta-label">Analyzer</div><div class="meta-value">${escapeHtml(s.analyzer_model || "—")}</div></div>
      <div class="meta-item"><div class="meta-label">Source IP</div><div class="meta-value">${escapeHtml(s.source_ip || "—")}</div></div>
      <div class="meta-item"><div class="meta-label">Received</div><div class="meta-value">${s.received_at ? new Date(s.received_at.replace(" ", "T")).toLocaleString() : "—"}</div></div>
      <div class="meta-item"><div class="meta-label">Matched results</div><div class="meta-value">${(data.matched || []).length}</div></div>
    `;

    const tbody = document.getElementById("sampleResultsBody");
    const matched = data.matched || [];
    document.getElementById("sampleResultsEmpty").hidden = matched.length > 0;
    tbody.innerHTML = "";
    matched.forEach((r) => {
      const tr = document.createElement("tr");
      const apiStatus = r.api_sent
        ? `<span class="badge badge-success">Sent${r.api_result_id ? " · #" + r.api_result_id : ""}</span>`
        : `<span class="badge">Staged only</span>`;
      const fullParamName = `${r.param_abbrev || ""} ${r.param_name ? "· " + r.param_name : ""}`.trim();
      tr.innerHTML = `
        <td data-label="Code"><span class="code-pill">${escapeHtml(r.test_code)}</span></td>
        <td data-label="Matched To"><div class="param-id-row">${r.param_id !== null && r.param_id !== undefined
          ? `<span class="match-kind-badge match-kind-param" title="Matched by param_id">param_id</span><span class="param-id">#${r.param_id}</span>`
          : `<span class="match-kind-badge match-kind-exam" title="No single param - matched by exam (service_tarification_id) instead">service_tarification_id</span><span class="param-id">#${r.service_tarification_id}</span>`}</div><div class="param-name" title="${escapeHtml(fullParamName)}">${escapeHtml(r.param_abbrev || "")} ${r.param_name ? "· " + escapeHtml(r.param_name) : ""}</div></td>
        <td data-label="Value" class="value-mono">${escapeHtml(r.result_value)} ${escapeHtml(r.unit || "")}</td>
        <td data-label="Received" class="timestamp-cell">${timeAgo(r.received_at)}</td>
        <td data-label="Clinic API">${apiStatus}</td>
      `;
      tbody.appendChild(tr);
    });

    // The clinic-API JSON shape: one item per matched result.
    const jsonPayload = matched.map((r) => {
      const item = { sample_id: sampleId, result_value: r.result_value };
      if (r.unit) item.unit = r.unit;
      if (r.param_id != null) item.param_id = r.param_id;
      else if (r.service_tarification_id != null) item.service_tarification_id = r.service_tarification_id;
      return item;
    });
    document.getElementById("sampleJson").textContent = JSON.stringify(jsonPayload, null, 2);
  } catch (e) {
    document.getElementById("sampleMeta").innerHTML =
      `<span class="muted-cell">Failed to load sample — ${escapeHtml(e.message || "request failed")}.</span>`;
  }
}

function closeSampleModal() { sampleScrim.classList.remove("open"); }
document.getElementById("sampleModalClose").addEventListener("click", closeSampleModal);
sampleScrim.addEventListener("click", (e) => { if (e.target === sampleScrim) closeSampleModal(); });
document.addEventListener("keydown", (e) => {
  if (e.key === "Escape" && sampleScrim.classList.contains("open")) closeSampleModal();
});
document.querySelectorAll(".sample-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".sample-tab").forEach((t) => t.classList.remove("active"));
    document.querySelectorAll(".sample-tab-panel").forEach((p) => p.classList.remove("active"));
    tab.classList.add("active");
    document.getElementById(`stab-${tab.dataset.stab}`).classList.add("active");
  });
});

// ---------------------------------------------------------------------------
// Init
// ---------------------------------------------------------------------------

(async function init() {
  await Promise.all([loadStatus(), loadMachines()]);
  showSection("machines");
  setInterval(loadStatus, 15000);
  // Fast poll while on the Machines page so "listening -> connected" state
  // updates feel near-instant as an analyzer actually connects.
  setInterval(() => {
    if (state.activeSection === "machines") loadMachines();
  }, 2000);
  // Live-refresh the Mappings section too, so Recent Samples / Pending /
  // Mapped update on their own as results stream in - no manual refresh.
  // Skipped while a modal is open (don't yank data out from under an edit)
  // and the mapped-table filter box keeps its text (loadMapped re-applies
  // the current filter value, it doesn't clear it).
  setInterval(() => {
    if (state.activeSection !== "mappings" || !state.mappingsMachine) return;
    if (scrim.classList.contains("open") || configScrim.classList.contains("open")
        || sampleScrim.classList.contains("open")) return;
    const m = state.mappingsMachine;
    loadMapped(m); loadPending(m); loadSamples(m); loadMachines();
  }, 3000);
  // The two intervals above already call loadMachines() while on the
  // Machines or Mappings pages - this covers the one remaining section
  // (API Settings) so the new-sample toast can still fire from there too;
  // slower interval since nothing on that page needs the data itself.
  setInterval(() => {
    if (state.activeSection === "api-settings") loadMachines();
  }, 5000);
})();
