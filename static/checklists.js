/* Event checklists. DOM is built with textContent, never HTML strings. */
(function () {
  var CFG = window.CHECKLISTS || { canWrite: false, roles: [] };
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k]; else if (k === 'class') n.className = attrs[k];
      else if (k === 'value') n.value = attrs[k]; else if (k === 'checked') n.checked = !!attrs[k];
      else if (k === 'onclick') n.addEventListener('click', attrs[k]); else if (k === 'onchange') n.addEventListener('change', attrs[k]);
      else n.setAttribute(k, attrs[k]);
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
  function fmtDate(iso) {
    if (!iso) return 'no date';
    var d = new Date(iso.length === 10 ? iso + 'T12:00:00' : iso);
    return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
  }
  function roleLabel(key) { var r = CFG.roles.filter(function (x) { return x.key === key; })[0]; return r ? r.label : ''; }
  function roleSelect(value, includeBlank) {
    var s = el('select');
    if (includeBlank) s.appendChild(el('option', { value: '', text: 'No owner role' }));
    CFG.roles.forEach(function (r) { var o = el('option', { value: r.key, text: r.label }); if (r.key === value) o.selected = true; s.appendChild(o); });
    return s;
  }
  var showAll = false, people = null;

  /* ── Task rows ─────────────────────────────────────────── */
  function taskRow(t, afterChange, showChecklist) {
    var box = el('input', { type: 'checkbox', 'aria-label': 'Done: ' + t.title, checked: t.done });
    if (!t.can_work) box.disabled = true;
    box.addEventListener('change', function () {
      api('PATCH', '/api/checklist-tasks/' + t.id, { done: box.checked }).then(function (d) {
        if (!d._ok) { box.checked = !box.checked; say(d.error || 'Could not save that.', false); return; }
        afterChange();
      });
    });
    var due = el('div', { class: 'due' + (t.overdue ? ' late' : t.catch_up ? ' catch' : ''),
      text: t.done ? 'Done' : (t.overdue ? 'Overdue: ' : (t.catch_up ? 'Catch up: ' : 'Due ')) + fmtDate(t.due_date) });
    var chips = el('div', { class: 'meta' }, [
      t.owner_label ? el('span', { class: 'chip', text: t.owner_label }) : null,
      t.assigned_name ? el('span', { class: 'chip', text: 'Assigned to ' + t.assigned_name }) : null,
      showChecklist ? el('span', { text: showChecklist }) : null,
      t.done && t.done_by ? el('span', { text: 'Done by ' + t.done_by }) : null,
    ]);
    var main = el('div', {}, [el('div', { class: 't', text: t.title }), t.notes ? el('div', { class: 'meta', text: t.notes }) : null, chips]);
    var right = el('div', {}, [due]);
    if (CFG.canWrite) right.appendChild(el('div', {}, [el('button', { class: 'link', text: 'Edit', onclick: function () { editTask(t, afterChange); } })]));
    return el('div', { class: 'task' + (t.done ? ' done' : '') }, [box, main, right]);
  }

  /* ── Lists ─────────────────────────────────────────────── */
  function loadMine() {
    api('GET', '/api/checklists/mine').then(function (d) {
      var box = document.getElementById('mine'); box.textContent = '';
      if (!d._ok) { box.textContent = d.error || 'Could not load.'; return; }
      if (!d.tasks.length) { box.appendChild(el('div', { class: 'empty', text: 'Nothing is waiting on you. Tasks owned by your role, or assigned to you, show up here.' })); return; }
      d.tasks.forEach(function (t) { box.appendChild(taskRow(t, loadAll, t.checklist + ' (' + fmtDate(t.event_date) + ')')); });
    });
  }
  function loadList() {
    api('GET', '/api/checklists?scope=' + (showAll ? 'all' : 'upcoming')).then(function (d) {
      var list = document.getElementById('list'); list.textContent = '';
      if (!d._ok) { list.textContent = d.error || 'Could not load.'; return; }
      if (!d.checklists.length) {
        list.appendChild(el('div', { class: 'card empty', text: CFG.canWrite ? 'No checklists yet. They appear on their own when a matching event is in Planning Center, or start one with New checklist.' : 'No checklists yet.' }));
        return;
      }
      d.checklists.forEach(function (c) {
        var pct = c.total ? Math.round(100 * c.done / c.total) : 0;
        var card = el('div', { class: 'card', id: 'c' + c.id }, [
          el('div', { class: 'row' }, [
            el('div', {}, [el('div', { class: 'name', text: c.name }), el('div', { class: 'meta', text: fmtDate(c.event_date) + (c.template ? ' · ' + c.template : '') + (c.status !== 'active' ? ' · ' + c.status : '') + (c.next_due && c.status === 'active' ? ' · next due ' + fmtDate(c.next_due) : '') })]),
            el('button', { text: c.done + ' of ' + c.total + ' done', onclick: function () { openDetail(c.id); } }),
          ]),
          el('div', { class: 'bar-track', role: 'img', 'aria-label': pct + ' percent done' }, [el('div', { class: 'bar-fill', style: 'width:' + pct + '%' })]),
        ]);
        list.appendChild(card);
      });
    });
  }
  function loadAll() { loadMine(); loadList(); var open = document.getElementById('detailDlg'); if (open.open && open.dataset.id) openDetail(open.dataset.id, true); }

  /* ── One checklist ─────────────────────────────────────── */
  function openDetail(id, keepOpen) {
    api('GET', '/api/checklists/' + id).then(function (d) {
      var dlg = document.getElementById('detailDlg'), box = document.getElementById('detail'); dlg.dataset.id = id; box.textContent = '';
      if (!d._ok) { box.textContent = d.error || 'Could not load.'; if (!dlg.open) dlg.showModal(); return; }
      box.appendChild(el('h2', { id: 'detailTitle', text: d.name, style: 'margin-top:0' }));
      box.appendChild(el('div', { class: 'meta', text: fmtDate(d.event_date) + ' · ' + d.done + ' of ' + d.total + ' done' + (d.status !== 'active' ? ' · ' + d.status : '') }));
      d.tasks.forEach(function (t) { box.appendChild(taskRow(t, function () { loadAll(); }, null)); });
      if (CFG.canWrite) {
        var add = el('div', { style: 'margin-top:12px' }, [
          el('button', { text: 'Add a task', onclick: function () { addTask(id); } }),
          el('button', { class: 'danger', style: 'margin-left:8px', text: d.status === 'cancelled' ? 'Reopen this checklist' : 'Cancel this checklist', onclick: function () {
            api('PATCH', '/api/checklists/' + id, { status: d.status === 'cancelled' ? 'active' : 'cancelled' }).then(function () { loadAll(); openDetail(id, true); });
          } }),
        ]);
        box.appendChild(add);
      }
      if (!dlg.open) dlg.showModal();
    });
  }
  function getPeople() {
    if (people) return Promise.resolve(people);
    return api('GET', '/api/checklists/people').then(function (d) { people = d.people || []; return people; });
  }
  function personSelect(value) {
    var s = el('select'); s.appendChild(el('option', { value: '', text: 'Nobody in particular' }));
    (people || []).forEach(function (p) { var o = el('option', { value: p.id, text: p.name }); if (p.id === value) o.selected = true; s.appendChild(o); });
    return s;
  }
  function taskForm(dlg, title, t, onSave, onDelete) {
    dlg.textContent = '';
    var f = {
      title: el('input', { value: t.title || '', maxlength: '300' }), notes: el('textarea', { rows: '2', maxlength: '500' }),
      role: roleSelect(t.owner_role || '', true), who: personSelect(t.assigned_user_id), due: el('input', { type: 'date', value: t.due_date || '' }),
    };
    f.notes.value = t.notes || '';
    var err = el('div', { class: 'err', role: 'alert' });
    dlg.appendChild(el('h2', { id: 'taskDlgTitle', text: title, style: 'margin-top:0' }));
    [['What needs doing', f.title], ['Notes', f.notes], ['Owner role', f.role], ['Assigned to', f.who], ['Due', f.due]].forEach(function (p) {
      var id = 'f-' + p[0].replace(/\W/g, ''); p[1].id = id; dlg.appendChild(el('label', { for: id, text: p[0] })); dlg.appendChild(p[1]);
    });
    dlg.appendChild(err);
    var actions = el('div', { style: 'display:flex;gap:8px;margin-top:14px;flex-wrap:wrap' }, [
      el('button', { class: 'primary', text: 'Save', onclick: function () {
        onSave({ title: f.title.value, notes: f.notes.value, owner_role: f.role.value, assigned_user_id: f.who.value ? parseInt(f.who.value, 10) : null, due_date: f.due.value || null }, err);
      } }),
      el('button', { text: 'Cancel', onclick: function () { dlg.close(); } }),
    ]);
    if (onDelete) actions.appendChild(el('button', { class: 'danger', text: 'Remove this task', onclick: onDelete }));
    dlg.appendChild(actions); dlg.showModal();
  }
  function editTask(t, after) {
    getPeople().then(function () {
      var dlg = document.getElementById('taskDlg');
      taskForm(dlg, 'Edit task', t, function (body, err) {
        api('PATCH', '/api/checklist-tasks/' + t.id, body).then(function (d) { if (!d._ok) { err.textContent = d.error || 'Could not save.'; return; } dlg.close(); after(); });
      }, function () {
        if (!confirm('Remove this task from the checklist?')) return;
        api('DELETE', '/api/checklist-tasks/' + t.id).then(function () { dlg.close(); after(); });
      });
    });
  }
  function addTask(checklistId) {
    getPeople().then(function () {
      var dlg = document.getElementById('taskDlg');
      taskForm(dlg, 'Add a task', {}, function (body, err) {
        api('POST', '/api/checklists/' + checklistId + '/tasks', body).then(function (d) { if (!d._ok) { err.textContent = d.error || 'Could not save.'; return; } dlg.close(); loadAll(); });
      });
    });
  }

  /* ── New checklist ─────────────────────────────────────── */
  function newChecklist() {
    Promise.all([api('GET', '/api/checklist-templates'), api('GET', '/api/checklists/events')]).then(function (res) {
      var tpls = res[0].templates || [], evs = res[1].events || [];
      var dlg = document.getElementById('newDlg'); dlg.textContent = '';
      var tpl = el('select'); tpls.forEach(function (t) { tpl.appendChild(el('option', { value: t.id, text: t.name })); });
      var ev = el('select'); ev.appendChild(el('option', { value: '', text: 'Not in Planning Center (type it in)' }));
      evs.forEach(function (e) { ev.appendChild(el('option', { value: e.instance_id, text: e.name + ' · ' + fmtDate(e.starts_at) })); });
      var name = el('input', { maxlength: '300' }), date = el('input', { type: 'date' }), err = el('div', { class: 'err', role: 'alert' });
      var manual = el('div', {}, [el('label', { for: 'nn', text: 'Event name' }), name, el('label', { for: 'nd', text: 'Event date' }), date]);
      name.id = 'nn'; date.id = 'nd'; tpl.id = 'nt'; ev.id = 'ne';
      ev.addEventListener('change', function () { manual.hidden = !!ev.value; });
      dlg.appendChild(el('h2', { id: 'newTitle', text: 'New checklist', style: 'margin-top:0' }));
      dlg.appendChild(el('label', { for: 'nt', text: 'Template' })); dlg.appendChild(tpl);
      dlg.appendChild(el('label', { for: 'ne', text: 'Event' })); dlg.appendChild(ev); dlg.appendChild(manual); dlg.appendChild(err);
      dlg.appendChild(el('div', { style: 'display:flex;gap:8px;margin-top:14px' }, [
        el('button', { class: 'primary', text: 'Make the checklist', onclick: function () {
          var body = { template_id: parseInt(tpl.value, 10) };
          if (ev.value) body.instance_id = ev.value; else { body.name = name.value; body.event_date = date.value; }
          api('POST', '/api/checklists', body).then(function (d) { if (!d._ok) { err.textContent = d.error || 'Could not make it.'; return; } dlg.close(); loadAll(); openDetail(d.id); });
        } }),
        el('button', { text: 'Cancel', onclick: function () { dlg.close(); } }),
      ]));
      dlg.showModal();
    });
  }

  /* ── Templates ─────────────────────────────────────────── */
  function editTemplates(selectedId) {
    api('GET', '/api/checklist-templates').then(function (d) {
      var dlg = document.getElementById('tplDlg'); dlg.textContent = '';
      var tpls = d.templates || [];
      var pick = el('select', { id: 'tplPick' }); pick.appendChild(el('option', { value: 'new', text: 'A new template' }));
      tpls.forEach(function (t) { var o = el('option', { value: t.id, text: t.name + (t.active ? '' : ' (off)') }); if (String(t.id) === String(selectedId)) o.selected = true; pick.appendChild(o); });
      dlg.appendChild(el('h2', { id: 'tplTitle', text: 'Checklist templates', style: 'margin-top:0' }));
      dlg.appendChild(el('p', { class: 'meta', text: 'An event gets a checklist from a template when one of its keywords appears in the event name or tags in Planning Center. Due dates count back from the event; use a negative number for after it.' }));
      dlg.appendChild(el('label', { for: 'tplPick', text: 'Template' })); dlg.appendChild(pick);
      var form = el('div'); dlg.appendChild(form);
      function render() {
        form.textContent = '';
        var cur = tpls.filter(function (t) { return String(t.id) === pick.value; })[0] || { name: '', description: '', keywords: '', active: true, tasks: [] };
        var name = el('input', { value: cur.name, maxlength: '120', id: 'tn' }), kw = el('input', { value: cur.keywords, maxlength: '500', id: 'tk' }),
            desc = el('input', { value: cur.description, maxlength: '300', id: 'td' }), act = el('input', { type: 'checkbox', checked: cur.active, id: 'ta', style: 'width:auto' });
        var rows = el('div'), err = el('div', { class: 'err', role: 'alert' });
        function addRow(t) {
          var title = el('input', { value: t.title || '', maxlength: '300', 'aria-label': 'Task' }), role = roleSelect(t.owner_role || '', true),
              days = el('input', { type: 'number', value: t.days_before === undefined ? 14 : t.days_before, 'aria-label': 'Days before the event' }), notes = el('input', { value: t.notes || '', maxlength: '500', 'aria-label': 'Notes', style: 'grid-column:1/-1' });
          var row = el('div', { class: 'tt' }, [title, role, days, el('button', { class: 'link danger', text: 'Remove', onclick: function () { row.remove(); } }), notes]);
          row.getValues = function () { return { title: title.value, owner_role: role.value, days_before: days.value, notes: notes.value }; };
          rows.appendChild(row);
        }
        cur.tasks.forEach(addRow);
        form.appendChild(el('label', { for: 'tn', text: 'Name' })); form.appendChild(name);
        form.appendChild(el('label', { for: 'td', text: 'What it is for' })); form.appendChild(desc);
        form.appendChild(el('label', { for: 'tk', text: 'Keywords (separated by commas)' })); form.appendChild(kw);
        form.appendChild(el('label', { for: 'ta', text: 'Use this template for new events' }, [])); form.appendChild(act);
        form.appendChild(el('label', { text: 'Tasks: what, who owns it, days before the event' })); form.appendChild(rows);
        form.appendChild(el('button', { text: 'Add a task', onclick: function () { addRow({}); } })); form.appendChild(err);
        var actions = el('div', { style: 'display:flex;gap:8px;margin-top:14px;flex-wrap:wrap' }, [
          el('button', { class: 'primary', text: 'Save template', onclick: function () {
            var body = { name: name.value, description: desc.value, keywords: kw.value, active: act.checked, tasks: Array.prototype.map.call(rows.children, function (r) { return r.getValues(); }) };
            api(cur.id ? 'PUT' : 'POST', cur.id ? '/api/checklist-templates/' + cur.id : '/api/checklist-templates', body).then(function (r) {
              if (!r._ok) { err.textContent = r.error || 'Could not save.'; return; } editTemplates(cur.id || r.id);
            });
          } }),
          el('button', { text: 'Close', onclick: function () { dlg.close(); loadAll(); } }),
        ]);
        if (cur.id) actions.appendChild(el('button', { class: 'danger', text: 'Delete this template', onclick: function () {
          if (!confirm('Delete this template? Checklists already made from it keep their tasks.')) return;
          api('DELETE', '/api/checklist-templates/' + cur.id).then(function () { editTemplates(); });
        } }));
        form.appendChild(actions);
      }
      pick.addEventListener('change', render); render();
      if (!dlg.open) dlg.showModal();
    });
  }

  /* ── Wiring ────────────────────────────────────────────── */
  document.getElementById('detailClose').addEventListener('click', function () { document.getElementById('detailDlg').close(); });
  document.getElementById('detailDlg').addEventListener('close', function () { history.replaceState(null, '', location.pathname); });
  document.getElementById('allBtn').addEventListener('click', function () { showAll = !showAll; this.textContent = showAll ? 'Show only upcoming checklists' : 'Show past and cancelled checklists too'; loadList(); });
  if (CFG.canWrite) {
    document.getElementById('manage').hidden = false;
    document.getElementById('newBtn').addEventListener('click', newChecklist);
    document.getElementById('tplBtn').addEventListener('click', function () { editTemplates(); });
    document.getElementById('refreshBtn').addEventListener('click', function () {
      api('POST', '/api/checklists/refresh').then(function (d) {
        if (!d._ok) { say(d.error || 'Could not check.', false); return; }
        var s = d.stats; say('Checked Planning Center: ' + s.created + ' new, ' + s.moved + ' moved, ' + s.cancelled + ' cancelled.', true); loadAll();
      });
    });
  }
  loadAll();
  if (/^#\d+$/.test(location.hash)) openDetail(location.hash.slice(1));
})();
