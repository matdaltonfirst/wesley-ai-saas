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
const embedSection    = document.getElementById("websiteEmbedSection");
const embedCodeEl     = document.getElementById("embedCode");
const copyEmbedBtn    = document.getElementById("copyEmbedBtn");
const crawlBtn        = document.getElementById("crawlBtn");
const crawlPagesCount = document.getElementById("crawlPagesCount");

// Bot customisation refs
const botNameEl      = document.getElementById("botName");
const botWelcomeEl   = document.getElementById("botWelcome");
const botCityEl      = document.getElementById("botCityInput");
const botColorPicker = document.getElementById("botColorPicker");
const botColorHex    = document.getElementById("botColorHex");
const botSaveBtn     = document.getElementById("botSaveBtn");
const botStatusEl    = document.getElementById("botStatus");

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
      embedSection.style.display = "";
    } else {
      websiteStatusEl.textContent = "Enter your church website URL to enable the chatbot widget.";
      websiteStatusEl.className = "website-status";
      embedSection.style.display = "none";
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

// ── Bot customisation ──────────────────────────────────────────────────────
const HEX_RE = /^#[0-9a-fA-F]{6}$/;

async function loadBotSettings() {
  try {
    const res  = await fetch("/api/church/branding");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();

    botNameEl.value    = data.bot_name    || "";
    botWelcomeEl.value = data.welcome_message || "";
    botCityEl.value    = data.church_city || "";

    const color = data.primary_color || "#0a3d3d";
    botColorHex.value    = color;
    botColorPicker.value = HEX_RE.test(color) ? color : "#0a3d3d";
  } catch {
    // non-critical — leave fields blank
  }
}

async function saveBotSettings() {
  const hex = botColorHex.value.trim();
  if (hex && !HEX_RE.test(hex)) {
    botStatusEl.textContent = "Invalid color — use format #rrggbb.";
    botStatusEl.className   = "website-status err";
    return;
  }

  botSaveBtn.disabled    = true;
  botSaveBtn.textContent = "Saving…";
  botStatusEl.textContent = "";
  botStatusEl.className   = "website-status";

  try {
    const res  = await fetch("/api/church/branding", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        bot_name:       botNameEl.value.trim(),
        welcome_message: botWelcomeEl.value.trim(),
        primary_color:  hex || "#0a3d3d",
        church_city:    botCityEl.value.trim(),
      }),
    });
    const data = await res.json();
    if (res.ok) {
      botStatusEl.textContent = "Saved!";
      botStatusEl.className   = "website-status ok";
    } else {
      botStatusEl.textContent = data.error || "Save failed.";
      botStatusEl.className   = "website-status err";
    }
  } catch {
    botStatusEl.textContent = "Network error. Please try again.";
    botStatusEl.className   = "website-status err";
  } finally {
    botSaveBtn.disabled    = false;
    botSaveBtn.textContent = "Save";
  }
}

// Keep color picker and hex text in sync
botColorPicker.addEventListener("input", () => {
  botColorHex.value = botColorPicker.value;
});
botColorHex.addEventListener("input", () => {
  const v = botColorHex.value.trim();
  if (HEX_RE.test(v)) botColorPicker.value = v;
});

// ── Widget Conversations ───────────────────────────────────────────────────
const wconvListEl    = document.getElementById("wconvList");
const wconvRefreshBtn = document.getElementById("wconvRefreshBtn");

function formatWconvDate(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  const now = new Date();
  const diffMs = now - d;
  const diffDays = Math.floor(diffMs / 86400000);
  if (diffDays === 0) return "Today";
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
        <div class="wconv-msg-role ${esc(m.role)}">${m.role === "user" ? "Visitor" : "Wesley"}</div>
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
botSaveBtn.addEventListener("click", saveBotSettings);
wconvRefreshBtn.addEventListener("click", loadWidgetConversations);

// ── Init ───────────────────────────────────────────────────────────────────
loadDocuments();
loadWebsiteSettings();
loadBotSettings();
loadWidgetConversations();
