"use strict";
const $ = (id) => document.getElementById(id);
const state = { token: sessionStorage.getItem("finance-token"), user: null, offset: 0, total: 0, tab: "transactions" };
const charts = {};
const escapeHtml = (value) => String(value ?? "—").replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]));
const number = (value) => new Intl.NumberFormat("en-IN", { maximumFractionDigits: 2 }).format(value);
const canWrite = () => ["admin", "finance_controller", "analyst"].includes(state.user?.role);
function notice(message, error = false) { $("notice").textContent = message; $("notice").className = error ? "error" : ""; $("notice").hidden = !message; }
async function api(path, options = {}) {
  const headers = new Headers(options.headers);
  if (state.token) headers.set("Authorization", `Bearer ${state.token}`);
  if (options.body && !(options.body instanceof FormData)) headers.set("Content-Type", "application/json");
  const response = await fetch(path, { ...options, headers });
  const body = await response.json().catch(() => ({}));
  if (!response.ok) {
    if (response.status === 401) signOut();
    const detail = body.detail;
    throw new Error(typeof detail === "string" ? detail : detail?.errors?.join("\n") || JSON.stringify(detail || `Request failed (${response.status})`));
  }
  return body;
}
async function busy(button, action) {
  if (button) button.disabled = true;
  try { await action(); } catch (error) { notice(error.message, true); }
  finally { if (button) button.disabled = false; }
}
function signOut() {
  state.token = null; state.user = null; sessionStorage.removeItem("finance-token");
  $("workspace").hidden = true; $("login-panel").hidden = false; $("detail").close();
}
function badge(value) {
  const kind = ["RECONCILED", "MATCHED_WITH_FEE", "RESOLVED", "COMPLETED"].includes(value) ? "good" : ["UNRESOLVED", "REJECTED"].includes(value) ? "bad" : "warn";
  return `<span class="badge ${kind}">${escapeHtml(value)}</span>`;
}
function table(target, headings, rows) {
  $(target).innerHTML = `<table><thead><tr>${headings.map(h => `<th>${escapeHtml(h)}</th>`).join("")}</tr></thead><tbody>${rows.length ? rows.map(row => `<tr>${row.map(cell => `<td>${cell}</td>`).join("")}</tr>`).join("") : `<tr><td colspan="${headings.length}">No records found.</td></tr>`}</tbody></table>`;
}
function showDetails(title, data) {
  $("detail-title").textContent = title;
  $("detail-body").innerHTML = `<dl>${Object.entries(data).map(([key, value]) => `<dt>${escapeHtml(key.replaceAll("_", " "))}</dt><dd>${escapeHtml(typeof value === "object" && value !== null ? JSON.stringify(value) : value)}</dd>`).join("")}</dl>`;
  $("detail").showModal();
}
async function summary() {
  const formatMoney = (obj) => Object.entries(obj || {}).map(([c, v]) => `${number(v)} ${c}`).join(" + ") || "0";
  const data = await api("/api/dashboard/summary");
  const cards = [["Transactions", number(data.total_transactions)], ["Match rate", `${(data.match_rate * 100).toFixed(1)}%`], ["Under review / unresolved", `${data.review} / ${data.unresolved}`], ["At-risk value", formatMoney(data.at_risk_value)], ["Total order value", formatMoney(data.total_value)], ["Reconciled receipts", formatMoney(data.reconciled_value)], ["High-value review flags", number(data.high_value_review_count)], ["Average confidence", data.avg_ai_confidence === null ? "—" : `${(data.avg_ai_confidence * 100).toFixed(1)}%`]];
  $("metrics").innerHTML = cards.map(([name, value]) => `<div class="metric"><span>${escapeHtml(name)}</span><strong>${escapeHtml(value)}</strong></div>`).join("");
  drawCharts(data);
}
function drawCharts(data) {
  if (!window.Chart) return;
  Chart.defaults.color = '#94a3b8';
  Chart.defaults.font.family = 'Inter, sans-serif';
  if (charts.exception) charts.exception.destroy();
  if (charts.status) charts.status.destroy();
  const ctxE = $('exceptionChart').getContext('2d');
  const labelsE = Object.keys(data.exception_breakdown);
  const dataE = Object.values(data.exception_breakdown);
  charts.exception = new Chart(ctxE, {
    type: 'doughnut',
    data: {
      labels: labelsE.length ? labelsE : ["No Exceptions"],
      datasets: [{ data: dataE.length ? dataE : [1], backgroundColor: ['#f87171', '#fbbf24', '#38bdf8', '#818cf8', '#34d399', '#334155'], borderWidth: 0, hoverOffset: 4 }]
    },
    options: { maintainAspectRatio: false, plugins: { legend: { position: 'bottom' } } }
  });
  const ctxS = $('statusChart').getContext('2d');
  charts.status = new Chart(ctxS, {
    type: 'bar',
    data: {
      labels: ['Matched', 'Review Required', 'Unresolved'],
      datasets: [{ label: 'Transactions', data: [data.matched, data.review, data.unresolved], backgroundColor: ['#34d399', '#fbbf24', '#f87171'], borderRadius: 4 }]
    },
    options: { maintainAspectRatio: false, plugins: { legend: { display: false } }, scales: { y: { beginAtZero: true, grid: { color: 'rgba(51, 65, 85, 0.3)' } }, x: { grid: { display: false } } } }
  });
}
async function transactions() {
  const params = new URLSearchParams({limit: "50", offset: String(state.offset)});
  if ($("search").value.trim()) params.set("search", $("search").value.trim());
  if ($("transaction-status").value) params.set("status", $("transaction-status").value);
  const data = await api(`/api/reconciliations?${params}`); state.total = data.total;
  table("transaction-table", ["Order / batch", "Status", "Expected", "Actual", "Difference", "Confidence", "Details"], data.items.map(r => [
    `${escapeHtml(r.order_id)} / #${r.batch_id}`, badge(r.status), `${number(r.expected_amount)} ${escapeHtml(r.currency)}`, number(r.actual_amount), number(r.difference), `${(r.confidence * 100).toFixed(0)}%${r.requires_human_review ? " · review" : ""}`, `<button data-record="${r.id}" class="secondary">View</button>`
  ]));
  $("page-info").textContent = data.total ? `${state.offset + 1}–${Math.min(state.offset + 50, data.total)} of ${data.total}` : "0 transactions";
  $("previous").disabled = state.offset === 0; $("next").disabled = state.offset + 50 >= data.total;
}
async function exceptions() {
  const params = new URLSearchParams({limit: "500"});
  if ($("exception-status").value) params.set("status", $("exception-status").value);
  const data = await api(`/api/exceptions?${params}`);
  table("exception-table", ["Case", "Type / severity", "Description", "Status", "Actions"], data.items.map(e => {
    let actions = `<button data-record="${e.reconciliation_id}" class="secondary">Details</button><button data-analyze="${e.id}" class="secondary">Analyze</button>`;
    if (canWrite() && !["RESOLVED", "REJECTED", "OVERRIDDEN"].includes(e.status)) {
      actions += `<button data-review="approve" data-id="${e.id}">Approve</button><button data-review="reject" data-id="${e.id}" class="secondary">Reject</button>`;
      if (["admin", "finance_controller"].includes(state.user.role)) actions += `<button data-review="override" data-id="${e.id}" class="secondary">Override</button>`;
    }
    return [`#${e.id}`, `${escapeHtml(e.type)}<br>${escapeHtml(e.severity)}`, escapeHtml(e.description), badge(e.status), actions];
  }));
}
async function batches() {
  const data = await api("/api/batches");
  table("batch-table", ["Batch", "Source records", "Status", "Created", "Action"], data.map(b => [escapeHtml(b.batch_name), number(b.total_records), badge(b.status), escapeHtml(b.created_at), canWrite() && !["COMPLETED", "PROCESSING"].includes(b.status) ? `<button data-reconcile="${b.id}">Reconcile</button>` : "—"]));
}
async function audit() {
  const data = await api("/api/audit-logs?limit=200");
  table("audit-table", ["Timestamp", "Actor", "Action", "Entity", "Reason"], data.items.map(a => [escapeHtml(a.timestamp), escapeHtml(a.actor), escapeHtml(a.action), `${escapeHtml(a.entity_type)} #${escapeHtml(a.entity_id)}`, escapeHtml(a.reason)]));
}
async function loadTab() { const loaders = {transactions, exceptions, batches, audit}; if (loaders[state.tab]) await loaders[state.tab](); }
async function refresh() { await Promise.all([summary(), loadTab()]); }
async function enter() {
  state.user = await api("/api/auth/me");
  $("identity").textContent = `${state.user.name} · ${state.user.role}`;
  document.querySelectorAll("[data-write]").forEach(el => { el.hidden = !canWrite(); });
  $("login-panel").hidden = true; $("workspace").hidden = false;
  await refresh();
}
async function login(email, password, demo = false) {
  const result = await api("/api/auth/login", {method: "POST", body: JSON.stringify({email, password})});
  state.token = result.access_token; sessionStorage.setItem("finance-token", state.token);
  $("login-form").elements.password.value = ""; notice(""); await enter();
  if (demo) await loadDemo();
}
async function loadDemo() {
  notice("Loading and reconciling demo data…");
  const result = await api("/api/demo/load", {method: "POST"});
  await refresh(); notice(`Demo ready: ${result.total} transactions, ${(result.match_rate * 100).toFixed(1)}% matched.`);
}
$("login-form").addEventListener("submit", event => { event.preventDefault(); const form = event.currentTarget; busy(event.submitter, () => login(form.elements.email.value, form.elements.password.value)); });
$("demo-login").addEventListener("click", event => busy(event.currentTarget, () => login("admin@example.com", "admin123", true)));
$("logout").addEventListener("click", () => { signOut(); notice(""); });
$("refresh").addEventListener("click", event => busy(event.currentTarget, refresh));
$("load-demo").addEventListener("click", event => busy(event.currentTarget, loadDemo));
$("close-detail").addEventListener("click", () => $("detail").close());
document.querySelectorAll("[data-tab]").forEach(button => button.addEventListener("click", () => {
  state.tab = button.dataset.tab;
  document.querySelectorAll(".view").forEach(el => { el.hidden = el.id !== state.tab; });
  document.querySelectorAll("[data-tab]").forEach(el => el.setAttribute("aria-pressed", String(el === button)));
  busy(button, loadTab);
}));
$("search-form").addEventListener("submit", event => { event.preventDefault(); state.offset = 0; busy(event.submitter, transactions); });
$("previous").addEventListener("click", () => { state.offset = Math.max(0, state.offset - 50); busy(null, transactions); });
$("next").addEventListener("click", () => { if (state.offset + 50 < state.total) state.offset += 50; busy(null, transactions); });
$("exception-status").addEventListener("change", () => busy(null, exceptions));
$("upload-form").addEventListener("submit", event => {
  event.preventDefault(); const form = event.currentTarget; const body = new FormData(form);
  busy(event.submitter, async () => {
    notice("Validating and uploading CSV files…");
    const batch = await api("/api/batches/upload", {method: "POST", body});
    notice(`Batch #${batch.batch_id} uploaded. Reconciling…`);
    await api(`/api/batches/${batch.batch_id}/reconcile`, {method: "POST"});
    form.reset(); await refresh(); notice(`Batch #${batch.batch_id} reconciled.`);
  });
});
$("ask-form").addEventListener("submit", event => {
  event.preventDefault(); const question = event.currentTarget.elements.question.value;
  busy(event.submitter, async () => { const result = await api(`/api/ai/ask?${new URLSearchParams({question})}`, {method: "POST"}); $("answer").textContent = `${result.answer}\nSource: ${result.source}`; });
});
$("workspace").addEventListener("click", event => {
  const button = event.target.closest("button"); if (!button) return;
  if (button.dataset.record) busy(button, async () => showDetails("Transaction details", await api(`/api/reconciliations/${button.dataset.record}`)));
  if (button.dataset.analyze) busy(button, async () => showDetails("Exception analysis — recommendation only", await api(`/api/exceptions/${button.dataset.analyze}/analyze`, {method: "POST"})));
  if (button.dataset.review) {
    const comment = window.prompt(`Reason for ${button.dataset.review} (recorded in the audit trail):`);
    if (comment === null) return;
    if (!comment.trim()) { notice("Please provide a review reason.", true); return; }
    busy(button, async () => {
      await api(`/api/exceptions/${button.dataset.id}/${button.dataset.review}`, {method: "POST", body: JSON.stringify({comment})});
      await refresh(); notice("Review decision recorded. Deterministic financial findings are unchanged.");
    });
  }
  if (button.dataset.reconcile) busy(button, async () => { await api(`/api/batches/${button.dataset.reconcile}/reconcile`, {method: "POST"}); await refresh(); notice("Reconciliation completed."); });
});
if (state.token) busy(null, enter);
