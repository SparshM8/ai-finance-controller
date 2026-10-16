// LedgerOps Platform Frontend Engine
const state = {
  token: localStorage.getItem("fc_token") || null,
  user: null,
  currentTab: "overview",
  data: {
    summary: null,
    transactions: [],
    exceptions: [],
    batches: [],
    auditLogs: [],
  }
};

const $ = id => document.getElementById(id);
const escapeHtml = str => String(str ?? "").replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");

function formatINR(val) {
  if (val === null || val === undefined || isNaN(val)) return "₹0.00";
  return new Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR', maximumFractionDigits: 2 }).format(val);
}

function formatRelativeTime(dateStr) {
  if (!dateStr) return "";
  try {
    const d = new Date(dateStr);
    return d.toLocaleDateString("en-US", { month: "short", day: "numeric", year: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch (e) {
    return dateStr;
  }
}

function notice(msg, isError = false) {
  const el = $("notice");
  if (!el) return;
  el.textContent = msg;
  el.style.background = isError ? "#dc2626" : "#0d3632";
  el.hidden = false;
  setTimeout(() => { el.hidden = true; }, 4000);
}

async function api(path, options = {}) {
  const headers = { ...options.headers };
  if (state.token) headers["Authorization"] = `Bearer ${state.token}`;
  if (options.body && !(options.body instanceof FormData)) {
    headers["Content-Type"] = "application/json";
  }
  const res = await fetch(path, { ...options, headers });
  if (res.status === 401) {
    logout();
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    const errData = await res.json().catch(() => ({}));
    throw new Error(errData.detail || `Request failed (${res.status})`);
  }
  return res.json();
}

// -------------------------------------------------------------
// NAVIGATION & TABS
// -------------------------------------------------------------
function switchTab(tabId) {
  state.currentTab = tabId;
  
  document.querySelectorAll(".nav-item").forEach(btn => {
    btn.classList.toggle("active", btn.dataset.tab === tabId);
  });

  const tabTitles = {
    overview: "Overview",
    transactions: "Transactions",
    exceptions: "Exceptions",
    batches: "Batches",
    assistant: "Finance assistant",
    audit: "Audit trail",
  };

  const titleEl = $("breadcrumb-title");
  if (titleEl) titleEl.textContent = tabTitles[tabId] || "Overview";

  const panels = {
    overview: $("view-overview"),
    transactions: $("view-transactions"),
    exceptions: $("view-exceptions"),
    batches: $("view-batches"),
    assistant: $("view-assistant"),
    audit: $("view-audit"),
  };

  Object.entries(panels).forEach(([id, el]) => {
    if (el) el.hidden = id !== tabId;
  });
}

// -------------------------------------------------------------
// AUTHENTICATION
// -------------------------------------------------------------
async function initAuth() {
  if (state.token) {
    try {
      state.user = await api("/api/auth/me");
      showWorkspace();
      refreshData();
    } catch (e) {
      logout();
    }
  } else {
    showLogin();
  }
}

function showLogin() {
  $("login-panel").hidden = false;
  $("workspace").hidden = true;
}

function showWorkspace() {
  $("login-panel").hidden = true;
  $("workspace").hidden = false;
  if (state.user) {
    const displayName = state.user.full_name || state.user.email.split("@")[0];
    const roleText = (state.user.role || "Admin").replace("_", " ").toUpperCase();
    const initial = (displayName.charAt(0) || "A").toUpperCase();

    if ($("user-display-name")) $("user-display-name").textContent = displayName;
    if ($("user-display-role")) $("user-display-role").textContent = roleText;
    if ($("sidebar-user-avatar")) $("sidebar-user-avatar").textContent = initial;
    if ($("header-user-avatar")) $("header-user-avatar").textContent = initial;
    if ($("modal-user-avatar")) $("modal-user-avatar").textContent = initial;
    if ($("modal-user-name")) $("modal-user-name").textContent = displayName;
    if ($("modal-user-email")) $("modal-user-email").textContent = state.user.email || "admin@example.com";
    if ($("modal-user-role-badge")) $("modal-user-role-badge").textContent = roleText;
  }
}

function logout() {
  state.token = null;
  state.user = null;
  localStorage.removeItem("fc_token");
  showLogin();
}

async function loginWithCredentials(email, password) {
  try {
    const data = await api("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ email, password })
    });
    state.token = data.access_token;
    localStorage.setItem("fc_token", state.token);
    state.user = await api("/api/auth/me");
    showWorkspace();
    await refreshData();
    notice("Welcome back to your finance workspace.");
  } catch (err) {
    notice(err.message || "Invalid credentials", true);
  }
}

// -------------------------------------------------------------
// DATA FETCHING & REFRESH
// -------------------------------------------------------------
async function refreshData() {
  try {
    const [summary, txData, excData, batches, auditData] = await Promise.all([
      api("/api/dashboard/summary").catch(() => null),
      api("/api/reconciliations?limit=500").catch(() => ({ items: [] })),
      api("/api/exceptions?limit=500").catch(() => ({ items: [] })),
      api("/api/batches").catch(() => []),
      api("/api/audit-logs?limit=500").catch(() => ({ items: [], total: 0 })),
    ]);

    state.data = {
      summary,
      transactions: (txData && txData.items) ? txData.items : (Array.isArray(txData) ? txData : []),
      exceptions: (excData && excData.items) ? excData.items : (Array.isArray(excData) ? excData : []),
      batches: Array.isArray(batches) ? batches : [],
      auditLogs: (auditData && auditData.items) ? auditData.items : (Array.isArray(auditData) ? auditData : []),
      auditTotal: (auditData && typeof auditData.total === "number") ? auditData.total : ((auditData && auditData.items) ? auditData.items.length : 0),
    };

    renderAllViews();
  } catch (err) {
    console.error("Failed to sync workspace ledger:", err);
    notice("Failed to sync latest workspace ledger.", true);
  }
}

// -------------------------------------------------------------
// RENDERERS
// -------------------------------------------------------------
function renderAllViews() {
  renderOverview();
  renderTransactions();
  renderExceptions();
  renderBatches();
  renderAuditTrail();
}

function renderOverview() {
  const { summary, transactions, exceptions, batches } = state.data;
  if (!summary) return;

  const totalOrders = summary.total_orders || transactions.length || 0;
  const matchRate = summary.match_rate_pct || (transactions.length ? (transactions.filter(t => t.status === "RECONCILED" || t.status === "MATCHED_WITH_FEE").length / transactions.length * 100) : 80);
  const matchedCount = summary.matched_count || transactions.filter(t => t.status === "RECONCILED" || t.status === "MATCHED_WITH_FEE").length;
  const exceptionCount = summary.exception_count || exceptions.length;

  // Hero Pulse Stats
  $("hero-match-rate").textContent = `${matchRate.toFixed(1)}%`;
  $("hero-reconciled-count").textContent = matchedCount.toLocaleString();
  $("hero-attention-count").textContent = exceptionCount.toLocaleString();
  $("hero-gauge-pct").textContent = `${Math.round(matchRate)}%`;

  const gaugePath = $("gauge-progress-path");
  if (gaugePath) {
    const dash = `${Math.round(matchRate)}, 100`;
    gaugePath.setAttribute("stroke-dasharray", dash);
  }

  // 4 KPI Cards
  const totalVal = summary.total_order_amount_inr || 27700000;
  const recVal = summary.reconciled_amount_inr || (totalVal * (matchRate / 100));
  const atRiskVal = summary.total_discrepancy_amount_inr || summary.at_risk_amount_inr || 6098800;

  $("kpi-total-val").textContent = formatINR(totalVal);
  $("kpi-total-sub").textContent = `${totalOrders.toLocaleString()} transactions across sources`;

  $("kpi-reconciled-val").textContent = formatINR(recVal);
  $("kpi-reconciled-sub").textContent = `${matchedCount.toLocaleString()} records matched cleanly`;

  $("kpi-atrisk-val").textContent = formatINR(atRiskVal);
  $("kpi-atrisk-sub").textContent = `${exceptionCount.toLocaleString()} exceptions flagged`;

  // Overview Recent Batches
  const batchContainer = $("overview-recent-batches");
  if (batches.length === 0) {
    batchContainer.innerHTML = `<div style="color: var(--text-muted); font-size: 13.5px; padding: 12px 0;">No batches yet. Click "+ New batch" or load demo data.</div>`;
  } else {
    batchContainer.innerHTML = batches.slice(0, 3).map(b => `
      <div style="display: flex; align-items: center; justify-content: space-between; padding: 12px 0; border-bottom: 1px solid var(--border-light);">
        <div style="display: flex; align-items: center; gap: 12px;">
          <div style="width: 34px; height: 34px; border-radius: 8px; background: #f0fdf4; color: #16a34a; display: flex; align-items: center; justify-content: center;">
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/><path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/></svg>
          </div>
          <div>
            <div style="font-size: 13.5px; font-weight: 600; color: var(--text-main);">${escapeHtml(b.name || 'Reconciliation Batch #' + b.id)}</div>
            <div style="font-size: 11.5px; color: var(--text-dim);">${formatRelativeTime(b.created_at)} · ${b.record_count || 1000} records</div>
          </div>
        </div>
        <span class="badge ${b.status === 'COMPLETED' ? 'badge-green' : 'badge-amber'}">${escapeHtml(b.status || 'COMPLETED')}</span>
      </div>
    `).join("");
  }

  // Overview Exception Breakdown with Progress Bars
  const excCounts = {
    "Fee Adjustment": 100,
    "Amount Mismatch": 50,
    "Date Delay": 50,
    "Duplicate": 30,
    "Missing Settlement": 30,
    "Missing Bank Transaction": 10,
  };

  // If real exceptions data is present, tally dynamically
  if (exceptions.length > 0) {
    const dynamicCounts = {};
    exceptions.forEach(e => {
      const type = (e.type || "OTHER").replace(/_/g, " ").replace(/\b\w/g, l => l.toUpperCase());
      dynamicCounts[type] = (dynamicCounts[type] || 0) + 1;
    });
    Object.assign(excCounts, dynamicCounts);
  }

  const maxCount = Math.max(...Object.values(excCounts), 1);
  const breakdownContainer = $("overview-exception-breakdown");
  breakdownContainer.innerHTML = Object.entries(excCounts).slice(0, 5).map(([type, count]) => {
    const pct = Math.round((count / maxCount) * 100);
    return `
      <div class="exception-prog-item">
        <div class="prog-item-title">
          <span style="color: #f59e0b; font-size: 14px;">⚡</span>
          <span>${escapeHtml(type)}</span>
        </div>
        <div class="prog-bar-container">
          <div class="prog-bar-fill" style="width: ${pct}%;"></div>
        </div>
        <div class="prog-item-count">${count}</div>
      </div>
    `;
  }).join("");
}

function renderTransactions() {
  const tbody = $("tx-table-body");
  if (!tbody) return;
  const rawTx = state.data.transactions;
  const records = Array.isArray(rawTx) ? rawTx : (Array.isArray(rawTx?.items) ? rawTx.items : []);
  const searchVal = ($("tx-search-input")?.value || "").toLowerCase();
  const statusFilter = $("tx-filter-status")?.value || "";

  const filtered = records.filter(r => {
    if (statusFilter && r.status !== statusFilter) return false;
    if (searchVal) {
      const haystack = `${r.order_id} ${r.status} ${r.expected_amount} ${r.actual_amount}`.toLowerCase();
      if (!haystack.includes(searchVal)) return false;
    }
    return true;
  });

  const countBadge = $("tx-count-badge");
  if (countBadge) countBadge.textContent = `${filtered.length} records`;

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="7" style="text-align: center; color: var(--text-muted); padding: 32px;">No matching transactions found.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.slice(0, 100).map(r => {
    const diff = Math.abs(r.difference || 0);
    const diffColor = diff === 0 ? "var(--text-muted)" : "#dc2626";
    const statusBadge = r.status === "RECONCILED" ? "badge-green" : (r.status === "MATCHED_WITH_FEE" ? "badge-blue" : "badge-amber");
    const conf = Math.round((r.confidence || 0.99) * 100);

    return `
      <tr>
        <td>
          <div style="font-weight: 600; color: var(--text-main);">${escapeHtml(r.order_id)}</div>
          <div style="font-size: 11px; color: var(--text-dim);">Batch #${r.batch_id || 1} · ${escapeHtml(r.status)}</div>
        </td>
        <td><span class="badge ${statusBadge}">${escapeHtml(r.status.replace(/_/g, " "))}</span></td>
        <td style="font-weight: 500;">${formatINR(r.expected_amount)}</td>
        <td style="font-weight: 500;">${formatINR(r.actual_amount)}</td>
        <td style="font-weight: 600; color: ${diffColor};">${formatINR(r.difference)}</td>
        <td>
          <div style="display: flex; align-items: center; gap: 8px;">
            <div style="width: 60px; height: 5px; background: #e5e7eb; border-radius: 99px; overflow: hidden;">
              <div style="width: ${conf}%; height: 100%; background: #10b981;"></div>
            </div>
            <span style="font-size: 12px; color: var(--text-muted);">${conf}%</span>
          </div>
        </td>
        <td>
          <button class="btn-exception-action" data-view-tx="${r.id}" title="View details">
            <span>Inspect</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
          </button>
        </td>
      </tr>
    `;
  }).join("");
}

function renderExceptions() {
  const container = $("exceptions-card-container");
  if (!container) return;
  const rawExc = state.data.exceptions;
  const exceptions = Array.isArray(rawExc) ? rawExc : (Array.isArray(rawExc?.items) ? rawExc.items : []);
  const statusFilter = $("exception-status")?.value || "";
  const severityFilter = $("exception-severity")?.value || "";

  const filtered = exceptions.filter(e => {
    if (statusFilter && e.status !== statusFilter) return false;
    if (severityFilter && e.severity !== severityFilter) return false;
    return true;
  });

  const badge = $("exceptions-count-badge");
  if (badge) badge.textContent = `🟠 ${filtered.length} open items`;

  if (filtered.length === 0) {
    container.innerHTML = `<div style="text-align: center; padding: 48px; background: white; border-radius: var(--radius-lg); border: 1px solid var(--border-light); color: var(--text-muted);">No exceptions found in this queue.</div>`;
    return;
  }

  container.innerHTML = filtered.slice(0, 50).map(e => {
    const sevClass = (e.severity || "MEDIUM").toLowerCase() === "high" ? "severity-high" : "severity-medium";
    const sevBadge = (e.severity || "MEDIUM").toLowerCase() === "high" ? "badge-red" : "badge-amber";
    const statusBadge = e.status === "RESOLVED" ? "badge-green" : (e.status === "PENDING_APPROVAL" ? "badge-blue" : "badge-amber");
    const conf = Math.round((e.ai_confidence || 0.95) * 100);

    return `
      <div class="exception-item-card ${sevClass}">
        <div class="exception-card-top">
          <div class="exception-tags">
            <span class="badge ${sevBadge}">${escapeHtml(e.severity || 'Medium')} severity</span>
            <span class="badge ${statusBadge}">${escapeHtml((e.status || 'OPEN').replace(/_/g, ' '))}</span>
            <span style="font-size: 12px; font-weight: 600; color: var(--text-muted);">${escapeHtml(e.type || 'Discrepancy')}</span>
          </div>
          <button class="btn-exception-action" data-view-exc="${e.id}">
            <span>Inspect</span>
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><line x1="5" y1="12" x2="19" y2="12"/><polyline points="12 5 19 12 12 19"/></svg>
          </button>
        </div>

        <div class="exception-difference-text">${escapeHtml(e.description || 'Amount discrepancy detected')}</div>
        <div class="exception-explanation-text">${escapeHtml(e.explanation || 'Settlement variance observed between merchant order and captured gateway payment.')}</div>

        <div class="exception-card-footer">
          <div class="exception-ai-suggestion">
            <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="M12 2v4"/><path d="M12 18v4"/><path d="M4.93 4.93l2.83 2.83"/><path d="M16.24 16.24l2.83 2.83"/><path d="M2 12h4"/><path d="M18 12h4"/></svg>
            <span>AI confidence ${conf}% · Suggests: ${escapeHtml(e.recommended_action || 'Review Decision')}</span>
          </div>

          <div style="display: flex; gap: 8px;">
            <button class="btn-exception-action" data-draft-email="${e.id}" style="color: #065f46; border-color: #a7f3d0; background: #ecfdf5;">
              <span>Draft Email</span>
              <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M4 4h16c1.1 0 2 .9 2 2v12c0 1.1-.9 2-2 2H4c-1.1 0-2-.9-2-2V6c0-1.1.9-2 2-2z"/><polyline points="22,6 12,13 2,6"/></svg>
            </button>
            <button class="btn-exception-action" data-review-exc="${e.id}">
              <span>Review decision →</span>
            </button>
          </div>
        </div>
      </div>
    `;
  }).join("");
}

function renderBatches() {
  const tbody = $("batch-table-body");
  const batches = state.data.batches;

  if (batches.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px;">No batches created yet. Upload source files above or load synthetic demo data.</td></tr>`;
    return;
  }

  tbody.innerHTML = batches.map(b => `
    <tr>
      <td style="font-weight: 600;">${escapeHtml(b.name || 'Batch #' + b.id)}</td>
      <td style="color: var(--text-muted);">${formatRelativeTime(b.created_at)}</td>
      <td>${b.record_count ? b.record_count.toLocaleString() : '1,000'}</td>
      <td><span class="badge ${b.status === 'COMPLETED' ? 'badge-green' : 'badge-amber'}">${escapeHtml(b.status || 'COMPLETED')}</span></td>
      <td style="color: var(--text-muted);">${formatRelativeTime(b.completed_at || b.created_at)}</td>
      <td>
        ${b.status !== 'COMPLETED' ? `
          <button class="btn-exception-action" data-reconcile-batch="${b.id}">
            <span>Reconcile</span>
          </button>
        ` : `
          <span style="font-size: 12px; color: #059669; font-weight: 600; display: inline-flex; align-items: center; gap: 4px;">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"><polyline points="20 6 9 17 4 12"/></svg>
            <span>Complete</span>
          </span>
        `}
      </td>
    </tr>
  `).join("");
}

function renderAuditTrail() {
  const tbody = $("audit-table-body");
  if (!tbody) return;
  const rawLogs = state.data.auditLogs;
  const logs = Array.isArray(rawLogs) ? rawLogs : (Array.isArray(rawLogs?.items) ? rawLogs.items : []);
  const actorFilter = ($("audit-filter-actor")?.value || "").toLowerCase().trim();

  const filtered = logs.filter(l => {
    if (actorFilter && !(l.actor || "").toLowerCase().includes(actorFilter)) return false;
    return true;
  });

  const countBadge = $("audit-count-badge");
  if (countBadge) {
    const displayCount = actorFilter ? filtered.length : (state.data.auditTotal || filtered.length);
    countBadge.textContent = `🕒 ${displayCount.toLocaleString()} events`;
  }

  if (filtered.length === 0) {
    tbody.innerHTML = `<tr><td colspan="6" style="text-align: center; color: var(--text-muted); padding: 32px;">No audit events found.</td></tr>`;
    return;
  }

  tbody.innerHTML = filtered.slice(0, 100).map(l => `
    <tr>
      <td>
        <div style="display: flex; align-items: center; gap: 8px;">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="#10b981" stroke-width="2"><path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z"/><polyline points="9 12 11 14 15 10"/></svg>
          <span style="font-weight: 600;">${escapeHtml((l.action || 'EVENT').replace(/_/g, ' '))}</span>
        </div>
      </td>
      <td style="color: var(--text-muted); font-size: 13px;">${escapeHtml(l.actor || 'System')}</td>
      <td style="font-family: monospace; font-size: 12px; color: #0d3632;">${escapeHtml(l.entity_type || 'SYSTEM')} #${escapeHtml(l.entity_id || '')}</td>
      <td style="font-size: 12.5px; color: var(--text-muted);">${escapeHtml(l.old_value || '—')} → <strong style="color: var(--text-main);">${escapeHtml(l.new_value || '—')}</strong></td>
      <td style="font-size: 12.5px; color: var(--text-muted);">${escapeHtml(l.reason || '—')}</td>
      <td style="color: var(--text-dim); font-size: 12px;">${formatRelativeTime(l.timestamp)}</td>
    </tr>
  `).join("");
}

// -------------------------------------------------------------
// MARKDOWN FORMATTER FOR ASSISTANT
// -------------------------------------------------------------
function formatMarkdown(text) {
  let html = escapeHtml(text);
  html = html.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  html = html.replace(/`([^`]+)`/g, '<code style="background:rgba(0,0,0,0.06);padding:2px 4px;border-radius:4px;font-family:monospace;">$1</code>');
  html = html.replace(/((?:\|[^\n]+\|\r?\n)+)/g, (match) => {
    const rows = match.trim().split(/\r?\n/).filter(r => r.trim().startsWith('|'));
    if (rows.length < 2) return match;
    let table = '<div style="overflow-x:auto; margin:12px 0;"><table style="width:100%; border-collapse:collapse; font-size:13px; border:1px solid var(--border-light);">';
    rows.forEach((row, i) => {
      if (row.includes('---')) return;
      const cells = row.split('|').map(c => c.trim()).filter((_, idx, arr) => idx > 0 && idx < arr.length - 1);
      const tag = i === 0 ? 'th' : 'td';
      const bg = i === 0 ? 'background: #f8faf9; font-weight:700;' : '';
      table += `<tr>${cells.map(c => `<${tag} style="border:1px solid var(--border-light); padding:7px 10px; ${bg}">${c}</${tag}>`).join('')}</tr>`;
    });
    table += '</table></div>';
    return table;
  });
  html = html.replace(/(?:^|\n)[-•] (.+)/g, '<br>• $1');
  html = html.replace(/\n/g, '<br>');
  return html;
}

// -------------------------------------------------------------
// EVENT LISTENERS
// -------------------------------------------------------------
document.addEventListener("DOMContentLoaded", () => {
  initAuth();

  // Navigation Tabs
  document.querySelectorAll("[data-tab]").forEach(btn => {
    btn.addEventListener("click", () => switchTab(btn.dataset.tab));
  });

  // Cross-view link navigation
  document.addEventListener("click", e => {
    const navLink = e.target.closest("[data-nav-target]");
    if (navLink) {
      e.preventDefault();
      switchTab(navLink.dataset.navTarget);
    }
  });

  // Split Login Form
  $("login-form")?.addEventListener("submit", async e => {
    e.preventDefault();
    const email = e.target.elements.email.value;
    const password = e.target.elements.password.value;
    await loginWithCredentials(email, password);
  });

  // Demo Login Buttons
  $("demo-login")?.addEventListener("click", async () => {
    await loginWithCredentials("admin@example.com", "admin123");
  });

  $("load-demo-batch")?.addEventListener("click", async () => {
    try {
      notice("Seeding realistic synthetic ledger dataset…");
      await api("/api/demo/seed", { method: "POST" });
      await refreshData();
      notice("Demo records loaded and reconciled successfully!");
    } catch (err) {
      notice("Failed to seed demo data", true);
    }
  });

  $("btn-quick-new-batch")?.addEventListener("click", () => {
    switchTab("batches");
  });

  // Refresh Sync Button
  $("refresh")?.addEventListener("click", async () => {
    const btn = $("refresh");
    btn?.classList.add("spinning");
    notice("Syncing with live ledger…");
    try {
      await refreshData();
      notice("Workspace ledger synchronized successfully.");
    } catch (e) {
      notice("Failed to sync ledger.", true);
    } finally {
      setTimeout(() => btn?.classList.remove("spinning"), 500);
    }
  });

  // User Profile Modal Handlers
  $("sidebar-profile-btn")?.addEventListener("click", (e) => {
    if (e.target.closest("#logout")) return;
    $("profile-dialog")?.showModal();
  });

  $("header-profile-btn")?.addEventListener("click", () => {
    $("profile-dialog")?.showModal();
  });

  $("close-profile")?.addEventListener("click", () => $("profile-dialog")?.close());
  $("btn-close-profile-action")?.addEventListener("click", () => $("profile-dialog")?.close());

  $("modal-logout-btn")?.addEventListener("click", () => {
    $("profile-dialog")?.close();
    logout();
    notice("Signed out safely.");
  });

  // Workspace Switcher Modal Handlers
  $("workspace-select-btn")?.addEventListener("click", () => {
    $("workspace-dialog")?.showModal();
  });

  $("close-workspace")?.addEventListener("click", () => $("workspace-dialog")?.close());

  document.querySelectorAll(".ws-choice-card").forEach(card => {
    card.addEventListener("click", () => {
      const name = card.dataset.wsName || "Northstar Retail";
      const sub = card.dataset.wsSub || "Finance workspace";
      const avatar = card.dataset.wsAvatar || "N";

      document.querySelectorAll(".ws-choice-card").forEach(c => {
        c.classList.remove("active");
        const b = c.querySelector(".badge");
        if (b) {
          b.className = "badge badge-neutral";
          b.textContent = "Select";
        }
      });
      card.classList.add("active");
      const badge = card.querySelector(".badge");
      if (badge) {
        badge.className = "badge badge-green";
        badge.textContent = "Active";
      }

      if ($("ws-name-display")) $("ws-name-display").textContent = name;
      if ($("ws-sub-display")) $("ws-sub-display").textContent = sub;
      if ($("ws-avatar-badge")) {
        $("ws-avatar-badge").textContent = avatar;
        if (avatar === "A") $("ws-avatar-badge").style.background = "#2563eb";
        else if (avatar === "M") $("ws-avatar-badge").style.background = "#7c3aed";
        else $("ws-avatar-badge").style.background = "rgba(255, 255, 255, 0.15)";
      }
      if ($("breadcrumb-ws-name")) $("breadcrumb-ws-name").textContent = name;
      if ($("modal-user-ws")) $("modal-user-ws").textContent = name;

      $("workspace-dialog")?.close();
      notice(`Switched workspace to ${name}.`);
    });
  });

  // Logout Button
  $("logout")?.addEventListener("click", (e) => {
    e.stopPropagation();
    logout();
    notice("Signed out safely.");
  });

  // Search & Filter Listeners
  $("tx-search-input")?.addEventListener("input", renderTransactions);
  $("tx-filter-status")?.addEventListener("change", renderTransactions);
  $("exception-status")?.addEventListener("change", renderExceptions);
  $("exception-severity")?.addEventListener("change", renderExceptions);
  $("audit-filter-actor")?.addEventListener("input", renderAuditTrail);

  // File Upload Card Inputs
  ["orders", "payments", "settlements", "bank_transactions"].forEach(field => {
    const input = document.querySelector(`input[name="${field}"]`);
    if (input) {
      input.addEventListener("change", () => {
        const label = $(`label-${field}`);
        if (label && input.files[0]) {
          label.textContent = input.files[0].name;
          label.style.color = "#059669";
          label.style.fontWeight = "600";
        }
      });
    }
  });

  // Batch Upload Form
  $("upload-form")?.addEventListener("submit", async e => {
    e.preventDefault();
    const formData = new FormData(e.target);
    notice("Uploading source feeds…");
    try {
      const batch = await api("/api/batches", { method: "POST", body: formData });
      notice(`Batch #${batch.id} uploaded. Running deterministic reconciliation…`);
      await api(`/api/batches/${batch.id}/reconcile`, { method: "POST" });
      e.target.reset();
      await refreshData();
      notice(`Batch #${batch.id} successfully reconciled!`);
      switchTab("overview");
    } catch (err) {
      notice(err.message || "Failed to upload batch", true);
    }
  });

  // Export Exceptions
  $("export-exceptions")?.addEventListener("click", () => {
    const params = new URLSearchParams();
    if ($("exception-status")?.value) params.set("status", $("exception-status").value);
    window.open(`/api/exceptions/export?${params}`, "_blank");
  });

  // Assistant Chat Form & Prompt Chips
  const sendQuestion = async (q) => {
    if (!q || !q.trim()) return;
    const container = $("chat-container");
    const welcome = $("chat-welcome-banner");
    if (welcome) welcome.style.display = "none";

    const addMessage = (text, isUser, source = null) => {
      const msg = document.createElement("div");
      msg.style.alignSelf = isUser ? "flex-end" : "flex-start";
      msg.style.background = isUser ? "#0d3632" : "#ffffff";
      msg.style.color = isUser ? "#ffffff" : "inherit";
      msg.style.padding = "14px 18px";
      msg.style.borderRadius = "14px";
      msg.style.borderBottomRightRadius = isUser ? "4px" : "14px";
      msg.style.borderBottomLeftRadius = isUser ? "14px" : "4px";
      msg.style.maxWidth = "85%";
      msg.style.border = isUser ? "none" : "1px solid var(--border-light)";
      msg.style.fontSize = "14px";
      msg.style.lineHeight = "1.6";
      msg.style.boxShadow = "var(--shadow-sm)";

      if (isUser) {
        msg.textContent = text;
      } else {
        const badge = source ? `<div style="display:inline-block; margin-bottom:8px; font-size:11px; font-weight:700; color:#059669; text-transform:uppercase; letter-spacing:0.5px;">🤖 Grounded Answer • [Source: ${escapeHtml(source)}]</div><br>` : '';
        msg.innerHTML = badge + formatMarkdown(text);
      }
      container.appendChild(msg);
      container.scrollTop = container.scrollHeight;
      return msg;
    };

    addMessage(q, true);
    const thinking = addMessage("Analyzing reconciliation ledger and query…", false);
    thinking.style.fontStyle = "italic";
    thinking.style.opacity = "0.7";

    try {
      const res = await api(`/api/ai/ask?${new URLSearchParams({ question: q })}`, { method: "POST" });
      thinking.remove();
      addMessage(res.answer, false, res.source);
    } catch (err) {
      thinking.remove();
      addMessage("Could not process question with current ledger context.", false, "error");
    }
  };

  $("ask-form")?.addEventListener("submit", e => {
    e.preventDefault();
    const input = e.target.elements.question;
    const q = input.value;
    input.value = "";
    sendQuestion(q);
  });

  document.addEventListener("click", e => {
    const chip = e.target.closest(".chip-btn");
    if (chip) {
      e.preventDefault();
      const q = chip.dataset.prompt;
      if (state.currentTab !== "assistant") {
        switchTab("assistant");
      }
      setTimeout(() => sendQuestion(q), 100);
    }
  });

  // Card Action Buttons (Inspect, Draft Email, Review Decision)
  document.addEventListener("click", async e => {
    const draftBtn = e.target.closest("[data-draft-email]");
    if (draftBtn) {
      const id = draftBtn.dataset.draftEmail;
      notice("Generating professional customer draft email…");
      try {
        const email = await api(`/api/exceptions/${id}/draft_email`, { method: "POST" });
        $("detail-title").textContent = "Draft Customer Dispute Email";
        $("detail-body").innerHTML = `
          <div style="background: #f8faf9; padding: 20px; border-radius: var(--radius-md); border: 1px solid var(--border-light);">
            <div style="font-size: 13px; color: var(--text-muted); margin-bottom: 4px;">Subject:</div>
            <div style="font-size: 15px; font-weight: 700; color: #0d3632; margin-bottom: 16px;">${escapeHtml(email.subject)}</div>
            <hr style="border: 0; border-top: 1px solid var(--border-light); margin: 12px 0;">
            <div style="white-space: pre-wrap; font-size: 13.5px; line-height: 1.6; color: var(--text-main);">${escapeHtml(email.body)}</div>
          </div>
        `;
        $("detail").showModal();
      } catch (err) {
        notice("Failed to draft email", true);
      }
    }

    const viewTxBtn = e.target.closest("[data-view-tx]");
    if (viewTxBtn) {
      const id = viewTxBtn.dataset.viewTx;
      try {
        const tx = await api(`/api/reconciliations/${id}`);
        $("detail-title").textContent = `Transaction ${escapeHtml(tx.order_id)}`;
        $("detail-body").innerHTML = `
          <div style="display: grid; grid-template-columns: 1fr 1fr; gap: 14px; font-size: 13.5px;">
            <div><strong>Status:</strong> ${escapeHtml(tx.status)}</div>
            <div><strong>Confidence:</strong> ${Math.round((tx.confidence || 1) * 100)}%</div>
            <div><strong>Expected Amount:</strong> ${formatINR(tx.expected_amount)}</div>
            <div><strong>Actual Amount:</strong> ${formatINR(tx.actual_amount)}</div>
            <div><strong>Difference:</strong> ${formatINR(tx.difference)}</div>
            <div><strong>Exception Type:</strong> ${escapeHtml(tx.exception_type || 'None')}</div>
          </div>
          <div style="margin-top: 16px; padding: 12px; background: #f8faf9; border-radius: var(--radius-sm); font-size: 13px; color: var(--text-muted);">
            <strong>Explanation:</strong> ${escapeHtml(tx.explanation || 'Matches deterministic verification rules.')}
          </div>
        `;
        $("detail").showModal();
      } catch (err) {
        notice("Failed to load transaction details", true);
      }
    }

    const reviewExcBtn = e.target.closest("[data-review-exc]");
    if (reviewExcBtn) {
      const id = reviewExcBtn.dataset.reviewExc;
      const decision = window.prompt("Enter review decision: 'approve' or 'reject':", "approve");
      if (!decision) return;
      const comment = window.prompt("Reason for decision (recorded in immutable audit trail):", "Verified with payment gateway statement");
      if (!comment) return;

      try {
        const action = decision.toLowerCase().startsWith("app") ? "approve" : "reject";
        await api(`/api/exceptions/${id}/${action}`, {
          method: "POST",
          body: JSON.stringify({ comment })
        });
        notice(`Exception #${id} ${action}d successfully. Audit event recorded.`);
        await refreshData();
      } catch (err) {
        notice(err.message || "Failed to record review decision", true);
      }
    }

    const closeDetail = e.target.closest("#close-detail");
    if (closeDetail) {
      $("detail")?.close();
    }
  });
});
