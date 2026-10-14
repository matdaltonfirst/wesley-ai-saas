/* The Needs attention list on the home page. DOM is built with textContent, never HTML strings. */
(function () {
  var box = document.getElementById('attn');
  if (!box) return;
  function el(tag, attrs, kids) {
    var n = document.createElement(tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (k === 'text') n.textContent = attrs[k]; else if (k === 'class') n.className = attrs[k]; else if (k === 'onclick') n.addEventListener('click', attrs[k]); else n.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (c) { if (c) n.appendChild(c); });
    return n;
  }
  function load() {
    fetch('/api/attention').then(function (r) { return r.json().then(function (d) { return { ok: r.ok, d: d }; }); }).then(function (res) {
      box.textContent = '';
      if (!res.ok) { box.appendChild(el('p', { class: 'clear', text: 'Could not check what needs attention. Reload to try again.' })); return; }
      if (!res.d.alerts.length) { box.appendChild(el('p', { class: 'clear', text: 'Nothing needs your attention right now.' })); return; }
      res.d.alerts.forEach(function (a) {
        box.appendChild(el('div', { class: 'alert ' + a.severity }, [
          el('div', {}, [el('div', { class: 't', text: a.title }), a.detail ? el('div', { class: 'd', text: a.detail }) : null, el('a', { class: 'go', href: a.link, text: a.action })]),
          el('button', { text: 'Hide for a week', 'aria-label': 'Hide for a week: ' + a.title, onclick: function () {
            fetch('/api/attention/snooze', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ key: a.key, days: 7 }) }).then(load);
          } }),
        ]));
      });
    }).catch(function () { box.textContent = ''; box.appendChild(el('p', { class: 'clear', text: 'Could not check what needs attention. Reload to try again.' })); });
  }
  load();
})();
