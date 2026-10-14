/* Sermon content review. DOM is built with textContent, never HTML strings. */
(function () {
  var CFG = window.REVIEW || { canWrite: false, canApprove: false };
  var STATUS = { approved: 'Approved', draft: 'Draft', needs_changes: 'Changes requested', in_review: 'In review', empty: 'No content' };
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k]; else if (k === 'class') n.className = attrs[k]; else if (k === 'value') n.value = attrs[k];
      else if (k === 'onclick') n.addEventListener('click', attrs[k]); else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function api(method, url, body) {
    var o = { method: method, headers: {} };
    if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch(url, o).then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { d._ok = r.ok; return d; }); });
  }
  function say(msg, ok) { var b = document.getElementById('banner'); b.hidden = false; b.className = 'banner ' + (ok ? 'ok' : 'bad'); b.textContent = msg; }
  function fmt(iso) { return iso ? new Date(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }) : ''; }
  function when(iso) { return iso ? new Date(iso).toLocaleString() : ''; }
  var currentId = null;

  /* ── Queue ─────────────────────────────────────────────── */
  function loadQueue(autoOpen) {
    api('GET', '/api/review').then(function (d) {
      var box = document.getElementById('picker'); box.textContent = '';
      if (!d._ok) { box.textContent = d.error || 'Could not load.'; return; }
      if (!d.items.length) { box.appendChild(el('div', { class: 'card', text: 'No sermon content has been built in the last two months. When a new sermon is ingested, its content shows up here.' })); return; }
      d.items.forEach(function (it) {
        var counts = Object.keys(it.pieces).map(function (k) { return it.pieces[k]; });
        var approved = counts.filter(function (s) { return s === 'approved'; }).length;
        var card = el('div', { class: 'card', tabindex: '0', role: 'button', 'aria-label': 'Review ' + it.sermon.title }, [
          el('div', { class: 'row' }, [
            el('div', {}, [el('div', { text: it.sermon.title, style: 'font-weight:700' }), el('div', { class: 'meta', text: (it.sermon.series ? it.sermon.series + ' · ' : '') + fmt(it.sermon.preached_at) + ' · ' + approved + ' of ' + counts.length + ' pieces approved' })]),
            el('span', { class: 'pill ' + it.status, text: STATUS[it.status] || it.status }),
          ]),
        ]);
        card.addEventListener('click', function () { openPacket(it.packet_id); });
        card.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); openPacket(it.packet_id); } });
        box.appendChild(card);
      });
      var first = d.items[0]; if (autoOpen && !currentId && first) openPacket(first.packet_id);
    });
  }

  /* ── One sermon ────────────────────────────────────────── */
  function openPacket(id) {
    currentId = id;
    api('GET', '/api/review/' + id).then(function (d) {
      var box = document.getElementById('detail'); box.hidden = false; box.textContent = '';
      if (!d._ok) { box.textContent = d.error || 'Could not load.'; return; }
      box.appendChild(el('h2', { text: d.sermon.title, style: 'margin-top:22px' }));
      box.appendChild(el('div', { class: 'meta', text: (d.sermon.series ? d.sermon.series + ' · ' : '') + fmt(d.sermon.preached_at) }));
      box.appendChild(el('div', { class: 'notice', text: d.notice, style: 'margin-top:12px' }));
      d.pieces.forEach(function (p) { box.appendChild(pieceCard(d.packet_id, p)); });
    });
  }
  function refresh() { loadQueue(false); openPacket(currentId); }

  function pieceCard(packetId, p) {
    var head = el('div', { class: 'row' }, [el('h3', { text: p.label }), el('span', { class: 'pill ' + p.status, text: STATUS[p.status] || p.status })]);
    var card = el('section', { class: 'card', 'aria-label': p.label }, [head]);
    if (p.status === 'approved') card.appendChild(el('div', { class: 'meta', text: 'Approved by ' + (p.decided_by || 'someone') + ', ' + when(p.decided_at) }));
    if (p.status === 'needs_changes' && p.note) card.appendChild(el('div', { class: 'note', text: 'Changes requested' + (p.decided_by ? ' by ' + p.decided_by : '') + ': ' + p.note }));
    if (p.error) card.appendChild(el('div', { class: 'err', text: 'This piece did not build: ' + p.error }));
    renderFields(card, p);

    var actions = el('div', { class: 'actions' });
    if (CFG.canApprove && p.status !== 'approved') actions.appendChild(el('button', { class: 'primary', text: 'Approve', onclick: function () { decide(packetId, p.piece, 'approved'); } }));
    if ((CFG.canApprove || CFG.canWrite) && p.status !== 'needs_changes') actions.appendChild(el('button', { text: 'Ask for changes', onclick: function () { askChanges(packetId, p); } }));
    if (CFG.canWrite && p.editable) actions.appendChild(el('button', { text: 'Edit', onclick: function () { editPiece(packetId, p); } }));
    if (CFG.canWrite) actions.appendChild(el('button', { text: 'Rebuild with AI', onclick: function () {
      if (!confirm('Rebuild this piece? The current text is kept in its history, and it goes back to draft.')) return;
      say('Rebuilding. This can take a minute.', true);
      api('POST', '/api/review/' + packetId + '/' + p.piece + '/regenerate').then(function (d) { if (!d._ok) { say(d.error || 'Could not rebuild.', false); return; } say('Rebuilt. The old text is in the history.', true); refresh(); });
    } }));
    if (p.versions) actions.appendChild(el('button', { class: 'link', text: 'History (' + p.versions + ')', onclick: function () { history_(packetId, p); } }));
    if (actions.children.length) card.appendChild(actions);
    return card;
  }

  function renderFields(card, p) {
    var f = p.fields || {};
    if (p.piece === 'youtube') {
      (f.titles || []).forEach(function (t, i) { card.appendChild(el('div', { text: (i + 1) + '. ' + t, style: 'margin-top:8px;font-weight:600' })); });
      if (f.description) card.appendChild(el('div', { class: 'body', text: f.description }));
      if ((f.chapters || []).length) card.appendChild(el('div', { class: 'body small', text: f.chapters.map(function (c) { return c.timestamp + ' ' + c.label; }).join('\n') }));
    } else if (p.piece === 'quotes') {
      (f.quotes || []).forEach(function (q) { card.appendChild(el('div', { class: 'quote' }, [el('div', { text: '“' + q.text + '”' }), el('div', { class: 'small', text: q.timestamp ? 'At ' + q.timestamp + ' in the video. Quotes are exactly what was said, so they cannot be edited.' : '' })])); });
    } else if (p.piece === 'social') {
      (f.social || []).forEach(function (s) { card.appendChild(el('div', { class: 'quote' }, [el('div', { class: 'small', text: s.platform }), el('div', { class: 'body', text: s.body, style: 'margin-top:2px' })])); });
    } else if (p.piece === 'blog' && f.blog) {
      card.appendChild(el('div', { text: f.blog.title, style: 'font-weight:700;margin-top:8px' })); card.appendChild(el('div', { class: 'body', text: f.blog.body }));
      if ((f.blog.unverified || []).length) card.appendChild(el('div', { class: 'note', text: 'Check these quoted lines against the recording: ' + f.blog.unverified.join(' | ') }));
    } else if (p.piece === 'guide' && f.guide) {
      card.appendChild(el('div', { class: 'body', text: f.guide.document || '' }));
    }
  }

  /* ── Actions ───────────────────────────────────────────── */
  function decide(packetId, piece, status, note) {
    return api('POST', '/api/review/' + packetId + '/' + piece + '/decision', { status: status, note: note || '' }).then(function (d) {
      if (!d._ok) { say(d.error || 'Could not save that.', false); return d; } refresh(); return d;
    });
  }
  function askChanges(packetId, p) {
    var dlg = document.getElementById('dlg'); dlg.textContent = '';
    var note = el('textarea', { rows: '3', maxlength: '500', id: 'chg' }), err = el('div', { class: 'err', role: 'alert' });
    dlg.appendChild(el('h2', { text: 'What needs to change in the ' + p.label.toLowerCase() + '?', style: 'margin-top:0' }));
    dlg.appendChild(el('label', { for: 'chg', text: 'Say it so whoever fixes it knows' })); dlg.appendChild(note); dlg.appendChild(err);
    dlg.appendChild(el('div', { class: 'actions' }, [
      el('button', { class: 'primary', text: 'Send back for changes', onclick: function () { decide(packetId, p.piece, 'needs_changes', note.value).then(function (d) { if (d && !d._ok) { err.textContent = d.error || ''; } else dlg.close(); }); } }),
      el('button', { text: 'Cancel', onclick: function () { dlg.close(); } }),
    ]));
    dlg.showModal();
  }
  function editPiece(packetId, p) {
    var dlg = document.getElementById('dlg'); dlg.textContent = ''; var f = p.fields || {}, err = el('div', { class: 'err', role: 'alert' }), fields = {}, getBody;
    dlg.appendChild(el('h2', { text: 'Edit: ' + p.label, style: 'margin-top:0' }));
    dlg.appendChild(el('p', { class: 'meta', text: 'Saving sends this piece back to draft, and the words it had before are kept in the history.' }));
    if (p.piece === 'youtube') {
      var titles = el('textarea', { rows: '4', id: 'e1' }); titles.value = (f.titles || []).join('\n');
      var desc = el('textarea', { rows: '10', id: 'e2' }); desc.value = f.description || '';
      dlg.appendChild(el('label', { for: 'e1', text: 'Title options (one per line)' })); dlg.appendChild(titles);
      dlg.appendChild(el('label', { for: 'e2', text: 'Description' })); dlg.appendChild(desc);
      getBody = function () { return { titles: titles.value.split('\n').filter(function (s) { return s.trim(); }), description: desc.value }; };
    } else if (p.piece === 'social') {
      var boxes = (f.social || []).map(function (s, i) { var ta = el('textarea', { rows: '4', id: 'es' + i }); ta.value = s.body; dlg.appendChild(el('label', { for: 'es' + i, text: 'Post for ' + s.platform })); dlg.appendChild(ta); return { ta: ta, platform: s.platform }; });
      getBody = function () { return { social: boxes.map(function (b) { return { platform: b.platform, body: b.ta.value }; }) }; };
    } else if (p.piece === 'blog') {
      var doc = el('textarea', { rows: '18', id: 'eb' }); doc.value = '# ' + (f.blog.title || '') + '\n\n' + (f.blog.body || '');
      dlg.appendChild(el('label', { for: 'eb', text: 'Blog post (the first line starting with # is the title)' })); dlg.appendChild(doc);
      getBody = function () { return { blog: { document: doc.value } }; };
    } else {
      var g = el('textarea', { rows: '18', id: 'eg' }); g.value = (f.guide && f.guide.document) || '';
      dlg.appendChild(el('label', { for: 'eg', text: 'Small group guide' })); dlg.appendChild(g);
      getBody = function () { return { guide: { document: g.value } }; };
    }
    dlg.appendChild(err);
    dlg.appendChild(el('div', { class: 'actions' }, [
      el('button', { class: 'primary', text: 'Save', onclick: function () {
        api('PATCH', '/api/packets/' + packetId, getBody()).then(function (d) { if (!d._ok) { err.textContent = d.error || 'Could not save.'; return; } dlg.close(); say('Saved. It is back in draft until someone approves it.', true); refresh(); });
      } }),
      el('button', { text: 'Cancel', onclick: function () { dlg.close(); } }),
    ]));
    dlg.showModal();
  }
  function history_(packetId, p) {
    api('GET', '/api/review/' + packetId + '/' + p.piece + '/versions').then(function (d) {
      var dlg = document.getElementById('dlg'); dlg.textContent = '';
      dlg.appendChild(el('h2', { text: 'History: ' + p.label, style: 'margin-top:0' }));
      if (!d.versions.length) dlg.appendChild(el('p', { text: 'No earlier versions.' }));
      d.versions.forEach(function (v) {
        var preview = JSON.stringify(v.fields, null, 1); if (preview.length > 700) preview = preview.slice(0, 700) + '…';
        dlg.appendChild(el('div', { class: 'card' }, [
          el('div', { class: 'meta', text: (v.reason === 'regenerated' ? 'Before a rebuild' : 'Before an edit') + ' · ' + when(v.at) + (v.by ? ' · ' + v.by : '') }),
          el('div', { class: 'body small', text: preview }),
          CFG.canWrite ? el('div', { class: 'actions' }, [el('button', { text: 'Put this version back', onclick: function () {
            api('POST', '/api/review/versions/' + v.id + '/restore').then(function (r) { if (!r._ok) { say(r.error || 'Could not restore.', false); return; } dlg.close(); say('Restored. It is back in draft.', true); refresh(); });
          } })]) : null,
        ]));
      });
      dlg.appendChild(el('div', { class: 'actions' }, [el('button', { text: 'Close', onclick: function () { dlg.close(); } })]));
      dlg.showModal();
    });
  }
  loadQueue(true);
})();
