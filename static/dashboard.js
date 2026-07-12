// ── Utilities ──────────────────────────────────────────────────────────────
function esc(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

// ── DOM refs ───────────────────────────────────────────────────────────────
const docList         = document.getElementById("docList");
const refreshBtn      = document.getElementById("refreshBtn");
const fileUpload      = document.getElementById("fileUpload");
const uploadProgress  = document.getElementById("uploadProgress");
const websiteUrlEl    = document.getElementById("websiteUrl");
const websiteSaveBtn  = document.getElementById("websiteSaveBtn");
const websiteStatusEl = document.getElementById("websiteStatus");
const websiteCrawlSection = document.getElementById("websiteCrawlSection");
const embedCodeEl     = document.getElementById("embedCode");
const copyEmbedBtn    = document.getElementById("copyEmbedBtn");
const crawlBtn        = document.getElementById("crawlBtn");
const crawlPagesCount = document.getElementById("crawlPagesCount");

// Playground refs
const pgNameEl      = document.getElementById("pgBotName");
const pgSubtitleEl  = document.getElementById("pgBotSubtitle");
const pgWelcomeEl   = document.getElementById("pgWelcome");
const pgCityEl      = document.getElementById("pgCity");
const pgColorPicker = document.getElementById("pgColorPicker");
const pgColorHex    = document.getElementById("pgColorHex");
const pgSugInputs   = [0,1,2,3].map(i => document.getElementById("pgSug" + i));
const pgSaveBtn     = document.getElementById("pgSaveBtn");
const pgStatusEl    = document.getElementById("pgStatus");

// ── Document list ──────────────────────────────────────────────────────────
function visibilityBadgeHTML(visibility) {
  if (visibility === "staff_and_chatbot") {
    return `<span class="doc-visibility-badge staff-chatbot">Staff + Chatbot</span>`;
  }
  return `<span class="doc-visibility-badge staff-only">Staff only</span>`;
}

function visibilityToggleHTML(id, visibility) {
  const isPublic = visibility === "staff_and_chatbot";
  return `
    <button class="doc-visibility-toggle ${isPublic ? "is-public" : ""}"
            data-id="${id}"
            data-visibility="${esc(visibility)}"
            title="${isPublic ? "Click to restrict to staff only" : "Click to also share with chatbot"}">
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        ${isPublic
          ? `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>`
          : `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/>`
        }
      </svg>
    </button>
  `;
}

async function loadDocuments() {
  docList.innerHTML = '<div class="doc-empty-full">Loading…</div>';
  try {
    const res = await fetch("/api/documents");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    const docs = data.documents || [];

    if (!docs.length) {
      docList.innerHTML = '<div class="doc-empty-full">No documents uploaded yet. Click "Upload PDF or DOCX" to add your first document.</div>';
      return;
    }

    docList.innerHTML = docs.map(d => `
      <div class="doc-item-full" data-id="${d.id}">
        <span class="doc-type-badge ${esc(d.type)}">${esc(d.type.toUpperCase())}</span>
        <div class="doc-info-full">
          <div class="doc-name-full" title="${esc(d.name)}">${esc(d.name)}</div>
          <div class="doc-size-full">${d.size_kb} KB</div>
        </div>
        ${visibilityBadgeHTML(d.visibility)}
        ${visibilityToggleHTML(d.id, d.visibility)}
        <button class="doc-delete-btn" data-id="${d.id}" title="Delete document" aria-label="Delete ${esc(d.name)}">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
          </svg>
        </button>
      </div>
    `).join("");

    docList.querySelectorAll(".doc-delete-btn").forEach(btn => {
      btn.addEventListener("click", e => {
        e.stopPropagation();
        deleteDocument(parseInt(btn.dataset.id));
      });
    });

    docList.querySelectorAll(".doc-visibility-toggle").forEach(btn => {
      btn.addEventListener("click", e => {
        e.stopPropagation();
        const current = btn.dataset.visibility;
        const next = current === "staff_only" ? "staff_and_chatbot" : "staff_only";
        toggleVisibility(parseInt(btn.dataset.id), next, btn);
      });
    });
  } catch {
    docList.innerHTML = '<div class="doc-empty-full">Could not load documents.</div>';
  }
}

// ── Upload ─────────────────────────────────────────────────────────────────
async function uploadDocument(file) {
  uploadProgress.classList.add("visible");
  const formData = new FormData();
  formData.append("file", file);
  try {
    const res  = await fetch("/api/documents/upload", { method: "POST", body: formData });
    const data = await res.json();
    if (!res.ok) { alert(data.error || "Upload failed."); return; }
    await loadDocuments();
  } catch {
    alert("Upload failed. Please check your connection and try again.");
  } finally {
    uploadProgress.classList.remove("visible");
  }
}

fileUpload.addEventListener("change", async e => {
  const file = e.target.files[0];
  if (!file) return;
  await uploadDocument(file);
  fileUpload.value = "";
});

// ── Delete ─────────────────────────────────────────────────────────────────
async function deleteDocument(id) {
  if (!confirm("Delete this document? It will no longer be available for chat.")) return;
  try {
    const res = await fetch(`/api/documents/${id}`, { method: "DELETE" });
    if (res.ok) { await loadDocuments(); return; }
    const data = await res.json();
    alert(data.error || "Delete failed.");
  } catch {
    alert("Delete failed. Please try again.");
  }
}

// ── Visibility toggle ───────────────────────────────────────────────────────
async function toggleVisibility(id, newVisibility, btn) {
  btn.disabled = true;
  try {
    const res = await fetch(`/api/documents/${id}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ visibility: newVisibility }),
    });
    if (!res.ok) {
      const data = await res.json();
      alert(data.error || "Could not update visibility.");
      btn.disabled = false;
      return;
    }
    // Update the row in-place without a full reload
    const row = btn.closest(".doc-item-full");
    const badge = row.querySelector(".doc-visibility-badge");
    const isPublic = newVisibility === "staff_and_chatbot";
    badge.className = `doc-visibility-badge ${isPublic ? "staff-chatbot" : "staff-only"}`;
    badge.textContent = isPublic ? "Staff + Chatbot" : "Staff only";
    btn.dataset.visibility = newVisibility;
    btn.className = `doc-visibility-toggle ${isPublic ? "is-public" : ""}`;
    btn.title = isPublic ? "Click to restrict to staff only" : "Click to also share with chatbot";
    btn.innerHTML = `
      <svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
        ${isPublic
          ? `<path d="M1 12s4-8 11-8 11 8 11 8-4 8-11 8-11-8-11-8z"/><circle cx="12" cy="12" r="3"/>`
          : `<path d="M17.94 17.94A10.07 10.07 0 0 1 12 20c-7 0-11-8-11-8a18.45 18.45 0 0 1 5.06-5.94"/><path d="M9.9 4.24A9.12 9.12 0 0 1 12 4c7 0 11 8 11 8a18.5 18.5 0 0 1-2.16 3.19"/><line x1="1" y1="1" x2="23" y2="23"/>`
        }
      </svg>`;
  } catch {
    alert("Could not update visibility. Please try again.");
  } finally {
    btn.disabled = false;
  }
}

// ── Website chatbot ────────────────────────────────────────────────────────
function formatCrawlDate(iso) {
  if (!iso) return null;
  const d = new Date(iso);
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" });
}

async function loadWebsiteSettings() {
  websiteStatusEl.textContent = "Loading…";
  websiteStatusEl.className = "website-status";
  try {
    const res  = await fetch("/api/church/settings");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();

    websiteUrlEl.value = data.website_url || "";

    const churchId = data.church_id || window.CHURCH_ID;
    const embedSnippet = `<script src="https://app.wesleyai.co/widget.js" data-church-id="${churchId}"><\/script>`;
    embedCodeEl.textContent = embedSnippet;

    if (data.website_url) {
      const lastCrawled = data.last_crawled_at
        ? `Last crawled ${formatCrawlDate(data.last_crawled_at)}`
        : "Not yet crawled";
      websiteStatusEl.textContent = lastCrawled;
      websiteStatusEl.className = "website-status ok";
      crawlPagesCount.textContent = data.page_count
        ? `${data.page_count.toLocaleString()} pages indexed`
        : "0 pages indexed";
      if (websiteCrawlSection) websiteCrawlSection.style.display = "";
    } else {
      websiteStatusEl.textContent = "Enter your church website URL to enable the chatbot widget.";
      websiteStatusEl.className = "website-status";
      if (websiteCrawlSection) websiteCrawlSection.style.display = "none";
    }
  } catch {
    websiteStatusEl.textContent = "Could not load website settings.";
    websiteStatusEl.className = "website-status err";
  }
}

async function saveWebsiteUrl() {
  const url = websiteUrlEl.value.trim();
  websiteSaveBtn.disabled = true;
  websiteSaveBtn.textContent = "Saving…";
  try {
    const res  = await fetch("/api/church/settings", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ website_url: url }),
    });
    const data = await res.json();
    if (res.ok) {
      await loadWebsiteSettings();
    } else {
      websiteStatusEl.textContent = data.error || "Save failed.";
      websiteStatusEl.className = "website-status err";
    }
  } catch {
    websiteStatusEl.textContent = "Network error. Please try again.";
    websiteStatusEl.className = "website-status err";
  } finally {
    websiteSaveBtn.disabled = false;
    websiteSaveBtn.textContent = "Save";
  }
}

async function triggerCrawl() {
  crawlBtn.disabled = true;
  crawlBtn.innerHTML = `
    <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
         stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"
         style="animation:spin 1s linear infinite">
      <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
      <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
    </svg>
    Crawling…`;
  try {
    const res  = await fetch("/api/church/crawl", { method: "POST" });
    const data = await res.json();
    if (res.ok) {
      websiteStatusEl.textContent = "Crawl started — this runs in the background. Refresh in a minute.";
      websiteStatusEl.className = "website-status ok";
    } else {
      websiteStatusEl.textContent = data.error || "Crawl failed to start.";
      websiteStatusEl.className = "website-status err";
    }
  } catch {
    websiteStatusEl.textContent = "Network error starting crawl.";
    websiteStatusEl.className = "website-status err";
  } finally {
    setTimeout(() => {
      crawlBtn.disabled = false;
      crawlBtn.innerHTML = `
        <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor"
             stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
          <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
          <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
        </svg>
        Re-crawl now`;
    }, 10_000);
  }
}

async function copyEmbedCode() {
  const snippet = embedCodeEl.textContent;
  try {
    await navigator.clipboard.writeText(snippet);
    copyEmbedBtn.textContent = "Copied!";
    setTimeout(() => { copyEmbedBtn.textContent = "Copy"; }, 2000);
  } catch {
    const range = document.createRange();
    range.selectNode(embedCodeEl);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    copyEmbedBtn.textContent = "Selected";
    setTimeout(() => { copyEmbedBtn.textContent = "Copy"; }, 2000);
  }
}

// ── Playground (Customise + Live Preview) ──────────────────────────────────
const HEX_RE = /^#[0-9a-fA-F]{6}$/;

/** Singleton WesleyWidget instance used for the playground preview. */
let _previewWidget = null;

/** Build a branding config object from the current form field values. */
const DEFAULT_BOT_NAME = "Wesley";
const DEFAULT_WELCOME  = "How can I help you today?";
const DEFAULT_COLOR    = "#0a3d3d";

function getCurrentConfig() {
  const rawColor = pgColorHex ? pgColorHex.value.trim() : DEFAULT_COLOR;
  return {
    bot_name:          pgNameEl      ? (pgNameEl.value.trim()      || DEFAULT_BOT_NAME) : DEFAULT_BOT_NAME,
    bot_subtitle:      pgSubtitleEl  ? pgSubtitleEl.value.trim()                        : "",
    welcome_message:   pgWelcomeEl   ? (pgWelcomeEl.value.trim()   || DEFAULT_WELCOME)  : DEFAULT_WELCOME,
    primary_color:     HEX_RE.test(rawColor) ? rawColor : DEFAULT_COLOR,
    church_city:       pgCityEl      ? pgCityEl.value.trim()  : "",
    starter_questions: pgSugInputs.map(el => el ? el.value.trim() : ""),
  };
}

/**
 * Sync the live preview panel with the current form values.
 * On first call, mounts a WesleyWidget in preview mode into #pg-widget-mount.
 * On subsequent calls, patches the existing widget via .update().
 */
function updatePreview() {
  const config  = getCurrentConfig();
  const mountEl = document.getElementById("pg-widget-mount");
  if (!mountEl) return;

  if (!_previewWidget) {
    // WesleyWidget is provided by widget-core.js, loaded before this script.
    _previewWidget = new WesleyWidget({
      previewMode: true,
      container:   mountEl,
      config:      config,
    });
  } else {
    _previewWidget.update(config);
  }
}

async function loadPlaygroundSettings() {
  try {
    const res  = await fetch("/api/church/branding");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();

    if (pgNameEl)     pgNameEl.value     = data.bot_name        || "";
    if (pgSubtitleEl) pgSubtitleEl.value = data.bot_subtitle    || "";
    if (pgWelcomeEl)  pgWelcomeEl.value  = data.welcome_message || "";
    if (pgCityEl)     pgCityEl.value     = data.church_city     || "";

    const color = data.primary_color || "#0a3d3d";
    if (pgColorHex)    pgColorHex.value    = color;
    if (pgColorPicker) pgColorPicker.value = HEX_RE.test(color) ? color : "#0a3d3d";

    // Starter questions
    const sugs = data.starter_questions || [];
    pgSugInputs.forEach((input, i) => { if (input) input.value = sugs[i] || ""; });

    updatePreview();
  } catch {
    // non-critical — leave fields blank, run preview with defaults
    updatePreview();
  }
}

async function savePlaygroundSettings() {
  const hex = pgColorHex ? pgColorHex.value.trim() : "";
  if (hex && !HEX_RE.test(hex)) {
    if (pgStatusEl) { pgStatusEl.textContent = "Invalid color — use format #rrggbb."; pgStatusEl.className = "pg-save-status err"; }
    return;
  }

  if (pgSaveBtn) { pgSaveBtn.disabled = true; pgSaveBtn.textContent = "Saving…"; }
  if (pgStatusEl) { pgStatusEl.textContent = ""; pgStatusEl.className = "pg-save-status"; }

  try {
    const res  = await fetch("/api/church/branding", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bot_name:          pgNameEl      ? pgNameEl.value.trim()      : "",
        bot_subtitle:      pgSubtitleEl  ? pgSubtitleEl.value.trim()  : "",
        welcome_message:   pgWelcomeEl   ? pgWelcomeEl.value.trim()   : "",
        primary_color:     hex || "#0a3d3d",
        church_city:       pgCityEl      ? pgCityEl.value.trim()      : "",
        starter_questions: pgSugInputs.map(el => el ? el.value.trim() : ""),
      }),
    });
    const data = await res.json();
    if (res.ok) {
      if (pgStatusEl) { pgStatusEl.textContent = "Saved!"; pgStatusEl.className = "pg-save-status ok"; }
    } else {
      if (pgStatusEl) { pgStatusEl.textContent = data.error || "Save failed."; pgStatusEl.className = "pg-save-status err"; }
    }
  } catch {
    if (pgStatusEl) { pgStatusEl.textContent = "Network error. Please try again."; pgStatusEl.className = "pg-save-status err"; }
  } finally {
    if (pgSaveBtn) { pgSaveBtn.disabled = false; pgSaveBtn.textContent = "Save Changes"; }
  }
}

// Color picker ↔ hex text sync + live preview
if (pgColorPicker) {
  pgColorPicker.addEventListener("input", () => {
    if (pgColorHex) pgColorHex.value = pgColorPicker.value;
    updatePreview();
  });
}
if (pgColorHex) {
  pgColorHex.addEventListener("input", () => {
    const v = pgColorHex.value.trim();
    if (HEX_RE.test(v) && pgColorPicker) pgColorPicker.value = v;
    updatePreview();
  });
}
// Live preview on every text input change
[pgNameEl, pgSubtitleEl, pgWelcomeEl, pgCityEl, ...pgSugInputs].forEach(el => {
  if (el) el.addEventListener("input", updatePreview);
});

// ── Widget Conversations ───────────────────────────────────────────────────
const wconvListEl    = document.getElementById("wconvList");
const wconvRefreshBtn = document.getElementById("wconvRefreshBtn");

function formatWconvDate(iso) {
  if (!iso) return "";
  // Server timestamps are UTC; treat strings without a zone marker as UTC.
  const hasZone = /(?:Z|[+-]\d{2}:?\d{2})$/i.test(iso);
  const d = new Date(hasZone ? iso : iso + "Z");
  const now = new Date();
  const startOfDay = (x) => new Date(x.getFullYear(), x.getMonth(), x.getDate());
  const diffDays = Math.round((startOfDay(now) - startOfDay(d)) / 86400000);
  if (diffDays <= 0) return "Today";
  if (diffDays === 1) return "Yesterday";
  if (diffDays < 7)  return `${diffDays}d ago`;
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

async function toggleWidgetConv(id, itemEl) {
  const isOpen = itemEl.classList.contains("wconv-open");
  if (isOpen) {
    itemEl.classList.remove("wconv-open");
    return;
  }
  itemEl.classList.add("wconv-open");

  // Lazy-load messages on first open (thread div is empty)
  const threadEl = itemEl.querySelector(".wconv-thread");
  if (threadEl.dataset.loaded) return;
  threadEl.dataset.loaded = "1";
  threadEl.innerHTML = '<div class="wconv-loading">Loading…</div>';

  try {
    const res  = await fetch(`/api/widget/conversations/${id}/messages`);
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    const msgs = data.messages || [];
    if (!msgs.length) {
      threadEl.innerHTML = '<div class="wconv-loading">No messages.</div>';
      return;
    }
    threadEl.innerHTML = msgs.map(m => `
      <div class="wconv-msg">
        <div class="wconv-msg-role ${esc(m.role)}">${m.role === "user" ? "Visitor" : (pgNameEl && pgNameEl.value.trim() || "Wesley")}</div>
        <div class="wconv-msg-content">${esc(m.content)}</div>
      </div>
    `).join("");
  } catch {
    threadEl.innerHTML = '<div class="wconv-loading">Could not load messages.</div>';
  }
}

async function loadWidgetConversations() {
  wconvListEl.innerHTML = '<div class="wconv-empty">Loading…</div>';
  try {
    const res  = await fetch("/api/widget/conversations");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    const convs = data.conversations || [];

    if (!convs.length) {
      wconvListEl.innerHTML = '<div class="wconv-empty">No visitor conversations yet. Once someone chats on your website, they\'ll appear here.</div>';
      return;
    }

    wconvListEl.innerHTML = convs.map(c => `
      <div class="wconv-item" data-id="${c.id}">
        <div class="wconv-row">
          <span class="wconv-date">${esc(formatWconvDate(c.updated_at))}</span>
          <span class="wconv-preview">${esc(c.preview || "(no messages)")}</span>
          <span class="wconv-count">${c.message_count} msg${c.message_count !== 1 ? "s" : ""}</span>
          <span class="wconv-chevron">▶</span>
        </div>
        <div class="wconv-thread"></div>
      </div>
    `).join("");

    wconvListEl.querySelectorAll(".wconv-item").forEach(itemEl => {
      itemEl.querySelector(".wconv-row").addEventListener("click", () => {
        toggleWidgetConv(parseInt(itemEl.dataset.id), itemEl);
      });
    });
  } catch {
    wconvListEl.innerHTML = '<div class="wconv-empty">Could not load conversations.</div>';
  }
}

// ── Event listeners ────────────────────────────────────────────────────────
refreshBtn.addEventListener("click", loadDocuments);
websiteSaveBtn.addEventListener("click", saveWebsiteUrl);
websiteUrlEl.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); saveWebsiteUrl(); }
});
crawlBtn.addEventListener("click", triggerCrawl);
copyEmbedBtn.addEventListener("click", copyEmbedCode);
if (pgSaveBtn) pgSaveBtn.addEventListener("click", savePlaygroundSettings);
wconvRefreshBtn.addEventListener("click", loadWidgetConversations);

// ── Init ───────────────────────────────────────────────────────────────────
loadDocuments();
loadWebsiteSettings();
loadPlaygroundSettings();
loadWidgetConversations();

// ── Analytics ──────────────────────────────────────────────────────────────

let _chatsChart1 = null;
let _chatsChart2 = null;
let _topicsChart  = null;

const CHART_TEAL        = "#1695a0";
const CHART_TEAL_FILL   = "rgba(22,149,160,0.15)";
const CHART_GRID_COLOR  = "#f1f5f9";
const CHART_TICK_FONT   = { size: 11, family: "'Plus Jakarta Sans', sans-serif" };

function anFormatDate(iso) {
  if (!iso) return "";
  const d = new Date(iso.includes("T") ? iso : iso + "T00:00:00Z");
  return d.toLocaleDateString(undefined, { month: "short", day: "numeric" });
}

function anBaseBarOptions(overrides) {
  return Object.assign({
    responsive: true,
    maintainAspectRatio: false,
    plugins: { legend: { display: false } },
    scales: {
      x: { grid: { display: false }, ticks: { font: CHART_TICK_FONT } },
      y: { beginAtZero: true, ticks: { precision: 0, font: CHART_TICK_FONT }, grid: { color: CHART_GRID_COLOR } }
    }
  }, overrides);
}

// ── Chats Analytics ──────────────────────────────────────────────────────

async function loadChatsAnalytics() {
  const panel = document.getElementById("panel-analytics-chats");
  if (!panel || panel.dataset.loaded) return;
  panel.dataset.loaded = "1";

  try {
    const res = await fetch("/api/analytics/chats");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const d = await res.json();

    // Summary cards
    const cards = document.getElementById("analyticsChatsCards");
    if (cards) {
      const vals = cards.querySelectorAll(".an-stat-value");
      [d.total_this_month, d.total_all_time, d.avg_messages, d.most_active_day].forEach((v, i) => {
        vals[i].textContent = v;
        vals[i].classList.remove("an-loading");
      });
    }

    // Daily conversations chart
    if (_chatsChart1) { _chatsChart1.destroy(); _chatsChart1 = null; }
    const ctx1 = document.getElementById("chartDailyConvs");
    if (ctx1 && d.daily_counts) {
      _chatsChart1 = new Chart(ctx1, {
        type: "bar",
        data: {
          labels: d.daily_counts.map(x => anFormatDate(x.date)),
          datasets: [{
            label: "Conversations",
            data: d.daily_counts.map(x => x.count),
            backgroundColor: CHART_TEAL_FILL,
            borderColor: CHART_TEAL,
            borderWidth: 1.5,
            borderRadius: 4,
          }]
        },
        options: anBaseBarOptions({ scales: { x: { grid: { display: false }, ticks: { maxTicksLimit: 10, font: CHART_TICK_FONT } }, y: { beginAtZero: true, ticks: { precision: 0, font: CHART_TICK_FONT }, grid: { color: CHART_GRID_COLOR } } } })
      });
    }

    // Peak hours chart
    if (_chatsChart2) { _chatsChart2.destroy(); _chatsChart2 = null; }
    const ctx2 = document.getElementById("chartPeakHours");
    if (ctx2 && d.hourly_counts) {
      _chatsChart2 = new Chart(ctx2, {
        type: "bar",
        data: {
          labels: d.hourly_counts.map(x => x.hour + ":00"),
          datasets: [{
            label: "Conversations",
            data: d.hourly_counts.map(x => x.count),
            backgroundColor: CHART_TEAL_FILL,
            borderColor: CHART_TEAL,
            borderWidth: 1.5,
            borderRadius: 3,
          }]
        },
        options: anBaseBarOptions()
      });
    }

    // Recent conversations table
    const tableEl = document.getElementById("analyticsChatsTable");
    if (tableEl) {
      const rows = d.recent_conversations || [];
      if (!rows.length) {
        tableEl.innerHTML = '<div class="an-empty">No conversations yet.</div>';
      } else {
        tableEl.innerHTML = `
          <table class="an-table">
            <thead><tr>
              <th>Preview</th><th>Messages</th><th>Date</th>
            </tr></thead>
            <tbody>
              ${rows.map(c => `
                <tr>
                  <td class="an-preview">${esc(c.preview)}</td>
                  <td>${c.message_count}</td>
                  <td style="white-space:nowrap">${esc(formatWconvDate(c.updated_at))}</td>
                </tr>`).join("")}
            </tbody>
          </table>`;
      }
    }
  } catch {
    const p = document.getElementById("panel-analytics-chats");
    if (p) p.querySelector(".db-panel-subtitle").textContent = "Could not load analytics.";
  }
}

// ── Topics Analytics ─────────────────────────────────────────────────────

async function loadTopicsAnalytics() {
  const panel = document.getElementById("panel-analytics-topics");
  if (!panel || panel.dataset.loaded) return;
  panel.dataset.loaded = "1";

  try {
    const res = await fetch("/api/analytics/topics");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const d = await res.json();
    const cats = d.categories || [];
    const nonEmpty = cats.filter(c => c.count > 0);

    // Donut chart
    if (_topicsChart) { _topicsChart.destroy(); _topicsChart = null; }
    const ctx = document.getElementById("chartTopics");
    const donutWrap = ctx && ctx.closest(".an-donut-wrap");
    if (ctx && nonEmpty.length) {
      const palette = ["#1695a0","#0ea5e9","#8b5cf6","#f59e0b","#10b981","#ef4444","#f97316","#6366f1","#94a3b8"];
      _topicsChart = new Chart(ctx, {
        type: "doughnut",
        data: {
          labels: nonEmpty.map(c => c.name),
          datasets: [{
            data: nonEmpty.map(c => c.count),
            backgroundColor: palette.slice(0, nonEmpty.length),
            borderWidth: 2,
            borderColor: "#fff",
          }]
        },
        options: {
          responsive: true,
          maintainAspectRatio: true,
          plugins: { legend: { position: "bottom", labels: { font: { size: 11 }, padding: 10, boxWidth: 12 } } }
        }
      });
    } else if (donutWrap) {
      donutWrap.innerHTML = '<div class="an-empty">No conversations yet.</div>';
    }

    // Ranked list
    const listEl = document.getElementById("topicsList");
    if (listEl) {
      if (!d.total) {
        listEl.innerHTML = '<div class="an-empty">No conversations yet.</div>';
      } else {
        const maxCount = Math.max(...cats.map(c => c.count), 1);
        listEl.innerHTML = cats.map(c => `
          <div>
            <div class="an-topic-row">
              <span class="an-topic-name">${esc(c.name)}</span>
              <div class="an-topic-bar-wrap">
                <div class="an-topic-bar" style="width:${Math.round(c.count / maxCount * 100)}%"></div>
              </div>
              <span class="an-topic-pct">${c.percentage}%</span>
            </div>
            ${c.examples.length ? `<div class="an-topic-examples">${c.examples.map(e => `"${esc(e.length > 70 ? e.substring(0,70) + "…" : e)}"`).join(" &nbsp;·&nbsp; ")}</div>` : ""}
          </div>`).join("");
      }
    }
  } catch {
    const p = document.getElementById("panel-analytics-topics");
    if (p) p.querySelector(".db-panel-subtitle").textContent = "Could not load topics.";
  }
}

// ── Sentiment Analytics ───────────────────────────────────────────────────

async function loadSentimentAnalytics() {
  const panel = document.getElementById("panel-analytics-sentiment");
  if (!panel || panel.dataset.loaded) return;
  panel.dataset.loaded = "1";

  try {
    const res = await fetch("/api/analytics/sentiment");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const d = await res.json();

    // Summary cards + progress bar
    const summaryEl = document.getElementById("sentimentSummary");
    if (summaryEl) {
      summaryEl.innerHTML = `
        <div class="an-stat-grid" style="grid-template-columns:repeat(3,1fr)">
          <div class="an-stat-card">
            <div class="an-stat-value">${d.total}</div>
            <div class="an-stat-label">Conversations analyzed</div>
          </div>
          <div class="an-stat-card">
            <div class="an-stat-value" style="color:#0d9488">${d.confident_pct}%</div>
            <div class="an-stat-label">Answered confidently</div>
          </div>
          <div class="an-stat-card">
            <div class="an-stat-value" style="color:#ef4444">${d.attention_pct}%</div>
            <div class="an-stat-label">Needs attention</div>
          </div>
        </div>
        <section class="settings-card" style="margin-top:16px;">
          <div class="an-progress-wrap">
            <div style="font-size:0.875rem;font-weight:600;color:#334155;margin-bottom:6px;">Response Confidence</div>
            <div class="an-progress-bar-outer">
              <div class="an-progress-bar-inner" style="width:${d.confident_pct}%"></div>
            </div>
            <div class="an-progress-labels">
              <span>✓ Confident: ${d.confident_count}</span>
              <span>⚠ Needs attention: ${d.attention_count}</span>
            </div>
          </div>
        </section>`;
    }

    // Needs attention table
    const tableEl = document.getElementById("sentimentTable");
    if (tableEl) {
      if (!d.needs_attention || !d.needs_attention.length) {
        tableEl.innerHTML = '<div class="an-empty">Great job! No low-confidence conversations found.</div>';
      } else {
        tableEl.innerHTML = `
          <p style="font-size:0.8125rem;color:#64748b;padding:12px 14px 4px;">These conversations suggest your document library may need updates in these areas.</p>
          <table class="an-table">
            <thead><tr><th>Opening Question</th><th>Bot Response</th><th>Date</th></tr></thead>
            <tbody>
              ${d.needs_attention.map(a => `
                <tr>
                  <td style="max-width:200px;white-space:normal;">${esc(a.question)}</td>
                  <td style="max-width:260px;white-space:normal;color:#94a3b8;">${esc(a.response_snippet)}${a.response_snippet.length >= 200 ? "…" : ""}</td>
                  <td style="white-space:nowrap">${esc(formatWconvDate(a.date))}</td>
                </tr>`).join("")}
            </tbody>
          </table>`;
      }
    }

    // Improvement suggestions
    const sugEl = document.getElementById("sentimentSuggestions");
    if (sugEl) {
      if (d.suggested_topics && d.suggested_topics.length) {
        sugEl.innerHTML = `
          <div class="settings-card-header"><h2 class="settings-card-title">Document Improvement Tips</h2></div>
          <div style="padding:4px 14px 16px;">
            <div class="an-tip-card">
              <div class="an-tip-title">Based on your gaps, consider adding documents about:</div>
              <div class="an-tip-body">${d.suggested_topics.map(t => `• ${esc(t)}`).join("<br>")}</div>
            </div>
          </div>`;
      } else {
        sugEl.style.display = "none";
      }
    }
  } catch {
    const p = document.getElementById("panel-analytics-sentiment");
    if (p) p.querySelector(".db-panel-subtitle").textContent = "Could not load sentiment data.";
  }
}

// Lazy-load analytics when a panel is first shown
document.addEventListener("panelShow", function(e) {
  const id = e.detail;
  if (id === "analytics-chats")      loadChatsAnalytics();
  else if (id === "analytics-topics")    loadTopicsAnalytics();
  else if (id === "analytics-sentiment") loadSentimentAnalytics();
  else if (id === "feedback")            loadFeedback();
  else if (id === "snippets")            loadSnippets();
  else if (id === "qna")                 loadQna();
  else if (id === "calendars")           loadCalendars();
  else if (id === "integrations")        loadIntegrations();
});

// ── Integrations: Planning Center ───────────────────────────────────────────
async function loadIntegrations() {
  const body = document.getElementById("pcoBody");
  if (!body) return;
  body.innerHTML = '<div class="an-empty">Loading…</div>';
  try {
    const res = await fetch("/api/pco/status");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const st = await res.json();

    if (!st.configured) {
      body.innerHTML = '<div class="an-empty">Planning Center integration is not enabled on this server yet. Contact Wesley AI support.</div>';
      return;
    }
    if (!st.connected) {
      body.innerHTML = `
        <a class="teal-btn" href="/pco/connect" style="text-decoration:none;display:inline-block;">
          Connect Planning Center
        </a>
        <p style="font-size:0.76rem;color:#94a3b8;margin:10px 0 0;">
          You'll sign in on Planning Center's website and approve access to People.
          Wesley never sees your Planning Center password.
        </p>`;
      return;
    }

    body.innerHTML = `
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:16px;">
        <span style="width:8px;height:8px;border-radius:50%;background:#16a34a;"></span>
        <span style="font-size:0.86rem;font-weight:600;color:#0f172a;">
          Connected${st.organization_name ? " to " + esc(st.organization_name) : ""}
        </span>
        <button class="cancel-btn" id="pcoDisconnectBtn" style="margin-left:auto;">Disconnect</button>
      </div>
      <label style="display:flex;align-items:center;gap:8px;font-size:0.84rem;color:#334155;margin-bottom:14px;cursor:pointer;">
        <input type="checkbox" id="pcoAutoSync" ${st.auto_sync ? "checked" : ""}>
        Automatically send new guest connections to Planning Center People
      </label>
      <div class="website-field-label">Add each guest to a follow-up workflow (optional)</div>
      <select class="ds-field-input" id="pcoWorkflowSel" style="max-width:380px;">
        <option value="">Loading workflows…</option>
      </select>
      <div id="pcoMsg" style="font-size:0.78rem;margin-top:10px;color:#176d73;"></div>`;

    document.getElementById("pcoDisconnectBtn").addEventListener("click", async () => {
      if (!confirm("Disconnect Planning Center? New guests will no longer sync.")) return;
      await fetch("/api/pco/disconnect", { method: "POST" });
      loadIntegrations();
    });
    document.getElementById("pcoAutoSync").addEventListener("change", (e) => {
      savePcoSettings({ auto_sync: e.target.checked });
    });

    const sel = document.getElementById("pcoWorkflowSel");
    try {
      const wres = await fetch("/api/pco/workflows");
      const wdata = await wres.json();
      const workflows = wdata.workflows || [];
      sel.innerHTML = '<option value="">No workflow — just create the person</option>' +
        workflows.map(w =>
          `<option value="${esc(w.id)}" ${w.id === st.workflow_id ? "selected" : ""}>${esc(w.name)}</option>`
        ).join("");
      sel.addEventListener("change", () => {
        savePcoSettings({
          workflow_id: sel.value,
          workflow_name: sel.options[sel.selectedIndex].text,
        });
      });
    } catch {
      sel.innerHTML = '<option value="">Could not load workflows</option>';
    }
  } catch {
    body.innerHTML = '<div class="an-empty">Could not load integration status.</div>';
  }
}

async function savePcoSettings(payload) {
  const msg = document.getElementById("pcoMsg");
  try {
    const res = await fetch("/api/pco/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    if (msg) {
      msg.textContent = res.ok ? "Saved." : "Could not save settings.";
      setTimeout(() => { msg.textContent = ""; }, 2500);
    }
  } catch {
    if (msg) msg.textContent = "Could not save settings.";
  }
}

// ── Event Calendars ─────────────────────────────────────────────────────────
const calendarUrlEl   = document.getElementById("calendarUrl");
const calendarAddBtn  = document.getElementById("calendarAddBtn");
const calendarListEl  = document.getElementById("calendarList");

function renderCalendarItem(cal) {
  const meta = cal.last_error
    ? ""
    : `${cal.event_count} upcoming event${cal.event_count === 1 ? "" : "s"}`;
  const preview = (cal.preview || []).slice(0, 10).map(ev => `
    <div class="cal-event-row">
      <span class="cal-event-when">${esc(ev.when)}</span>
      <span class="cal-event-title">${esc(ev.title)}</span>
      ${ev.location ? `<span class="cal-event-loc">· ${esc(ev.location)}</span>` : ""}
    </div>`).join("");
  return `
    <div class="cal-item" data-id="${cal.id}">
      <div class="cal-item-head">
        <div>
          <div class="cal-item-title" title="${esc(cal.url)}">${esc(cal.label)}</div>
          <div class="cal-item-meta">${esc(meta)}</div>
        </div>
        <div class="cal-item-actions">
          <button class="cal-icon-btn cal-refresh-btn" data-id="${cal.id}" title="Refresh now">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <polyline points="23 4 23 10 17 10"/><polyline points="1 20 1 14 7 14"/>
              <path d="M3.51 9a9 9 0 0 1 14.85-3.36L23 10M1 14l4.64 4.36A9 9 0 0 0 20.49 15"/>
            </svg>
          </button>
          <button class="cal-icon-btn cal-delete-btn" data-id="${cal.id}" title="Disconnect calendar">
            <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
              <line x1="18" y1="6" x2="6" y2="18"/><line x1="6" y1="6" x2="18" y2="18"/>
            </svg>
          </button>
        </div>
      </div>
      ${cal.last_error ? `<div class="cal-item-error">${esc(cal.last_error)}</div>` : ""}
      ${preview ? `<div class="cal-events"><div class="cal-events-label">This is what Wesley will know about</div>${preview}</div>` : ""}
    </div>`;
}

function bindCalendarButtons() {
  calendarListEl.querySelectorAll(".cal-delete-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      if (!confirm("Disconnect this calendar? Wesley will forget its events.")) return;
      await fetch(`/api/calendars/${btn.dataset.id}`, { method: "DELETE" });
      loadCalendars();
    });
  });
  calendarListEl.querySelectorAll(".cal-refresh-btn").forEach(btn => {
    btn.addEventListener("click", async () => {
      btn.disabled = true;
      await fetch(`/api/calendars/${btn.dataset.id}/refresh`, { method: "POST" });
      loadCalendars();
    });
  });
}

async function loadCalendars() {
  if (!calendarListEl) return;
  calendarListEl.innerHTML = '<div class="an-empty">Loading…</div>';
  try {
    const res = await fetch("/api/calendars");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    const cals = data.calendars || [];
    calendarListEl.innerHTML = cals.length
      ? cals.map(renderCalendarItem).join("")
      : '<div class="an-empty">No calendars connected yet. Paste your public events calendar feed above.</div>';
    bindCalendarButtons();
  } catch {
    calendarListEl.innerHTML = '<div class="an-empty">Could not load calendars.</div>';
  }
}

if (calendarAddBtn) {
  calendarAddBtn.addEventListener("click", async () => {
    const url = (calendarUrlEl.value || "").trim();
    if (!url) return;
    calendarAddBtn.disabled = true;
    calendarAddBtn.textContent = "Connecting…";
    try {
      const res = await fetch("/api/calendars", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ url }),
      });
      const data = await res.json();
      if (!res.ok) { alert(data.error || "Could not connect that calendar."); return; }
      calendarUrlEl.value = "";
      loadCalendars();
    } catch {
      alert("Could not connect that calendar. Check your connection and try again.");
    } finally {
      calendarAddBtn.disabled = false;
      calendarAddBtn.textContent = "Connect";
    }
  });
}

// ── Feedback & Corrections ──────────────────────────────────────────────────

var _feedbackItems = [];
var _feedbackStatus = "open";

async function loadFeedback(status) {
  if (status) _feedbackStatus = status;
  const list = document.getElementById("feedbackList");
  if (!list) return;
  list.innerHTML = '<div class="an-empty">Loading…</div>';
  try {
    const res = await fetch(`/api/feedback?status=${encodeURIComponent(_feedbackStatus)}`);
    if (res.status === 401) { window.location.href = "/login"; return; }
    if (!res.ok) throw new Error("Feedback request failed");
    const data = await res.json();
    _feedbackItems = data.items || [];
    const stats = data.stats || {};
    document.getElementById("fbStatOpen").textContent = stats.open || 0;
    document.getElementById("fbStatHelpful").textContent = stats.helpful || 0;
    document.getElementById("fbStatNotHelpful").textContent = stats.not_helpful || 0;
    document.getElementById("fbStatCorrected").textContent = stats.corrected || 0;
    renderFeedback();
  } catch {
    list.innerHTML = '<div class="an-empty">Could not load feedback.</div>';
  }
}

function feedbackReasonLabel(item) {
  if (item.rating === "auto_flagged") return "Couldn't answer";
  const labels = {
    incorrect: "Incorrect", outdated: "Outdated", incomplete: "Incomplete",
    confusing: "Confusing", other: "Other", "": "Not helpful",
  };
  return labels[item.reason] || "Not helpful";
}

function renderFeedback() {
  const list = document.getElementById("feedbackList");
  if (!list) return;
  if (!_feedbackItems.length) {
    const copy = _feedbackStatus === "open"
      ? "No answers need review. Thumbs-down feedback and questions Wesley couldn't answer will appear here."
      : `No ${_feedbackStatus} feedback yet.`;
    list.innerHTML = `<div class="an-empty">${esc(copy)}</div>`;
    return;
  }
  list.innerHTML = _feedbackItems.map(item => {
    const sourceHtml = (item.sources || []).map(source => {
      const label = `${source.title}${source.location ? " · " + source.location : ""}`;
      return source.url
        ? `<a class="fb-source" href="${esc(source.url)}" target="_blank" rel="noopener noreferrer">${esc(label)}</a>`
        : `<span class="fb-source">${esc(label)}</span>`;
    }).join("");
    const cleanAnswer = item.answer.replace(/\s*\[[\d,\s]+\]/g, "").trim();
    const resolvedCopy = item.status === "corrected"
      ? `<div class="fb-comment" style="background:#ecfdf5;color:#166534;">Published correction: ${esc(item.corrected_answer)}</div>`
      : "";
    return `
      <article class="fb-item" data-id="${item.id}">
        <div class="fb-item-head">
          <span class="fb-reason">${esc(feedbackReasonLabel(item))}</span>
          <span class="fb-date">${esc(formatWconvDate(item.created_at))}</span>
        </div>
        <div class="fb-item-body">
          <div class="fb-field-label">Visitor question</div>
          <div class="fb-question">${esc(item.question)}</div>
          <div class="fb-field-label">Wesley's answer</div>
          <div class="fb-answer">${esc(item.answer)}</div>
          ${sourceHtml ? `<div class="fb-sources">${sourceHtml}</div>` : ""}
          ${item.comment ? `<div class="fb-comment">Visitor note: ${esc(item.comment)}</div>` : ""}
          ${resolvedCopy}
          ${item.status === "open" ? `
            <div class="fb-actions">
              <button class="teal-btn" type="button" onclick="feedbackShowCorrection(${item.id})">Correct Answer</button>
              <button class="cancel-btn" type="button" onclick="feedbackDismiss(${item.id})">Dismiss</button>
            </div>
            <div class="fb-correction" id="fb-correction-${item.id}">
              <label class="field-label" for="fb-question-${item.id}">Approved question</label>
              <input class="ds-field-input" id="fb-question-${item.id}" maxlength="500" value="${esc(item.question)}">
              <label class="field-label" for="fb-answer-${item.id}">Correct answer</label>
              <textarea class="ds-field-input" id="fb-answer-${item.id}" rows="5">${esc(cleanAnswer)}</textarea>
              <div class="fb-actions">
                <button class="teal-btn" type="button" onclick="feedbackPublishCorrection(${item.id})">Publish to Q&amp;A</button>
                <button class="cancel-btn" type="button" onclick="feedbackHideCorrection(${item.id})">Cancel</button>
              </div>
              <div id="fb-msg-${item.id}" style="font-size:0.78rem;margin-top:8px;"></div>
            </div>` : ""}
        </div>
      </article>`;
  }).join("");
}

function feedbackShowCorrection(id) {
  document.getElementById(`fb-correction-${id}`)?.classList.add("fb-visible");
  document.getElementById(`fb-answer-${id}`)?.focus();
}

function feedbackHideCorrection(id) {
  document.getElementById(`fb-correction-${id}`)?.classList.remove("fb-visible");
}

async function feedbackPublishCorrection(id) {
  const question = document.getElementById(`fb-question-${id}`).value.trim();
  const answer = document.getElementById(`fb-answer-${id}`).value.trim();
  const msg = document.getElementById(`fb-msg-${id}`);
  if (!question || !answer) {
    msg.textContent = "Question and corrected answer are required.";
    msg.style.color = "#dc2626";
    return;
  }
  const res = await fetch(`/api/feedback/${id}/correct`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ question, answer }),
  });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    msg.textContent = data.error || "Could not publish correction.";
    msg.style.color = "#dc2626";
    return;
  }
  _qnaPairs = [];
  const qnaPanel = document.getElementById("panel-qna");
  if (qnaPanel) delete qnaPanel.dataset.loaded;
  loadFeedback(_feedbackStatus);
}

async function feedbackDismiss(id) {
  if (!confirm("Dismiss this feedback without creating a correction?")) return;
  const res = await fetch(`/api/feedback/${id}/dismiss`, { method: "POST" });
  if (res.ok) loadFeedback(_feedbackStatus);
}

(function wireFeedback() {
  document.querySelectorAll(".fb-filter-btn").forEach(button => {
    button.addEventListener("click", function () {
      document.querySelectorAll(".fb-filter-btn").forEach(b => b.classList.remove("fb-filter-active"));
      button.classList.add("fb-filter-active");
      loadFeedback(button.dataset.status);
    });
  });
  document.getElementById("feedbackRefreshBtn")?.addEventListener("click", () => loadFeedback());
})();

// ── Text Snippets ─────────────────────────────────────────────────────────────

var _snippets = [];
var _snippetCategories = [];

async function loadSnippets() {
  const grid = document.getElementById("snippetGrid");
  if (!grid) return;
  try {
    const res = await fetch("/api/snippets");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const d = await res.json();
    _snippets = d.snippets || [];
    _snippetCategories = d.categories || [];
    renderSnippetGrid();
  } catch {
    if (grid) grid.innerHTML = '<div class="wconv-empty">Could not load snippets.</div>';
  }
}

function renderSnippetGrid() {
  const grid = document.getElementById("snippetGrid");
  if (!grid) return;
  if (!_snippets.length) {
    grid.innerHTML = `<div class="wconv-empty" style="grid-column:1/-1;">No snippets yet. Add information your documents don't cover — like your pastor's bio, parking instructions, or current sermon series.</div>`;
    return;
  }
  grid.innerHTML = _snippets.map(s => `
    <div class="sn-card${s.is_active ? "" : " sn-inactive"}" data-id="${s.id}">
      <div class="sn-card-title">${esc(s.title)}</div>
      ${s.category ? `<div><span class="sn-cat-badge">${esc(s.category)}</span></div>` : ""}
      <div class="sn-preview">${esc(s.content.length > 100 ? s.content.substring(0, 100) + "…" : s.content)}</div>
      <div class="sn-footer">
        <span class="sn-status ${s.is_active ? "sn-status-active" : "sn-status-inactive"}">${s.is_active ? "Active" : "Inactive"}</span>
        <div class="sn-actions">
          <button class="sn-btn" onclick="snippetEdit(${s.id})">Edit</button>
          <button class="sn-btn sn-btn-del" onclick="snippetDelete(${s.id})">Delete</button>
        </div>
      </div>
    </div>`).join("");
}

function snippetShowForm(editId) {
  const card = document.getElementById("snippetFormCard");
  const title = document.getElementById("snippetFormTitle");
  const idInp = document.getElementById("snippetEditId");
  const inp   = document.getElementById("snippetTitle");
  const cat   = document.getElementById("snippetCategory");
  const txt   = document.getElementById("snippetContent");
  const active= document.getElementById("snippetActive");
  const msg   = document.getElementById("snippetFormMsg");
  if (msg) msg.textContent = "";

  if (editId) {
    const s = _snippets.find(x => x.id === editId);
    if (!s) return;
    title.textContent = "Edit Snippet";
    idInp.value = s.id;
    inp.value   = s.title;
    cat.value   = s.category || "";
    txt.value   = s.content;
    active.checked = s.is_active;
  } else {
    title.textContent = "New Snippet";
    idInp.value = "";
    inp.value   = "";
    cat.value   = "";
    txt.value   = "";
    active.checked = true;
  }
  updateSnippetCharCount();
  card.style.display = "";
  inp.focus();
}

function updateSnippetCharCount() {
  const txt = document.getElementById("snippetContent");
  const counter = document.getElementById("snippetCharCount");
  if (txt && counter) counter.textContent = `${txt.value.length} / 1000`;
}

function snippetEdit(id) { snippetShowForm(id); }

async function snippetDelete(id) {
  if (!confirm("Delete this snippet?")) return;
  const res = await fetch(`/api/snippets/${id}`, { method: "DELETE" });
  if (res.ok) {
    _snippets = _snippets.filter(s => s.id !== id);
    renderSnippetGrid();
  }
}

(function wireSnippets() {
  const addBtn    = document.getElementById("snippetAddBtn");
  const cancelBtn = document.getElementById("snippetCancelBtn");
  const saveBtn   = document.getElementById("snippetSaveBtn");
  const txt       = document.getElementById("snippetContent");

  if (addBtn)    addBtn.addEventListener("click",   () => snippetShowForm(null));
  if (cancelBtn) cancelBtn.addEventListener("click", () => {
    document.getElementById("snippetFormCard").style.display = "none";
  });
  if (txt) txt.addEventListener("input", updateSnippetCharCount);

  if (saveBtn) saveBtn.addEventListener("click", async () => {
    const idVal   = document.getElementById("snippetEditId").value;
    const title   = (document.getElementById("snippetTitle").value || "").trim();
    const content = (document.getElementById("snippetContent").value || "").trim();
    const category= document.getElementById("snippetCategory").value || "";
    const active  = document.getElementById("snippetActive").checked;
    const msg     = document.getElementById("snippetFormMsg");

    if (!title || !content) {
      msg.textContent = "Title and content are required.";
      msg.style.color = "#ef4444";
      return;
    }
    const body = { title, content, category: category || null, is_active: active };
    const url    = idVal ? `/api/snippets/${idVal}` : "/api/snippets";
    const method = idVal ? "PATCH" : "POST";
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await res.json();
    if (!res.ok) {
      msg.textContent = d.error || "Error saving snippet.";
      msg.style.color = "#ef4444";
      return;
    }
    if (idVal) {
      const idx = _snippets.findIndex(s => s.id === parseInt(idVal));
      if (idx !== -1) _snippets[idx] = d.snippet;
    } else {
      _snippets.unshift(d.snippet);
    }
    renderSnippetGrid();
    document.getElementById("snippetFormCard").style.display = "none";
  });
})();


// ── Q&A ───────────────────────────────────────────────────────────────────────

var _qnaPairs = [];

async function loadQna() {
  const list = document.getElementById("qnaList");
  if (!list) return;
  try {
    const res = await fetch("/api/qna");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const d = await res.json();
    _qnaPairs = d.pairs || [];
    renderQnaList();
  } catch {
    if (list) list.innerHTML = '<div class="wconv-empty">Could not load Q&A.</div>';
  }
}

function renderQnaList() {
  const list = document.getElementById("qnaList");
  if (!list) return;
  if (!_qnaPairs.length) {
    list.innerHTML = `<div class="wconv-empty">No Q&A pairs yet. Add common questions and write the exact answers you want your bot to give — great for theology, beliefs, and anything that needs a consistent response.</div>`;
    return;
  }
  list.innerHTML = _qnaPairs.map(p => `
    <div class="qna-item${p.is_active ? "" : " qna-inactive"}" data-id="${p.id}">
      <div class="qna-header" onclick="qnaToggle(this)">
        <span class="qna-question">${esc(p.question)}</span>
        <div class="qna-header-right">
          <span class="qna-status ${p.is_active ? "qna-status-active" : "qna-status-inactive"}">${p.is_active ? "Active" : "Inactive"}</span>
          <svg class="qna-chevron" width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
            <polyline points="9 18 15 12 9 6"/>
          </svg>
        </div>
      </div>
      <div class="qna-body">
        <div class="qna-answer">${esc(p.answer)}</div>
        <div class="qna-body-actions">
          <button class="sn-btn" onclick="qnaEdit(${p.id})">Edit</button>
          <button class="sn-btn sn-btn-del" onclick="qnaDelete(${p.id})">Delete</button>
        </div>
      </div>
    </div>`).join("");
}

function qnaToggle(header) {
  header.closest(".qna-item").classList.toggle("qna-open");
}

function qnaShowForm(editId) {
  const card = document.getElementById("qnaFormCard");
  const title = document.getElementById("qnaFormTitle");
  const idInp = document.getElementById("qnaEditId");
  const q     = document.getElementById("qnaQuestion");
  const a     = document.getElementById("qnaAnswer");
  const active= document.getElementById("qnaActive");
  const msg   = document.getElementById("qnaFormMsg");
  if (msg) msg.textContent = "";

  if (editId) {
    const p = _qnaPairs.find(x => x.id === editId);
    if (!p) return;
    title.textContent = "Edit Q&A";
    idInp.value   = p.id;
    q.value       = p.question;
    a.value       = p.answer;
    active.checked = p.is_active;
  } else {
    title.textContent = "New Q&A";
    idInp.value = "";
    q.value = "";
    a.value = "";
    active.checked = true;
  }
  card.style.display = "";
  q.focus();
}

function qnaEdit(id) { qnaShowForm(id); }

async function qnaDelete(id) {
  if (!confirm("Delete this Q&A pair?")) return;
  const res = await fetch(`/api/qna/${id}`, { method: "DELETE" });
  if (res.ok) {
    _qnaPairs = _qnaPairs.filter(p => p.id !== id);
    renderQnaList();
  }
}

(function wireQna() {
  const addBtn    = document.getElementById("qnaAddBtn");
  const cancelBtn = document.getElementById("qnaCancelBtn");
  const saveBtn   = document.getElementById("qnaSaveBtn");

  if (addBtn)    addBtn.addEventListener("click",   () => qnaShowForm(null));
  if (cancelBtn) cancelBtn.addEventListener("click", () => {
    document.getElementById("qnaFormCard").style.display = "none";
  });

  if (saveBtn) saveBtn.addEventListener("click", async () => {
    const idVal   = document.getElementById("qnaEditId").value;
    const question= (document.getElementById("qnaQuestion").value || "").trim();
    const answer  = (document.getElementById("qnaAnswer").value || "").trim();
    const active  = document.getElementById("qnaActive").checked;
    const msg     = document.getElementById("qnaFormMsg");

    if (!question || !answer) {
      msg.textContent = "Question and answer are required.";
      msg.style.color = "#ef4444";
      return;
    }
    const body   = { question, answer, is_active: active };
    const url    = idVal ? `/api/qna/${idVal}` : "/api/qna";
    const method = idVal ? "PATCH" : "POST";
    const res = await fetch(url, {
      method,
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    });
    const d = await res.json();
    if (!res.ok) {
      msg.textContent = d.error || "Error saving Q&A.";
      msg.style.color = "#ef4444";
      return;
    }
    if (idVal) {
      const idx = _qnaPairs.findIndex(p => p.id === parseInt(idVal));
      if (idx !== -1) _qnaPairs[idx] = d.pair;
    } else {
      _qnaPairs.unshift(d.pair);
    }
    renderQnaList();
    document.getElementById("qnaFormCard").style.display = "none";
  });
})();
