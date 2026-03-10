/* Wesley AI — Embeddable Website Chat Widget
 * Usage: <script src="https://app.wesleyai.co/widget.js" data-church-id="CHURCH_ID"></script>
 * Self-contained IIFE — no external dependencies required.
 */
(function () {
  "use strict";

  // ── Config ────────────────────────────────────────────────────────────────
  var SCRIPT    = document.currentScript;
  var CHURCH_ID = SCRIPT && SCRIPT.getAttribute("data-church-id");
  var API_BASE  = (SCRIPT && SCRIPT.getAttribute("data-api-base")) ||
                  "https://app.wesleyai.co";

  if (!CHURCH_ID) {
    console.warn("[WesleyAI] data-church-id is required.");
    return;
  }

  // ── Styles ────────────────────────────────────────────────────────────────
  var css = [
    /* Button */
    "#wai-btn{position:fixed;bottom:24px;right:24px;width:56px;height:56px;",
    "border-radius:50%;background:#29abb5;border:none;cursor:pointer;",
    "box-shadow:0 4px 20px rgba(41,171,181,0.45);display:flex;align-items:center;",
    "justify-content:center;z-index:2147483646;transition:background 0.18s,transform 0.18s;}",
    "#wai-btn:hover{background:#1f969f;transform:scale(1.07);}",
    "#wai-btn svg{pointer-events:none;}",

    /* Panel */
    "#wai-panel{position:fixed;bottom:92px;right:24px;width:340px;",
    "height:500px;background:#fff;border-radius:16px;",
    "box-shadow:0 8px 40px rgba(12,61,67,0.18);display:flex;flex-direction:column;",
    "overflow:hidden;z-index:2147483645;",
    "opacity:0;transform:translateY(12px) scale(0.97);",
    "pointer-events:none;transition:opacity 0.22s ease,transform 0.22s ease;}",
    "#wai-panel.wai-open{opacity:1;transform:translateY(0) scale(1);pointer-events:auto;}",

    /* Header */
    "#wai-header{background:#0c3d43;padding:14px 16px;display:flex;",
    "align-items:center;justify-content:space-between;flex-shrink:0;}",
    "#wai-header-left{display:flex;align-items:center;gap:10px;}",
    "#wai-avatar{width:30px;height:30px;border-radius:50%;background:#29abb5;",
    "display:flex;align-items:center;justify-content:center;",
    "font-size:0.7rem;font-weight:700;color:#fff;font-family:sans-serif;}",
    "#wai-title{color:#fff;font-size:0.88rem;font-weight:600;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",
    "#wai-subtitle{color:#8ecdd2;font-size:0.72rem;font-family:sans-serif;}",
    "#wai-close{background:none;border:none;cursor:pointer;color:#8ecdd2;",
    "padding:4px;display:flex;align-items:center;border-radius:4px;",
    "transition:color 0.15s;}",
    "#wai-close:hover{color:#fff;}",

    /* Messages */
    "#wai-msgs{flex:1;overflow-y:auto;padding:14px 12px;display:flex;",
    "flex-direction:column;gap:10px;scroll-behavior:smooth;}",
    ".wai-msg{display:flex;gap:8px;align-items:flex-end;max-width:100%;}",
    ".wai-msg.wai-user{flex-direction:row-reverse;}",
    ".wai-bubble{padding:9px 12px;border-radius:14px;font-size:0.84rem;",
    "line-height:1.5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "max-width:250px;word-break:break-word;white-space:pre-wrap;}",
    ".wai-bubble.wai-bot{background:#eaf7f8;color:#1f2328;border-bottom-left-radius:4px;}",
    ".wai-bubble.wai-user{background:#0c3d43;color:#fff;border-bottom-right-radius:4px;}",
    ".wai-bot-av{width:24px;height:24px;border-radius:50%;background:#29abb5;",
    "flex-shrink:0;display:flex;align-items:center;justify-content:center;",
    "font-size:0.6rem;font-weight:700;color:#fff;font-family:sans-serif;}",

    /* Typing dots */
    ".wai-typing{display:flex;gap:4px;padding:10px 12px;background:#eaf7f8;",
    "border-radius:14px;border-bottom-left-radius:4px;align-items:center;}",
    ".wai-dot{width:6px;height:6px;border-radius:50%;background:#29abb5;",
    "animation:waiDot 1.2s infinite ease-in-out;}",
    ".wai-dot:nth-child(2){animation-delay:0.2s;}",
    ".wai-dot:nth-child(3){animation-delay:0.4s;}",
    "@keyframes waiDot{0%,80%,100%{transform:scale(0.7);opacity:0.5;}",
    "40%{transform:scale(1);opacity:1;}}",

    /* Input area */
    "#wai-footer{padding:10px 12px;border-top:1px solid #e8f4f5;",
    "display:flex;gap:8px;align-items:flex-end;flex-shrink:0;}",
    "#wai-input{flex:1;border:1.5px solid #d0d7de;border-radius:10px;",
    "padding:8px 12px;font-size:0.84rem;font-family:sans-serif;",
    "resize:none;outline:none;max-height:100px;overflow-y:auto;",
    "line-height:1.45;color:#1f2328;background:#fafcfc;",
    "transition:border-color 0.15s;}",
    "#wai-input:focus{border-color:#29abb5;}",
    "#wai-send{width:34px;height:34px;border-radius:8px;background:#29abb5;",
    "border:none;cursor:pointer;display:flex;align-items:center;",
    "justify-content:center;flex-shrink:0;transition:background 0.15s;}",
    "#wai-send:hover:not(:disabled){background:#1f969f;}",
    "#wai-send:disabled{background:#a0d4d8;cursor:not-allowed;}",

    /* Branding */
    "#wai-brand{text-align:center;font-size:0.65rem;color:#94a3b8;",
    "padding:4px 0 6px;font-family:sans-serif;}",
    "#wai-brand a{color:#29abb5;text-decoration:none;}",

    /* Mobile */
    "@media(max-width:400px){",
    "#wai-panel{width:calc(100vw - 32px);right:16px;bottom:80px;}",
    "#wai-btn{bottom:16px;right:16px;}}",
  ].join("");

  var style = document.createElement("style");
  style.textContent = css;
  document.head.appendChild(style);

  // ── DOM ───────────────────────────────────────────────────────────────────
  // Floating button
  var btn = document.createElement("button");
  btn.id = "wai-btn";
  btn.setAttribute("aria-label", "Open church chat");
  btn.innerHTML = [
    '<svg width="24" height="24" viewBox="0 0 24 24" fill="none"',
    ' stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">',
    '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
    "</svg>",
  ].join("");

  // Panel
  var panel = document.createElement("div");
  panel.id = "wai-panel";
  panel.setAttribute("role", "dialog");
  panel.setAttribute("aria-label", "Church chat");
  panel.innerHTML = [
    '<div id="wai-header">',
    '  <div id="wai-header-left">',
    '    <div id="wai-avatar">AI</div>',
    '    <div>',
    '      <div id="wai-title">Wesley AI</div>',
    '      <div id="wai-subtitle">Ask me anything about our church</div>',
    '    </div>',
    '  </div>',
    '  <button id="wai-close" aria-label="Close chat">',
    '    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"',
    '         stroke="currentColor" stroke-width="2.5"',
    '         stroke-linecap="round" stroke-linejoin="round">',
    '      <line x1="18" y1="6" x2="6" y2="18"/>',
    '      <line x1="6" y1="6" x2="18" y2="18"/>',
    '    </svg>',
    '  </button>',
    '</div>',
    '<div id="wai-msgs" role="log" aria-live="polite"></div>',
    '<div id="wai-footer">',
    '  <textarea id="wai-input" rows="1"',
    '            placeholder="Ask a question about our church…"',
    '            aria-label="Message"></textarea>',
    '  <button id="wai-send" disabled aria-label="Send">',
    '    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"',
    '         stroke="#fff" stroke-width="2.5"',
    '         stroke-linecap="round" stroke-linejoin="round">',
    '      <line x1="22" y1="2" x2="11" y2="13"/>',
    '      <polygon points="22 2 15 22 11 13 2 9 22 2"/>',
    '    </svg>',
    '  </button>',
    '</div>',
    '<div id="wai-brand">Powered by <a href="https://wesleyai.co" target="_blank" rel="noopener">Wesley AI</a></div>',
  ].join("");

  document.body.appendChild(btn);
  document.body.appendChild(panel);

  // ── State ─────────────────────────────────────────────────────────────────
  var isOpen    = false;
  var isBusy    = false;
  // session_id groups all messages from this browser session into one
  // WidgetConversation on the server. Stored in sessionStorage so it
  // survives same-tab page navigations but resets when the tab is closed.
  var SESSION_KEY = "wai_session_" + CHURCH_ID;
  var sessionId   = sessionStorage.getItem(SESSION_KEY) || null;

  var msgsEl  = document.getElementById("wai-msgs");
  var inputEl = document.getElementById("wai-input");
  var sendEl  = document.getElementById("wai-send");

  // ── Helpers ───────────────────────────────────────────────────────────────
  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  /** Minimal markdown: **bold**, line breaks */
  function renderMd(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>");
  }

  function scrollBottom() {
    msgsEl.scrollTop = msgsEl.scrollHeight;
  }

  function appendBot(html) {
    var row = document.createElement("div");
    row.className = "wai-msg wai-bot";
    row.innerHTML =
      '<div class="wai-bot-av">AI</div>' +
      '<div class="wai-bubble wai-bot">' + html + "</div>";
    msgsEl.appendChild(row);
    scrollBottom();
    return row;
  }

  function appendUser(text) {
    var row = document.createElement("div");
    row.className = "wai-msg wai-user";
    row.innerHTML = '<div class="wai-bubble wai-user">' + esc(text) + "</div>";
    msgsEl.appendChild(row);
    scrollBottom();
  }

  function showTyping() {
    var row = document.createElement("div");
    row.className = "wai-msg wai-bot";
    row.id = "wai-typing";
    row.innerHTML =
      '<div class="wai-bot-av">AI</div>' +
      '<div class="wai-typing">' +
      '<div class="wai-dot"></div><div class="wai-dot"></div><div class="wai-dot"></div>' +
      "</div>";
    msgsEl.appendChild(row);
    scrollBottom();
  }

  function removeTyping() {
    var t = document.getElementById("wai-typing");
    if (t) t.parentNode.removeChild(t);
  }

  // ── Open / Close ──────────────────────────────────────────────────────────
  function openPanel() {
    isOpen = true;
    panel.classList.add("wai-open");
    btn.setAttribute("aria-expanded", "true");
    // Show welcome message on first open
    if (!msgsEl.children.length) {
      appendBot(
        "👋 Hi there! I'm <strong>Wesley</strong>, your church's AI assistant.<br>" +
        "Ask me anything about our services, programs, events, or beliefs."
      );
    }
    inputEl.focus();
  }

  function closePanel() {
    isOpen = false;
    panel.classList.remove("wai-open");
    btn.setAttribute("aria-expanded", "false");
  }

  btn.addEventListener("click", function () {
    isOpen ? closePanel() : openPanel();
  });

  document.getElementById("wai-close").addEventListener("click", closePanel);

  // ── Input auto-resize ─────────────────────────────────────────────────────
  inputEl.addEventListener("input", function () {
    inputEl.style.height = "auto";
    inputEl.style.height = Math.min(inputEl.scrollHeight, 100) + "px";
    sendEl.disabled = !inputEl.value.trim() || isBusy;
  });

  inputEl.addEventListener("keydown", function (e) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      sendMessage();
    }
  });

  sendEl.addEventListener("click", sendMessage);

  // ── Send ──────────────────────────────────────────────────────────────────
  function sendMessage() {
    var text = inputEl.value.trim();
    if (!text || isBusy) return;

    isBusy = true;
    sendEl.disabled = true;
    inputEl.value = "";
    inputEl.style.height = "auto";

    appendUser(text);
    showTyping();

    fetch(API_BASE + "/api/widget/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        church_id:  CHURCH_ID,
        question:   text,
        session_id: sessionId,
      }),
    })
      .then(function (res) {
        return res.json().then(function (data) {
          return { ok: res.ok, data: data };
        });
      })
      .then(function (result) {
        removeTyping();
        if (!result.ok || result.data.error) {
          appendBot(
            "⚠ " + esc(result.data.error || "Something went wrong. Please try again.")
          );
        } else {
          var answer = result.data.answer || "";
          appendBot(renderMd(answer));
          // Persist the session_id returned by the server for subsequent messages
          if (result.data.session_id) {
            sessionId = result.data.session_id;
            sessionStorage.setItem(SESSION_KEY, sessionId);
          }
        }
      })
      .catch(function () {
        removeTyping();
        appendBot("⚠ Network error — please try again.");
      })
      .finally(function () {
        isBusy = false;
        sendEl.disabled = !inputEl.value.trim();
        inputEl.focus();
      });
  }
})();
