/* People, roles and permissions, and the audit log (admin screens).
   Everything is built with DOM calls and textContent, never HTML strings, so a
   name or email can never be interpreted as markup. */
(function () {
  function el(tag, attrs, children) {
    var node = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') node.textContent = attrs[k];
      else if (k === 'class') node.className = attrs[k];
      else node.setAttribute(k, attrs[k]);
    });
    (children || []).forEach(function (c) { if (c) node.appendChild(c); });
    return node;
  }
  function api(method, url, body) {
    var opts = { method: method, headers: {} };
    if (body !== undefined) { opts.headers['Content-Type'] = 'application/json'; opts.body = JSON.stringify(body); }
    return fetch(url, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (d) { d._ok = r.ok; return d; });
    });
  }
  function setStatus(node, text, ok) {
    if (!node) return;
    node.textContent = text;
    node.className = 'website-status' + (text ? (ok ? ' ok' : ' err') : '');
  }

  // ── People ────────────────────────────────────────────────────────────────
  var teamList = document.getElementById('teamList');
  var roles = [];

  function roleBoxes(selected, onChange, idPrefix) {
    var wrap = el('div', { style: 'display:flex;flex-wrap:wrap;gap:6px 16px;' });
    roles.forEach(function (r) {
      var id = idPrefix + '-' + r.key;
      var box = el('input', { type: 'checkbox', id: id, value: r.key });
      box.checked = selected.indexOf(r.key) !== -1;
      if (onChange) box.addEventListener('change', onChange);
      wrap.appendChild(el('label', { for: id, style: 'display:flex;gap:6px;align-items:center;font-size:.85rem;' },
        [box, el('span', { text: r.label })]));
    });
    return wrap;
  }
  function checked(wrap) {
    return Array.prototype.map.call(wrap.querySelectorAll('input:checked'), function (b) { return b.value; });
  }

  function loadTeam() {
    if (!teamList) return;
    api('GET', '/api/team').then(function (d) {
      if (!d._ok) { teamList.textContent = d.error || 'Could not load the team.'; return; }
      roles = d.roles;
      teamList.textContent = '';
      var table = el('table', { class: 'an-table', style: 'width:100%;border-collapse:collapse;' });
      table.appendChild(el('thead', {}, [el('tr', {}, ['Person', 'Roles', 'Last sign-in', 'Active'].map(function (h) {
        return el('th', { text: h, style: 'text-align:left;padding:8px 6px;font-size:.78rem;' }); }))]));
      var body = el('tbody');
      d.people.forEach(function (p) {
        var status = el('span', { class: 'website-status', role: 'status' });
        var boxes = roleBoxes(p.roles, function () {
          api('PATCH', '/api/team/' + p.id, { roles: checked(boxes) }).then(function (r) {
            if (!r._ok) { setStatus(status, r.error || 'Could not save.', false); loadTeam(); }
            else setStatus(status, 'Saved.', true);
          });
        }, 'role-' + p.id);
        var active = el('input', { type: 'checkbox', 'aria-label': 'Active: ' + p.email });
        active.checked = p.active;
        active.addEventListener('change', function () {
          api('PATCH', '/api/team/' + p.id, { active: active.checked }).then(function (r) {
            if (!r._ok) { alert(r.error || 'Could not change that.'); loadTeam(); }
          });
        });
        var who = el('div', {}, [
          el('div', { text: p.display_name || p.email, style: 'font-weight:600;' }),
          p.display_name ? el('div', { text: p.email, style: 'font-size:.78rem;opacity:.7;' }) : null,
          p.signed_in_with_google ? null : el('div', { text: 'Has not signed in with Google yet', style: 'font-size:.75rem;opacity:.7;' }),
        ]);
        body.appendChild(el('tr', { style: 'border-top:1px solid rgba(0,0,0,.08);' }, [
          el('td', { style: 'padding:10px 6px;vertical-align:top;' }, [who]),
          el('td', { style: 'padding:10px 6px;' }, [boxes, status]),
          el('td', { text: p.last_login_at ? new Date(p.last_login_at).toLocaleString() : 'Never', style: 'padding:10px 6px;font-size:.8rem;vertical-align:top;' }),
          el('td', { style: 'padding:10px 6px;vertical-align:top;' }, [active]),
        ]));
      });
      table.appendChild(body);
      teamList.appendChild(table);

      var addRoles = document.getElementById('teamAddRoles');
      if (addRoles && !addRoles.firstChild) addRoles.appendChild(roleBoxes([], null, 'add'));
    });
  }

  var addBtn = document.getElementById('teamAddBtn');
  if (addBtn) addBtn.addEventListener('click', function () {
    var status = document.getElementById('teamAddStatus');
    var email = document.getElementById('teamAddEmail');
    api('POST', '/api/team', {
      email: email.value.trim(),
      display_name: document.getElementById('teamAddName').value.trim(),
      roles: checked(document.getElementById('teamAddRoles')),
    }).then(function (d) {
      if (!d._ok) { setStatus(status, d.error || 'Could not add that person.', false); return; }
      setStatus(status, 'Added. They can now sign in with their church Google account.', true);
      email.value = ''; document.getElementById('teamAddName').value = '';
      document.querySelectorAll('#teamAddRoles input').forEach(function (b) { b.checked = false; });
      loadTeam();
    });
  });

  // ── Permission matrix ─────────────────────────────────────────────────────
  var matrixWrap = document.getElementById('permMatrix');
  function loadMatrix() {
    if (!matrixWrap) return;
    api('GET', '/api/permissions').then(function (d) {
      if (!d._ok) { matrixWrap.textContent = d.error || 'Could not load permissions.'; return; }
      matrixWrap.textContent = '';
      var table = el('table', { style: 'width:100%;border-collapse:collapse;font-size:.84rem;' });
      var head = el('tr', {}, [el('th', { text: 'Permission', style: 'text-align:left;padding:8px 6px;' })]);
      d.roles.forEach(function (r) { head.appendChild(el('th', { text: r.label, style: 'padding:8px 6px;font-size:.75rem;' })); });
      table.appendChild(el('thead', {}, [head]));
      var body = el('tbody'); var group = null;
      d.permissions.forEach(function (p) {
        if (p.group !== group) {
          group = p.group;
          body.appendChild(el('tr', {}, [el('td', { text: group, colspan: String(d.roles.length + 1),
            style: 'font-weight:700;padding:14px 6px 6px;' })]));
        }
        var row = el('tr', { style: 'border-top:1px solid rgba(0,0,0,.06);' }, [el('td', { text: p.label, style: 'padding:6px;' })]);
        d.roles.forEach(function (r) {
          var locked = (d.locked[r.key] || []).indexOf(p.key) !== -1;
          var box = el('input', { type: 'checkbox', 'aria-label': r.label + ': ' + p.label });
          box.checked = d.matrix[r.key].indexOf(p.key) !== -1;
          box.disabled = locked;
          var isDefault = d.defaults[r.key].indexOf(p.key) !== -1;
          box.addEventListener('change', function () {
            api('POST', '/api/permissions', { role: r.key, permission: p.key, allowed: box.checked }).then(function (res) {
              if (!res._ok) { alert(res.error || 'Could not change that.'); box.checked = !box.checked; }
            });
          });
          row.appendChild(el('td', { style: 'text-align:center;padding:6px;' + (box.checked !== isDefault ? 'background:rgba(42,173,181,.15);' : '') }, [box]));
        });
        body.appendChild(row);
      });
      table.appendChild(body);
      matrixWrap.appendChild(table);
      matrixWrap.appendChild(el('p', { text: 'Shaded cells differ from the standard setup. Giving is off for every role until you turn it on by name.', style: 'font-size:.78rem;opacity:.7;margin-top:10px;' }));
    });
  }

  // ── Audit log ─────────────────────────────────────────────────────────────
  var auditList = document.getElementById('auditList');
  var auditFilter = document.getElementById('auditFilter');
  function loadAudit() {
    if (!auditList) return;
    api('GET', '/api/audit?limit=200&action=' + encodeURIComponent(auditFilter ? auditFilter.value : '')).then(function (d) {
      if (!d._ok) { auditList.textContent = d.error || 'Could not load the log.'; return; }
      auditList.textContent = '';
      if (!d.entries.length) { auditList.textContent = 'Nothing recorded yet.'; return; }
      var table = el('table', { style: 'width:100%;border-collapse:collapse;font-size:.8rem;' });
      table.appendChild(el('thead', {}, [el('tr', {}, ['When', 'Who', 'What', 'Source', 'Details'].map(function (h) {
        return el('th', { text: h, style: 'text-align:left;padding:6px;' }); }))]));
      var body = el('tbody');
      d.entries.forEach(function (e) {
        body.appendChild(el('tr', { style: 'border-top:1px solid rgba(0,0,0,.06);vertical-align:top;' }, [
          el('td', { text: new Date(e.at).toLocaleString(), style: 'padding:6px;white-space:nowrap;' }),
          el('td', { text: e.email || '', style: 'padding:6px;' }),
          el('td', { text: e.action, style: 'padding:6px;' }),
          el('td', { text: e.source, style: 'padding:6px;' }),
          el('td', { text: e.detail, style: 'padding:6px;font-family:monospace;font-size:.72rem;word-break:break-all;' }),
        ]));
      });
      table.appendChild(body);
      auditList.appendChild(table);
    });
  }
  if (auditFilter) auditFilter.addEventListener('change', loadAudit);

  // Load each panel the first time it is opened.
  var loaded = {};
  function onPanel(id) {
    if (loaded[id]) { if (id === 'audit') loadAudit(); return; }
    loaded[id] = true;
    if (id === 'team') loadTeam();
    if (id === 'permissions') loadMatrix();
    if (id === 'audit') loadAudit();
  }
  document.querySelectorAll('.db-nav-item[data-panel]').forEach(function (b) {
    b.addEventListener('click', function () { onPanel(b.dataset.panel); });
  });
  var initial = (location.hash || '').replace('#', '');
  if (initial) onPanel(initial);
})();
