const $ = (selector) => document.querySelector(selector);
const state = {
  tests: [], eventsAfter: 0, ordersSignature: "", outboundFields: {},
  warningNotices: new Set(),
  orders: [], orderPage: 1, orderPageSize: 9, selectedOrders: new Set(),
};
let liveResponsesArmed = false;
let continuousProbeArmed = false;
let apiAutoArmEnabled = false;
let toastTimer = null;

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, (character) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  })[character]);
}

async function api(url, options = {}) {
  const response = await fetch(url, { cache: "no-store", ...options });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `${response.status} ${response.statusText}`);
  return body;
}

function toast(message) {
  const element = $("#toast");
  element.textContent = message;
  element.classList.add("show");
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => element.classList.remove("show"), 3200);
}

function renderTests() {
  const container = $("#testChips");
  if (!state.tests.length) {
    container.innerHTML = '<span class="empty-tests">No tests selected</span>';
    return;
  }
  container.innerHTML = state.tests.map((test, index) => `
    <span class="test-chip">${escapeHtml(test)}<button type="button" data-remove-test="${index}" aria-label="Remove ${escapeHtml(test)}">×</button></span>
  `).join("");
  container.querySelectorAll("[data-remove-test]").forEach((button) => {
    button.addEventListener("click", () => {
      state.tests.splice(Number(button.dataset.removeTest), 1);
      renderTests();
    });
  });
}

function addTest() {
  const input = $("#testCodeInput");
  const value = input.value.trim();
  if (!value) return;
  if (/[|\\^&\r\n]/.test(value)) {
    toast("Test code contains a reserved LIS2-A delimiter.");
    return;
  }
  if (!state.tests.includes(value)) state.tests.push(value);
  input.value = "";
  renderTests();
  input.focus();
}

function formatTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
}

function renderOutboundPolicy(fields = {}) {
  state.outboundFields = fields;
  let enabledCount = 0;
  document.querySelectorAll("[data-outbound-field]").forEach((button) => {
    const enabled = fields[button.dataset.outboundField] === true;
    if (enabled) enabledCount += 1;
    button.setAttribute("aria-checked", String(enabled));
    button.querySelector("span").textContent = enabled ? "Sent" : "Not sent";
  });
  $("#policySummaryState").textContent = enabledCount
    ? `${enabledCount} optional sent`
    : "Minimal";
}

async function loadStatus() {
  const status = await api("/api/status");
  const pill = $("#modePill");
  const anyArmed = status.api_armed_orders || status.armed || status.probe_armed || status.cyanvision?.armed;
  pill.className = `mode-pill ${anyArmed ? "armed" : "safe"}`;
  pill.querySelector("strong").textContent = status.api_armed_orders
    ? `${status.api_armed_orders} Selectra order${status.api_armed_orders === 1 ? "" : "s"} armed`
    : status.cyanvision?.armed
    ? "CYANVision load armed"
    : status.probe_armed
    ? "Continuous probe armed"
    : status.armed ? "Live responses armed" : "Observation mode";
  liveResponsesArmed = status.armed;
  apiAutoArmEnabled = status.api_auto_arm === true;
  renderOutboundPolicy(status.selectra_outbound_fields || {});
  const autoArmControl = $("#autoArmControl");
  autoArmControl.classList.toggle("armed", apiAutoArmEnabled);
  $("#autoArmTitle").textContent = apiAutoArmEnabled ? "Auto-arm active" : "Manual arming";
  $("#autoArmCopy").textContent = apiAutoArmEnabled
    ? "Exact-ID server orders are armed on arrival."
    : "New server orders wait for review.";
  $("#autoArmButton").textContent = apiAutoArmEnabled ? "Stop auto-arm" : "Start auto-arm";
  const armingPanel = $("#armingPanel");
  const armingButton = $("#armingButton");
  armingPanel.classList.toggle("armed", status.armed);
  $("#armingTitle").textContent = status.armed ? "Manual replies are armed" : "Manual replies are disarmed";
  $("#armingCopy").textContent = status.armed
    ? "A matching query can receive a manually created development order. Server orders still use their own card controls."
    : "This global switch applies only to manually created development orders.";
  armingButton.textContent = status.armed ? "Disarm manual replies" : "Arm manual replies";
  continuousProbeArmed = status.probe_armed;
  const probePanel = $("#probePanel");
  probePanel.classList.toggle("armed", status.probe_armed);
  $("#probeTitle").textContent = status.probe_armed
    ? "Answering every Selectra Q"
    : "Automatic probe is disarmed";
  $("#probeCopy").textContent = status.probe_armed
    ? "Every valid query receives this payload. It stays armed until you disarm it or restart LaboBridge."
    : "When armed, every Selectra Q—regardless of sample ID—receives the alert order until you disarm this probe.";
  $("#probePatientName").textContent = status.probe_patient_name;
  $("#probeTests").textContent = (status.probe_tests || []).join(" · ");
  $("#probeButton").textContent = status.probe_armed ? "Disarm continuous probe" : "Arm continuous probe";
  $("#webEndpoint").textContent = location.host;
  $("#instrumentEndpoint").textContent = `<computer-IP>:${status.listener_port}`;
  $("#clientState").textContent = status.connected_clients
    ? `${status.connected_clients} connected`
    : status.last_peer ? `Last: ${status.last_peer}` : "Waiting";
  $("#orderCount").textContent = String(status.orders);
}

async function loadCyanvision() {
  const status = await api("/api/cyanvision/worklist");
  if (status.available === false) {
    $("#cyanvisionPanel").hidden = true;
    return;
  }
  $("#cyanvisionPanel").hidden = false;
  $("#cyanvisionPort").textContent = String(status.listener_port);
  const panel = $("#cyanvisionPanel");
  const stateElement = $("#cyanvisionState");
  panel.classList.toggle("armed", status.armed);
  panel.classList.toggle("pending", status.pending_ack);
  panel.classList.toggle("acknowledged", status.status === "acknowledged");
  panel.classList.toggle("rejected", status.status === "rejected");
  const labels = {
    empty: "Draft",
    disarmed: "Disarmed",
    armed: "Armed — waiting for Load from LIS",
    waiting_for_ack: "Sent — waiting for ACK^Q03",
    acknowledged: "ACK received — load complete",
    rejected: "Rejected — check protocol trace",
  };
  stateElement.querySelector("span").textContent = labels[status.status] || status.status;
  $("#cyanDisarmButton").hidden = !status.armed;
  $("#cyanArmButton").querySelector("span").textContent = status.armed
    ? "Replace and re-arm this load"
    : "Stage and arm one load";
  if (status.order) {
    $("#cyanResponseRecords").textContent = (status.response_preview || []).join("\n");
    $("#cyanvisionInstruction").textContent = status.pending_ack
      ? `Worklist ${status.order.sample_id} was sent; waiting for CYANVision ACK^Q03.`
      : status.status === "acknowledged"
        ? `CYANVision acknowledged ${status.order.sample_id}. The one-load order is disarmed.`
        : status.armed
          ? `Now ask the operator to open Patient Worklist and press Load from LIS. Only ${status.order.sample_id} will be offered.`
          : `Last prepared worklist: ${status.order.sample_id}.`;
  } else if (status.api_ready_orders) {
    $("#cyanvisionInstruction").textContent = `${status.api_ready_orders} API worklist item${status.api_ready_orders === 1 ? " is" : "s are"} ready. CYANVision will download the queue when the operator presses Load from LIS.`;
  }
}

async function loadCyanvisionTests() {
  const result = await api("/api/cyanvision/tests");
  if (result.available === false) return;
  const select = $("#cyanTestCode");
  const previous = select.value;
  const options = result.tests || [];
  select.innerHTML = '<option value="">Choose a CYANVision program</option>' + options.map((test) => {
    const provenance = test.observed && test.mapped
      ? "received + mapped"
      : test.observed ? "received" : "mapped";
    const name = test.name && test.name !== test.code ? ` — ${test.name}` : "";
    return `<option value="${escapeHtml(test.code)}">${escapeHtml(test.code)}${escapeHtml(name)} · Program ID ${escapeHtml(test.program_id)} · ${provenance}</option>`;
  }).join("");
  select.disabled = options.length === 0;
  if (options.some((test) => test.code === previous)) select.value = previous;
  $("#cyanTestHelp").textContent = options.length
    ? `${options.length} test${options.length === 1 ? "" : "s"} with field-observed Program IDs. The numeric Program ID is sent in DSP line 8.`
    : "No field-observed CYANVision Program IDs are configured.";
}

async function stageCyanvision(event) {
  event.preventDefault();
  const payload = {
    sample_id: $("#cyanSampleId").value.trim(),
    given_name: $("#cyanGivenName").value.trim(),
    family_name: $("#cyanFamilyName").value.trim(),
    birth_date: $("#cyanBirthDate").value,
    sex: $("#cyanSex").value,
    test_code: $("#cyanTestCode").value.trim(),
    confirmation: "ARM CYANVISION WORKLIST",
  };
  if (!window.confirm(
    `Arm CYANVision worklist ${payload.sample_id} for ${payload.test_code}? Its numeric Program ID will be sent on the next Load from LIS request.`
  )) return;
  const alert = $("#cyanFormAlert");
  const button = $("#cyanArmButton");
  alert.hidden = true;
  button.disabled = true;
  try {
    const result = await api("/api/cyanvision/worklist", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    $("#cyanResponseRecords").textContent = result.response_preview.join("\n");
    toast(`CYANVision worklist ${result.order.sample_id} armed for one load.`);
    await Promise.all([loadCyanvision(), loadStatus(), loadEvents()]);
  } catch (error) {
    alert.textContent = error.message;
    alert.hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function disarmCyanvision() {
  await api("/api/cyanvision/worklist", { method: "DELETE" });
  toast("CYANVision worklist disarmed.");
  await Promise.all([loadCyanvision(), loadStatus(), loadEvents()]);
}

async function toggleContinuousProbe() {
  const nextArmed = !continuousProbeArmed;
  if (nextArmed && !window.confirm(
    "Arm continuous probe? EVERY Selectra sample query will receive three appended tests and the APPELLE MANEL/FODHIL order notice until you manually disarm it or restart LaboBridge."
  )) return;
  const button = $("#probeButton");
  button.disabled = true;
  try {
    const result = await api("/api/continuous-probe", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        armed: nextArmed,
        confirmation: nextArmed ? "ARM CONTINUOUS PROBE" : "",
      }),
    });
    continuousProbeArmed = result.probe_armed;
    toast(result.probe_armed
      ? "Continuous probe armed for every Selectra Q."
      : "Continuous probe disarmed.");
    await Promise.all([loadStatus(), loadEvents()]);
  } finally {
    button.disabled = false;
  }
}

async function toggleLiveResponses() {
  const nextArmed = !liveResponsesArmed;
  if (nextArmed && !window.confirm(
    "Arm manual Selectra replies? A matching query can immediately receive a manually created development order."
  )) return;
  const button = $("#armingButton");
  button.disabled = true;
  try {
    const result = await api("/api/live-responses", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        armed: nextArmed,
        confirmation: nextArmed ? "ARM SELECTRA" : "",
      }),
    });
    liveResponsesArmed = result.armed;
    toast(result.armed ? "Manual Selectra replies armed." : "Manual Selectra replies disarmed.");
    await Promise.all([loadStatus(), loadEvents()]);
  } finally {
    button.disabled = false;
  }
}

async function toggleApiAutoArm() {
  const enabled = !apiAutoArmEnabled;
  if (enabled && !window.confirm(
    "Start Selectra API auto-arm? All waiting server orders and every new authenticated server order will be armed. Replies still require an exact sample-ID query."
  )) return;
  const button = $("#autoArmButton");
  button.disabled = true;
  try {
    const result = await api("/api/selectra/auto-arm", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled,
        confirmation: enabled ? "ENABLE SELECTRA AUTO ARM" : "",
      }),
    });
    apiAutoArmEnabled = result.enabled;
    state.ordersSignature = "";
    toast(result.enabled
      ? `Auto-arm started; ${result.updated_orders} waiting order(s) armed.`
      : `Auto-arm stopped; ${result.updated_orders} waiting order(s) disarmed.`);
    await Promise.all([loadStatus(), loadOrders(), loadEvents()]);
  } finally {
    button.disabled = false;
  }
}

async function toggleOutboundField(button) {
  const field = button.dataset.outboundField;
  const label = button.dataset.fieldLabel;
  const enabled = state.outboundFields[field] !== true;
  if (enabled && !window.confirm(
    `Send ${label} to Selectra for every API order? Incorrect values can make Selectra reject an existing sample.`
  )) return;
  button.disabled = true;
  try {
    const result = await api("/api/selectra/outbound-fields", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        field,
        enabled,
        confirmation: enabled ? "ENABLE SELECTRA OUTBOUND FIELD" : "",
      }),
    });
    renderOutboundPolicy(result.fields);
    toast(`${label} ${enabled ? "will now be sent" : "will no longer be sent"}.`);
    await loadEvents();
  } finally {
    button.disabled = false;
  }
}

async function resetOutboundFields() {
  const result = await api("/api/selectra/outbound-fields", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ reset: true }),
  });
  renderOutboundPolicy(result.fields);
  toast("Selectra API output returned to minimal fields.");
  await loadEvents();
}

function eventClass(direction) {
  if (direction === "instrument") return "instrument";
  if (direction === "host") return "host";
  return "system";
}

function appendEvents(events) {
  if (!events.length) return;
  const stream = $("#traceStream");
  stream.querySelector(".trace-empty")?.remove();
  events.forEach((event) => {
    state.eventsAfter = Math.max(state.eventsAfter, event.id);
    const article = document.createElement("article");
    article.className = `trace-event ${eventClass(event.direction)}`;
    article.innerHTML = `
      <div class="trace-meta"><span>${escapeHtml(event.direction)} · ${escapeHtml(event.kind)}</span><time>${escapeHtml(formatTime(event.created_at))}</time></div>
      <div class="trace-message">${escapeHtml(event.message)}</div>
      ${event.sample_id ? `<div class="trace-sample">sample ${escapeHtml(event.sample_id)}</div>` : ""}
      ${event.raw_text ? `<pre class="trace-raw">${escapeHtml(event.raw_text)}</pre>` : ""}
    `;
    stream.appendChild(article);
  });
  while (stream.children.length > 160) stream.firstElementChild.remove();
  stream.scrollTop = stream.scrollHeight;
}

async function loadEvents() {
  const result = await api(`/api/events?after=${state.eventsAfter}`);
  appendEvents(result.events);
}

function orderCard(order) {
  const statusClass = ["error", "rejected"].includes(order.status) ? "error" : "";
  const canArm = order.source === "api" && ["staged", "queried", "error"].includes(order.status);
  const displayStatus = order.source === "api" && canArm
    ? (order.ready ? "armed" : "awaiting arm")
    : order.status;
  const warnings = Array.isArray(order.validation_warnings) ? order.validation_warnings : [];
  const selected = state.selectedOrders.has(order.sample_id);
  return `
    <article class="order-card ${order.ready ? "is-armed" : ""} ${selected ? "is-selected" : ""}">
      <div class="order-card-head">
        <label class="order-select" title="Select order ${escapeHtml(order.sample_id)}">
          <input type="checkbox" data-select-order="${escapeHtml(order.sample_id)}" ${selected ? "checked" : ""} aria-label="Select order ${escapeHtml(order.sample_id)}">
        </label>
        <h3>${escapeHtml(order.sample_id)}</h3>
        <span class="order-status ${statusClass}">${escapeHtml(displayStatus)}</span>
      </div>
      <p class="order-person">${escapeHtml(order.family_name)} ${escapeHtml(order.given_name)} · ${escapeHtml(order.patient_id)}</p>
      <div class="order-tests">${order.tests.map(escapeHtml).join(" · ")}</div>
      ${warnings.length ? `
        <div class="order-validation-warning" role="status">
          <strong>${warnings.length} requested test${warnings.length === 1 ? " was" : "s were"} skipped</strong>
          <ul>${warnings.map((warning) => `
            <li><span>Item ${Number(warning.index) + 1}</span>${escapeHtml(warning.reason)}</li>
          `).join("")}</ul>
        </div>
      ` : ""}
      <div class="order-meta"><span>${order.source === "api" ? "Server order" : "Local order"}</span><span>${order.query_count} quer${order.query_count === 1 ? "y" : "ies"}</span><span>${escapeHtml(formatTime(order.updated_at))}</span></div>
      <div class="order-actions">
        ${canArm ? `<button class="arm-order-button" type="button" data-${order.ready ? "disarm" : "arm"}="${escapeHtml(order.sample_id)}">${order.ready ? "Disarm" : "Arm for Selectra"}</button>` : ""}
        <button class="simulate-button" type="button" data-simulate="${escapeHtml(order.sample_id)}">Simulate query</button>
        <button class="remove-order-button" type="button" data-remove-order="${escapeHtml(order.sample_id)}">Remove</button>
      </div>
    </article>
  `;
}

async function setOrderArmed(sampleId, armed, button) {
  if (armed && !window.confirm(
    `Arm order ${sampleId}? The next exact Selectra query for this ID will receive its patient and tests.`
  )) return;
  button.disabled = true;
  try {
    await api(`/api/orders/${encodeURIComponent(sampleId)}/arm`, {
      method: armed ? "POST" : "DELETE",
      headers: armed ? { "Content-Type": "application/json" } : undefined,
      body: armed ? JSON.stringify({ confirmation: "ARM SELECTRA ORDER" }) : undefined,
    });
    toast(`Order ${sampleId} ${armed ? "armed for Selectra" : "disarmed"}.`);
    state.ordersSignature = "";
    await Promise.all([loadOrders(), loadEvents(), loadStatus()]);
  } finally {
    button.disabled = false;
  }
}

async function removeOrder(sampleId, button) {
  if (!window.confirm(`Remove staged order ${sampleId}? It will no longer be available to Selectra.`)) return;
  button.disabled = true;
  try {
    await api(`/api/orders/${encodeURIComponent(sampleId)}`, { method: "DELETE" });
    state.selectedOrders.delete(sampleId);
    toast(`Order ${sampleId} removed from staging.`);
    state.ordersSignature = "";
    await Promise.all([loadOrders(), loadEvents(), loadStatus()]);
  } finally {
    button.disabled = false;
  }
}

function renderOrderQueue() {
  const total = state.orders.length;
  const totalPages = Math.max(1, Math.ceil(total / state.orderPageSize));
  state.orderPage = Math.min(Math.max(1, state.orderPage), totalPages);
  const start = (state.orderPage - 1) * state.orderPageSize;
  const pageOrders = state.orders.slice(start, start + state.orderPageSize);
  const end = start + pageOrders.length;
  const selectedCount = state.selectedOrders.size;
  const allPageSelected = pageOrders.length > 0
    && pageOrders.every((order) => state.selectedOrders.has(order.sample_id));

  $("#orderRange").textContent = total ? `${start + 1}–${end} of ${total}` : "0 orders";
  $("#orderPageLabel").textContent = `Page ${state.orderPage} of ${totalPages}`;
  $("#previousOrdersButton").disabled = state.orderPage <= 1;
  $("#nextOrdersButton").disabled = state.orderPage >= totalPages;
  $("#selectPageButton").disabled = pageOrders.length === 0;
  $("#selectPageButton").textContent = allPageSelected ? "Deselect this page" : "Select this page";
  $("#clearSelectionButton").disabled = selectedCount === 0;
  $("#selectionCount").textContent = `${selectedCount} selected`;
  $("#bulkRemoveButton").disabled = selectedCount === 0;
  $("#bulkRemoveButton").textContent = selectedCount
    ? `Remove selected (${selectedCount})`
    : "Remove selected";

  const container = $("#ordersList");
  container.innerHTML = pageOrders.length
    ? pageOrders.map(orderCard).join("")
    : '<div class="orders-empty"><strong>No active Selectra orders</strong><span>Orders sent by the clinic server will appear here.</span></div>';
  container.querySelectorAll("[data-select-order]").forEach((checkbox) => {
    checkbox.addEventListener("change", () => {
      if (checkbox.checked) state.selectedOrders.add(checkbox.dataset.selectOrder);
      else state.selectedOrders.delete(checkbox.dataset.selectOrder);
      renderOrderQueue();
    });
  });
  container.querySelectorAll("[data-simulate]").forEach((button) => {
    button.addEventListener("click", () => simulate(button.dataset.simulate).catch((error) => toast(error.message)));
  });
  container.querySelectorAll("[data-arm]").forEach((button) => {
    button.addEventListener("click", () => setOrderArmed(button.dataset.arm, true, button).catch((error) => toast(error.message)));
  });
  container.querySelectorAll("[data-disarm]").forEach((button) => {
    button.addEventListener("click", () => setOrderArmed(button.dataset.disarm, false, button).catch((error) => toast(error.message)));
  });
  container.querySelectorAll("[data-remove-order]").forEach((button) => {
    button.addEventListener("click", () => removeOrder(button.dataset.removeOrder, button).catch((error) => toast(error.message)));
  });
}

function togglePageSelection() {
  const start = (state.orderPage - 1) * state.orderPageSize;
  const pageOrders = state.orders.slice(start, start + state.orderPageSize);
  const allSelected = pageOrders.length > 0
    && pageOrders.every((order) => state.selectedOrders.has(order.sample_id));
  pageOrders.forEach((order) => {
    if (allSelected) state.selectedOrders.delete(order.sample_id);
    else state.selectedOrders.add(order.sample_id);
  });
  renderOrderQueue();
}

async function bulkRemoveOrders() {
  const sampleIds = [...state.selectedOrders];
  if (!sampleIds.length) return;
  const armedCount = state.orders.filter(
    (order) => state.selectedOrders.has(order.sample_id) && order.ready,
  ).length;
  const armedWarning = armedCount
    ? ` ${armedCount} selected order${armedCount === 1 ? " is" : "s are"} currently armed.`
    : "";
  if (!window.confirm(
    `Remove ${sampleIds.length} selected order${sampleIds.length === 1 ? "" : "s"}?${armedWarning} They will no longer be available to Selectra.`
  )) return;
  const button = $("#bulkRemoveButton");
  button.disabled = true;
  try {
    const result = await api("/api/orders/bulk-remove", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        sample_ids: sampleIds,
        confirmation: "REMOVE SELECTRA ORDERS",
      }),
    });
    result.removed_sample_ids.forEach((sampleId) => state.selectedOrders.delete(sampleId));
    state.ordersSignature = "";
    toast(`Removed ${result.removed_count} selected order${result.removed_count === 1 ? "" : "s"}.`);
    await Promise.all([loadOrders(), loadEvents(), loadStatus()]);
  } finally {
    button.disabled = false;
  }
}

async function simulate(sampleId) {
  const result = await api("/api/simulate-query", {
    method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sample_id: sampleId }),
  });
  $("#responseRecords").textContent = result.response_records.join("\n");
  $("#responsePreview").hidden = false;
  toast(`Simulated query for ${sampleId}; no network bytes sent.`);
  await Promise.all([loadOrders(), loadEvents(), loadStatus()]);
}

async function loadOrders() {
  const result = await api("/api/orders");
  result.orders.forEach((order) => {
    const warnings = Array.isArray(order.validation_warnings) ? order.validation_warnings : [];
    const noticeKey = `${order.sample_id}:${order.updated_at}`;
    if (warnings.length && !state.warningNotices.has(noticeKey)) {
      state.warningNotices.add(noticeKey);
      toast(`Order ${order.sample_id} was kept, but ${warnings.length} test${warnings.length === 1 ? " was" : "s were"} skipped.`);
    }
  });
  const signature = JSON.stringify(result.orders);
  if (signature === state.ordersSignature) return;
  state.ordersSignature = signature;
  state.orders = result.orders;
  const activeIds = new Set(state.orders.map((order) => order.sample_id));
  [...state.selectedOrders].forEach((sampleId) => {
    if (!activeIds.has(sampleId)) state.selectedOrders.delete(sampleId);
  });
  renderOrderQueue();
}

async function stageOrder(event) {
  event.preventDefault();
  const alert = $("#formAlert");
  alert.hidden = true;
  const payload = {
    sample_id: $("#sampleId").value.trim(),
    patient_id: $("#patientId").value.trim(),
    family_name: $("#familyName").value.trim(),
    given_name: $("#givenName").value.trim(),
    birth_date: $("#birthDate").value,
    sex: $("#patientSex").value,
    specimen_type: $("#specimenType").value,
    tests: state.tests,
  };
  const button = $("#stageButton");
  button.disabled = true;
  try {
    const result = await api("/api/orders", {
      method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload),
    });
    $("#responseRecords").textContent = result.response_preview.join("\n");
    $("#responsePreview").hidden = false;
    toast(`Order ${result.order.sample_id} staged locally.`);
    await Promise.all([loadOrders(), loadEvents(), loadStatus()]);
  } catch (error) {
    alert.textContent = error.message;
    alert.hidden = false;
  } finally {
    button.disabled = false;
  }
}

async function initialize() {
  const assays = await api("/api/assays");
  $("#assaySuggestions").innerHTML = assays.assays.map((assay) => `<option value="${escapeHtml(assay)}"></option>`).join("");
  await Promise.all([loadStatus(), loadCyanvision(), loadCyanvisionTests(), loadOrders(), loadEvents()]);
  setInterval(() => loadStatus().catch(() => {}), 2500);
  setInterval(() => loadOrders().catch(() => {}), 2200);
  setInterval(() => loadEvents().catch(() => {}), 1200);
  setInterval(() => loadCyanvision().catch(() => {}), 1800);
  setInterval(() => loadCyanvisionTests().catch(() => {}), 30000);
}

function setConsoleView(name, updateHash = true) {
  const valid = ["selectra", "cyanvision", "diagnostics"];
  const selected = valid.includes(name) ? name : "selectra";
  document.querySelectorAll("[data-console-tab]").forEach((button) => {
    const active = button.dataset.consoleTab === selected;
    button.setAttribute("aria-selected", String(active));
    button.tabIndex = active ? 0 : -1;
  });
  document.querySelectorAll("[data-console-view]").forEach((view) => {
    view.hidden = view.dataset.consoleView !== selected;
  });
  if (updateHash) history.replaceState(null, "", `#${selected}`);
}

document.querySelectorAll("[data-console-tab]").forEach((button) => {
  button.addEventListener("click", () => setConsoleView(button.dataset.consoleTab));
  button.addEventListener("keydown", (event) => {
    if (!["ArrowLeft", "ArrowRight"].includes(event.key)) return;
    event.preventDefault();
    const tabs = [...document.querySelectorAll("[data-console-tab]")];
    const direction = event.key === "ArrowRight" ? 1 : -1;
    const next = tabs[(tabs.indexOf(button) + direction + tabs.length) % tabs.length];
    next.focus();
    setConsoleView(next.dataset.consoleTab);
  });
});

$("#addTest").addEventListener("click", addTest);
$("#testCodeInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); addTest(); }
});
$("#orderForm").addEventListener("submit", stageOrder);
$("#autoArmButton").addEventListener("click", () => toggleApiAutoArm().catch((error) => toast(error.message)));
$("#selectPageButton").addEventListener("click", togglePageSelection);
$("#clearSelectionButton").addEventListener("click", () => {
  state.selectedOrders.clear();
  renderOrderQueue();
});
$("#bulkRemoveButton").addEventListener("click", () => bulkRemoveOrders().catch((error) => toast(error.message)));
$("#previousOrdersButton").addEventListener("click", () => {
  state.orderPage -= 1;
  renderOrderQueue();
  $("#ordersTitle").scrollIntoView({ behavior: "smooth", block: "start" });
});
$("#nextOrdersButton").addEventListener("click", () => {
  state.orderPage += 1;
  renderOrderQueue();
  $("#ordersTitle").scrollIntoView({ behavior: "smooth", block: "start" });
});
document.querySelectorAll("[data-outbound-field]").forEach((button) => {
  button.addEventListener("click", () => toggleOutboundField(button).catch((error) => toast(error.message)));
});
$("#resetOutboundFields").addEventListener("click", () => resetOutboundFields().catch((error) => toast(error.message)));
$("#armingButton").addEventListener("click", () => toggleLiveResponses().catch((error) => toast(error.message)));
$("#probeButton").addEventListener("click", () => toggleContinuousProbe().catch((error) => toast(error.message)));
$("#cyanvisionForm").addEventListener("submit", stageCyanvision);
$("#cyanDisarmButton").addEventListener("click", () => disarmCyanvision().catch((error) => toast(error.message)));
renderTests();
setConsoleView(location.hash.slice(1), false);
initialize().catch((error) => toast(`Bench failed to initialize: ${error.message}`));
