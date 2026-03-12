/* Wesley AI — Unified Chat Widget (widget-core.js)
 * Single source of truth for the Wesley AI widget UI.
 *
 * EMBED MODE:   Auto-inits when script tag has data-church-id attribute.
 *               Fetches /api/widget/branding on load and applies saved config.
 *               Renders a floating FAB + expandable chat panel on the page.
 *
 * PREVIEW MODE: Instantiated as new WesleyWidget({ previewMode:true, container, config }).
 *               Renders a static inline panel inside the given container element.
 *               Call .update(config) to live-update all branding without re-mounting.
 *
 * No external dependencies. Vanilla JS + injected CSS.
 */
(function (global) {
  "use strict";

  // Capture currentScript immediately — only valid during initial parse, not in callbacks.
  var SCRIPT   = document.currentScript;
  var STYLE_ID = "wai-core-styles";

  // ── CSS (injected once) ─────────────────────────────────────────────────────
  var CSS = [
    /* ═══════════════════════════════════════════════════
       PREVIEW FRAME  (.wai-preview-frame  /  .wai-pv-*)
       Used by the dashboard Playground — static, inline.
       ═══════════════════════════════════════════════════ */

    /* Outer shell */
    ".wai-preview-frame{width:360px;max-width:calc(100% - 32px);",
    "background:#fff;border-radius:18px;",
    "box-shadow:0 8px 48px rgba(0,0,0,0.14),0 2px 8px rgba(0,0,0,0.06);",
    "display:flex;flex-direction:column;overflow:hidden;",
    "max-height:540px;min-height:420px;}",

    /* Header */
    ".wai-pv-header{display:flex;align-items:center;gap:10px;",
    "padding:14px 16px;flex-shrink:0;}",
    ".wai-pv-header-left{display:flex;align-items:center;gap:10px;flex:1;min-width:0;}",

    /* Small initial avatar in header */
    ".wai-pv-avatar-sm{width:32px;height:32px;border-radius:50%;",
    "display:flex;align-items:center;justify-content:center;flex-shrink:0;",
    "background:rgba(255,255,255,0.22);",
    "font-size:0.9rem;font-weight:800;color:#fff;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "line-height:1;letter-spacing:-0.01em;user-select:none;}",

    /* Name + subtitle stack */
    ".wai-pv-name-stack{min-width:0;}",
    ".wai-pv-bot-name{font-size:0.95rem;font-weight:700;color:#fff;",
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",
    ".wai-pv-subtitle{font-size:0.72rem;color:rgba(255,255,255,0.7);",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "white-space:nowrap;overflow:hidden;text-overflow:ellipsis;}",

    ".wai-pv-close{width:24px;height:24px;background:rgba(255,255,255,0.2);",
    "border-radius:50%;display:flex;align-items:center;justify-content:center;",
    "color:rgba(255,255,255,0.85);flex-shrink:0;",
    "font-size:10px;line-height:1;user-select:none;}",

    /* Body / scroll area */
    ".wai-pv-body{flex:1;overflow-y:auto;padding:20px 16px 12px;}",
    ".wai-pv-body::-webkit-scrollbar{width:3px;}",
    ".wai-pv-body::-webkit-scrollbar-thumb{background:rgba(0,0,0,0.1);border-radius:2px;}",

    /* Greeting section */
    ".wai-pv-greeting{display:flex;flex-direction:column;align-items:center;",
    "text-align:center;margin-bottom:16px;}",

    /* Large initial avatar above welcome message */
    ".wai-pv-avatar-lg{width:58px;height:58px;border-radius:50%;margin-bottom:12px;",
    "display:flex;align-items:center;justify-content:center;flex-shrink:0;",
    "font-size:1.5rem;font-weight:800;color:#fff;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "line-height:1;letter-spacing:-0.01em;user-select:none;}",

    ".wai-pv-welcome{font-size:0.9rem;font-weight:600;color:#1a202c;",
    "line-height:1.4;margin:0;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",

    /* Suggestion chips */
    ".wai-pv-sugs{display:flex;flex-direction:column;gap:8px;}",
    ".wai-pv-sug{background:#f7f8fa;border:1px solid #e2e8f0;border-radius:10px;",
    "padding:9px 13px;font-size:0.82rem;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "color:#2d3748;text-align:left;width:100%;line-height:1.4;cursor:default;}",

    /* Footer / input area */
    ".wai-pv-footer{padding:10px 12px 12px;border-top:1px solid #f1f5f9;flex-shrink:0;}",
    ".wai-pv-input-row{display:flex;align-items:center;gap:8px;background:#f7f8fa;",
    "border:1.5px solid #e2e8f0;border-radius:10px;padding:7px 7px 7px 13px;}",
    ".wai-pv-input{flex:1;border:none;background:transparent;font-size:0.84rem;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "color:#94a3b8;outline:none;pointer-events:none;}",
    ".wai-pv-send{width:30px;height:30px;border-radius:8px;border:none;",
    "display:flex;align-items:center;justify-content:center;flex-shrink:0;cursor:default;}",
    ".wai-pv-powered{text-align:center;font-size:0.67rem;color:#cbd5e0;margin:7px 0 0;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",
    ".wai-pv-powered a{color:#94a3b8;text-decoration:none;}",

    /* ═══════════════════════════════════════════════════
       EMBED MODE  (#wai-btn  /  #wai-panel  /  .wai-*)
       Used on church websites via script embed tag.
       ═══════════════════════════════════════════════════ */

    /* Floating action button */
    "#wai-btn{position:fixed;bottom:24px;right:24px;width:56px;height:56px;",
    "border-radius:50%;border:none;cursor:pointer;",
    "box-shadow:0 4px 20px rgba(0,0,0,0.25);",
    "display:flex;align-items:center;justify-content:center;",
    "z-index:2147483646;transition:filter 0.18s,transform 0.18s;}",
    "#wai-btn:hover{filter:brightness(1.1);transform:scale(1.07);}",
    "#wai-btn svg{pointer-events:none;}",

    /* Chat panel */
    "#wai-panel{position:fixed;bottom:92px;right:24px;width:360px;height:520px;",
    "background:#fff;border-radius:18px;",
    "box-shadow:0 8px 48px rgba(0,0,0,0.14),0 2px 8px rgba(0,0,0,0.06);",
    "display:flex;flex-direction:column;overflow:hidden;z-index:2147483645;",
    "opacity:0;transform:translateY(12px) scale(0.97);pointer-events:none;",
    "transition:opacity 0.22s ease,transform 0.22s ease;}",
    "#wai-panel.wai-open{opacity:1;transform:translateY(0) scale(1);pointer-events:auto;}",

    /* Header */
    ".wai-header{padding:14px 16px;display:flex;align-items:center;",
    "justify-content:space-between;flex-shrink:0;}",
    ".wai-header-left{display:flex;align-items:center;gap:10px;}",
    ".wai-avatar{width:30px;height:30px;border-radius:50%;",
    "background:rgba(255,255,255,0.22);",
    "display:flex;align-items:center;justify-content:center;flex-shrink:0;",
    "font-size:0.78rem;font-weight:800;color:#fff;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "line-height:1;letter-spacing:-0.01em;user-select:none;}",
    ".wai-title{color:#fff;font-size:0.88rem;font-weight:600;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",
    ".wai-subtitle{color:rgba(255,255,255,0.7);font-size:0.72rem;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",
    ".wai-close{background:none;border:none;cursor:pointer;",
    "color:rgba(255,255,255,0.7);padding:4px;display:flex;align-items:center;",
    "border-radius:4px;transition:color 0.15s;}",
    ".wai-close:hover{color:#fff;}",

    /* Message list */
    ".wai-msgs{flex:1;overflow-y:auto;padding:14px 12px;",
    "display:flex;flex-direction:column;gap:10px;scroll-behavior:smooth;}",
    ".wai-msg{display:flex;gap:8px;align-items:flex-end;max-width:100%;}",
    ".wai-msg-user{flex-direction:row-reverse;}",
    ".wai-bubble{padding:9px 12px;border-radius:14px;font-size:0.84rem;line-height:1.5;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "max-width:250px;word-break:break-word;white-space:pre-wrap;}",
    ".wai-bubble-bot{background:#eaf7f8;color:#1f2328;border-bottom-left-radius:4px;}",
    ".wai-bubble-user{background:#0c3d43;color:#fff;border-bottom-right-radius:4px;}",
    ".wai-bot-av{width:24px;height:24px;border-radius:50%;",
    "background:rgba(255,255,255,0.22);",
    "flex-shrink:0;display:flex;align-items:center;justify-content:center;",
    "font-size:0.6rem;font-weight:800;color:#fff;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",

    /* Typing indicator */
    ".wai-typing{display:flex;gap:4px;padding:10px 12px;background:#eaf7f8;",
    "border-radius:14px;border-bottom-left-radius:4px;align-items:center;}",
    ".wai-dot{width:6px;height:6px;border-radius:50%;background:#29abb5;",
    "animation:waiDot 1.2s infinite ease-in-out;}",
    ".wai-dot:nth-child(2){animation-delay:0.2s;}",
    ".wai-dot:nth-child(3){animation-delay:0.4s;}",
    "@keyframes waiDot{0%,80%,100%{transform:scale(0.7);opacity:0.5;}",
    "40%{transform:scale(1);opacity:1;}}",

    /* Input footer */
    ".wai-footer{padding:10px 12px;border-top:1px solid #e8f4f5;",
    "display:flex;gap:8px;align-items:flex-end;flex-shrink:0;}",
    ".wai-input{flex:1;border:1.5px solid #d0d7de;border-radius:10px;",
    "padding:8px 12px;font-size:0.84rem;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "resize:none;outline:none;max-height:100px;overflow-y:auto;",
    "line-height:1.45;color:#1f2328;background:#fafcfc;",
    "transition:border-color 0.15s;}",
    ".wai-input:focus{border-color:#29abb5;}",
    ".wai-send{width:34px;height:34px;border-radius:8px;border:none;cursor:pointer;",
    "display:flex;align-items:center;justify-content:center;flex-shrink:0;",
    "transition:filter 0.15s;}",
    ".wai-send:hover:not(:disabled){filter:brightness(1.1);}",
    ".wai-send:disabled{opacity:0.5;cursor:not-allowed;}",

    /* Powered-by brand line */
    ".wai-brand{text-align:center;font-size:0.65rem;color:#94a3b8;",
    "padding:4px 0 6px;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;}",
    ".wai-brand a{color:#29abb5;text-decoration:none;}",

    /* Starter question chips (shown when panel first opens) */
    ".wai-sug-wrap{display:flex;flex-direction:column;gap:6px;padding:4px 0;}",
    ".wai-sug-btn{background:#f0f9fa;border:1px solid #c9e8eb;border-radius:10px;",
    "padding:8px 12px;font-size:0.82rem;",
    "font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif;",
    "color:#0c3d43;text-align:left;width:100%;line-height:1.4;",
    "cursor:pointer;transition:background 0.15s;}",
    ".wai-sug-btn:hover{background:#d8f0f3;}",

    /* Mobile tweaks */
    "@media(max-width:400px){",
    "#wai-panel{width:calc(100vw - 32px);right:16px;bottom:80px;}",
    "#wai-btn{bottom:16px;right:16px;}}",
  ].join("");

  function injectStyles() {
    if (document.getElementById(STYLE_ID)) return;
    var s = document.createElement("style");
    s.id = STYLE_ID;
    s.textContent = CSS;
    (document.head || document.documentElement).appendChild(s);
  }

  // ── Utilities ───────────────────────────────────────────────────────────────

  function esc(s) {
    return String(s)
      .replace(/&/g, "&amp;").replace(/</g, "&lt;")
      .replace(/>/g, "&gt;").replace(/"/g, "&quot;");
  }

  function renderMd(s) {
    return esc(s)
      .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
      .replace(/\n/g, "<br>");
  }

  /** First letter of bot name, uppercased. Falls back to "W". */
  function initial(name) {
    var ch = ((name || "W").trim().charAt(0) || "W");
    return ch.toUpperCase();
  }

  // ── Branding defaults (must match the Python constants in app.py) ───────────
  var DEFAULT_BOT_NAME = "Wesley";
  var DEFAULT_WELCOME  = "How can I help you today?";
  var DEFAULT_COLOR    = "#0a3d3d";
  var DEFAULT_SUBTITLE = "Ask me anything about our church";

  var SUGGESTION_DEFAULTS = [
    "What is our volunteer policy?",
    "Help me draft a Sunday bulletin",
    "What events are coming up?",
    "Write a prayer for our newsletter",
  ];

  // ── WesleyWidget Constructor ─────────────────────────────────────────────────

  /**
   * @param {object}  opts
   * @param {boolean} [opts.previewMode=false]  Render inline static panel (no chat).
   * @param {Element} [opts.container]          Mount point for preview mode.
   * @param {object}  [opts.config]             Initial branding config object.
   * @param {string}  [opts.churchId]           Church ID for embed mode API calls.
   * @param {string}  [opts.apiBase]            Base URL for API calls.
   */
  function WesleyWidget(opts) {
    opts = opts || {};
    this._previewMode = !!opts.previewMode;
    this._churchId    = opts.churchId  || null;
    this._apiBase     = opts.apiBase   || "";
    this._config      = opts.config    || {};
    this._container   = opts.container || null;
    this._refs        = {};
    this._isOpen      = false;
    this._isBusy      = false;
    this._sessionId   = null;
    this._SESSION_KEY = this._churchId ? ("wai_session_" + this._churchId) : null;

    injectStyles();

    if (this._previewMode) {
      this._buildPreview();
    } else {
      this._buildEmbed();
    }
  }

  // ════════════════════════════════════════════════════════════════════════════
  // PREVIEW MODE
  // ════════════════════════════════════════════════════════════════════════════

  WesleyWidget.prototype._buildPreview = function () {
    var cfg      = this._config;
    var color    = cfg.primary_color   || DEFAULT_COLOR;
    var name     = cfg.bot_name        || DEFAULT_BOT_NAME;
    var subtitle = cfg.bot_subtitle    || DEFAULT_SUBTITLE;
    var msg      = cfg.welcome_message || DEFAULT_WELCOME;
    var sugs     = cfg.starter_questions || [];
    var ini      = initial(name);

    var sugHTML = "";
    for (var i = 0; i < 4; i++) {
      var label = ((sugs[i] || "")).trim() || SUGGESTION_DEFAULTS[i];
      sugHTML += '<button class="wai-pv-sug">' + esc(label) + "</button>";
    }

    var frame = document.createElement("div");
    frame.className = "wai-preview-frame";
    frame.innerHTML = [
      '<div class="wai-pv-header" id="wai-pv-hdr">',
      '  <div class="wai-pv-header-left">',
      '    <div class="wai-pv-avatar-sm" id="wai-pv-av-sm">' + esc(ini) + '</div>',
      '    <div class="wai-pv-name-stack">',
      '      <div class="wai-pv-bot-name" id="wai-pv-name">' + esc(name) + '</div>',
      '      <div class="wai-pv-subtitle" id="wai-pv-subtitle">' + esc(subtitle) + '</div>',
      '    </div>',
      '  </div>',
      '  <div class="wai-pv-close">&#10005;</div>',
      '</div>',
      '<div class="wai-pv-body">',
      '  <div class="wai-pv-greeting">',
      '    <div class="wai-pv-avatar-lg" id="wai-pv-av-lg">' + esc(ini) + '</div>',
      '    <p class="wai-pv-welcome" id="wai-pv-welcome">' + esc(msg) + '</p>',
      '  </div>',
      '  <div class="wai-pv-sugs" id="wai-pv-sugs">' + sugHTML + '</div>',
      '</div>',
      '<div class="wai-pv-footer">',
      '  <div class="wai-pv-input-row">',
      '    <input class="wai-pv-input" type="text" placeholder="Ask a question\u2026" disabled />',
      '    <button class="wai-pv-send" id="wai-pv-send" disabled>',
      '      <svg width="14" height="14" viewBox="0 0 24 24" fill="none"',
      '           stroke="#fff" stroke-width="2.5"',
      '           stroke-linecap="round" stroke-linejoin="round">',
      '        <line x1="22" y1="2" x2="11" y2="13"/>',
      '        <polygon points="22 2 15 22 11 13 2 9 22 2"/>',
      '      </svg>',
      '    </button>',
      '  </div>',
      '  <p class="wai-pv-powered">Powered by',
      '    <a href="https://wesleyai.co" target="_blank" rel="noopener">Wesley AI</a>',
      '  </p>',
      '</div>',
    ].join("\n");

    // Apply initial colors
    frame.querySelector("#wai-pv-hdr").style.background    = color;
    frame.querySelector("#wai-pv-av-lg").style.background  = color;
    frame.querySelector("#wai-pv-send").style.background   = color;

    if (this._container) {
      this._container.innerHTML = "";
      this._container.appendChild(frame);
    }

    // Cache refs for efficient patching in update()
    this._refs.frame    = frame;
    this._refs.header   = frame.querySelector("#wai-pv-hdr");
    this._refs.avatarSm = frame.querySelector("#wai-pv-av-sm");
    this._refs.avatarLg = frame.querySelector("#wai-pv-av-lg");
    this._refs.botName  = frame.querySelector("#wai-pv-name");
    this._refs.subtitle = frame.querySelector("#wai-pv-subtitle");
    this._refs.welcome  = frame.querySelector("#wai-pv-welcome");
    this._refs.sugsEl   = frame.querySelector("#wai-pv-sugs");
    this._refs.sendBtn  = frame.querySelector("#wai-pv-send");
  };

  /**
   * Live-update the preview with new branding values.
   * Patches DOM nodes in place — no re-mount, no flash.
   *
   * @param {object} cfg  Branding config (same shape as /api/church/branding response).
   */
  WesleyWidget.prototype.update = function (cfg) {
    if (!this._previewMode) return;
    this._config = cfg || {};

    var color    = cfg.primary_color   || DEFAULT_COLOR;
    var name     = cfg.bot_name        || DEFAULT_BOT_NAME;
    var subtitle = cfg.bot_subtitle    || DEFAULT_SUBTITLE;
    var msg      = cfg.welcome_message || DEFAULT_WELCOME;
    var sugs     = cfg.starter_questions || [];
    var ini      = initial(name);

    var r = this._refs;
    if (!r.frame) return;

    if (r.header)   r.header.style.background  = color;
    if (r.avatarLg) { r.avatarLg.style.background = color; r.avatarLg.textContent = ini; }
    if (r.avatarSm) r.avatarSm.textContent = ini;  // bg stays rgba(255,255,255,0.22)
    if (r.botName)  r.botName.textContent  = name;
    if (r.subtitle) r.subtitle.textContent = subtitle;
    if (r.welcome)  r.welcome.textContent  = msg;
    if (r.sendBtn)  r.sendBtn.style.background = color;

    if (r.sugsEl) {
      var btns = r.sugsEl.querySelectorAll(".wai-pv-sug");
      for (var i = 0; i < btns.length; i++) {
        btns[i].textContent = ((sugs[i] || "")).trim() || SUGGESTION_DEFAULTS[i];
      }
    }
  };

  // ════════════════════════════════════════════════════════════════════════════
  // EMBED MODE
  // ════════════════════════════════════════════════════════════════════════════

  WesleyWidget.prototype._buildEmbed = function () {
    var self     = this;
    var cfg      = this._config;
    var color    = cfg.primary_color || DEFAULT_COLOR;
    var name     = cfg.bot_name      || DEFAULT_BOT_NAME;
    var subtitle = cfg.bot_subtitle  || DEFAULT_SUBTITLE;
    var ini      = initial(name);

    // ── Floating button ──────────────────────────────────────────────────────
    var btn = document.createElement("button");
    btn.id = "wai-btn";
    btn.setAttribute("aria-label", "Open church chat");
    btn.setAttribute("aria-expanded", "false");
    btn.style.background = color;
    btn.innerHTML = [
      '<svg width="24" height="24" viewBox="0 0 24 24" fill="none"',
      ' stroke="#fff" stroke-width="2" stroke-linecap="round" stroke-linejoin="round">',
      '<path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"/>',
      "</svg>",
    ].join("");
    this._refs.btn = btn;

    // ── Panel ────────────────────────────────────────────────────────────────
    var panel = document.createElement("div");
    panel.id = "wai-panel";
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Church chat");
    panel.innerHTML = [
      '<div class="wai-header" id="wai-e-hdr">',
      '  <div class="wai-header-left">',
      '    <div class="wai-avatar" id="wai-e-av">' + esc(ini) + '</div>',
      '    <div>',
      '      <div class="wai-title" id="wai-e-title">' + esc(name) + '</div>',
      '      <div class="wai-subtitle" id="wai-e-subtitle">' + esc(subtitle) + '</div>',
      '    </div>',
      '  </div>',
      '  <button class="wai-close" id="wai-e-close" aria-label="Close chat">',
      '    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"',
      '         stroke="currentColor" stroke-width="2.5"',
      '         stroke-linecap="round" stroke-linejoin="round">',
      '      <line x1="18" y1="6" x2="6" y2="18"/>',
      '      <line x1="6" y1="6" x2="18" y2="18"/>',
      '    </svg>',
      '  </button>',
      '</div>',
      '<div class="wai-msgs" id="wai-e-msgs" role="log" aria-live="polite"></div>',
      '<div class="wai-footer">',
      '  <textarea class="wai-input" id="wai-e-input" rows="1"',
      '            placeholder="Ask a question about our church\u2026"',
      '            aria-label="Message"></textarea>',
      '  <button class="wai-send" id="wai-e-send" disabled aria-label="Send">',
      '    <svg width="16" height="16" viewBox="0 0 24 24" fill="none"',
      '         stroke="#fff" stroke-width="2.5"',
      '         stroke-linecap="round" stroke-linejoin="round">',
      '      <line x1="22" y1="2" x2="11" y2="13"/>',
      '      <polygon points="22 2 15 22 11 13 2 9 22 2"/>',
      '    </svg>',
      '  </button>',
      '</div>',
      '<div class="wai-brand">Powered by',
      '  <a href="https://wesleyai.co" target="_blank" rel="noopener">Wesley AI</a>',
      '</div>',
    ].join("");

    // Apply colors before appending
    panel.querySelector("#wai-e-hdr").style.background  = color;
    panel.querySelector("#wai-e-send").style.background = color;

    document.body.appendChild(btn);
    document.body.appendChild(panel);

    // Cache refs
    this._refs.header   = panel.querySelector("#wai-e-hdr");
    this._refs.avatar   = panel.querySelector("#wai-e-av");
    this._refs.title    = panel.querySelector("#wai-e-title");
    this._refs.subtitle = panel.querySelector("#wai-e-subtitle");
    this._refs.msgs     = panel.querySelector("#wai-e-msgs");
    this._refs.input    = panel.querySelector("#wai-e-input");
    this._refs.send     = panel.querySelector("#wai-e-send");

    // Restore session from previous page navigation (same tab)
    if (this._SESSION_KEY) {
      this._sessionId = sessionStorage.getItem(this._SESSION_KEY) || null;
    }

    // Events
    btn.addEventListener("click", function () {
      self._isOpen ? self._embedClose() : self._embedOpen();
    });
    panel.querySelector("#wai-e-close").addEventListener("click", function () {
      self._embedClose();
    });
    this._refs.input.addEventListener("input", function () {
      var inp = self._refs.input;
      inp.style.height = "auto";
      inp.style.height = Math.min(inp.scrollHeight, 100) + "px";
      self._refs.send.disabled = !inp.value.trim() || self._isBusy;
    });
    this._refs.input.addEventListener("keydown", function (e) {
      if (e.key === "Enter" && !e.shiftKey) {
        e.preventDefault();
        self._sendMessage();
      }
    });
    this._refs.send.addEventListener("click", function () { self._sendMessage(); });

    // Fetch live branding from server asynchronously.
    // Defaults are already applied above; this updates to saved config.
    if (this._churchId && this._apiBase) {
      fetch(this._apiBase + "/api/widget/branding?church_id=" + encodeURIComponent(this._churchId))
        .then(function (r) { return r.json(); })
        .then(function (data) {
          self._config = data;
          self._applyEmbedBranding();
        })
        .catch(function () { /* keep defaults */ });
    }
  };

  /** Patch the embed UI after branding is fetched from the server. */
  WesleyWidget.prototype._applyEmbedBranding = function () {
    var cfg      = this._config;
    var color    = cfg.primary_color || DEFAULT_COLOR;
    var name     = cfg.bot_name      || DEFAULT_BOT_NAME;
    var subtitle = cfg.bot_subtitle  || DEFAULT_SUBTITLE;
    var ini      = initial(name);
    var r        = this._refs;

    if (r.btn)      r.btn.style.background      = color;
    if (r.header)   r.header.style.background   = color;
    if (r.avatar)   { r.avatar.textContent = ini; }  // bg stays rgba(255,255,255,0.22)
    if (r.title)    r.title.textContent    = name;
    if (r.subtitle) r.subtitle.textContent = subtitle;
    if (r.send)     r.send.style.background     = color;
  };

  WesleyWidget.prototype._embedOpen = function () {
    var self = this;
    this._isOpen = true;
    document.getElementById("wai-panel").classList.add("wai-open");
    this._refs.btn.setAttribute("aria-expanded", "true");

    // Show greeting + suggestions on first open only
    if (!this._refs.msgs.children.length) {
      var cfg  = this._config;
      var name = cfg.bot_name        || DEFAULT_BOT_NAME;
      var msg  = cfg.welcome_message || DEFAULT_WELCOME;
      var sugs = cfg.starter_questions || [];

      this._appendBot(
        "👋 Hi! I'm <strong>" + esc(name) + "</strong>, your church's AI assistant.<br>" +
        esc(msg)
      );

      var shown = sugs.filter(function (s) { return s && s.trim(); });
      if (!shown.length) shown = SUGGESTION_DEFAULTS;

      var wrap = document.createElement("div");
      wrap.className = "wai-sug-wrap";
      shown.slice(0, 4).forEach(function (label) {
        var b = document.createElement("button");
        b.className = "wai-sug-btn";
        b.textContent = label;
        b.addEventListener("click", function () {
          if (wrap.parentNode) wrap.parentNode.removeChild(wrap);
          self._refs.input.value = label;
          self._refs.send.disabled = false;
          self._sendMessage();
        });
        wrap.appendChild(b);
      });
      this._refs.msgs.appendChild(wrap);
      this._refs.msgs.scrollTop = this._refs.msgs.scrollHeight;
    }

    this._refs.input.focus();
  };

  WesleyWidget.prototype._embedClose = function () {
    this._isOpen = false;
    document.getElementById("wai-panel").classList.remove("wai-open");
    this._refs.btn.setAttribute("aria-expanded", "false");
  };

  WesleyWidget.prototype._appendBot = function (html) {
    var ini = initial(this._config.bot_name || DEFAULT_BOT_NAME);
    var row = document.createElement("div");
    row.className = "wai-msg wai-msg-bot";
    row.innerHTML =
      '<div class="wai-bot-av">' + esc(ini) + "</div>" +
      '<div class="wai-bubble wai-bubble-bot">' + html + "</div>";
    this._refs.msgs.appendChild(row);
    this._refs.msgs.scrollTop = this._refs.msgs.scrollHeight;
    return row;
  };

  WesleyWidget.prototype._appendUser = function (text) {
    var row = document.createElement("div");
    row.className = "wai-msg wai-msg-user";
    row.innerHTML = '<div class="wai-bubble wai-bubble-user">' + esc(text) + "</div>";
    this._refs.msgs.appendChild(row);
    this._refs.msgs.scrollTop = this._refs.msgs.scrollHeight;
  };

  WesleyWidget.prototype._showTyping = function () {
    var ini = initial(this._config.bot_name || DEFAULT_BOT_NAME);
    var row = document.createElement("div");
    row.className = "wai-msg wai-msg-bot";
    row.id = "wai-e-typing";
    row.innerHTML =
      '<div class="wai-bot-av">' + esc(ini) + "</div>" +
      '<div class="wai-typing">' +
      '<div class="wai-dot"></div><div class="wai-dot"></div><div class="wai-dot"></div>' +
      "</div>";
    this._refs.msgs.appendChild(row);
    this._refs.msgs.scrollTop = this._refs.msgs.scrollHeight;
  };

  WesleyWidget.prototype._removeTyping = function () {
    var t = document.getElementById("wai-e-typing");
    if (t && t.parentNode) t.parentNode.removeChild(t);
  };

  WesleyWidget.prototype._sendMessage = function () {
    var self = this;
    var inp  = this._refs.input;
    var text = inp.value.trim();
    if (!text || this._isBusy) return;

    this._isBusy = true;
    this._refs.send.disabled = true;
    inp.value = "";
    inp.style.height = "auto";

    this._appendUser(text);
    this._showTyping();

    fetch(this._apiBase + "/api/widget/chat", {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        church_id:  this._churchId,
        question:   text,
        session_id: this._sessionId,
      }),
    })
      .then(function (res) {
        return res.json().then(function (data) { return { ok: res.ok, data: data }; });
      })
      .then(function (result) {
        self._removeTyping();
        if (!result.ok || result.data.error) {
          self._appendBot("⚠ " + esc(result.data.error || "Something went wrong. Please try again."));
        } else {
          self._appendBot(renderMd(result.data.answer || ""));
          if (result.data.session_id) {
            self._sessionId = result.data.session_id;
            if (self._SESSION_KEY) {
              sessionStorage.setItem(self._SESSION_KEY, self._sessionId);
            }
          }
        }
      })
      .catch(function () {
        self._removeTyping();
        self._appendBot("⚠ Network error — please try again.");
      })
      .finally(function () {
        self._isBusy = false;
        self._refs.send.disabled = !inp.value.trim();
        inp.focus();
      });
  };

  // ── Export ───────────────────────────────────────────────────────────────────
  global.WesleyWidget = WesleyWidget;

  // ── Auto-init in embed mode ───────────────────────────────────────────────────
  // SCRIPT is captured at parse time above. If data-church-id is present,
  // this file is being embedded on a church website — auto-init the widget.
  if (SCRIPT) {
    var churchId = SCRIPT.getAttribute("data-church-id");
    if (churchId) {
      var apiBase = SCRIPT.getAttribute("data-api-base") || "https://app.wesleyai.co";
      if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function () {
          new WesleyWidget({ churchId: churchId, apiBase: apiBase });
        });
      } else {
        new WesleyWidget({ churchId: churchId, apiBase: apiBase });
      }
    }
  }

}(window));
