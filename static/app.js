// ── Config ─────────────────────────────────────────────────────────────────
marked.setOptions({ breaks: true, gfm: true });

// ── State ──────────────────────────────────────────────────────────────────
let conversationHistory = [];

// ── DOM refs ───────────────────────────────────────────────────────────────
const messagesEl       = document.getElementById("messages");
const inputField       = document.getElementById("inputField");
const sendBtn          = document.getElementById("sendBtn");
const docList          = document.getElementById("docList");
const newChatBtn       = document.getElementById("newChatBtn");
const refreshBtn       = document.getElementById("refreshBtn");
const sidebar          = document.getElementById("sidebar");
const backdrop         = document.getElementById("sidebarBackdrop");
const hamburgerBtn     = document.getElementById("hamburgerBtn");
const mobileNewChatBtn = document.getElementById("mobileNewChatBtn");
const fileUpload       = document.getElementById("fileUpload");
const uploadProgress   = document.getElementById("uploadProgress");

// ── Utilities ──────────────────────────────────────────────────────────────
function esc(str) {
  return String(str)
    .replace(/&/g, "&amp;").replace(/</g, "&lt;")
    .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}

function renderMd(text) {
  return DOMPurify.sanitize(marked.parse(text));
}

function scrollBottom() {
  messagesEl.scrollTop = messagesEl.scrollHeight;
}

function hideGreeting() {
  const g = document.getElementById("greeting");
  if (g) g.remove();
}

// ── Document list ──────────────────────────────────────────────────────────
async function loadDocuments() {
  docList.innerHTML = '<div class="doc-empty">Loading…</div>';
  try {
    const res = await fetch("/api/documents");
    if (res.status === 401) { window.location.href = "/login"; return; }
    const data = await res.json();
    const docs = data.documents || [];

    if (!docs.length) {
      docList.innerHTML = '<div class="doc-empty">No documents yet.<br>Click + to upload a PDF or DOCX.</div>';
      return;
    }

    docList.innerHTML = docs.map(d => `
      <div class="doc-item" data-id="${d.id}">
        <span class="doc-type-badge ${esc(d.type)}">${esc(d.type.toUpperCase())}</span>
        <div class="doc-info">
          <div class="doc-name" title="${esc(d.name)}">${esc(d.name)}</div>
          <div class="doc-size">${d.size_kb} KB</div>
        </div>
        <button class="doc-delete-btn" data-id="${d.id}" title="Delete document" aria-label="Delete ${esc(d.name)}">
          <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round">
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
  } catch {
    docList.innerHTML = '<div class="doc-empty">Could not load documents.</div>';
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

// ── Append helpers ─────────────────────────────────────────────────────────
function appendUserMsg(text) {
  hideGreeting();
  const row = document.createElement("div");
  row.className = "msg-row user";
  row.innerHTML = `
    <div class="msg-body">
      <div class="msg-bubble user">${esc(text)}</div>
    </div>`;
  messagesEl.appendChild(row);
  scrollBottom();
}

function appendAssistantMsg(text, sources) {
  const row = document.createElement("div");
  row.className = "msg-row assistant";

  const sourcesHtml = sources && sources.length
    ? `<div class="msg-sources">${
        sources.map(s =>
          `<span class="source-chip" title="${esc(s.file)} — ${esc(s.location)}">
            <span class="chip-dot"></span>${esc(s.file)} · ${esc(s.location)}
          </span>`
        ).join("")
      }</div>`
    : "";

  row.innerHTML = `
    <div class="msg-avatar assistant">AI</div>
    <div class="msg-body">
      <div class="msg-bubble assistant">
        <div class="assistant-text">${renderMd(text)}</div>
      </div>
      ${sourcesHtml}
    </div>`;
  messagesEl.appendChild(row);
  scrollBottom();
}

function showTyping() {
  const row = document.createElement("div");
  row.className = "msg-row assistant";
  row.id = "typingRow";
  row.innerHTML = `
    <div class="msg-avatar assistant">AI</div>
    <div class="msg-body">
      <div class="typing"><span></span><span></span><span></span></div>
    </div>`;
  messagesEl.appendChild(row);
  scrollBottom();
}

function removeTyping() { document.getElementById("typingRow")?.remove(); }

function appendError(msg) {
  const row = document.createElement("div");
  row.className = "error-row";
  row.innerHTML = `
    <div class="error-bubble">
      <span class="error-icon">⚠</span>
      <span>${esc(msg)}</span>
    </div>`;
  messagesEl.appendChild(row);
  scrollBottom();
}

// ── Send message ───────────────────────────────────────────────────────────
async function sendMessage() {
  const text = inputField.value.trim();
  if (!text || sendBtn.disabled) return;

  inputField.value = "";
  inputField.style.height = "auto";
  sendBtn.disabled = true;

  appendUserMsg(text);
  showTyping();
  closeSidebar();

  const historySnapshot = [...conversationHistory];

  try {
    const res = await fetch("/api/chat", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ question: text, history: historySnapshot }),
    });

    removeTyping();

    if (res.status === 401) { window.location.href = "/login"; return; }

    if (!res.ok) {
      const err = await res.json().catch(() => ({}));
      appendError(err.error || "Something went wrong. Please try again.");
      return;
    }

    const data = await res.json();
    if (data.error) {
      appendError(data.error);
    } else {
      appendAssistantMsg(data.answer, data.sources);
      conversationHistory.push({ role: "user",      content: text });
      conversationHistory.push({ role: "assistant", content: data.answer });
    }
  } catch {
    removeTyping();
    appendError("Network error — please try again.");
  } finally {
    sendBtn.disabled = !inputField.value.trim();
    inputField.focus();
  }
}

// ── New chat ───────────────────────────────────────────────────────────────
function newChat() {
  conversationHistory = [];
  messagesEl.innerHTML = `
    <div class="greeting" id="greeting">
      <div class="greeting-glow"></div>
      <div class="greeting-icon">
        <img src="/static/WesleyAI.png" alt="Wesley AI" class="greeting-logo" />
      </div>
      <h1 class="greeting-title">How can I help you today?</h1>
      <p class="greeting-sub">I can answer questions, find information in your church documents, help draft communications, assist with planning, and more.</p>
      <div class="suggestions">
        <button class="suggestion-btn"><span class="sug-icon">📋</span><span>What is our volunteer policy?</span></button>
        <button class="suggestion-btn"><span class="sug-icon">📝</span><span>Help me draft a Sunday bulletin</span></button>
        <button class="suggestion-btn"><span class="sug-icon">📅</span><span>What events are coming up?</span></button>
        <button class="suggestion-btn"><span class="sug-icon">🙏</span><span>Write a prayer for our newsletter</span></button>
      </div>
    </div>`;
  bindSuggestions();
  inputField.focus();
}

// ── Input auto-resize ──────────────────────────────────────────────────────
inputField.addEventListener("input", () => {
  inputField.style.height = "auto";
  inputField.style.height = Math.min(inputField.scrollHeight, 180) + "px";
  sendBtn.disabled = !inputField.value.trim();
});

inputField.addEventListener("keydown", e => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendMessage(); }
});

// ── Suggestion buttons ─────────────────────────────────────────────────────
function bindSuggestions() {
  document.querySelectorAll(".suggestion-btn").forEach(btn => {
    btn.addEventListener("click", () => {
      inputField.value = btn.querySelector("span:last-child").textContent.trim();
      inputField.dispatchEvent(new Event("input"));
      sendMessage();
    });
  });
}

// ── Sidebar toggle (mobile) ────────────────────────────────────────────────
function openSidebar() {
  sidebar.classList.add("open");
  backdrop.classList.add("visible");
  document.body.style.overflow = "hidden";
}
function closeSidebar() {
  sidebar.classList.remove("open");
  backdrop.classList.remove("visible");
  document.body.style.overflow = "";
}

// ── Event listeners ────────────────────────────────────────────────────────
sendBtn.addEventListener("click", sendMessage);
newChatBtn.addEventListener("click", newChat);
mobileNewChatBtn.addEventListener("click", () => { newChat(); closeSidebar(); });
refreshBtn.addEventListener("click", loadDocuments);
hamburgerBtn.addEventListener("click", openSidebar);
backdrop.addEventListener("click", closeSidebar);

// ── Website chatbot section ────────────────────────────────────────────────
const websiteUrlEl     = document.getElementById("websiteUrl");
const websiteSaveBtn   = document.getElementById("websiteSaveBtn");
const websiteStatusEl  = document.getElementById("websiteStatus");
const embedSection     = document.getElementById("websiteEmbedSection");
const embedCodeEl      = document.getElementById("embedCode");
const copyEmbedBtn     = document.getElementById("copyEmbedBtn");
const crawlBtn         = document.getElementById("crawlBtn");
const crawlPagesCount  = document.getElementById("crawlPagesCount");

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
    <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
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
    // Re-enable after 10s to avoid accidental double-triggers
    setTimeout(() => {
      crawlBtn.disabled = false;
      crawlBtn.innerHTML = `
        <svg width="11" height="11" viewBox="0 0 24 24" fill="none" stroke="currentColor"
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
    // Fallback: select the element text
    const range = document.createRange();
    range.selectNode(embedCodeEl);
    window.getSelection().removeAllRanges();
    window.getSelection().addRange(range);
    copyEmbedBtn.textContent = "Selected";
    setTimeout(() => { copyEmbedBtn.textContent = "Copy"; }, 2000);
  }
}

// Spin keyframe for crawl button
(function () {
  const s = document.createElement("style");
  s.textContent = "@keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}";
  document.head.appendChild(s);
})();

websiteSaveBtn.addEventListener("click", saveWebsiteUrl);
websiteUrlEl.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); saveWebsiteUrl(); }
});
crawlBtn.addEventListener("click", triggerCrawl);
copyEmbedBtn.addEventListener("click", copyEmbedCode);

// ── Init ───────────────────────────────────────────────────────────────────
loadDocuments();
loadWebsiteSettings();
bindSuggestions();
inputField.focus();
