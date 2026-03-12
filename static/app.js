// ── Config ─────────────────────────────────────────────────────────────────
marked.setOptions({ breaks: true, gfm: true });

// ── State ──────────────────────────────────────────────────────────────────
let currentConversationId = null;

// ── DOM refs ───────────────────────────────────────────────────────────────
const messagesEl       = document.getElementById("messages");
const inputField       = document.getElementById("inputField");
const sendBtn          = document.getElementById("sendBtn");
const newChatBtn       = document.getElementById("newChatBtn");
const refreshBtn       = document.getElementById("refreshBtn");
const sidebar          = document.getElementById("sidebar");
const backdrop         = document.getElementById("sidebarBackdrop");
const hamburgerBtn     = document.getElementById("hamburgerBtn");
const mobileNewChatBtn = document.getElementById("mobileNewChatBtn");
const convList         = document.getElementById("convList");

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

  try {
    const res = await fetch("/api/chat", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify({ question: text, conversation_id: currentConversationId }),
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
      currentConversationId = data.conversation_id;
      loadConversations();
    }
  } catch {
    removeTyping();
    appendError("Network error — please try again.");
  } finally {
    sendBtn.disabled = !inputField.value.trim();
    inputField.focus();
  }
}

// ── Conversations sidebar ──────────────────────────────────────────────────
async function loadConversations() {
  try {
    const res = await fetch("/api/conversations");
    if (!res.ok) return;
    const data = await res.json();
    renderConvList(data.conversations);
  } catch {
    // silently ignore — sidebar is non-critical
  }
}

function renderConvList(conversations) {
  if (!convList) return;
  if (!conversations.length) {
    convList.innerHTML = '<div class="doc-empty">No conversations yet.</div>';
    return;
  }
  convList.innerHTML = conversations.map(c => `
    <button class="conv-item${c.id === currentConversationId ? " active" : ""}"
            data-id="${c.id}" title="${esc(c.title)}">
      <span class="conv-title">${esc(c.title)}</span>
    </button>
  `).join("");

  convList.querySelectorAll(".conv-item").forEach(btn => {
    btn.addEventListener("click", () => loadConversation(parseInt(btn.dataset.id)));
  });
}

async function loadConversation(convId) {
  try {
    const res = await fetch(`/api/conversations/${convId}/messages`);
    if (!res.ok) return;
    const data = await res.json();

    currentConversationId = convId;

    // Clear chat and render all messages
    messagesEl.innerHTML = "";
    data.messages.forEach(m => {
      if (m.role === "user") {
        appendUserMsg(m.content);
      } else {
        appendAssistantMsg(m.content, []);
      }
    });

    // Refresh sidebar to update active state
    loadConversations();
    closeSidebar();
    inputField.focus();
  } catch {
    appendError("Could not load conversation.");
  }
}

// ── Suggestion icons paired with the 4 default starter questions ───────────
const SUG_ICONS = ["📋", "📝", "📅", "🙏"];

// ── New chat ───────────────────────────────────────────────────────────────
function newChat() {
  const botName    = window.BOT_NAME       || "Wesley";
  const welcomeMsg = window.WELCOME_MESSAGE || "How can I help you today?";
  const starters   = (window.STARTER_QUESTIONS && window.STARTER_QUESTIONS.length)
    ? window.STARTER_QUESTIONS
    : ["What is our volunteer policy?", "Help me draft a Sunday bulletin",
       "What events are coming up?", "Write a prayer for our newsletter"];
  const initial    = (botName.trim().charAt(0) || "W").toUpperCase();

  const sugsHTML = starters.slice(0, 4).map((q, i) =>
    `<button class="suggestion-btn"><span class="sug-icon">${SUG_ICONS[i] || "💬"}</span><span>${esc(q)}</span></button>`
  ).join("");

  currentConversationId = null;
  messagesEl.innerHTML = `
    <div class="greeting" id="greeting">
      <div class="greeting-glow"></div>
      <div class="greeting-icon">
        <div class="greeting-initial-avatar">${esc(initial)}</div>
      </div>
      <h1 class="greeting-title">${esc(welcomeMsg)}</h1>
      <p class="greeting-sub">I can answer questions, find information in your church documents, help draft communications, assist with planning, and more.</p>
      <div class="suggestions">${sugsHTML}</div>
    </div>`;
  bindSuggestions();
  loadConversations();
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
refreshBtn.addEventListener("click", () => window.location.reload());
hamburgerBtn.addEventListener("click", openSidebar);
backdrop.addEventListener("click", closeSidebar);

// ── Init ───────────────────────────────────────────────────────────────────
bindSuggestions();
loadConversations();
inputField.focus();
