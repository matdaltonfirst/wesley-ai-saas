/* The streaming numbers page. DOM is built with textContent, never HTML strings. */
(function () {
  var PLAT = { youtube: 'YouTube', facebook: 'Facebook', subsplash: 'Subsplash', other: 'Other' };
  var SRC = { api: 'API', csv: 'CSV', manual: 'Entered by hand' };
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k]; else if (k === 'class') n.className = attrs[k]; else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function num(v) { return v === null || v === undefined ? '' : Number(v).toLocaleString(); }
  function dash(v) { return v === null || v === undefined ? '–' : Number(v).toLocaleString(); }
  function dt(iso) { var p = iso.split('-'); return new Date(+p[0], +p[1] - 1, +p[2]); }
  function fmt(iso) { return dt(iso).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' }); }
  function api(method, url, body) {
    var o = { method: method, headers: {} };
    if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch(url, o).then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { d._ok = r.ok; return d; }); });
  }
  var addBtn = document.getElementById('addBtn');
  var canWrite = !!addBtn;

  // ── Summary ──
  function change(week) {
    if (week.views_change === null) return el('span', { text: '–' });
    var up = week.views_change >= 0;
    var pct = week.views_change_pct === null ? '' : ' (' + (up ? '+' : '') + week.views_change_pct + '%)';
    var span = el('span', { class: up ? 'up' : 'down', text: (up ? '+' : '') + week.views_change.toLocaleString() + pct });
    if (!week.comparable) span.title = 'These two weeks did not have numbers from the same platforms, so this change is not like for like.';
    return el('span', {}, [span, week.comparable ? null : el('span', { class: 'badge', text: 'not like for like' })]);
  }
  function stat(value, label) { return el('div', { class: 'stat' }, [el('div', { class: 'v', text: value }), el('div', { class: 'l', text: label })]); }

  function trend(weeks) {
    var box = document.getElementById('trend'); box.textContent = '';
    var pts = weeks.map(function (w, i) { return { i: i, v: w.total_views, w: w }; });
    var vals = pts.filter(function (p) { return p.v !== null; });
    if (vals.length < 2) { box.appendChild(el('p', { text: 'The trend appears once two weeks have numbers.', style: 'opacity:.7;font-size:.88rem' })); return; }
    var W = 760, H = 180, padL = 48, padB = 26, padT = 10, max = Math.max.apply(null, vals.map(function (p) { return p.v; })) * 1.1 || 1;
    var ns = 'http://www.w3.org/2000/svg', svg = document.createElementNS(ns, 'svg');
    svg.setAttribute('viewBox', '0 0 ' + W + ' ' + H); svg.setAttribute('width', '100%'); svg.setAttribute('aria-hidden', 'true');
    function X(i) { return padL + (W - padL - 10) * (pts.length === 1 ? 0 : i / (pts.length - 1)); }
    function Y(v) { return padT + (H - padT - padB) * (1 - v / max); }
    [0, .5, 1].forEach(function (f) {
      var y = Y(max * f), l = document.createElementNS(ns, 'line');
      l.setAttribute('x1', padL); l.setAttribute('x2', W - 10); l.setAttribute('y1', y); l.setAttribute('y2', y); l.setAttribute('stroke', '#e3e8ee'); svg.appendChild(l);
      var t = document.createElementNS(ns, 'text'); t.setAttribute('x', padL - 6); t.setAttribute('y', y + 4); t.setAttribute('text-anchor', 'end'); t.setAttribute('font-size', '11'); t.setAttribute('fill', '#6b7787');
      t.textContent = Math.round(max * f).toLocaleString(); svg.appendChild(t);
    });
    var d = '';
    vals.forEach(function (p, k) { d += (k ? 'L' : 'M') + X(p.i) + ' ' + Y(p.v); });
    var path = document.createElementNS(ns, 'path'); path.setAttribute('d', d); path.setAttribute('fill', 'none'); path.setAttribute('stroke', '#1695a0'); path.setAttribute('stroke-width', '2.5'); svg.appendChild(path);
    vals.forEach(function (p) {
      var c = document.createElementNS(ns, 'circle'); c.setAttribute('cx', X(p.i)); c.setAttribute('cy', Y(p.v)); c.setAttribute('r', 3.5); c.setAttribute('fill', '#1695a0'); svg.appendChild(c);
    });
    [0, pts.length - 1].forEach(function (i) {
      var t = document.createElementNS(ns, 'text'); t.setAttribute('x', X(i)); t.setAttribute('y', H - 6); t.setAttribute('font-size', '11'); t.setAttribute('fill', '#6b7787'); t.setAttribute('text-anchor', i === 0 ? 'start' : 'end');
      t.textContent = 'Week of ' + fmt(pts[i].w.week_start); svg.appendChild(t);
    });
    box.appendChild(svg);
  }

  function loadSummary() {
    api('GET', '/api/streaming/summary?weeks=12').then(function (d) {
      if (!d._ok) return;
      var weeks = d.weeks.slice();
      var cur = weeks[weeks.length - 1], last = null;
      for (var i = weeks.length - 1; i >= 0; i--) { if (weeks[i].total_views !== null) { last = weeks[i]; break; } }
      var stats = document.getElementById('stats'); stats.textContent = '';
      if (last) {
        stats.appendChild(stat(num(last.total_views), 'Total views, week of ' + fmt(last.week_start) + (last === cur ? '' : ' (latest week with numbers)')));
        var chg = el('div', { class: 'stat' }, [el('div', { class: 'v' }, [change(last)]), el('div', { class: 'l', text: 'Change from the week before' })]);
        stats.appendChild(chg);
        stats.appendChild(stat(dash(last.highest_peak), 'Highest single-platform peak concurrent'));
        stats.appendChild(stat(dash(last.watch_minutes), 'Watch minutes (platforms that report it)'));
      } else {
        stats.appendChild(el('div', { class: 'stat' }, [el('div', { class: 'v', text: 'No numbers yet' }), el('div', { class: 'l', text: 'Enter this week’s numbers or import a CSV to begin.' })]));
      }
      trend(weeks);
      var t = document.getElementById('weeks'); t.textContent = '';
      var head = el('tr', {}, [el('th', { text: 'Week of' })]);
      ['youtube', 'facebook', 'subsplash'].forEach(function (p) { head.appendChild(el('th', { class: 'n', text: PLAT[p] + ' views' })); });
      ['Total views', 'Highest peak', 'Change'].forEach(function (h) { head.appendChild(el('th', { class: h === 'Change' ? '' : 'n', text: h })); });
      t.appendChild(el('thead', {}, [head]));
      var body = el('tbody');
      weeks.slice().reverse().forEach(function (w) {
        var tr = el('tr', {}, [el('td', { text: fmt(w.week_start) })]);
        ['youtube', 'facebook', 'subsplash'].forEach(function (p) { tr.appendChild(el('td', { class: 'n', text: dash((w.platforms[p] || {}).total_views) })); });
        tr.appendChild(el('td', { class: 'n', text: dash(w.total_views), style: 'font-weight:700' }));
        tr.appendChild(el('td', { class: 'n', text: dash(w.highest_peak) }));
        tr.appendChild(el('td', {}, [change(w)]));
        body.appendChild(tr);
      });
      t.appendChild(body);
    });
  }

  // ── Services ──
  var currentNumbers = [];
  function loadServices() {
    var end = new Date(), start = new Date(); start.setDate(start.getDate() - 7 * 8);
    function iso(d) { return d.toISOString().slice(0, 10); }
    document.getElementById('exportBtn').href = '/api/streaming/export.csv?start=' + iso(new Date(end.getFullYear() - 1, end.getMonth(), end.getDate())) + '&end=' + iso(end);
    api('GET', '/api/streaming/numbers?start=' + iso(start) + '&end=' + iso(end)).then(function (d) {
      var t = document.getElementById('services'); t.textContent = '';
      if (!d._ok) { t.textContent = d.error || 'Could not load.'; return; }
      currentNumbers = d.numbers;
      if (!d.numbers.length) { t.appendChild(el('caption', { text: 'No numbers in the last eight weeks yet.', style: 'text-align:left;padding:8px 0;opacity:.7' })); return; }
      t.appendChild(el('thead', {}, [el('tr', {}, ['Date', 'Service', 'Platform', 'Peak concurrent', 'Total views', 'Watch minutes', ''].map(function (h, i) { return el('th', { class: i >= 3 && i <= 5 ? 'n' : '', text: h }); }))]));
      var body = el('tbody');
      d.numbers.forEach(function (n) {
        function cell(field) {
          var v = n[field], src = n.sources[field];
          return el('td', { class: 'n' }, [el('span', { text: dash(v) }), src ? el('span', { class: 'badge ' + src, text: SRC[src], title: 'Source of this number' }) : null]);
        }
        var actions = el('td', {});
        var h = el('button', { class: 'link', type: 'button', text: 'History' });
        h.addEventListener('click', function () { showHistory(n); }); actions.appendChild(h);
        if (canWrite) {
          var e = el('button', { class: 'link', type: 'button', text: 'Correct', style: 'margin-left:10px' });
          e.addEventListener('click', function () { openEntry(n); }); actions.appendChild(e);
        }
        body.appendChild(el('tr', {}, [el('td', { text: fmt(n.service_date) }), el('td', { text: n.service_label }), el('td', { text: n.platform_label }),
          cell('peak_concurrent'), cell('total_views'), cell('watch_minutes'), actions]));
      });
      t.appendChild(body);
    });
  }

  function showHistory(n) {
    var dlg = document.getElementById('histDlg'), body = document.getElementById('histBody'); body.textContent = 'Loading…';
    dlg.showModal();
    api('GET', '/api/streaming/numbers/' + n.id + '/history').then(function (d) {
      body.textContent = '';
      body.appendChild(el('p', { text: n.platform_label + ', ' + n.service_label + ', ' + fmt(n.service_date), style: 'font-size:.86rem;opacity:.75' }));
      if (!d.history || !d.history.length) { body.appendChild(el('p', { text: 'No changes recorded.' })); return; }
      var ul = el('ul', { style: 'padding-left:18px;font-size:.86rem;line-height:1.5' });
      d.history.forEach(function (h) {
        ul.appendChild(el('li', { text: new Date(h.at).toLocaleString() + ': ' + h.field_label + ' ' + (h.old === null ? 'set to ' : 'changed from ' + h.old + ' to ') + (h.new === null ? 'blank' : h.new) + ' by ' + h.by + ' (' + (SRC[h.source] || h.source) + ')' + (h.reason ? '. ' + h.reason : '') }));
      });
      body.appendChild(ul);
    });
  }
  document.getElementById('hClose').addEventListener('click', function () { document.getElementById('histDlg').close(); });

  // ── Entry ──
  var dlg = document.getElementById('entryDlg');
  function openEntry(n) {
    document.getElementById('eErr').textContent = '';
    document.getElementById('eDate').value = n ? n.service_date : '';
    document.getElementById('eLabel').value = n ? n.service_label : 'Sunday service';
    document.getElementById('ePlatform').value = n ? n.platform : 'youtube';
    document.getElementById('ePeak').value = n && n.peak_concurrent !== null ? n.peak_concurrent : '';
    document.getElementById('eViews').value = n && n.total_views !== null ? n.total_views : '';
    document.getElementById('eMin').value = n && n.watch_minutes !== null ? n.watch_minutes : '';
    document.getElementById('eReason').value = '';
    dlg.showModal();
  }
  if (addBtn) addBtn.addEventListener('click', function () { openEntry(null); });
  document.getElementById('eCancel').addEventListener('click', function () { dlg.close(); });
  document.getElementById('entryForm').addEventListener('submit', function (ev) {
    ev.preventDefault();
    var err = document.getElementById('eErr'); err.textContent = '';
    api('POST', '/api/streaming/numbers', {
      service_date: document.getElementById('eDate').value, service_label: document.getElementById('eLabel').value,
      platform: document.getElementById('ePlatform').value, peak_concurrent: document.getElementById('ePeak').value,
      total_views: document.getElementById('eViews').value, watch_minutes: document.getElementById('eMin').value,
      reason: document.getElementById('eReason').value,
    }).then(function (d) {
      if (!d._ok) { err.textContent = d.error || 'Could not save.'; return; }
      dlg.close(); loadSummary(); loadServices();
    });
  });

  // ── Import ──
  var importBtn = document.getElementById('importBtn'), idlg = document.getElementById('importDlg');
  function runImport(commit) {
    var f = document.getElementById('iFile').files[0], out = document.getElementById('iResult');
    if (!f) { out.textContent = 'Choose a file first.'; return; }
    var fd = new FormData(); fd.append('file', f); fd.append('commit', commit ? 'true' : 'false'); fd.append('platform', document.getElementById('iPlatform').value);
    fetch('/api/streaming/import', { method: 'POST', body: fd }).then(function (r) { return r.json().then(function (d) { d._ok = r.ok; return d; }); }).then(function (d) {
      out.textContent = '';
      if (!d._ok) { out.appendChild(el('div', { class: 'err', text: d.error || 'Import failed.' })); document.getElementById('iGo').disabled = true; return; }
      out.appendChild(el('div', { text: (commit ? 'Imported. ' : 'Preview, nothing saved yet. ') + d.created + ' new, ' + d.updated + ' updated, ' + d.unchanged + ' unchanged' + (d.skipped ? ', ' + d.skipped + ' left alone because they were entered by hand' : '') + '.' }));
      (d.errors || []).slice(0, 10).forEach(function (e) { out.appendChild(el('div', { class: 'err', text: 'Line ' + e.line + ': ' + e.error })); });
      document.getElementById('iGo').disabled = commit || (d.created + d.updated === 0);
      if (commit) { loadSummary(); loadServices(); }
    });
  }
  if (importBtn) {
    importBtn.addEventListener('click', function () { document.getElementById('iResult').textContent = ''; document.getElementById('iGo').disabled = true; idlg.showModal(); });
    document.getElementById('iPreview').addEventListener('click', function () { runImport(false); });
    document.getElementById('iGo').addEventListener('click', function () { runImport(true); });
    document.getElementById('iClose').addEventListener('click', function () { idlg.close(); });
  }
  loadSummary(); loadServices();
})();
