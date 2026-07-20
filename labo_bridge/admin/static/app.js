// Labo Bridge Admin — vanilla JS, no build step, single-operator local tool.

const state = {
  machines: [],
  activeSection: "machines", // "machines" | "mappings" | "api-settings"
  mappingsMachine: null,     // machine whose Mapped/Pending/Samples tabs are shown
  mappings: [],
  pending: [],
  samples: [],
  editingCode: null, // null => "add new", string => "editing existing"
};

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

// ---------------------------------------------------------------------------
// Toast
// ---------------------------------------------------------------------------

let toastTimer = null;
function toast(message, kind = "success") {
  const el = document.getElementById("toast");
  el.textContent = "";
  const dot = document.createElement("span");
  dot.className = "toast-dot";
  el.appendChild(dot);
  const text = document.createElement("span");
  text.textContent = message;
  el.appendChild(text);
  el.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { el.classList.remove("show"); }, 3600);
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
  if (mins < 60) return `${mins}m ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24) return `${hrs}h ago`;
  const days = Math.floor(hrs / 24);
  if (days < 30) return `${days}d ago`;
  return then.toLocaleDateString();
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

async function loadStatus() {
  try {
    const s = await apiGet("/api/status");
    const pgPill = document.getElementById("pgStatusPill");
    pgPill.className = `status-pill ${s.postgres_ok ? "ok" : "err"}`;
    pgPill.querySelector(".status-text").textContent =
      s.postgres_ok ? "Clinic DB connected" : "Clinic DB unreachable";

    const apiPill = document.getElementById("apiStatusPill");
    apiPill.className = `status-pill ${s.use_machine_result_api ? "warn" : "ok"}`;
    apiPill.querySelector(".status-text").textContent =
      s.use_machine_result_api ? "API mode: live push" : "API mode: staging only";
  } catch (e) {
    console.error(e);
  }
}

// ---------------------------------------------------------------------------
// Section navigation (Machines / Mappings / API Settings)
// ---------------------------------------------------------------------------

function showSection(section) {
  state.activeSection = section;
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

async function loadMachines() {
  state.machines = await apiGet("/api/machines");
  if (state.activeSection === "machines") {
    renderOverview();
  }
  if (state.activeSection === "mappings") {
    renderMappingsMachinePicker();
  }
}

function renderOverview() {
  const totalSamples = state.machines.reduce((a, m) => a + m.sample_count, 0);
  const totalMatched = state.machines.reduce((a, m) => a + m.matched_count, 0);
  const totalPending = state.machines.reduce((a, m) => a + m.pending_count, 0);
  const connectedNow = state.machines.filter((m) => m.live_state === "connected").length;

  document.getElementById("overviewStats").innerHTML = `
    <div class="stat-chip"><div class="stat-value">${connectedNow}/${state.machines.length}</div><div class="stat-label">Connected</div></div>
    <div class="stat-chip"><div class="stat-value">${totalSamples}</div><div class="stat-label">Samples</div></div>
    <div class="stat-chip"><div class="stat-value">${totalMatched}</div><div class="stat-label">Matched</div></div>
    <div class="stat-chip"><div class="stat-value">${totalPending}</div><div class="stat-label">Pending</div></div>
  `;

  const grid = document.getElementById("machineGrid");
  grid.innerHTML = "";
  state.machines.forEach((m) => {
    // live_state comes straight from the listener thread itself:
    // "connected" = an analyzer is connected to this port right now,
    // "listening" = the port is open and waiting, "unknown" = the listener
    // isn't running in this process (e.g. admin UI run standalone).
    const liveClass = m.live_state === "connected" ? "connected"
                     : m.live_state === "listening" ? "listening" : "unknown";
    const liveLabel = m.live_state === "connected" ? "Connected"
                     : m.live_state === "listening" ? "Listening" : "Unknown";
    const card = document.createElement("div");
    card.className = "machine-card";
    card.style.setProperty("--m-color", m.color || "");
    const cardBgClass = m.photo_bg === "card" ? " has-photo-card" : "";
    card.innerHTML = `
      <div class="card-photo-frame${cardBgClass}">
        <div class="card-live-badge ${liveClass}">
          <span class="dot"></span>${liveLabel}
        </div>
        <button class="icon-btn card-config-btn" title="Machine settings" type="button">
          <svg viewBox="0 0 24 24" fill="none"><path d="M4 12h4M16 12h4M12 4v4M12 16v4" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="12" r="3.2" stroke="currentColor" stroke-width="1.8"/></svg>
        </button>
        ${m.photo
          ? `<img class="card-photo" src="/${m.photo}" alt="${escapeHtml(m.label)}" loading="lazy">`
          : `<div class="card-avatar">${escapeHtml(initials(m.label))}</div>`}
      </div>
      <h3 class="card-title">${escapeHtml(m.label)}</h3>
      <p class="card-kind">${escapeHtml(m.kind)}</p>
      <div class="card-metrics">
        <div class="card-metric" title="Results received from this analyzer that hit a curated mapping (${m.mapped_codes} code(s) mapped for this machine) — counts every result ever recorded, not distinct codes">
          <div class="card-metric-value matched">${m.matched_count}</div>
          <div class="card-metric-label">Matched</div>
        </div>
        <div class="card-metric" title="Results received with no curated mapping yet — staged for review in Mappings">
          <div class="card-metric-value pending">${m.pending_count}</div>
          <div class="card-metric-label">Pending</div>
        </div>
        <div class="card-metric" title="Distinct samples/orders received from this analyzer">
          <div class="card-metric-value">${m.sample_count}</div>
          <div class="card-metric-label">Samples</div>
        </div>
      </div>
      <div class="card-footer">
        <span class="protocol-tag">${escapeHtml(m.protocol)}</span>
        <span>port ${m.port}</span>
      </div>
    `;
    card.querySelector(".card-config-btn").addEventListener("click", (e) => {
      e.stopPropagation();
      openConfigModal(m);
    });
    card.addEventListener("click", () => {
      state.mappingsMachine = m.machine;
      showSection("mappings");
    });
    grid.appendChild(card);
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
  state.mappingsMachine = machine;
  renderMappingsMachinePicker();
  const mySeq = ++mappingsLoadSeq;

  const meta = state.machines.find((m) => m.machine === machine) || {};
  const avatarEl = document.getElementById("mappingsAvatar");
  avatarEl.style.setProperty("--m-color", meta.color || "");
  avatarEl.innerHTML = avatarHtml(meta, "machine-avatar");
  document.getElementById("mappingsTitle").textContent = meta.label || machine;
  document.getElementById("mappingsSub").textContent =
    "Search the clinic database and match machine test codes to a parameter or exam.";
  document.getElementById("mappingsBadges").innerHTML = `
    <span class="badge">${escapeHtml(meta.protocol || "")}</span>
    <span class="badge">port ${meta.port}</span>
    <span class="badge">${meta.editable ? "editable map" : "aliased map (read-only)"}</span>
  `;

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
      <td><span class="code-pill">${escapeHtml(r.test_code)}</span></td>
      <td><span class="value-mono">${escapeHtml(r.sample_value)} ${escapeHtml(r.sample_unit || "")}</span></td>
      <td>${r.seen_count}×</td>
      <td class="timestamp-cell">${timeAgo(r.last_seen)}</td>
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
async function loadSamples(machine) {
  const rows = await apiGet(`/api/machines/${machine}/samples`);
  if (machine !== state.mappingsMachine) return; // superseded by a newer selection
  state.samples = rows;
  const tbody = document.getElementById("samplesTableBody");
  document.getElementById("samplesEmpty").hidden = state.samples.length > 0;
  tbody.innerHTML = "";
  state.samples.forEach((s) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="code-pill">${escapeHtml(s.sample_id.trim())}</span></td>
      <td>${escapeHtml(s.paillasse || "—")}</td>
      <td>${escapeHtml((s.patient_name || "").trim() || "—")}</td>
      <td class="value-mono">${escapeHtml(s.source_ip || "—")}</td>
      <td class="timestamp-cell">${timeAgo(s.received_at)}</td>
    `;
    tbody.appendChild(tr);
  });
}

function renderMappedTable(filter = "") {
  const tbody = document.getElementById("mappedTableBody");
  const f = filter.trim().toLowerCase();
  const rows = state.mappings.filter((r) =>
    !f || r.code.toLowerCase().includes(f) ||
    (r.name || "").toLowerCase().includes(f) ||
    (r.service_tarification_name || "").toLowerCase().includes(f)
  );
  document.getElementById("mappedEmpty").hidden = rows.length > 0;
  tbody.innerHTML = "";
  rows.forEach((r) => {
    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td><span class="code-pill">${escapeHtml(r.code)}</span></td>
      <td>
        <div class="param-id">${r.param_id !== null ? "#" + r.param_id : "—"}</div>
        <div class="param-name">${escapeHtml(r.abbrev || "")} ${r.name ? "· " + escapeHtml(r.name) : ""}</div>
      </td>
      <td><span class="exam-tag">${escapeHtml(r.service_tarification_name || "—")}</span></td>
      <td>${r.last_value !== null
        ? `<span class="value-mono">${escapeHtml(r.last_value)} ${escapeHtml(r.last_unit || "")}</span>`
        : '<span class="muted-cell">no data yet</span>'}</td>
      <td class="timestamp-cell">${timeAgo(r.last_seen)}</td>
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
  } else {
    matchPickedName.textContent = match.service_tarification_name || "Exam";
    matchPickedMeta.textContent = `Exam #${match.service_tarification_id} · no single-value breakdown`;
  }
  fSearch.value = "";
  searchResults.classList.remove("open");
}

document.getElementById("matchPickedClear").addEventListener("click", () => showPickedMatch(null));

function openEditModal(entry, isNewFromPending = false) {
  state.editingCode = isNewFromPending ? null : entry.code;
  document.getElementById("modalTitle").textContent =
    state.editingCode ? `Edit "${entry.code}"` : `Map "${entry.code}"`;
  fCode.value = entry.code || "";
  fCode.disabled = !!state.editingCode; // code is the key; don't rename in place

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

fSearch.addEventListener("input", () => {
  clearTimeout(searchTimer);
  const q = fSearch.value.trim();
  if (q.length < 2) { searchResults.classList.remove("open"); return; }

  renderSearchMessage("Searching…");
  const mySeq = ++searchSeq;

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
  }, 280);
});

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
const configModalAlert = document.getElementById("configModalAlert");
let configEditingMachine = null;

function openConfigModal(m) {
  configEditingMachine = m.machine;
  document.getElementById("configModalTitle").textContent = `${m.label} Settings`;
  cfgLabel.value = m.label || "";
  cfgPort.value = m.port || "";
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

document.getElementById("configModalSave").addEventListener("click", async () => {
  const label = cfgLabel.value.trim();
  const portValue = cfgPort.value.trim();
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

  try {
    await apiPut(`/api/machines/${configEditingMachine}/config`, { label, port });
    closeConfigModal();
    toast(`Settings saved for "${label}". Port changes apply immediately.`, "success");
    await loadMachines();
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

  const form = new FormData();
  form.append("machine", machine);
  form.append("label", label);
  form.append("kind", naKind.value.trim());
  form.append("reuse_decoder_from", naDecoder.value);
  form.append("port", String(portNum));
  form.append("color", naColorHex.value);
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
})();
