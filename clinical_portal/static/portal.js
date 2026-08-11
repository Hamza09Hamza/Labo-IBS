"use strict";

const portalState = {
  windowSeconds: 10,
  selectedChamber: null,
  data: null,
  loading: false,
  failures: 0,
};

const CHANNEL_MAP = {
  "umec12:101": "hr",
  "umec12:151": "rr",
  "umec12:160": "spo2",
  "umec12:161": "spo2",
  "umec12:162": "spo2",
  "umec12:170": "nibp",
  "umec12:171": "nibp",
  "umec12:172": "nibp",
  "umec12:173": "nibp",
  "umec12:200": "temp",
  "umec12:201": "temp",
  "umec12:202": "temp",
};

const WALL_SLOTS = [
  { source: "umec12", code: "101", short: "HR", label: "Heart rate", unit: "bpm" },
  { source: "umec12", code: "160", short: "SpO₂", label: "Oxygen saturation", unit: "%" },
  { source: "umec12", code: "151", short: "RESP", label: "Respiration", unit: "rpm" },
  { source: "umec12", code: "172", short: "NIBP", label: "Non-invasive pressure", unit: "mmHg", pairWith: ["170", "171"] },
  { source: "umec12", code: "200", short: "TEMP", label: "Temperature", unit: "°C" },
  { source: "wato", code: "MDC_CONC_AWAY_CO2_ET", short: "EtCO₂", label: "End-tidal CO₂", unit: "mmHg" },
];

const ROOM_COLORS = ["#0c8599", "#7c3aed", "#f59e0b"];
function roomColor(chamberId) {
  return ROOM_COLORS[(Number(chamberId) - 1) % ROOM_COLORS.length];
}

const $ = (selector) => document.querySelector(selector);

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#039;");
}

function channelFor(source, code) {
  return CHANNEL_MAP[`${source}:${code}`] || (source === "wato" ? "vent" : "hr");
}

function formatValue(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) return "—";
  const number = Number(value);
  const absolute = Math.abs(number);
  if (absolute >= 100 || Number.isInteger(number)) return number.toFixed(0);
  if (absolute >= 10) return number.toFixed(1).replace(/\.0$/, "");
  return number.toFixed(2).replace(/0+$/, "").replace(/\.$/, "");
}

function formatAge(iso) {
  if (!iso) return "Never received";
  const seconds = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / 1000));
  if (seconds < 2) return "Just now";
  if (seconds < 60) return `${seconds}s ago`;
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  return `${Math.floor(minutes / 60)}h ago`;
}

function stateLabel(state) {
  return ({ live: "Live", stale: "Delayed", alarm: "Alarm", offline: "Offline" })[state] || state;
}

function patientLabel(patient) {
  if (!patient || (!patient.name && !patient.id)) return "";
  return patient.name || `Patient ${patient.id}`;
}

function parameterFor(chamber, source, code) {
  const device = chamber.devices.find((item) => item.source === source);
  return device?.parameters.find((item) => item.code === code) || null;
}

function alarmSeverity(chamber) {
  if (chamber.alarms.some((alarm) => alarm.level !== "technical")) return "physiological";
  return chamber.alarms.length ? "technical" : null;
}

function trendPolyline(history, width = 100, height = 30, padding = 2) {
  const values = (history || [])
    .filter((item) => Number.isFinite(Number(item.value)))
    .slice(-30)
    .map((item) => Number(item.value));
  if (!values.length) return "";
  if (values.length === 1) return `0,${height / 2} ${width},${height / 2}`;
  const minimum = Math.min(...values);
  const maximum = Math.max(...values);
  const spread = maximum - minimum || 1;
  return values.map((value, index) => {
    const x = (index / (values.length - 1)) * width;
    const y = padding + (1 - (value - minimum) / spread) * (height - padding * 2);
    return `${x.toFixed(1)},${y.toFixed(1)}`;
  }).join(" ");
}

function rangeText(parameter) {
  if (!parameter?.valid) return "No signal";
  if (portalState.windowSeconds === 0) return formatAge(parameter.last_seen);
  if (!parameter.count) return "No samples";
  return `${formatValue(parameter.min)}–${formatValue(parameter.max)}`;
}

function displayReading(chamber, slot) {
  const parameter = parameterFor(chamber, slot.source, slot.code);
  if (!parameter?.valid) return { parameter, valid: false, value: "NO SIGNAL", unit: "" };

  if (slot.pairWith) {
    const systolic = parameterFor(chamber, slot.source, slot.pairWith[0]);
    const diastolic = parameterFor(chamber, slot.source, slot.pairWith[1]);
    if (systolic?.valid && diastolic?.valid) {
      return {
        parameter,
        valid: true,
        value: `${formatValue(systolic.latest)}/${formatValue(diastolic.latest)}<small>(${formatValue(parameter.latest)})</small>`,
        unit: parameter.unit || slot.unit,
      };
    }
  }

  return {
    parameter,
    valid: true,
    value: formatValue(parameter.latest),
    unit: parameter.unit || slot.unit,
  };
}

function renderRoomTabs(chambers) {
  const container = $("#roomTabs");
  const signature = chambers.map((room) => `${room.id}:${room.code}:${room.name}:${room.configuration?.color}:${room.state}:${patientLabel(room.patient)}`).join("|");
  const tabs = [`
    <p class="nav-label">Live supervision</p>
    <button class="side-nav-item" type="button" data-tab="all">
      <span class="side-nav-icon" aria-hidden="true"><svg viewBox="0 0 24 24" fill="none"><rect x="3" y="4" width="8" height="7" rx="2" stroke="currentColor" stroke-width="1.7"/><rect x="13" y="4" width="8" height="7" rx="2" stroke="currentColor" stroke-width="1.7"/><rect x="3" y="13" width="8" height="7" rx="2" stroke="currentColor" stroke-width="1.7"/><rect x="13" y="13" width="8" height="7" rx="2" stroke="currentColor" stroke-width="1.7"/></svg></span>
      <span class="side-nav-copy"><strong>Overview</strong><small>All operation blocks</small></span>
    </button>
    <p class="nav-label rooms-label">Operation blocks</p>`]
    .concat(chambers.map((room) => `
      <button class="side-nav-item" type="button" data-tab="${room.id}" data-state="${escapeHtml(room.state)}" style="--room-color:${escapeHtml(room.configuration?.color || roomColor(room.id))}">
        <span class="side-nav-icon room-index" aria-hidden="true">${String(room.id).padStart(2, "0")}</span>
        <span class="side-nav-copy"><strong>${escapeHtml(room.name)}</strong><small>${escapeHtml(patientLabel(room.patient) || room.code)}</small></span>
        <span class="nav-state-dot" aria-hidden="true"></span>
      </button>`));
  if (container.dataset.signature !== signature) {
    container.innerHTML = tabs.join("");
    container.dataset.signature = signature;
    container.querySelectorAll(".side-nav-item").forEach((tab) => {
      tab.addEventListener("click", () => {
        if (tab.dataset.tab === "all") closeChamber(true);
        else openChamber(Number(tab.dataset.tab), true);
        closeDrawer();
      });
    });
  }
  document.querySelectorAll(".side-nav-item").forEach((tab) => {
    const active = tab.dataset.tab === "all"
      ? portalState.selectedChamber === null
      : Number(tab.dataset.tab) === portalState.selectedChamber;
    tab.classList.toggle("active", active);
    if (active) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
}

function renderCardAlarm(chamber) {
  if (!chamber.alarms.length) return "";
  const technical = alarmSeverity(chamber) === "technical";
  return `
    <div class="card-alarm ${technical ? "technical" : ""}">
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 9v4M12 17h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M10.3 4.4L2.8 17.3A2 2 0 004.5 20h15a2 2 0 001.7-2.7L13.7 4.4a2 2 0 00-3.4 0z" stroke="currentColor" stroke-width="1.7"/></svg>
      <strong>${chamber.alarms.length} ${technical ? "technical" : "physiological"} alarm${chamber.alarms.length === 1 ? "" : "s"} · ${escapeHtml(chamber.alarms[0].text)}</strong>
    </div>`;
}

function renderMetric(chamber, slot) {
  const reading = displayReading(chamber, slot);
  const points = reading.parameter?.valid ? trendPolyline(reading.parameter.history, 100, 18, 1) : "";
  return `
    <div class="metric-block ${reading.valid ? "" : "no-signal"}" data-channel="${channelFor(slot.source, slot.code)}">
      <div class="metric-label">${escapeHtml(slot.short)}</div>
      <div class="metric-value"><strong>${reading.valid ? reading.value : "NO SIGNAL"}</strong>${reading.valid ? `<span>${escapeHtml(reading.unit)}</span>` : ""}</div>
      <svg class="micro-trend" viewBox="0 0 100 18" preserveAspectRatio="none" aria-hidden="true">${points ? `<polyline points="${points}"/>` : ""}</svg>
    </div>`;
}

function renderSupportReading(chamber, slot, extraClass = "") {
  const reading = displayReading(chamber, slot);
  return `<span class="support-reading ${extraClass}"><span>${escapeHtml(slot.short)}</span><strong>${reading.valid ? `${reading.value} ${escapeHtml(reading.unit)}` : "—"}</strong></span>`;
}

function machineFallbackIcon(source) {
  if (source === "umec12") {
    return `<svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><rect x="6" y="7" width="36" height="27" rx="5" stroke="currentColor" stroke-width="2"/><path d="M12 22h7l3-8 5 15 3-7h6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/><path d="M17 41h14M24 34v7" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
  }
  return `<svg viewBox="0 0 48 48" fill="none" aria-hidden="true"><rect x="11" y="5" width="26" height="35" rx="5" stroke="currentColor" stroke-width="2"/><rect x="16" y="10" width="16" height="11" rx="2" stroke="currentColor" stroke-width="2"/><path d="M17 28h14M17 33h9" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M16 40v4M32 40v4" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>`;
}

function renderMachineTile(chamber, source) {
  const configuration = chamber.configuration?.machines?.[source] || {};
  const device = chamber.devices.find((item) => item.source === source) || {};
  const enabled = Boolean(configuration.enabled);
  const state = enabled ? (device.state === "live" ? "live" : "waiting") : "disabled";
  const stateText = state === "live" ? "Connected" : state === "waiting" ? "Waiting" : "Disabled";
  const connection = source === "umec12"
    ? `Bridge:${configuration.local_port || "auto"} → ${configuration.ip || "Monitor IP not set"}:4601`
    : `${configuration.ip || "Bridge IP not set"}:${configuration.port || "—"} · listener`;
  const photo = configuration.photo
    ? `<img class="machine-photo" src="/${escapeHtml(configuration.photo)}" alt="${escapeHtml(configuration.label || source)}">`
    : `<div class="machine-placeholder">${machineFallbackIcon(source)}</div>`;
  return `
    <article class="machine-tile">
      <div class="machine-photo-frame">
        <span class="machine-status ${state}">${stateText}</span>
        <button class="machine-config-button" type="button" data-config-block="${chamber.id}" data-config-source="${source}" aria-label="Configure ${escapeHtml(configuration.label || source)}">
          <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><circle cx="12" cy="12" r="3" stroke="currentColor" stroke-width="1.7"/><path d="M19.4 15a1.7 1.7 0 00.34 1.88l.05.05-2.86 2.86-.05-.05A1.7 1.7 0 0015 19.4a1.7 1.7 0 00-1 1.55V21h-4v-.05a1.7 1.7 0 00-1-1.55 1.7 1.7 0 00-1.88.34l-.05.05-2.86-2.86.05-.05A1.7 1.7 0 004.6 15a1.7 1.7 0 00-1.55-1H3v-4h.05A1.7 1.7 0 004.6 9a1.7 1.7 0 00-.34-1.88l-.05-.05 2.86-2.86.05.05A1.7 1.7 0 009 4.6a1.7 1.7 0 001-1.55V3h4v.05a1.7 1.7 0 001 1.55 1.7 1.7 0 001.88-.34l.05-.05 2.86 2.86-.05.05A1.7 1.7 0 0019.4 9a1.7 1.7 0 001.55 1H21v4h-.05a1.7 1.7 0 00-1.55 1z" stroke="currentColor" stroke-width="1.35" stroke-linejoin="round"/></svg>
        </button>
        ${photo}
      </div>
      <div class="machine-tile-body">
        <h3>${escapeHtml(configuration.label || device.label || source)}</h3>
        <p class="machine-kind">${escapeHtml(configuration.kind || device.kind || "Clinical device")}</p>
        <div class="machine-connection"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M8 12a4 4 0 018 0M5 9a8 8 0 0114 0M12 17h.01" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg><span>${escapeHtml(connection)}</span></div>
      </div>
    </article>`;
}

function renderMonitor(chamber) {
  const patient = patientLabel(chamber.patient);
  const color = chamber.configuration?.color || roomColor(chamber.id);
  return `
    <section class="block-card" data-chamber="${chamber.id}" data-state="${escapeHtml(chamber.state)}" style="--block-color:${escapeHtml(color)}">
      <header class="block-card-head">
        <span class="block-index">${escapeHtml(chamber.code)}</span>
        <div class="block-heading"><h2>${escapeHtml(chamber.name)}</h2><p>Patient monitor + anesthesia workstation</p></div>
        <span class="block-state">${escapeHtml(stateLabel(chamber.state))}</span>
      </header>
      <div class="block-machine-grid">${renderMachineTile(chamber, "umec12")}${renderMachineTile(chamber, "wato")}</div>
      <div class="block-summary">
        <div class="block-patient"><span>Patient context</span><strong>${patient ? escapeHtml(patient) : "Not received"}</strong></div>
        <span class="block-alarm-count ${chamber.alarms.length ? "has-alarm" : ""}">${chamber.alarms.length} alarm${chamber.alarms.length === 1 ? "" : "s"}</span>
      </div>
      <footer class="block-card-footer"><button class="block-monitor-button" type="button" data-open-block="${chamber.id}"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M3 12h4l2-6 5 12 2-6h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>Open live monitoring</button></footer>
    </section>`;
}

function renderSummary(chambers) {
  const liveRooms = chambers.filter((room) => room.state === "live" || room.state === "alarm").length;
  const alarmCount = chambers.reduce((sum, room) => sum + room.alarms.length, 0);
  const devicesOnline = chambers.reduce(
    (sum, room) => sum + room.devices.filter((device) => device.state === "live").length,
    0,
  );
  $("#overviewSummary").innerHTML = `
    <div class="stat-chip"><strong class="tabular">${liveRooms}/${chambers.length}</strong><span>Blocks online</span></div>
    <div class="stat-chip"><strong class="tabular">${alarmCount}</strong><span>Active alarms</span></div>
    <div class="stat-chip"><strong class="tabular">${devicesOnline}/${chambers.length * 2}</strong><span>Devices live</span></div>`;
}

function sparkline(history, cssClass = "sparkline", height = 32) {
  const points = trendPolyline(history, 100, height, 1);
  if (!points) return `<svg class="${cssClass}" viewBox="0 0 100 ${height}" preserveAspectRatio="none" aria-hidden="true"><line class="baseline" x1="0" y1="${height - 1}" x2="100" y2="${height - 1}"/></svg>`;
  const endpoint = points.trim().split(" ").at(-1).split(",");
  const area = `M ${points.replaceAll(" ", " L ")} L 100 ${height} L 0 ${height} Z`;
  return `
    <svg class="${cssClass}" viewBox="0 0 100 ${height}" preserveAspectRatio="none" aria-hidden="true">
      <line class="baseline" x1="0" y1="${height - 1}" x2="100" y2="${height - 1}"/>
      <path class="area" d="${area}"/><polyline points="${points}"/><circle cx="${endpoint[0]}" cy="${endpoint[1]}" r="1.2"/>
    </svg>`;
}

function renderSignalCard(chamber, slot) {
  const reading = displayReading(chamber, slot);
  const parameter = reading.parameter;
  const summary = portalState.windowSeconds === 0
    ? { mean: parameter?.latest, min: parameter?.latest, max: parameter?.latest }
    : parameter || {};
  return `
    <article class="signal-panel" data-channel="${channelFor(slot.source, slot.code)}">
      <div class="signal-panel-head">
        <div class="signal-panel-label"><strong>${escapeHtml(slot.short)}</strong><span>${escapeHtml(slot.label)}</span></div>
        <span class="signal-panel-range">${escapeHtml(rangeText(parameter))}</span>
      </div>
      <div class="signal-reading ${reading.valid ? "" : "no-signal"}">
        <strong>${reading.valid ? reading.value : "NO SIGNAL"}</strong>
        ${reading.valid ? `<span>${escapeHtml(reading.unit)}</span>` : ""}
      </div>
      ${sparkline(parameter?.history, "signal-chart", 40)}
      <div class="signal-stats">
        <span>Mean<strong>${formatValue(summary.mean)}</strong></span>
        <span>Minimum<strong>${formatValue(summary.min)}</strong></span>
        <span>Maximum<strong>${formatValue(summary.max)}</strong></span>
      </div>
    </article>`;
}

function parameterCard(source, parameter) {
  const summary = portalState.windowSeconds === 0
    ? { mean: parameter.latest, min: parameter.latest, max: parameter.latest }
    : parameter;
  return `
    <article class="parameter-card" data-channel="${channelFor(source, parameter.code)}">
      <div class="parameter-card-top">
        <div class="parameter-card-label"><strong>${escapeHtml(parameter.short)}</strong><span>${escapeHtml(parameter.label)}</span></div>
      </div>
      <div class="parameter-reading ${parameter.valid ? "" : "no-signal"}">
        <strong>${parameter.valid ? formatValue(parameter.latest) : "NO SIGNAL"}</strong>
        ${parameter.valid ? `<span>${escapeHtml(parameter.unit)}</span>` : ""}
      </div>
      ${sparkline(parameter.history)}
      <div class="parameter-summary">
        <span>Mean<strong>${formatValue(summary.mean)}</strong></span>
        <span>Min<strong>${formatValue(summary.min)}</strong></span>
        <span>Max<strong>${formatValue(summary.max)}</strong></span>
      </div>
    </article>`;
}

function renderDeviceSection(device) {
  const parameters = device.parameters.length
    ? `<div class="parameter-grid">${device.parameters.map((parameter) => parameterCard(device.source, parameter)).join("")}</div>`
    : `<div class="device-empty">No measurements have been received from this device.</div>`;
  return `
    <section class="device-section">
      <header class="device-section-head">
        <div class="device-identity"><strong>${escapeHtml(device.label)}</strong><small>${escapeHtml(device.kind)} · ${escapeHtml(formatAge(device.last_seen))}</small></div>
        <span class="device-live-state ${escapeHtml(device.state)}">${escapeHtml(stateLabel(device.state))}</span>
      </header>
      ${parameters}
    </section>`;
}

function renderAlarmBand(chamber) {
  if (!chamber.alarms.length) return "";
  const technical = alarmSeverity(chamber) === "technical";
  return `
    <div class="detail-alert ${technical ? "technical" : ""}">
      <svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M12 9v4M12 17h.01" stroke="currentColor" stroke-width="2" stroke-linecap="round"/><path d="M10.3 4.4L2.8 17.3A2 2 0 004.5 20h15a2 2 0 001.7-2.7L13.7 4.4a2 2 0 00-3.4 0z" stroke="currentColor" stroke-width="1.7"/></svg>
      <div><strong>${technical ? "Technical / low priority" : "Physiological alarm"} · ${chamber.alarms.length}</strong><p>${chamber.alarms.map((alarm) => `${escapeHtml(alarm.text)} · ${escapeHtml(alarm.source)}`).join(" &nbsp;•&nbsp; ")}</p></div>
    </div>`;
}

function renderDetail(chamber) {
  const restoreBackFocus = document.activeElement?.id === "detailBack";
  const patient = patientLabel(chamber.patient);
  const chamberColor = chamber.configuration?.color || roomColor(chamber.id);
  const picker = (portalState.data?.chambers || []).map((room) => `<button class="room-pill ${room.id === chamber.id ? "active" : ""}" type="button" data-room-picker="${room.id}" style="--room-color:${escapeHtml(room.configuration?.color || roomColor(room.id))}"><span class="room-pill-index">${String(room.id).padStart(2, "0")}</span>${escapeHtml(room.name)}</button>`).join("");
  $("#roomDetail").innerHTML = `
    <header class="detail-header">
      <button class="back-button" type="button" id="detailBack" aria-label="Back to all operating rooms"><svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M15 18l-6-6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg></button>
      <div class="room-avatar" style="--room-color:${escapeHtml(chamberColor)}">${escapeHtml(chamber.code)}</div>
      <div class="detail-title"><h1>${escapeHtml(chamber.name)}</h1><p>${escapeHtml(chamber.code)} · ${escapeHtml(portalState.windowSeconds ? `${portalState.windowSeconds}s rolling trend` : "latest readings")}</p></div>
      <div class="detail-badges"><span class="detail-badge ${escapeHtml(chamber.state)}">${escapeHtml(stateLabel(chamber.state))}</span><span class="detail-badge patient">${patient ? escapeHtml(patient) : "Patient not received"}</span><span class="detail-badge patient tabular">${chamber.patient?.id ? escapeHtml(chamber.patient.id) : "No ID"}</span></div>
    </header>
    <nav class="room-picker" aria-label="Switch operating room">${picker}</nav>
    <div class="detail-window-toolbar"><span>Live trend window</span><div class="window-switch" id="windowSwitch">
      <button type="button" data-window="0" class="${portalState.windowSeconds === 0 ? "active" : ""}">Now</button>
      <button type="button" data-window="10" class="${portalState.windowSeconds === 10 ? "active" : ""}">10 sec</button>
      <button type="button" data-window="20" class="${portalState.windowSeconds === 20 ? "active" : ""}">20 sec</button>
      <button type="button" data-window="30" class="${portalState.windowSeconds === 30 ? "active" : ""}">30 sec</button>
      <button type="button" data-window="60" class="${portalState.windowSeconds === 60 ? "active" : ""}">1 min</button>
    </div></div>
    ${renderAlarmBand(chamber)}
    <div class="section-heading"><div><h2>Signal overview</h2><p>Current readings with short-term movement</p></div><span>${escapeHtml(portalState.windowSeconds ? `${portalState.windowSeconds} SEC` : "NOW")}</span></div>
    <div class="signal-grid">${WALL_SLOTS.map((slot) => renderSignalCard(chamber, slot)).join("")}</div>
    <div class="section-heading"><div><h2>Source parameters</h2><p>Full measurement set by originating device</p></div></div>
    <div class="device-sections">${chamber.devices.map(renderDeviceSection).join("")}</div>`;
  $("#detailBack").addEventListener("click", () => closeChamber(true));
  document.querySelectorAll("[data-room-picker]").forEach((button) => button.addEventListener("click", () => openChamber(Number(button.dataset.roomPicker), true)));
  if (restoreBackFocus) $("#detailBack").focus({ preventScroll: true });
}

function render(data) {
  portalState.data = data;
  $("#demoBanner").hidden = !data.demo_mode;
  $("#simulationStatus").hidden = !data.demo_mode;
  renderRoomTabs(data.chambers);
  renderSummary(data.chambers);
  $("#lastUpdated").textContent = formatAge(data.generated_at);
  const focusedBlock = document.activeElement?.dataset?.openBlock;
  $("#roomBoard").innerHTML = data.chambers.map(renderMonitor).join("");
  document.querySelectorAll("[data-open-block]").forEach((button) => {
    button.addEventListener("click", () => openChamber(Number(button.dataset.openBlock), true));
  });
  document.querySelectorAll("[data-config-block]").forEach((button) => {
    button.addEventListener("click", () => openMachineConfiguration(Number(button.dataset.configBlock), button.dataset.configSource));
  });
  if (focusedBlock) {
    document.querySelector(`[data-open-block="${focusedBlock}"]`)?.focus({ preventScroll: true });
  }
  if (portalState.selectedChamber !== null) {
    const chamber = data.chambers.find((item) => item.id === portalState.selectedChamber);
    if (chamber) renderDetail(chamber);
    else closeChamber(false);
  }
}

async function loadPortal() {
  if (portalState.loading) return;
  portalState.loading = true;
  try {
    const response = await fetch(`/api/chambers?window=${portalState.windowSeconds}`, { cache: "no-store" });
    if (!response.ok) throw new Error(`GET /api/chambers returned ${response.status}`);
    const data = await response.json();
    portalState.failures = 0;
    $("#portalError").hidden = true;
    const health = $("#bridgeHealth");
    health.className = "status-pill live";
    health.querySelector("span:last-child").textContent = data.demo_mode ? "Simulation feed" : "Live feed";
    render(data);
  } catch (error) {
    portalState.failures += 1;
    console.error(error);
    if (portalState.failures >= 2) {
      $("#portalError").hidden = false;
      const health = $("#bridgeHealth");
      health.className = "status-pill error";
      health.querySelector("span:last-child").textContent = "Feed unavailable";
    }
  } finally {
    portalState.loading = false;
  }
}

function updateView(scrollToTop = false) {
  const hasSelection = portalState.selectedChamber !== null;
  $("#overviewView").classList.toggle("active", !hasSelection);
  $("#detailView").classList.toggle("active", hasSelection);
  document.querySelectorAll(".side-nav-item").forEach((tab) => {
    const active = tab.dataset.tab === "all"
      ? !hasSelection
      : Number(tab.dataset.tab) === portalState.selectedChamber;
    tab.classList.toggle("active", active);
    if (active) tab.setAttribute("aria-current", "page");
    else tab.removeAttribute("aria-current");
  });
  if (scrollToTop) window.scrollTo({ top: 0, behavior: "smooth" });
}

function openChamber(chamberId, pushHistory = false) {
  portalState.selectedChamber = chamberId;
  const chamber = portalState.data?.chambers.find((item) => item.id === chamberId);
  if (pushHistory && location.hash !== `#chamber-${chamberId}`) history.pushState({ chamberId }, "", `#chamber-${chamberId}`);
  if (chamber) renderDetail(chamber);
  closeDrawer();
  updateView(true);
}

function closeChamber(pushHistory = false) {
  portalState.selectedChamber = null;
  if (pushHistory && location.hash) history.pushState({}, "", `${location.pathname}${location.search}`);
  updateView(true);
}

function chamberFromHash() {
  const match = location.hash.match(/^#chamber-(\d+)$/);
  return match ? Number(match[1]) : null;
}

function selectWindow(seconds) {
  portalState.windowSeconds = seconds;
  document.querySelectorAll("#windowSwitch button").forEach((button) => {
    button.classList.toggle("active", Number(button.dataset.window) === seconds);
  });
  const explanation = $("#windowExplain");
  if (explanation) {
    explanation.textContent = seconds === 0
      ? "Showing the latest valid reading"
      : `Rolling range from the last ${seconds === 60 ? "minute" : `${seconds} seconds`}`;
  }
  loadPortal();
}

const machineConfigState = { blockId: null, source: null };
let toastTimer = null;

function showToast(message, kind = "success") {
  const toast = $("#toast");
  $("#toastText").textContent = message;
  toast.className = `toast show ${kind}`;
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => toast.classList.remove("show"), 3600);
}

function closeMachineConfiguration() {
  const scrim = $("#machineConfigScrim");
  scrim.classList.remove("open");
  scrim.setAttribute("aria-hidden", "true");
  document.body.classList.remove("modal-open");
}

function openMachineConfiguration(blockId, source) {
  const chamber = portalState.data?.chambers.find((item) => item.id === blockId);
  const block = chamber?.configuration;
  const machine = block?.machines?.[source];
  if (!chamber || !block || !machine) return;

  machineConfigState.blockId = blockId;
  machineConfigState.source = source;
  $("#machineConfigTitle").textContent = `${machine.label} settings`;
  $("#machineSummaryName").textContent = machine.label;
  $("#machineSummaryKind").textContent = `${block.name} · ${machine.kind}`;
  $("#configBlockName").value = block.name;
  $("#configMachineLabel").value = machine.label;
  $("#configMachineKind").value = machine.kind;
  $("#configMachinePort").value = machine.port || "";
  $("#configLocalPort").value = machine.local_port || "";
  $("#configMachineIp").value = machine.ip || "";
  $("#configMachineEnabled").checked = Boolean(machine.enabled);
  $("#configBlockColor").value = block.color;
  $("#configBlockColorHex").value = block.color;
  $("#configMachinePhoto").value = "";
  $("#configIpField").hidden = false;
  $("#configLocalPortField").hidden = source !== "umec12";
  const portInput = $("#configMachinePort");
  portInput.readOnly = source === "umec12";
  portInput.setAttribute("aria-readonly", source === "umec12" ? "true" : "false");
  $("#configPortLabel").textContent = source === "umec12" ? "uMEC12 PDS service port" : "Bridge listening port";
  $("#configPortHelp").textContent = source === "umec12"
    ? "Fixed by the uMEC12 PDS protocol at 4601."
    : "Enter this same port in the WATO Network Protocol settings.";
  $("#configIpLabel").textContent = source === "umec12" ? "uMEC12 IP address" : "Bridge destination IP";
  $("#configMachineIp").placeholder = source === "umec12" ? "192.168.1.113" : "192.168.1.100";
  $("#configIpHelp").textContent = source === "umec12"
    ? "The network address assigned to this patient monitor."
    : "Enter this same address as Destination IP on the WATO EX-35.";
  $("#machineConfigAlert").hidden = true;

  const previewImage = $("#machinePhotoPreviewImage");
  const previewFallback = $("#machinePhotoFallback");
  if (machine.photo) {
    previewImage.src = `/${machine.photo}?v=${Date.now()}`;
    previewImage.alt = machine.label;
    previewImage.hidden = false;
    previewFallback.hidden = true;
  } else {
    previewImage.hidden = true;
    previewFallback.hidden = false;
    previewFallback.textContent = source === "umec12" ? "PM" : "AW";
  }

  const scrim = $("#machineConfigScrim");
  scrim.classList.add("open");
  scrim.setAttribute("aria-hidden", "false");
  document.body.classList.add("modal-open");
  setTimeout(() => $("#configBlockName").focus(), 80);
}

async function saveMachineConfiguration() {
  const blockId = machineConfigState.blockId;
  const source = machineConfigState.source;
  if (!blockId || !source) return;

  const blockName = $("#configBlockName").value.trim();
  const label = $("#configMachineLabel").value.trim();
  const kind = $("#configMachineKind").value.trim();
  const port = Number($("#configMachinePort").value);
  const localPortValue = $("#configLocalPort").value.trim();
  const localPort = localPortValue ? Number(localPortValue) : null;
  const ipAddress = $("#configMachineIp").value.trim();
  const color = $("#configBlockColorHex").value.trim();
  const enabled = $("#configMachineEnabled").checked;
  const alert = $("#machineConfigAlert");

  if (!blockName || !label || !kind) {
    alert.textContent = "Block name, machine display name, and machine type are required.";
    alert.hidden = false;
    return;
  }
  if (!Number.isInteger(port) || port < 1 || port > 65535) {
    alert.textContent = "Port must be a whole number between 1 and 65535.";
    alert.hidden = false;
    return;
  }
  if (source === "umec12" && localPort !== null && (!Number.isInteger(localPort) || localPort < 1 || localPort > 65535)) {
    alert.textContent = "Bridge source port must be blank or a whole number between 1 and 65535.";
    alert.hidden = false;
    return;
  }
  if (!/^#[0-9a-fA-F]{6}$/.test(color)) {
    alert.textContent = "Block accent must be a six-digit hex color.";
    alert.hidden = false;
    return;
  }
  if (enabled && !ipAddress) {
    alert.textContent = source === "umec12"
      ? "An enabled uMEC12 requires its monitor IP address."
      : "An enabled WATO requires the bridge destination IP configured on the machine.";
    alert.hidden = false;
    return;
  }

  const form = new FormData();
  form.append("block_name", blockName);
  form.append("label", label);
  form.append("kind", kind);
  if (source === "wato") form.append("port", String(port));
  if (source === "umec12") form.append("local_port", localPort === null ? "" : String(localPort));
  form.append("enabled", enabled ? "true" : "false");
  form.append("color", color);
  form.append("ip", ipAddress);
  const photo = $("#configMachinePhoto").files[0];
  if (photo) form.append("photo", photo);

  const saveButton = $("#machineConfigSave");
  saveButton.disabled = true;
  saveButton.textContent = "Saving…";
  alert.hidden = true;
  try {
    const response = await fetch(`/api/blocks/${blockId}/machines/${source}/config`, { method: "PUT", body: form });
    const result = await response.json().catch(() => ({}));
    if (!response.ok) throw new Error(result.error || `Save failed with ${response.status}`);
    closeMachineConfiguration();
    showToast(`${label} settings saved. Restart the bridge to apply connection changes.`);
    await loadPortal();
  } catch (error) {
    alert.textContent = error.message || "Could not save machine settings.";
    alert.hidden = false;
  } finally {
    saveButton.disabled = false;
    saveButton.textContent = "Save settings";
  }
}

function setTheme(theme) {
  document.documentElement.dataset.theme = theme;
  localStorage.setItem("operationbloc-bridge-theme", theme);
  const light = theme === "light";
  $("#themeToggle").setAttribute("aria-label", light ? "Switch to dark theme" : "Switch to light theme");
  document.querySelector('meta[name="theme-color"]').content = light ? "#eef6f7" : "#070b10";
}

function openDrawer() {
  $(".clinical-app").classList.add("nav-open");
  $("#drawerScrim").classList.add("open");
  $("#navToggle").setAttribute("aria-expanded", "true");
}

function closeDrawer() {
  $(".clinical-app").classList.remove("nav-open");
  $("#drawerScrim").classList.remove("open");
  $("#navToggle").setAttribute("aria-expanded", "false");
}

setTheme(localStorage.getItem("operationbloc-bridge-theme") || "light");

$("#themeToggle").addEventListener("click", () => {
  setTheme(document.documentElement.dataset.theme === "dark" ? "light" : "dark");
});
$("#navToggle").addEventListener("click", () => {
  if ($(".clinical-app").classList.contains("nav-open")) closeDrawer();
  else openDrawer();
});
$("#drawerClose").addEventListener("click", closeDrawer);
$("#drawerScrim").addEventListener("click", closeDrawer);
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") {
    closeDrawer();
    closeMachineConfiguration();
  }
});
document.addEventListener("click", (event) => {
  const button = event.target.closest("button[data-window]");
  if (button) selectWindow(Number(button.dataset.window));
});
$("#machineConfigClose").addEventListener("click", closeMachineConfiguration);
$("#machineConfigCancel").addEventListener("click", closeMachineConfiguration);
$("#machineConfigSave").addEventListener("click", saveMachineConfiguration);
$("#machineConfigScrim").addEventListener("click", (event) => {
  if (event.target === $("#machineConfigScrim")) closeMachineConfiguration();
});
$("#configBlockColor").addEventListener("input", () => {
  $("#configBlockColorHex").value = $("#configBlockColor").value.toUpperCase();
});
$("#configBlockColorHex").addEventListener("input", () => {
  const value = $("#configBlockColorHex").value;
  if (/^#[0-9a-fA-F]{6}$/.test(value)) $("#configBlockColor").value = value;
});
$("#configMachinePhoto").addEventListener("change", () => {
  const file = $("#configMachinePhoto").files[0];
  if (!file) return;
  const reader = new FileReader();
  reader.onload = () => {
    $("#machinePhotoPreviewImage").src = reader.result;
    $("#machinePhotoPreviewImage").hidden = false;
    $("#machinePhotoFallback").hidden = true;
  };
  reader.readAsDataURL(file);
});
$("#retryPortal").addEventListener("click", loadPortal);
window.addEventListener("popstate", () => {
  portalState.selectedChamber = chamberFromHash();
  const chamber = portalState.data?.chambers.find((item) => item.id === portalState.selectedChamber);
  if (chamber) renderDetail(chamber);
  updateView(false);
});

function tickClock() {
  $("#clinicalClock").textContent = new Intl.DateTimeFormat(undefined, {
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  }).format(new Date());
}

portalState.selectedChamber = chamberFromHash();
tickClock();
setInterval(tickClock, 1000);
loadPortal().then(() => updateView(false));
setInterval(loadPortal, 1000);
