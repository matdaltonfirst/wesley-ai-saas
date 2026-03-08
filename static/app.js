// ── Config ─────────────────────────────────────────────────────────────────
marked.setOptions({ breaks: true, gfm: true });

// ── State ──────────────────────────────────────────────────────────────────
let conversationHistory = [];

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
refreshBtn.addEventListener("click", () => window.location.reload());
hamburgerBtn.addEventListener("click", openSidebar);
backdrop.addEventListener("click", closeSidebar);

// ── Init ───────────────────────────────────────────────────────────────────
bindSuggestions();
inputField.focus();
