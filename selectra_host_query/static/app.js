const $ = (selector) => document.querySelector(selector);
const state = { tests: [], eventsAfter: 0, ordersSignature: "" };
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

async function loadStatus() {
  const status = await api("/api/status");
  const pill = $("#modePill");
  pill.className = `mode-pill ${status.armed ? "armed" : "safe"}`;
  pill.querySelector("strong").textContent = status.armed ? "Live responses armed" : "Observation mode";
  $("#webEndpoint").textContent = location.host;
  $("#instrumentEndpoint").textContent = `<computer-IP>:${status.listener_port}`;
  $("#clientState").textContent = status.connected_clients
    ? `${status.connected_clients} connected`
    : status.last_peer ? `Last: ${status.last_peer}` : "Waiting";
  $("#orderCount").textContent = String(status.orders);
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
  const statusClass = order.status === "error" ? "error" : "";
  return `
    <article class="order-card">
      <div class="order-card-head"><h3>${escapeHtml(order.sample_id)}</h3><span class="order-status ${statusClass}">${escapeHtml(order.status)}</span></div>
      <p class="order-person">${escapeHtml(order.family_name)} ${escapeHtml(order.given_name)} · ${escapeHtml(order.patient_id)}</p>
      <div class="order-tests">${order.tests.map(escapeHtml).join(" · ")}</div>
      <div class="order-meta"><span>${escapeHtml(order.specimen_type)}</span><span>${order.query_count} quer${order.query_count === 1 ? "y" : "ies"}</span><span>${escapeHtml(formatTime(order.updated_at))}</span></div>
      <button class="simulate-button" type="button" data-simulate="${escapeHtml(order.sample_id)}">Simulate exact-ID query</button>
    </article>
  `;
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
  const signature = JSON.stringify(result.orders);
  if (signature === state.ordersSignature) return;
  state.ordersSignature = signature;
  const container = $("#ordersList");
  container.innerHTML = result.orders.length
    ? result.orders.map(orderCard).join("")
    : '<div class="orders-empty">No orders staged yet.</div>';
  container.querySelectorAll("[data-simulate]").forEach((button) => {
    button.addEventListener("click", () => simulate(button.dataset.simulate).catch((error) => toast(error.message)));
  });
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
  await Promise.all([loadStatus(), loadOrders(), loadEvents()]);
  setInterval(() => loadStatus().catch(() => {}), 2500);
  setInterval(() => loadOrders().catch(() => {}), 2200);
  setInterval(() => loadEvents().catch(() => {}), 1200);
}

$("#addTest").addEventListener("click", addTest);
$("#testCodeInput").addEventListener("keydown", (event) => {
  if (event.key === "Enter") { event.preventDefault(); addTest(); }
});
$("#orderForm").addEventListener("submit", stageOrder);
renderTests();
initialize().catch((error) => toast(`Bench failed to initialize: ${error.message}`));

