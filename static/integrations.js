/* The Integrations page. DOM is built with textContent, never HTML strings. */
(function () {
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k]; else if (k === 'class') n.className = attrs[k]; else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function api(method, url, body) {
    var o = { method: method, headers: {} };
    if (body !== undefined) { o.headers['Content-Type'] = 'application/json'; o.body = JSON.stringify(body); }
    return fetch(url, o).then(function (r) { return r.json().catch(function () { return {}; }).then(function (d) { d._ok = r.ok; return d; }); });
  }
  function ago(iso) {
    if (!iso) return 'never';
    var s = Math.max(0, (Date.now() - new Date(iso).getTime()) / 1000);
    if (s < 90) return 'just now'; if (s < 5400) return Math.round(s / 60) + ' minutes ago';
    if (s < 129600) return Math.round(s / 3600) + ' hours ago'; return Math.round(s / 86400) + ' days ago';
  }
  var STATUS = { healthy: 'Working', stale: 'Not synced recently', failing: 'Needs attention', needs_reconnect: 'Needs to be reconnected',
                 waiting: 'Waiting', not_set_up: 'Not set up', off: 'Turned off', manual: 'By hand or CSV' };
  var list = document.getElementById('list'), banner = document.getElementById('banner');
  var params = new URLSearchParams(location.search), canManage = false;
  if (params.get('connected')) { banner.className = 'banner ok'; banner.textContent = 'Connected. The first sync will run shortly, or use Sync now.'; }
  if (params.get('error')) { banner.className = 'banner bad'; banner.textContent = { denied: 'Access was not granted, so nothing was connected.', state: 'That sign-in expired. Please try again.', exchange: 'The provider would not complete the sign-in. Please try again.', unknown: 'Unknown integration.' }[params.get('error')] || 'Something went wrong.'; }

  function card(i) {
    var actions = el('div', { class: 'actions' });
    function btn(text, fn, primary) { var b = el('button', { type: 'button', text: text, class: primary ? 'primary' : '' }); b.addEventListener('click', fn); actions.appendChild(b); return b; }
    function link(text, href, primary) { actions.appendChild(el('a', { class: 'btn' + (primary ? ' primary' : ''), href: href, text: text })); }
    if (canManage) {
      var needsConnect = i.status === 'not_set_up' || i.status === 'needs_reconnect' || (i.status === 'waiting' && /Reconnect|Connect|Add the/.test(i.detail + i.action));
      if (i.how === 'oauth' || i.how === 'pco') { if (i.status !== 'manual') link(i.status === 'needs_reconnect' || i.status === 'waiting' ? 'Reconnect' : (i.account ? 'Reconnect' : 'Connect'), '/integrations/' + i.key + '/connect', needsConnect); }
      if (i.how === 'token') btn(i.account ? 'Update token' : 'Add token', function () { document.getElementById('fbDlg').showModal(); }, needsConnect);
      if (i.how === 'key') btn(i.account ? 'Replace API key' : 'Add API key', function () { document.getElementById('keyDlg').showModal(); }, needsConnect);
      if (['healthy', 'stale', 'failing', 'waiting'].indexOf(i.status) !== -1 && i.how !== 'none' && !/Waiting for/.test(i.headline) || i.status === 'healthy') {
        btn('Sync now', function () { api('POST', '/api/integrations/' + i.key + '/sync').then(function (d) { banner.className = 'banner ' + (d._ok ? 'ok' : 'bad'); banner.textContent = d._ok ? 'Sync started. This page refreshes in a moment.' : (d.error || 'Could not sync.'); setTimeout(load, 2500); }); });
      }
      if (i.status === 'manual') link('Open streaming numbers', '/streaming', true);
      if (i.account && i.how !== 'none') btn('Disconnect', function () { if (confirm('Disconnect ' + i.label + '? Data already collected is kept.')) api('POST', '/api/integrations/' + i.key + '/disconnect').then(load); });
      if (i.status !== 'manual') btn(i.status === 'off' ? 'Turn on' : 'Turn off', function () { api('POST', '/api/integrations/' + i.key + '/enabled', { enabled: i.status === 'off' }).then(load); });
    }
    var runs = el('details', {}, [el('summary', { text: 'Recent syncs', style: 'cursor:pointer;font-size:.84rem;margin-top:10px' })]);
    if (!i.runs.length) runs.appendChild(el('p', { text: 'No syncs yet.', style: 'font-size:.82rem;opacity:.7' }));
    else {
      var t = el('table'); t.appendChild(el('thead', {}, [el('tr', {}, ['When', 'Result', 'Rows', 'Message'].map(function (h) { return el('th', { text: h }); }))]));
      var b = el('tbody');
      i.runs.forEach(function (r) { b.appendChild(el('tr', {}, [el('td', { text: new Date(r.started_at).toLocaleString() + ' (' + r.trigger + ')' }), el('td', { text: r.status }), el('td', { text: r.rows_fetched + ' read, ' + r.rows_changed + ' changed' }), el('td', { text: r.error })])); });
      t.appendChild(b); runs.appendChild(t);
    }
    return el('section', { class: 'card', 'aria-labelledby': 'h-' + i.key }, [
      el('div', { class: 'top' }, [
        el('div', {}, [el('div', { class: 'name', id: 'h-' + i.key, text: i.label }), el('div', { class: 'desc', text: i.description })]),
        el('span', { class: 'pill ' + i.status, text: STATUS[i.status] || i.status }),
      ]),
      el('p', { class: 'detail', text: [i.headline, i.detail].filter(Boolean).join('. ').replace(/\.\.$/, '.') }),
      el('div', { class: 'meta', text: (i.account ? 'Connected as ' + i.account + '. ' : '') + (i.status === 'manual' ? '' : 'Last good sync: ' + ago(i.last_success_at) + (i.interval_minutes ? '. Syncs every ' + i.interval_minutes + ' minutes.' : '.')) }),
      actions, runs,
    ]);
  }
  function load() {
    api('GET', '/api/integrations').then(function (d) {
      if (!d._ok) { list.textContent = d.error || 'Could not load.'; return; }
      canManage = d.can_manage; list.textContent = '';
      d.integrations.forEach(function (i) { list.appendChild(card(i)); });
    });
  }
  document.getElementById('fbCancel').addEventListener('click', function () { document.getElementById('fbDlg').close(); });
  document.getElementById('fbSave').addEventListener('click', function () {
    api('POST', '/api/integrations/facebook/connect', { token: document.getElementById('fbToken').value, page_id: document.getElementById('fbPage').value, ig_user_id: document.getElementById('fbIg').value }).then(function (d) {
      if (!d._ok) { document.getElementById('fbErr').textContent = d.error || 'Could not save.'; return; }
      document.getElementById('fbToken').value = ''; document.getElementById('fbDlg').close(); load();
    });
  });
  document.getElementById('keyCancel').addEventListener('click', function () { document.getElementById('keyDlg').close(); });
  document.getElementById('keySave').addEventListener('click', function () {
    api('POST', '/api/integrations/text_in_church/key', { api_key: document.getElementById('tcKey').value }).then(function (d) {
      if (!d._ok) { document.getElementById('keyErr').textContent = d.error || 'Could not save.'; return; }
      document.getElementById('tcKey').value = ''; document.getElementById('keyDlg').close(); load();
    });
  });
  load();
})();
