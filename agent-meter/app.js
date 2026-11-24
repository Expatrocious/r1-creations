/* agent meter: AI agent usage on the rabbit r1.
 *
 * Data: the PC writes usage.json to a secret gist every few minutes; this page
 * reads it from GitHub. The address carries the gist: ?g=<gist id>&u=<owner>.
 * r1 controls: side button = check now; scroll wheel = next agent (or, with one
 * agent, flip reset times between a countdown and the clock time).
 */
'use strict';
(function () {
  var POLL_MS = 90 * 1000;        // GitHub allows 60 unauthenticated API calls an hour
  var MANUAL_GAP_MS = 8 * 1000;   // side-button checks at most this often
  var TIMEOUT_MS = 15 * 1000;
  var LIVE_S = 15 * 60;           // the PC publishes at least every 10 minutes
  var OFFLINE_S = 30 * 60;        // nothing newer than this: the PC is off or asleep
  var GIST_FILE = 'usage.json';
  var STORE_KEY = 'agent-meter';
  var RING_R = 37, RING_C = 2 * Math.PI * RING_R;

  var $ = function (id) { return document.getElementById(id); };
  var app = $('app');
  var state = {
    data: null, ok: false, busy: false, page: 0, clock: false,
    lastTry: 0, failure: '', shown: {}
  };

  // ------------------------------------------------------------ config
  var params = new URLSearchParams(location.search);
  var cfg = { gist: clean(params.get('g'), /^[0-9a-f]{20,64}$/i), owner: clean(params.get('u'), /^[A-Za-z0-9-]{1,39}$/) };
  function clean(v, re) { return v && re.test(v) ? v : ''; }
  function ownerGuess() {
    var host = location.hostname.split('.');
    return host.length > 2 && host[1] === 'github' ? host[0] : '';
  }

  // ------------------------------------------------------------ storage
  // r1: window.creationStorage (values must be base64); a browser: localStorage.
  function b64(s) { return btoa(unescape(encodeURIComponent(s))); }
  function unb64(s) { return decodeURIComponent(escape(atob(s))); }
  var store = {
    get: function () {
      try {
        if (window.creationStorage && window.creationStorage.plain) {
          return Promise.resolve(window.creationStorage.plain.getItem(STORE_KEY)).then(function (v) {
            return v ? JSON.parse(unb64(v)) : null;
          }).catch(function () { return null; });
        }
        var v = window.localStorage.getItem(STORE_KEY);
        return Promise.resolve(v ? JSON.parse(v) : null);
      } catch (e) { return Promise.resolve(null); }
    },
    set: function (obj) {
      try {
        var s = JSON.stringify(obj);
        if (window.creationStorage && window.creationStorage.plain) {
          Promise.resolve(window.creationStorage.plain.setItem(STORE_KEY, b64(s))).catch(function () {});
        } else {
          window.localStorage.setItem(STORE_KEY, s);
        }
      } catch (e) { /* storage is a convenience */ }
    }
  };
  function persist() { store.set({ gist: cfg.gist, owner: cfg.owner, data: state.data }); }

  // ------------------------------------------------------------ time
  function parseTime(iso) {
    if (!iso) return NaN;
    return Date.parse(String(iso).replace(/(\.\d{3})\d+/, '$1'));
  }
  function countdown(t) {
    var s = Math.round((t - Date.now()) / 1000);
    if (s < 60) return 'now';
    var m = Math.floor(s / 60), h = Math.floor(m / 60), d = Math.floor(h / 24);
    if (d > 0) return d + 'd ' + (h % 24) + 'h';
    if (h > 0) return h + 'h ' + (m % 60) + 'm';
    return m + 'm';
  }
  function clockTime(t) {
    var d = new Date(t), now = new Date();
    var h = d.getHours(), m = d.getMinutes();
    var hm = ((h % 12) || 12) + (m ? ':' + (m < 10 ? '0' : '') + m : '') + (h < 12 ? ' AM' : ' PM');
    if (d.toDateString() === now.toDateString()) return hm;
    var tmrw = new Date(now.getFullYear(), now.getMonth(), now.getDate() + 1);
    if (d.toDateString() === tmrw.toDateString()) return 'tmrw ' + hm;
    return ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'][d.getDay()] + ' ' + hm;
  }
  function resetText(iso) {
    var t = parseTime(iso);
    if (isNaN(t)) return '';
    if (state.clock) return 'resets ' + clockTime(t);
    var c = countdown(t);
    return c === 'now' ? 'resets now' : 'resets in ' + c;
  }
  function ago(iso) {
    var t = parseTime(iso);
    if (isNaN(t)) return '';
    var s = Math.max(0, Math.round((Date.now() - t) / 1000));
    if (s < 60) return 'just now';
    if (s < 3600) return Math.floor(s / 60) + 'm ago';
    if (s < 86400) return Math.floor(s / 3600) + 'h ago';
    return Math.floor(s / 86400) + 'd ago';
  }
  function ageS(iso) {
    var t = parseTime(iso);
    return isNaN(t) ? Infinity : (Date.now() - t) / 1000;
  }
  // How far through its window we are, 0..1 (where an even pace would put usage).
  function elapsed(w) {
    var t = parseTime(w.resets_at);
    if (isNaN(t) || !w.period_s) return null;
    var f = 1 - (t - Date.now()) / 1000 / w.period_s;
    return f >= 0 && f <= 1 ? f : null;
  }

  // ------------------------------------------------------------ drawing
  var SVGNS = 'http://www.w3.org/2000/svg';
  function node(tag, cls, text) {
    var e = document.createElement(tag);
    if (cls) e.className = cls;
    if (text !== undefined) e.textContent = text;
    return e;
  }
  function svg(tag, attrs) {
    var e = document.createElementNS(SVGNS, tag);
    for (var k in attrs) e.setAttribute(k, attrs[k]);
    return e;
  }

  function gauge(w) {
    var pct = Math.max(0, Math.min(100, +w.pct || 0));
    var hot = pct >= 90;
    var b = node('button', 'gauge' + (hot ? ' hot' : ''));
    b.type = 'button';
    var ring = node('div', 'ring');
    var s = svg('svg', { viewBox: '0 0 84 84', 'aria-hidden': 'true' });
    s.appendChild(svg('circle', { class: 'track', cx: 42, cy: 42, r: RING_R }));
    var prev = state.shown[w.id] || 0;
    var arc = svg('circle', { class: 'arc', cx: 42, cy: 42, r: RING_R, 'stroke-dasharray': RING_C.toFixed(2) });
    arc.style.strokeDashoffset = (RING_C * (1 - prev / 100)).toFixed(2);
    arc.style.opacity = prev < 0.5 ? '0' : '1';
    s.appendChild(arc);
    var f = elapsed(w);
    if (f !== null) {
      var a = f * 2 * Math.PI;
      s.appendChild(svg('circle', { class: 'pace', cx: (42 + RING_R * Math.cos(a)).toFixed(2), cy: (42 + RING_R * Math.sin(a)).toFixed(2),
        r: 2.8, fill: pct >= f * 100 ? '#000' : '#d6d4d0' }));
    }
    ring.appendChild(s);
    var value = node('div', 'value');
    var num = node('span', 'num', String(Math.round(prev)));
    value.appendChild(num);
    value.appendChild(node('span', 'unit', '%'));
    ring.appendChild(value);
    b.appendChild(ring);
    b.appendChild(node('span', 'label', w.label || ''));
    b.appendChild(node('span', 'reset', resetText(w.resets_at)));
    b.addEventListener('click', function () { state.clock = !state.clock; render(); });
    // once on the page with the old value: the arc sweeps, the number counts (see settle)
    if (Math.round(prev * 10) !== Math.round(pct * 10)) {
      settles.push(function () {
        arc.style.opacity = pct < 0.5 ? '0' : '1';
        arc.style.strokeDashoffset = (RING_C * (1 - pct / 100)).toFixed(2);
        countTo(num, w.id, pct);
      });
    }
    return b;
  }

  // Elements start at the value shown last time; after one forced style pass they
  // move to the new one, so CSS transitions run without waiting on animation frames
  // (which the r1 may pause while its screen dims).
  var settles = [];
  function settle() {
    if (!settles.length) return;
    void document.body.offsetHeight;
    var run = settles;
    settles = [];
    run.forEach(function (fn) { fn(); });
  }

  var counters = {};
  function countTo(el, id, target) {
    var from = state.shown[id] || 0;
    var last = String(Math.round(target));
    state.shown[id] = target;
    if (counters[id]) { cancelAnimationFrame(counters[id].raf); clearTimeout(counters[id].timer); }
    if (Math.round(from) === Math.round(target) || document.hidden) { el.textContent = last; return; }
    var t0 = performance.now(), dur = 900, c = counters[id] = {};
    // the count is decoration: the final number lands on time even without frames
    c.timer = setTimeout(function () { cancelAnimationFrame(c.raf); el.textContent = last; }, dur + 150);
    (function step(now) {
      var k = Math.min(1, Math.max(0, (now - t0) / dur));
      k = 1 - Math.pow(1 - k, 3);
      el.textContent = String(Math.round(from + (target - from) * k));
      if (k < 1) c.raf = requestAnimationFrame(step);
      else clearTimeout(c.timer);
    })(t0);
  }

  function mini(label, pct) {
    pct = Math.max(0, Math.min(100, +pct || 0));
    var key = 'bar:' + label, prev = state.shown[key] || 0;
    var row = node('div', 'mini' + (pct >= 90 ? ' hot' : ''));
    row.appendChild(node('span', 'name', label));
    var bar = node('span', 'bar');
    var fill = node('i');
    fill.style.width = prev + '%';
    bar.appendChild(fill);
    row.appendChild(bar);
    row.appendChild(node('b', '', Math.round(pct) + '%'));
    if (prev !== pct) settles.push(function () { fill.style.width = pct + '%'; state.shown[key] = pct; });
    return row;
  }

  function insight(a) {
    var ws = a.windows || [];
    for (var i = 0; i < ws.length; i++) {
      if (ws[i].pct >= 100) return ['warn', ws[i].label + ' limit hit', ' · ' + resetText(ws[i].resets_at).replace('resets in ', 'resets ')];
    }
    for (i = 0; i < ws.length; i++) {
      if (ws[i].pct >= 90) return ['warn', ws[i].label + ' nearly out', ' · ' + resetText(ws[i].resets_at).replace('resets in ', 'resets ')];
    }
    var week = ws.filter(function (w) { return w.id === 'seven_day'; })[0] || ws[ws.length - 1];
    var f = week && elapsed(week);
    if (f === null || f === undefined) return null;
    var delta = Math.round(week.pct - f * 100);
    var span = week.period_s >= 6 * 86400 ? 'this week' : 'this session';
    if (delta <= -5) return ['good', (-delta) + '%', ' under pace ' + span];
    if (delta >= 5) return ['warn', delta + '%', ' over pace ' + span];
    return ['', '', 'right on pace ' + span];
  }

  // ------------------------------------------------------------ render
  function render() {
    var data = state.data;
    var agents = (data && data.agents) || [];
    if (state.page >= agents.length) state.page = 0;
    var a = agents[state.page];

    if (!a) {
      app.classList.add('loading');
      $('splash-text').textContent = !cfg.gist
        ? 'Scan the agent meter QR code from your PC to set this up.'
        : state.busy ? 'connecting…' : (state.failure || 'No reading yet.') + ' Press the side button or tap to try again.';
      return;
    }
    app.classList.remove('loading');

    $('agent-name').textContent = a.name || a.id || 'agent';
    var plan = $('plan');
    if (a.plan) { plan.textContent = a.plan; plan.hidden = false; } else { plan.hidden = true; }
    var pager = $('pager');
    pager.textContent = '';
    if (agents.length > 1) agents.forEach(function (_, i) { pager.appendChild(node('i', i === state.page ? 'on' : '')); });

    var ws = a.windows || [];
    var gauges = $('gauges'), more = $('more');
    gauges.textContent = ''; more.textContent = '';
    gauges.className = ws.length === 1 ? 'single' : '';
    ws.slice(0, 2).forEach(function (w) { gauges.appendChild(gauge(w)); });
    ws.slice(2).forEach(function (w) { more.appendChild(mini(w.label, w.pct)); });
    if (a.extra && typeof a.extra.pct === 'number') more.appendChild(mini('extra usage', a.extra.pct));
    settle();

    var ins = $('insight');
    var tip = ws.length ? insight(a) : null;
    if (!ws.length) tip = ['', '', a.error ? 'the PC can’t read usage yet' : 'no plan limits on this account'];
    if (tip && more.childElementCount < 2) {
      ins.className = tip[0];
      ins.textContent = '';
      if (tip[1]) ins.appendChild(node('b', '', tip[1]));
      ins.appendChild(document.createTextNode(tip[2]));
      ins.hidden = false;
    } else {
      ins.hidden = true;
    }

    // footer: how fresh, and whether the PC can take phone sessions
    var age = ageS(a.updated_at);
    var dot = $('dot'), status = $('status');
    dot.className = 'dot' + (state.busy ? ' busy' : '');
    var stale = false;
    if (state.busy && state.manual) {
      status.textContent = 'checking…';
    } else if (!state.ok && state.failure) {
      dot.className += ' bad';
      status.textContent = 'no connection · ' + ago(a.updated_at);
      stale = age > OFFLINE_S;
    } else if (!state.ok) {
      status.textContent = 'updated ' + ago(a.updated_at);
    } else if (a.error) {
      dot.className += ' bad';
      status.textContent = 'PC can’t read usage · ' + ago(a.updated_at);
    } else if (age <= LIVE_S) {
      dot.className += ' live';
      status.textContent = 'live · ' + ago(a.updated_at);
    } else if (age <= OFFLINE_S) {
      status.textContent = 'updated ' + ago(a.updated_at);
    } else {
      dot.className += ' bad';
      status.textContent = 'PC offline · ' + ago(a.updated_at);
      stale = true;
    }
    app.classList.toggle('stale', stale);
    var server = $('server');
    if (a.server) {
      var on = a.server === 'active';
      server.textContent = on ? 'sessions on' : 'sessions off';
      server.className = on ? 'on' : '';
    } else {
      server.textContent = '';
    }
  }

  // ------------------------------------------------------------ data
  var apiBlockedUntil = 0;
  function timed(url, opts) {
    var ctrl = window.AbortController ? new AbortController() : null;
    var timer = setTimeout(function () { if (ctrl) ctrl.abort(); }, TIMEOUT_MS);
    opts = opts || {};
    if (ctrl) opts.signal = ctrl.signal;
    return fetch(url, opts).then(function (r) { clearTimeout(timer); return r; },
      function (e) { clearTimeout(timer); throw e; });
  }
  function fail(message) { var e = new Error(message); e.friendly = true; return e; }
  function fromApi() {
    return timed('https://api.github.com/gists/' + cfg.gist, { cache: 'no-cache', headers: { Accept: 'application/vnd.github+json' } })
      .then(function (r) {
        var left = r.headers.get('x-ratelimit-remaining'), reset = +r.headers.get('x-ratelimit-reset');
        if (r.status === 403 || r.status === 429 || left === '0') {
          apiBlockedUntil = reset ? reset * 1000 : Date.now() + 15 * 60 * 1000;
        }
        if (r.status === 404) { var gone = fail('This QR code was replaced. Scan the new one from your PC.'); gone.gone = true; throw gone; }
        if (!r.ok) throw fail('GitHub said ' + r.status + '.');
        return r.json();
      })
      .then(function (g) {
        var f = g && g.files && g.files[GIST_FILE];
        if (!f) throw fail('No usage file yet.');
        if (g.owner && g.owner.login && !cfg.owner) cfg.owner = g.owner.login;
        if (f.truncated && f.raw_url) return timed(f.raw_url, { cache: 'no-store' }).then(function (r) { return r.json(); });
        return JSON.parse(f.content);
      });
  }
  function fromRaw() {
    var owner = cfg.owner || ownerGuess();
    if (!owner) return Promise.reject(fail('Can’t reach GitHub right now.'));
    return timed('https://gist.githubusercontent.com/' + owner + '/' + cfg.gist + '/raw/' + GIST_FILE + '?t=' + Date.now(), { cache: 'no-store' })
      .then(function (r) {
        if (!r.ok) throw fail(r.status === 404 ? 'This QR code was replaced. Scan the new one from your PC.' : 'GitHub said ' + r.status + '.');
        return r.json();
      });
  }

  var inflight = null;
  function load(manual) {
    if (!cfg.gist) { render(); return Promise.resolve(); }
    if (inflight) return inflight;
    if (manual && Date.now() - state.lastTry < MANUAL_GAP_MS && state.ok) { flash(); return Promise.resolve(); }
    state.lastTry = Date.now();
    state.busy = true; state.manual = !!manual;
    render();
    var get = Date.now() >= apiBlockedUntil
      ? fromApi().catch(function (e) { if (e.gone) throw e; return fromRaw(); })
      : fromRaw();
    inflight = get.then(function (doc) {
      if (!doc || !Array.isArray(doc.agents)) throw fail('The usage file looks wrong.');
      state.data = doc; state.ok = true; state.failure = '';
      persist();
    }).catch(function (e) {
      state.ok = false;
      state.failure = e && e.name === 'AbortError' ? 'GitHub took too long.' : (e && e.friendly ? e.message : 'Can’t reach GitHub right now.');
    }).then(function () {
      state.busy = false; state.manual = false; inflight = null;
      render();
    });
    return inflight;
  }
  function flash() {
    var dot = $('dot');
    dot.classList.add('busy');
    setTimeout(function () { dot.classList.remove('busy'); }, 700);
  }

  // ------------------------------------------------------------ r1 hardware
  function wheel(dir) {
    var agents = (state.data && state.data.agents) || [];
    if (agents.length > 1) {
      state.page = (state.page + dir + agents.length) % agents.length;
      state.shown = {};
    } else {
      state.clock = !state.clock;
    }
    render();
  }
  window.addEventListener('sideClick', function () { load(true); });
  window.addEventListener('scrollUp', function () { wheel(-1); });
  window.addEventListener('scrollDown', function () { wheel(1); });
  $('footer').addEventListener('click', function () { load(true); });
  $('splash').addEventListener('click', function () { load(true); });
  document.addEventListener('keydown', function (e) {   // a desktop browser, for testing
    if (e.key === 'ArrowUp') wheel(-1);
    else if (e.key === 'ArrowDown') wheel(1);
    else if (e.key === 'Enter' || e.key === ' ') load(true);
  });

  // ------------------------------------------------------------ start
  document.addEventListener('visibilitychange', function () { if (!document.hidden) load(false); });
  setInterval(function () { if (!document.hidden) load(false); }, POLL_MS);
  setInterval(function () { if (!state.busy && state.data) render(); }, 20 * 1000);   // countdowns, "ago"

  store.get().then(function (saved) {
    if (saved) {
      if (!cfg.gist && saved.gist) { cfg.gist = saved.gist; cfg.owner = cfg.owner || saved.owner || ''; }
      if (saved.data && saved.gist === cfg.gist && Array.isArray(saved.data.agents) && !state.data) {
        state.data = saved.data; state.ok = false;
      }
    }
    if (!cfg.owner) cfg.owner = ownerGuess();
    render();
    load(false);
  });
})();
