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

// ── Document list ──────────────────────────────────────────────────────────
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

// ── Event listeners ────────────────────────────────────────────────────────
refreshBtn.addEventListener("click", loadDocuments);
websiteSaveBtn.addEventListener("click", saveWebsiteUrl);
websiteUrlEl.addEventListener("keydown", e => {
  if (e.key === "Enter") { e.preventDefault(); saveWebsiteUrl(); }
});
crawlBtn.addEventListener("click", triggerCrawl);
copyEmbedBtn.addEventListener("click", copyEmbedCode);

// ── Init ───────────────────────────────────────────────────────────────────
loadDocuments();
loadWebsiteSettings();
