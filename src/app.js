/* Phosphor OS: a phone-style launcher. Tiles open app links (tel:, sms:, maps://),
   Shortcuts (shortcuts://run-shortcut), Floyd's other tools, or built-in apps. */
(() => {
  const $ = s => document.querySelector(s);
  const $$ = s => [...document.querySelectorAll(s)];
  const VERSION = '1.59';

  /* ---------- Storage (prefixed: every tool shares bgriffie.github.io) ---------- */
  const KEY = 'phosphor-os:data:';
  const load = (k, d) => { try { const v = localStorage.getItem(KEY + k); return v === null ? d : JSON.parse(v); } catch { return d; } };
  const save = (k, v) => { try { localStorage.setItem(KEY + k, JSON.stringify(v)); } catch { PH.toast('Storage is full or blocked.', 'error'); } };
  const uid = () => Date.now().toString(36) + Math.random().toString(36).slice(2, 6);

  /* ---------- Glyphs: 24x24, stroke=currentColor, square caps ---------- */
  const G = {
    phone: '<path d="M5 3h4l2 5-3 2a12 12 0 0 0 6 6l2-3 5 2v4a2 2 0 0 1-2 2A18 18 0 0 1 3 5a2 2 0 0 1 2-2z"/>',
    chat: '<path d="M3 4h18v12H9l-6 4z"/><path d="M7 9h10M7 12h6"/>',
    mail: '<rect x="3" y="5" width="18" height="14"/><path d="M3 6l9 7 9-7"/>',
    video: '<rect x="2" y="6" width="14" height="12"/><path d="M16 10l6-3v10l-6-3"/>',
    map: '<path d="M9 3 3 5v16l6-2 6 2 6-2V3l-6 2-6-2zM9 3v16M15 5v16"/>',
    camera: '<path d="M3 7h4l2-3h6l2 3h4v13H3z"/><circle cx="12" cy="13" r="4"/>',
    music: '<path d="M9 18V5l11-2v13"/><circle cx="6" cy="18" r="3"/><circle cx="17" cy="16" r="3"/>',
    game: '<rect x="2" y="7" width="20" height="11"/><path d="M6 12h4M8 10v4M15 11h1M18 14h1"/>',
    dumbbell: '<path d="M6 6v12M18 6v12M3 9v6M21 9v6M6 12h12"/>',
    pot: '<path d="M3 10h18M5 10v9h14v-9M8 5v2M12 4v3M16 5v2"/>',
    note: '<path d="M5 3h10l4 4v14H5zM15 3v4h4M8 12h8M8 16h8"/>',
    calc: '<rect x="5" y="3" width="14" height="18"/><path d="M8 7h8M8 12h2M14 12h2M8 16h2M14 16h2"/>',
    gear: '<circle cx="12" cy="12" r="3"/><path d="M12 2v3M12 19v3M2 12h3M19 12h3M5 5l2 2M17 17l2 2M5 19l2-2M17 7l2-2"/>',
    globe: '<circle cx="12" cy="12" r="9"/><path d="M3 12h18M12 3c3 3 3 15 0 18M12 3c-3 3-3 15 0 18"/>',
    bolt: '<path d="M13 2 4 14h7l-1 8 9-12h-7z"/>',
    star: '<path d="M12 3l2.7 5.6 6.1.9-4.4 4.3 1 6.1L12 17l-5.4 2.9 1-6.1-4.4-4.3 6.1-.9z"/>',
    clock: '<circle cx="12" cy="12" r="9"/><path d="M12 7v5l3 3"/>',
    term: '<rect x="3" y="4" width="18" height="16"/><path d="M7 9l3 3-3 3M12 15h5"/>',
    home: '<path d="M3 11 12 3l9 8M5 9v12h14V9"/>',
    heart: '<path d="M12 20 4 12a5 5 0 0 1 8-6 5 5 0 0 1 8 6z"/>',
    cart: '<path d="M3 4h3l3 11h10l2-8H7"/><circle cx="10" cy="19" r="1.5"/><circle cx="17" cy="19" r="1.5"/>',
    book: '<path d="M4 4h6a2 2 0 0 1 2 2v14a2 2 0 0 0-2-2H4zM20 4h-6a2 2 0 0 0-2 2v14a2 2 0 0 1 2-2h6z"/>',
    bulb: '<path d="M9 18h6M10 21h4M12 3a6 6 0 0 0-4 10.5V16h8v-2.5A6 6 0 0 0 12 3z"/>',
    monitor: '<rect x="3" y="4" width="18" height="12" rx="1"/><path d="M8 20h8M12 16v4"/>',
    tv: '<rect x="3" y="8" width="18" height="12" rx="1"/><path d="M9 22h6M8 3l4 4 4-4"/>',
    house: '<path d="M3 11l9-7 9 7"/><path d="M5 10v10h14V10"/><path d="M10 20v-6h4v6"/>',
    film: '<rect x="3" y="4" width="18" height="16" rx="1"/><path d="M7 4v16M17 4v16M3 9h4M3 15h4M17 9h4M17 15h4"/>',
    sun: '<circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.5 1.5M17.5 17.5 19 19M5 19l1.5-1.5M17.5 6.5 19 5"/>',
  };
  const svg = (name, cls = '') =>
    `<svg class="${cls}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square" stroke-linejoin="miter" aria-hidden="true">${G[name] || G.term}</svg>`;

  /* ---------- Tiles ---------- */
  // Built-in apps: OS pages, or Floyd's other tools bundled under apps/<id>/ and run in a full-screen frame
  const APPS = { phone: ['Phone', '/phone'], notes: ['Notes', '/notes'], calc: ['Calculator', '/calc'], settings: ['Settings', '/settings'], music: ['Spotify', '/music'], lights: ['Lights', null, 'apps/lights/', false], pc: ['PC', null, 'apps/pc/', false], tv: ['TV', null, 'apps/tv/', false], watch: ['What to Watch', null, 'apps/watch/'], home: ['Home Map', null, 'apps/home/', false],
    arcade: ['Arcade', null, 'apps/arcade/'], workout: ['Workout Log', null, 'apps/workout/'], kitchen: ['Kitchen Log', null, 'apps/kitchen/'] };
  const DEFAULT_TILES = () => [
    ['Phone', 'phone', 'app', 'phone'],
    ['Messages', 'chat', 'shortcut', 'Open Messages'],
    ['Mail', 'mail', 'link', 'googlegmail://'],
    ['FaceTime', 'video', 'shortcut', 'Open FaceTime'],
    ['Maps', 'map', 'link', 'comgooglemaps://'],
    ['Camera', 'camera', 'shortcut', 'Open Camera'],
    ['Spotify', 'music', 'app', 'music'],
    ['Lights', 'bulb', 'app', 'lights'],
    ['PC', 'monitor', 'app', 'pc'],
    ['TV', 'tv', 'app', 'tv'],
    ['Watch', 'film', 'app', 'watch'],
    ['Map', 'house', 'app', 'home'],
    ['Notes', 'note', 'app', 'notes'],
    ['Arcade', 'game', 'app', 'arcade'],
    ['Workout', 'dumbbell', 'app', 'workout'],
    ['Kitchen', 'pot', 'app', 'kitchen'],
    ['Calc', 'calc', 'app', 'calc'],
  ].map(([label, glyph, kind, target]) => ({ id: uid(), label, glyph, kind, target }));

  let tiles = load('tiles', null);
  if (!Array.isArray(tiles)) { tiles = DEFAULT_TILES(); save('tiles', tiles); }
  // v1.1: tiles that linked out to the standalone tools now open the bundled copies
  const BUNDLED = { 'phosphor-arcade': 'arcade', gymlog: 'workout', kitchentracker: 'kitchen' };
  tiles.forEach(t => {
    const m = t.kind === 'link' && /^https:\/\/bgriffie\.github\.io\/([^/?#]+)\/?$/i.exec(t.target);
    if (m && BUNDLED[m[1].toLowerCase()]) { t.kind = 'app'; t.target = BUNDLED[m[1].toLowerCase()]; }
    // v1.2: the Spotify tile opens the built-in remote (an app link can't leave Guided Access)
    if (t.kind === 'link' && /^spotify:\/*$/i.test(t.target)) { t.kind = 'app'; t.target = 'music'; }
    // v1.5: Messages opens the app itself, not a blank new message
    // v1.6: iOS treats sms: and messages:// as "new message", so Messages runs a one-step Shortcut that opens the app
    if (t.kind === 'link' && /^(sms:|messages:)\/*$/i.test(t.target)) { t.kind = 'shortcut'; t.target = 'Open Messages'; }
    // v1.6: Floyd uses Gmail and Google Maps, not Apple Mail and Maps
    if (t.kind === 'link' && /^message:\/*$/i.test(t.target)) t.target = 'googlegmail://';
    if (t.kind === 'link' && /^maps:\/*$/i.test(t.target)) t.target = 'comgooglemaps://';
  });
  // v1.7: a Lights tile, once, for phones set up before it existed
  if (!load('mig-lights', false)) {
    if (!tiles.some(t => t.kind === 'app' && t.target === 'lights')) {
      const i = tiles.findIndex(t => t.kind === 'app' && t.target === 'music');
      tiles.splice(i < 0 ? tiles.length : i + 1, 0, { id: uid(), label: 'Lights', glyph: 'bulb', kind: 'app', target: 'lights' });
    }
    save('mig-lights', true);
  }
  // v1.13: a PC tile (PC Remote, through the same home relay), once, after Lights
  if (!load('mig-pc', false)) {
    if (!tiles.some(t => t.kind === 'app' && t.target === 'pc')) {
      const i = tiles.findIndex(t => t.kind === 'app' && t.target === 'lights');
      tiles.splice(i < 0 ? tiles.length : i + 1, 0, { id: uid(), label: 'PC', glyph: 'monitor', kind: 'app', target: 'pc' });
    }
    save('mig-pc', true);
  }
  // v1.26: a TV tile (Phosphor TV on the Chromecast, through the same relay), once, after PC
  if (!load('mig-tv', false)) {
    if (!tiles.some(t => t.kind === 'app' && t.target === 'tv')) {
      const i = tiles.findIndex(t => t.kind === 'app' && t.target === 'pc');
      tiles.splice(i < 0 ? tiles.length : i + 1, 0, { id: uid(), label: 'TV', glyph: 'tv', kind: 'app', target: 'tv' });
    }
    save('mig-tv', true);
  }
  // v1.47: a What to Watch tile (movie and show picks), once, after TV
  if (!load('mig-watch', false)) {
    if (!tiles.some(t => t.kind === 'app' && t.target === 'watch')) {
      const i = tiles.findIndex(t => t.kind === 'app' && t.target === 'tv');
      tiles.splice(i < 0 ? tiles.length : i + 1, 0, { id: uid(), label: 'Watch', glyph: 'film', kind: 'app', target: 'watch' });
    }
    save('mig-watch', true);
  }
  // v1.57: a Home Map tile (3D floor plan with tappable lights and the TV), once, after Watch
  if (!load('mig-home', false)) {
    if (!tiles.some(t => t.kind === 'app' && t.target === 'home')) {
      const i = tiles.findIndex(t => t.kind === 'app' && t.target === 'watch');
      tiles.splice(i < 0 ? tiles.length : i + 1, 0, { id: uid(), label: 'Map', glyph: 'house', kind: 'app', target: 'home' });
    }
    save('mig-home', true);
  }
  save('tiles', tiles);
  const saveTiles = () => { save('tiles', tiles); renderGrid(); renderTileList(); };

  const hrefFor = t =>
    t.kind === 'shortcut' ? 'shortcuts://run-shortcut?name=' + encodeURIComponent(t.target) :
    t.kind === 'link' ? t.target : null;

  function launch(t) {
    if (t.kind === 'app') { const a = APPS[t.target] || APPS.notes; if (a[2]) openApp(t.target); else PH.go(a[1]); return; }
    const href = hrefFor(t);
    if (!href) return;
    location.href = href;   // iOS hands schemes to the app; a missing app silently does nothing
  }

  function renderGrid() {
    const grid = $('#grid');
    grid.style.setProperty('--cols', PH.store.get('cols'));
    grid.textContent = '';
    tiles.forEach(t => {
      const b = document.createElement('button');
      b.className = 'tile kind-' + t.kind;
      b.dataset.tile = t.id;
      b.innerHTML = `<span class="ico">${svg(t.glyph)}</span><span class="lbl"></span>`;
      b.querySelector('.lbl').textContent = t.label;
      b.setAttribute('aria-label', t.label);
      grid.appendChild(b);
    });
    const add = document.createElement('button');
    add.className = 'tile add'; add.dataset.act = 'add-tile'; add.setAttribute('aria-label', 'Add tile');
    add.innerHTML = '<span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></span><span class="lbl">Add</span>';
    grid.appendChild(add);
    $('#tile-count').textContent = tiles.length;
    sphere.build();
  }

  // Tap launches; hold (550ms) opens the tile editor
  let holdTimer = null, held = false, hx = 0, hy = 0;
  const grid = $('#grid');
  grid.addEventListener('pointerdown', e => {
    const b = e.target.closest('[data-tile]'); held = false; if (!b) return;
    hx = e.clientX; hy = e.clientY;
    holdTimer = setTimeout(() => { held = true; b.classList.add('is-held'); setTimeout(() => b.classList.remove('is-held'), 200); editTile(b.dataset.tile); }, 550);
  });
  grid.addEventListener('pointermove', e => { if (holdTimer && Math.hypot(e.clientX - hx, e.clientY - hy) > 10) { clearTimeout(holdTimer); holdTimer = null; } });
  ['pointerup', 'pointercancel', 'pointerleave'].forEach(n => grid.addEventListener(n, () => { clearTimeout(holdTimer); holdTimer = null; }));
  grid.addEventListener('contextmenu', e => e.preventDefault());
  grid.addEventListener('click', e => {
    const b = e.target.closest('[data-tile]'); if (!b) return;
    if (held) { held = false; return; }
    const t = tiles.find(x => x.id === b.dataset.tile); if (t) launch(t);
  });

  /* ---------- Sphere home: the tiles as a draggable 3D web ---------- */
  const sphere = (() => {
    const wrap = $('#sphere'), cv = $('#sphere-lines'), cx2 = cv.getContext('2d');
    const reduce = matchMedia('(prefers-reduced-motion: reduce)').matches;
    let nodes = [], edges = [], pulses = [], R = 120, SX = 1, cx = 0, cy = 0, W = 0, H = 0;
    let M = [1, 0, 0, 0, 1, 0, 0, 0, 1], vx = 0, vy = 0, raf = null, idleAt = 0, frames = 0;
    const mul = (a, b) => { const r = []; for (let i = 0; i < 3; i++) for (let j = 0; j < 3; j++) r[i * 3 + j] = a[i * 3] * b[j] + a[i * 3 + 1] * b[3 + j] + a[i * 3 + 2] * b[6 + j]; return r; };
    const rotate = (ax, ay) => {   // drag right spins the front to the right, drag down spins it down
      const c = Math.cos(ay), s = Math.sin(ay), C = Math.cos(ax), S = Math.sin(ax);
      M = mul([1, 0, 0, 0, C, -S, 0, S, C], mul([c, 0, s, 0, 1, 0, -s, 0, c], M));
      if (++frames % 120 === 0) orthonormalize();
    };
    function orthonormalize() {   // keep the matrix a pure rotation as float error builds up
      const a = M.slice(0, 3), b = M.slice(3, 6), n = v => { const l = Math.hypot(...v); return v.map(x => x / l); };
      const x = n(a), d = x[0] * b[0] + x[1] * b[1] + x[2] * b[2], y = n(b.map((v, i) => v - d * x[i]));
      M = [...x, ...y, x[1] * y[2] - x[2] * y[1], x[2] * y[0] - x[0] * y[2], x[0] * y[1] - x[1] * y[0]];
    }
    function build() {
      wrap.querySelectorAll('.orb').forEach(el => el.remove());
      const items = tiles.map(t => ({ t })).concat([{ add: true }]);
      const n = items.length, golden = Math.PI * (3 - Math.sqrt(5));
      nodes = items.map((it, i) => {
        const y = 1 - 2 * (i + .5) / n, r = Math.sqrt(1 - y * y), th = i * golden;
        const el = document.createElement('button');
        el.className = 'orb' + (it.add ? ' add' : ' kind-' + it.t.kind);
        if (it.add) { el.dataset.act = 'add-tile'; el.setAttribute('aria-label', 'Add tile'); el.innerHTML = '<span class="ico"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="square" aria-hidden="true"><path d="M12 5v14M5 12h14"/></svg></span><span class="lbl">Add</span>'; }
        else { el.dataset.tile = it.t.id; el.setAttribute('aria-label', it.t.label); el.innerHTML = `<span class="ico">${svg(it.t.glyph)}</span><span class="lbl"></span>`; el.querySelector('.lbl').textContent = it.t.label; }
        wrap.appendChild(el);
        return { p: [Math.cos(th) * r, y, Math.sin(th) * r], el, q: [0, 0, 0], d: 0, x: 0, y: 0 };
      });
      // link each node to its 3 nearest neighbours
      const seen = new Set(); edges = [];
      nodes.forEach((a, i) => nodes.map((b, j) => [j, a.p[0] * b.p[0] + a.p[1] * b.p[1] + a.p[2] * b.p[2]]).filter(([j]) => j !== i)
        .sort((u, v) => v[1] - u[1]).slice(0, 3).forEach(([j]) => { const k = i < j ? i + '-' + j : j + '-' + i; if (!seen.has(k)) { seen.add(k); edges.push([i, j]); } }));
      pulses = Array.from({ length: Math.min(6, edges.length) }, () => ({ e: Math.random() * edges.length | 0, t: Math.random(), v: .004 + Math.random() * .006 }));
      size(); draw();
    }
    function size() {
      W = wrap.clientWidth; H = wrap.clientHeight; if (!W || !H) return;
      const dpr = Math.min(2, devicePixelRatio || 1);
      cv.width = W * dpr; cv.height = H * dpr; cv.style.width = W + 'px'; cv.style.height = H + 'px';
      cx2.setTransform(dpr, 0, 0, dpr, 0, 0);
      R = Math.min(W * .4, H * .36); cx = W / 2; cy = H / 2;
      SX = Math.max(1, Math.min(W * .42 / R, 1.7));   // a wide screen stretches the web sideways to use the room
    }
    function draw() {
      if (!W) size(); if (!W) return;
      nodes.forEach(nd => {
        const p = nd.p, q = [M[0] * p[0] + M[1] * p[1] + M[2] * p[2], M[3] * p[0] + M[4] * p[1] + M[5] * p[2], M[6] * p[0] + M[7] * p[1] + M[8] * p[2]];
        const d = (q[2] + 1) / 2, k = R * (.82 + .3 * d);   // d: 0 at the back, 1 facing you
        nd.q = q; nd.d = d; nd.x = cx + q[0] * k * SX; nd.y = cy - q[1] * k;
        const st = nd.el.style;
        st.transform = `translate3d(${(nd.x - 30).toFixed(1)}px,${(nd.y - 30).toFixed(1)}px,0) scale(${(.5 + .62 * d).toFixed(3)})`;
        st.opacity = (.15 + .85 * d * d).toFixed(3); st.zIndex = 1 + Math.round(d * 100);
        st.pointerEvents = d > .42 ? 'auto' : 'none';
        nd.el.classList.toggle('front', d > .72);
      });
      const g = cx2; g.clearRect(0, 0, W, H);
      // core glow + outer ring
      const rad = g.createRadialGradient(cx, cy, 0, cx, cy, R * 1.25);
      rad.addColorStop(0, 'rgba(57,255,106,.10)'); rad.addColorStop(1, 'rgba(57,255,106,0)');
      g.fillStyle = rad; g.beginPath(); g.ellipse(cx, cy, R * 1.25 * SX, R * 1.25, 0, 0, 7); g.fill();
      g.strokeStyle = 'rgba(57,255,106,.22)'; g.lineWidth = 1; g.setLineDash([2, 6]);
      g.beginPath(); g.ellipse(cx, cy, R * 1.18 * SX, R * 1.18, 0, 0, 7); g.stroke(); g.setLineDash([]);
      // two wireframe rings that turn with the sphere
      [[1, 0, 0], [0, 1, 0]].forEach(axis => {
        g.beginPath();
        for (let i = 0; i <= 48; i++) {
          const a = i / 48 * Math.PI * 2, c = Math.cos(a), s = Math.sin(a);
          const p = axis[0] ? [0, c, s] : [c, 0, s];
          const q = [M[0] * p[0] + M[1] * p[1] + M[2] * p[2], M[3] * p[0] + M[4] * p[1] + M[5] * p[2], M[6] * p[0] + M[7] * p[1] + M[8] * p[2]];
          const k = R * (.82 + .3 * (q[2] + 1) / 2), x = cx + q[0] * k * SX, y = cy - q[1] * k;
          i ? g.lineTo(x, y) : g.moveTo(x, y);
        }
        g.strokeStyle = 'rgba(57,255,106,.10)'; g.stroke();
      });
      // links, brighter in front
      edges.forEach(([i, j]) => {
        const a = nodes[i], b = nodes[j], d = (a.d + b.d) / 2;
        g.strokeStyle = `rgba(57,255,106,${(.05 + .5 * d * d).toFixed(3)})`; g.lineWidth = .6 + d;
        g.beginPath(); g.moveTo(a.x, a.y); g.lineTo(b.x, b.y); g.stroke();
      });
      // data packets running along the links
      pulses.forEach(pu => {
        const [i, j] = edges[pu.e] || [0, 0], a = nodes[i], b = nodes[j]; if (!a || !b) return;
        const x = a.x + (b.x - a.x) * pu.t, y = a.y + (b.y - a.y) * pu.t, d = (a.d + b.d) / 2;
        g.fillStyle = `rgba(255,177,59,${(.2 + .8 * d).toFixed(3)})`; g.shadowColor = '#FFB13B'; g.shadowBlur = 8 * d;
        g.fillRect(x - 1.5, y - 1.5, 3, 3); g.shadowBlur = 0;
      });
    }
    function tick(t) {
      raf = requestAnimationFrame(tick);
      if (!drag) {
        if (Math.abs(vx) + Math.abs(vy) > .0002) { rotate(vx, vy); vx *= .94; vy *= .94; }
        else if (!reduce && performance.now() - idleAt > 1500) rotate(0.0006, .0028);   // slow idle spin
      }
      if (!reduce) pulses.forEach(pu => { pu.t += pu.v; if (pu.t > 1) { pu.t = 0; pu.e = Math.random() * edges.length | 0; } });
      draw();
    }
    const start = () => { if (!raf && !wrap.hidden && !document.hidden) { size(); raf = requestAnimationFrame(tick); } };
    const stop = () => { if (raf) cancelAnimationFrame(raf); raf = null; };

    // Drag spins (with inertia); a tap opens a tile; holding a tile edits it
    let drag = null;
    wrap.addEventListener('pointerdown', e => {
      const el = e.target.closest('.orb');
      drag = { x: e.clientX, y: e.clientY, lx: e.clientX, ly: e.clientY, t: performance.now(), el, moved: false, held: false,
        hold: el && el.dataset.tile ? setTimeout(() => { if (drag && !drag.moved) { drag.held = true; el.classList.add('is-held'); setTimeout(() => el.classList.remove('is-held'), 200); editTile(el.dataset.tile); } }, 550) : null };
      vx = vy = 0; wrap.setPointerCapture(e.pointerId);
    });
    wrap.addEventListener('pointermove', e => {
      if (!drag) return;
      const [dx, dy] = toScreen(e.clientX - drag.lx, e.clientY - drag.ly);
      if (!drag.moved && Math.hypot(e.clientX - drag.x, e.clientY - drag.y) > 7) { drag.moved = true; clearTimeout(drag.hold); }
      if (drag.moved) { const k = 1.6 / Math.max(160, R * 2); rotate(dy * k * Math.PI, dx * k * Math.PI); vx = dy * k * Math.PI; vy = dx * k * Math.PI; if (!raf) draw(); }
      drag.lx = e.clientX; drag.ly = e.clientY;
    });
    const end = e => {
      if (!drag) return; clearTimeout(drag.hold);
      const d = drag; drag = null; idleAt = performance.now();
      if (e.type === 'pointerup' && !d.moved && !d.held && d.el) {
        if (d.el.dataset.act === 'add-tile') editTile(null);
        else { const t = tiles.find(x => x.id === d.el.dataset.tile); if (t) launch(t); }
      }
    };
    wrap.addEventListener('pointerup', end); wrap.addEventListener('pointercancel', end);
    wrap.addEventListener('contextmenu', e => e.preventDefault());
    wrap.addEventListener('click', e => { if (e.target.closest('.orb')) { e.preventDefault(); e.stopPropagation(); } }, true);   // taps are handled on pointerup
    addEventListener('resize', () => { size(); draw(); });
    document.addEventListener('visibilitychange', () => document.hidden ? stop() : start());
    return { build, start, stop };
  })();

  // Sphere or grid, per Settings › Home screen
  function applyHome() {
    const sph = PH.store.get('home') !== 'grid', onHome = (location.hash.replace(/^#/, '') || '/') === '/';
    $('.view[data-route="/"]').classList.toggle('fill', sph);
    $('#sphere').hidden = !sph; $('#grid').hidden = sph;
    $('#home-hint').textContent = sph ? 'Drag to spin · tap to open · hold to edit' : 'Hold a tile to edit it.';
    if (sph && onHome) requestAnimationFrame(() => sphere.start()); else sphere.stop();
  }

  /* ---------- Tile list + editor ---------- */
  function renderTileList() {
    const list = $('#tile-list'); list.textContent = '';
    tiles.forEach((t, i) => {
      const row = document.createElement('div'); row.className = 'tile-row';
      row.innerHTML = `<button class="menu-item" data-edit="${t.id}"><span class="mi-glyph">${svg(t.glyph, 'gl')}</span><span class="mi-label"></span><span class="mi-value"></span></button>
        <button class="btn btn-icon btn-ghost" data-move="${i}" data-dir="-1" aria-label="Move up" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="btn btn-icon btn-ghost" data-move="${i}" data-dir="1" aria-label="Move down" ${i === tiles.length - 1 ? 'disabled' : ''}>↓</button>`;
      row.querySelector('.mi-label').textContent = t.label;
      row.querySelector('.mi-value').textContent = t.kind === 'app' ? 'Built-in' : t.kind === 'shortcut' ? 'Shortcut' : 'Link';
      list.appendChild(row);
    });
  }
  $('#tile-list').addEventListener('click', e => {
    const ed = e.target.closest('[data-edit]'); if (ed) { editTile(ed.dataset.edit); return; }
    const mv = e.target.closest('[data-move]'); if (!mv) return;
    const i = +mv.dataset.move, j = i + +mv.dataset.dir;
    if (j < 0 || j >= tiles.length) return;
    [tiles[i], tiles[j]] = [tiles[j], tiles[i]]; saveTiles();
  });

  // glyph picker + built-in app list
  $('#tf-glyphs').innerHTML = Object.keys(G).map(k => `<button type="button" role="radio" data-glyph="${k}" aria-label="${k}" aria-checked="false">${svg(k)}</button>`).join('');
  $('#tf-app').innerHTML = Object.entries(APPS).map(([k, [n]]) => `<option value="${k}">${n}</option>`).join('');
  let editing = null;   // tile object being edited (a copy)
  const pickGlyph = g => $$('#tf-glyphs [data-glyph]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.glyph === g)));
  $('#tf-glyphs').addEventListener('click', e => { const b = e.target.closest('[data-glyph]'); if (b && editing) { editing.glyph = b.dataset.glyph; pickGlyph(editing.glyph); } });

  function syncKind() {
    const k = $('#tf-kind').value;
    $('#tf-target-field').hidden = k === 'app';
    $('#tf-app-field').hidden = k !== 'app';
    $('#tf-target-label').textContent = k === 'shortcut' ? 'Shortcut name' : 'Link';
    $('#tf-target').placeholder = k === 'shortcut' ? 'Open Camera' : 'googlegmail://  comgooglemaps://  https://…';
    $('#tf-target').setAttribute('inputmode', k === 'shortcut' ? 'text' : 'url');
    $('#tf-hint').textContent = k === 'shortcut'
      ? 'Must match the shortcut\'s name in the Shortcuts app exactly.'
      : 'An app link like tel:5551234 or spotify:, or any web address.';
  }
  $('#tf-kind').addEventListener('change', syncKind);

  function editTile(id) {
    const t = id ? tiles.find(x => x.id === id) : null;
    editing = t ? { ...t } : { id: null, label: '', glyph: 'star', kind: 'link', target: '' };
    $('#tf-label').value = editing.label;
    $('#tf-kind').value = editing.kind;
    $('#tf-target').value = editing.kind === 'app' ? '' : editing.target;
    $('#tf-app').value = editing.kind === 'app' ? editing.target : 'notes';
    $('#tf-delete').hidden = !t;
    $('.view[data-route="/tiles/edit"]').dataset.title = t ? 'Edit tile' : 'New tile';
    pickGlyph(editing.glyph); syncKind();
    $('#tf-label').removeAttribute('aria-invalid'); $('#tf-target').removeAttribute('aria-invalid');
    PH.go('/tiles/edit');
  }

  function readForm() {
    const kind = $('#tf-kind').value;
    return { ...editing, label: $('#tf-label').value.trim(), kind,
      target: kind === 'app' ? $('#tf-app').value : $('#tf-target').value.trim() };
  }
  function validate(t) {
    let ok = true;
    $('#tf-label').setAttribute('aria-invalid', String(!t.label)); if (!t.label) ok = false;
    const badTarget = t.kind !== 'app' && (!t.target || (t.kind === 'link' && !/^[a-z][\w+.-]*:/i.test(t.target)));
    $('#tf-target').setAttribute('aria-invalid', String(badTarget)); if (badTarget) ok = false;
    if (!ok) PH.toast(badTarget && t.label ? 'Links start with a scheme, like maps:// or https://' : 'Fill in the highlighted fields.', 'warn');
    return ok;
  }
  function saveTile() {
    if (!editing) return;
    const t = readForm(); if (!validate(t)) return;
    if (t.id) tiles = tiles.map(x => x.id === t.id ? t : x);
    else { t.id = uid(); tiles.push(t); }
    saveTiles(); editing = null; PH.toast('Tile saved.'); PH.back();
  }
  $('#tile-form').addEventListener('submit', e => { e.preventDefault(); saveTile(); });

  /* ---------- Phone ---------- */
  let dial = '';
  let favs = load('favs', []);
  const fmt = n => { const d = n.replace(/\D/g, ''); return /^\d{10}$/.test(n) ? `(${d.slice(0, 3)}) ${d.slice(3, 6)}-${d.slice(6)}` : /^1\d{10}$/.test(n) ? `1 (${d.slice(1, 4)}) ${d.slice(4, 7)}-${d.slice(7)}` : n; };
  function renderDial() {
    const r = $('#dial-num'); r.textContent = fmt(dial) || '';
    r.classList.toggle('long', dial.length > 11);
    const f = favs.find(x => x.num === dial);
    $('#dial-name').textContent = f ? f.name : dial ? '' : 'Enter a number';
    renderFavs();
  }
  function renderFavs() {
    const list = $('#fav-list'); list.textContent = '';
    const chip = (cls, initial, label) => { const b = document.createElement('button'); b.className = 'fav' + (cls ? ' ' + cls : '');
      const i = document.createElement('b'); i.textContent = initial; const t = document.createElement('span'); t.textContent = label; b.append(i, t); return b; };
    favs.forEach((f, i) => { const b = chip(f.num === dial ? 'on' : '', (f.name[0] || '#').toUpperCase(), f.name); b.dataset.fav = i; b.setAttribute('aria-label', f.name + ', ' + fmt(f.num)); list.appendChild(b); });
    if (dial && !favs.some(f => f.num === dial)) { const b = chip('add', '+', 'Save'); b.dataset.act = 'fav-add'; list.appendChild(b); }
    else if (!favs.length) { const b = chip('hint', '★', 'Favorites show here'); b.disabled = true; list.appendChild(b); }
  }
  // hold a favorite to remove it
  let favHold = null;
  $('#fav-list').addEventListener('pointerdown', e => { const f = e.target.closest('[data-fav]'); if (!f) return;
    favHold = setTimeout(async () => { favHold = 'done'; const i = +f.dataset.fav, x = favs[i];
      if (await PH.confirm(`Remove ${x.name} from favorites?`, { title: 'Remove favorite', ok: 'Remove', danger: true })) { favs.splice(i, 1); save('favs', favs); renderDial(); } }, 600); });
  ['pointerup', 'pointerleave', 'pointercancel'].forEach(n => $('#fav-list').addEventListener(n, () => { if (favHold !== 'done') clearTimeout(favHold); }));
  $('#fav-list').addEventListener('click', async e => {
    const f = e.target.closest('[data-fav]'); if (f) { if (favHold === 'done') { favHold = null; return; } dial = favs[+f.dataset.fav].num; renderDial(); return; }
    const u = e.target.closest('[data-unfav]');
    if (u) { const x = favs[+u.dataset.unfav];
      if (await PH.confirm(`Remove ${x.name} from favorites?`, { title: 'Remove favorite', ok: 'Remove', danger: true })) { favs.splice(+u.dataset.unfav, 1); save('favs', favs); renderDial(); } }
  });
  $('#dial-pad').addEventListener('click', e => {
    const k = e.target.closest('[data-dial]'); if (!k) return;
    if (dial.length < 20) dial += k.dataset.dial; renderDial();
  });

  /* ---------- Calculator (own parser, no eval) ---------- */
  let expr = '', calcDone = false, lastExpr = '';
  const OPS = '+−×÷';
  const trailing = () => expr.match(/(-?)([\d.]*)$/);
  function evaluate(s) {
    const toks = s.match(/-?[\d.]+|[+−×÷]/g);   // '-' is only ever a sign; the minus operator is '−'
    if (!toks || OPS.includes(toks[toks.length - 1])) return null;
    const nums = [], ops = [];
    for (const t of toks) if (OPS.includes(t) && t.length === 1) ops.push(t); else nums.push(parseFloat(t));
    if (nums.some(isNaN) || nums.length !== ops.length + 1) return null;
    // × ÷ first, then + −
    const n2 = [nums[0]], o2 = [];
    ops.forEach((o, i) => {
      const b = nums[i + 1];
      if (o === '×') n2.push(n2.pop() * b);
      else if (o === '÷') { const a = n2.pop(); n2.push(b === 0 ? NaN : a / b); }
      else { o2.push(o); n2.push(b); }
    });
    let r = n2[0]; o2.forEach((o, i) => { r = o === '+' ? r + n2[i + 1] : r - n2[i + 1]; });
    return isFinite(r) ? parseFloat(r.toPrecision(12)) : NaN;
  }
  const show = n => { if (Number.isNaN(n)) return 'Error'; const s = String(n); return s.replace('-', '−').length > 14 ? n.toExponential(6) : s; };
  const pretty = s => s.replace(/(^|[+−×÷])-/g, '$1−');   // show negative signs as a proper minus
  function renderCalc() {
    const out = $('#calc-out');
    if (calcDone) { $('#calc-expr').textContent = pretty(lastExpr) + ' ='; out.textContent = pretty(expr) || '0'; }
    else { const v = evaluate(expr); $('#calc-expr').textContent = v !== null && /[+−×÷]/.test(expr.replace(/^-/, '')) ? '= ' + show(v) : ''; out.textContent = pretty(expr) || '0'; }
    out.classList.toggle('long', out.textContent.length > 9);
  }
  $('#calc-pad').addEventListener('click', e => {
    const k = e.target.closest('[data-calc]'); if (!k) return;
    const c = k.dataset.calc;
    if (/\d/.test(c)) { if (calcDone) expr = ''; calcDone = false; const m = trailing(); if (m[2] === '0') expr = expr.slice(0, -1); expr += c; }
    else if (c === '.') { if (calcDone) expr = ''; calcDone = false; const m = trailing(); if (!m[2].includes('.')) expr += m[2] ? '.' : '0.'; }
    else if (OPS.includes(c)) { calcDone = false; if (expr === 'Error') expr = ''; if (!expr || expr === '-') expr = '0'; if (OPS.includes(expr.slice(-1))) expr = expr.slice(0, -1); expr += c; }
    else if (c === 'C') { expr = ''; calcDone = false; }
    else if (c === '⌫') { if (calcDone) { calcDone = false; } else expr = expr.slice(0, -1); }
    else if (c === '±') { calcDone = false; const m = trailing(); if (m[2]) expr = expr.slice(0, expr.length - m[0].length) + (m[1] ? '' : '-') + m[2]; }
    else if (c === '%') { calcDone = false; const m = trailing(); if (m[2]) expr = expr.slice(0, expr.length - m[0].length) + String(parseFloat((parseFloat(m[1] + m[2]) / 100).toPrecision(12))); }
    else if (c === '=') { const v = evaluate(expr); if (v === null) return; lastExpr = expr; expr = Number.isNaN(v) ? 'Error' : String(v); calcDone = true; }
    if (expr === 'Error' && c !== '=') expr = '';
    renderCalc();
  });

  /* ---------- Notes ---------- */
  let notes = load('notes', []);
  let openNote = load('open', null);
  const saveNotes = () => { save('notes', notes); renderNotes(); };
  const titleOf = n => (n.text.split('\n').find(l => l.trim()) || 'Untitled').trim().slice(0, 60);
  const when = t => { const d = new Date(t), now = new Date();
    return d.toDateString() === now.toDateString() ? d.toLocaleTimeString([], { hour: 'numeric', minute: '2-digit', hour12: PH.store.get('clock') === '12' }) : d.toLocaleDateString([], { month: 'short', day: 'numeric' }); };
  function renderNotes() {
    const q = $('#note-search').value.trim().toLowerCase();
    const list = $('#note-list'); list.textContent = '';
    [...notes].sort((a, b) => b.t - a.t).filter(n => !q || n.text.toLowerCase().includes(q)).forEach(n => {
      const li = document.createElement('li'); li.dataset.note = n.id; li.style.cursor = 'pointer';
      li.innerHTML = '<span></span><span class="meta"></span>';
      li.firstChild.textContent = titleOf(n); li.lastChild.textContent = when(n.t);
      list.appendChild(li);
    });
    $('#note-empty').hidden = notes.length > 0;
    $('#note-search-wrap').hidden = notes.length < 8;
    $('#note-new').hidden = !notes.length;   // the empty screen has its own button
    const c = $('#note-count'); c.hidden = !notes.length; c.textContent = notes.length;
  }
  $('#note-search').addEventListener('input', renderNotes);
  $('#note-list').addEventListener('click', e => { const li = e.target.closest('[data-note]'); if (li) openNoteById(li.dataset.note); });
  function openNoteById(id) { openNote = id; save('open', id); loadNoteEditor(); PH.go('/notes/edit'); }
  function newNote() { const n = { id: uid(), text: '', t: Date.now() }; notes.push(n); save('notes', notes); openNoteById(n.id); setTimeout(() => $('#note-text').focus(), 200); }
  function loadNoteEditor() {
    const n = notes.find(x => x.id === openNote);
    $('#note-text').value = n ? n.text : '';
    $('#note-stamp').textContent = n && n.text ? 'Edited ' + when(n.t) : '';
  }
  $('#note-text').addEventListener('input', () => {
    const n = notes.find(x => x.id === openNote); if (!n) return;
    n.text = $('#note-text').value; n.t = Date.now(); save('notes', notes);
    $('#note-stamp').textContent = 'Saved';
  });
  function closeEmptyNote() {   // leaving a blank note throws it away
    const n = notes.find(x => x.id === openNote);
    if (n && !n.text.trim()) { notes = notes.filter(x => x !== n); save('notes', notes); }
    renderNotes();
  }

  /* ---------- Clock ---------- */
  function tick() {
    const d = new Date(), h12 = PH.store.get('clock') === '12';
    const hh = h12 ? (d.getHours() % 12 || 12) : String(d.getHours()).padStart(2, '0');
    const mm = String(d.getMinutes()).padStart(2, '0');
    $('#clock').innerHTML = `${hh}:${mm}${h12 ? `<small>${d.getHours() < 12 ? 'AM' : 'PM'}</small>` : ''}`;
    $('#date').textContent = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' }).replace(/,/g, '');
  }
  const startClock = () => { tick(); setTimeout(() => { tick(); setInterval(tick, 60000); }, 60000 - Date.now() % 60000 + 50); };

  /* ---------- Boot screen ---------- */
  function boot() {
    const last = load('seen', 0);
    const el = $('#boot');
    if (!PH.store.get('boot') || Date.now() - last < 10 * 60000 || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const lines = [`<b>PHOSPHOR.OS ${VERSION}</b>`, 'MEMORY TEST ...... 640K OK', `TILES ............ ${tiles.length} LOADED`, `NOTES ............ ${notes.length}`, 'CLOCK ............ SYNCED', '', '<b>READY</b><span class="cursor"></span>'];
    el.hidden = false; el.innerHTML = '';
    let i = 0, done = false;
    const finish = () => { if (done) return; done = true; el.hidden = true; };
    el.addEventListener('click', finish, { once: true });
    const step = () => { if (done) return; if (i < lines.length) { el.innerHTML += lines[i++] + '\n'; setTimeout(step, 110); } else setTimeout(finish, 350); };
    step();
  }
  const markSeen = () => save('seen', Date.now());
  document.addEventListener('visibilitychange', () => { if (document.hidden) markSeen(); });
  addEventListener('pagehide', markSeen);

  /* ---------- Actions ---------- */
  const actions = {
    'add-tile': () => editTile(null),
    'edit-tiles': () => PH.go('/tiles'),
    'save-tile': saveTile,
    'test-tile': () => { const t = readForm(); if (validate({ ...t, label: t.label || 'x' })) launch(t); },
    'delete-tile': async () => {
      if (!editing?.id) return;
      if (await PH.confirm(`Delete the ${editing.label} tile?`, { title: 'Delete tile', ok: 'Delete', danger: true })) {
        tiles = tiles.filter(x => x.id !== editing.id); editing = null; saveTiles(); PH.back();
      }
    },
    'reset-tiles': async () => {
      if (await PH.confirm('Put back the default tiles? Your own tiles will be removed.', { title: 'Reset tiles', ok: 'Reset', danger: true })) {
        tiles = DEFAULT_TILES(); saveTiles(); PH.toast('Tiles reset.', 'warn');
      }
    },
    'dial-call': () => { if (dial) location.href = 'tel:' + dial; else PH.toast('Enter a number first.'); },
    'dial-sms': () => { location.href = dial ? 'sms:' + dial : 'shortcuts://run-shortcut?name=' + encodeURIComponent('Open Messages'); },
    'dial-del': () => { dial = dial.slice(0, -1); renderDial(); },
    'fav-add': () => {
      const name = (prompt('Name for ' + fmt(dial)) || '').trim(); if (!name) return;
      favs.push({ name: name.slice(0, 24), num: dial }); save('favs', favs); renderDial(); PH.toast('Saved to favorites.');
    },
    'new-note': newNote,
    'delete-note': async () => {
      const n = notes.find(x => x.id === openNote); if (!n) return PH.back();
      if (!n.text.trim() || await PH.confirm('Delete this note?', { title: 'Delete note', ok: 'Delete', danger: true })) {
        notes = notes.filter(x => x !== n); saveNotes(); PH.back();
      }
    },
    'reset-display': () => { PH.store.set('crt', true); PH.store.set('rain', false); PH.toast('Display reset.'); },
  };
  document.addEventListener('click', e => {
    const a = e.target.closest('[data-act], [data-action]'); if (!a) return;
    const fn = actions[a.dataset.act || a.dataset.action]; if (fn) { e.preventDefault(); fn(); }
  });
  // hold-to-delete on the dial pad clears the whole number
  let delTimer;
  $('[data-act="dial-del"]').addEventListener('pointerdown', () => { delTimer = setTimeout(() => { dial = ''; renderDial(); }, 600); });
  ['pointerup', 'pointerleave', 'pointercancel'].forEach(n => $('[data-act="dial-del"]').addEventListener(n, () => clearTimeout(delTimer)));

  /* ---------- Data ---------- */
  document.getElementById('export').onclick = () =>
    PH.exportFile('phosphor-os-backup.json', JSON.stringify({ app: 'phosphor-os', version: VERSION, tiles, notes, favs,
      settings: { clock: PH.store.get('clock'), cols: PH.store.get('cols'), boot: PH.store.get('boot') } }, null, 2));
  document.getElementById('import').onclick = () => $('#import-file').click();
  $('#import-file').addEventListener('change', async e => {
    const f = e.target.files[0]; e.target.value = ''; if (!f) return;
    try {
      const d = JSON.parse(await f.text());
      if (d.app !== 'phosphor-os' || !Array.isArray(d.tiles)) throw new Error('not a backup');
      if (!await PH.confirm('Replace your tiles, notes and favorites with this backup?', { title: 'Import data', ok: 'Import', danger: true })) return;
      tiles = d.tiles.filter(t => t && t.label && t.kind).map(t => ({ id: t.id || uid(), label: String(t.label).slice(0, 16), glyph: G[t.glyph] ? t.glyph : 'star', kind: ['link', 'shortcut', 'app'].includes(t.kind) ? t.kind : 'link', target: String(t.target || '') }));
      notes = Array.isArray(d.notes) ? d.notes.filter(n => n && typeof n.text === 'string').map(n => ({ id: n.id || uid(), text: n.text, t: +n.t || Date.now() })) : [];
      favs = Array.isArray(d.favs) ? d.favs.filter(x => x && x.num).map(x => ({ name: String(x.name || x.num).slice(0, 24), num: String(x.num).replace(/[^\d*#+]/g, '') })) : [];
      save('notes', notes); save('favs', favs); saveTiles(); renderNotes(); renderDial();
      if (d.settings) ['clock', 'cols', 'boot'].forEach(k => { if (k in d.settings) PH.store.set(k, d.settings[k]); });
      PH.toast('Backup imported.');
    } catch { PH.toast('That file isn\'t a phosphor.os backup.', 'error'); }
  });
  document.getElementById('erase').onclick = async () => {
    if (await PH.confirm('Erase all tiles, notes and favorites? This can\'t be undone.', { title: 'Erase data', ok: 'Erase', danger: true })) {
      Object.keys(localStorage).filter(k => k.startsWith(KEY)).forEach(k => localStorage.removeItem(k));
      PH.store.reset();
      tiles = DEFAULT_TILES(); notes = []; favs = []; dial = ''; expr = '';
      save('tiles', tiles); renderGrid(); renderTileList(); renderNotes(); renderDial(); renderCalc();
      PH.toast('All data erased.', 'warn');
    }
  };

  /* ---------- Bundled apps: run inside the screen (#/app/<id>); the case and keys never move ---------- */
  let frame = null, frameId = null;
  const routeKey = id => 'route:' + id;
  const home = () => location.href.split('#')[0] + '#/';   // absolute: a relative URL would resolve against a calling app's page
  function openApp(id) { location.replace('#/app/' + id); }   // replace: the frame's own pages fill the history
  function showFrame(id) {
    if (frameId === id) return;
    hideFrame();
    const a = APPS[id]; if (!a || !a[2]) { location.replace('#/'); return; }
    frame = document.createElement('iframe');
    frame.className = 'app-frame'; frame.title = a[0]; frame.allow = 'web-share; clipboard-write';
    const last = a[3] === false ? '/' : load(routeKey(id), '/');   // some apps always open on their first page
    frame.src = a[2] + '#' + (typeof last === 'string' && last.startsWith('/') ? last : '/');
    $('.screen').appendChild(frame); $('.screen').classList.add('has-frame'); frameId = id;
    frame.addEventListener('load', () => { frame.classList.add('is-ready'); try { frame.contentWindow.focus(); } catch {} renderKeys(); });
    renderKeys();
  }
  // The frame disappears at once but stays loaded until the app has tidied up
  // (Kasa Lights puts the bulbs back after a party), then it is removed
  function hideFrame() {
    if (!frame) return;
    const f = frame, c = child();
    frame = null; frameId = null; f.style.display = 'none';
    $('.screen').classList.remove('has-frame'); renderKeys();
    let done = null;
    try { done = c && c.prepareClose && c.prepareClose(); } catch {}
    Promise.race([Promise.resolve(done), new Promise(r => setTimeout(r, 5000))]).catch(() => {}).finally(() => f.remove());
  }
  const child = () => { try { return frame && frame.contentWindow.OSChild; } catch { return null; } };
  // Read by the bundled apps (src/embed.js)
  window.PhosphorOS = {
    closeApp: () => { hideFrame(); location.replace(home()); },
    saveRoute: r => { if (frameId) save(routeKey(frameId), r); },
    keysChanged: () => renderKeys(),
    openTerm: () => openTerm(),
  };
  // v1.8: Lights is the bundled Kasa Lights app now; old #/lights links open it
  if (/^#\/lights(\/|$)/.test(location.hash)) location.replace('#/app/lights');
  const syncFrame = () => { const m = /^#\/app\/([\w-]+)/.exec(location.hash); if (m) showFrame(m[1]); else hideFrame(); };
  addEventListener('hashchange', syncFrame);

  /* ---------- Keys under the screen: Back, Home, and the turn key ---------- */
  const KI = {
    back: '<path d="M15 4 7 12l8 8"/>',
    home: '<path d="M3 11 12 3l9 8M6 9v11h12V9"/>',
    menu: '<path d="M4 6h16M4 12h16M4 18h16"/>',
    plus: '<path d="M12 4v16M4 12h16"/>',
    retry: '<path d="M20 12a8 8 0 1 1-2.3-5.7M20 4v5h-5"/>',
    flag: '<path d="M6 21V4M6 4h12l-3 4 3 4H6"/>',
    music: G.music, lights: G.bulb, notes: G.note, phone: G.phone, calc: G.calc, settings: G.gear,
  };
  const keySvg = n => `<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="square" stroke-linejoin="miter" aria-hidden="true">${KI[n]}</svg>`;
  KI.turn = '<rect x="3" y="4" width="9" height="16" rx="1"/><path d="M6 17h3M15 5a8 8 0 0 1 6 8M18 11l3 2 2-3"/>';
  const curRoute = () => (location.hash.replace(/^#/, '') || '/').replace(/\/+$/, '') || '/';
  // The app's own button (Keyboard, Pause, Party…) past its first page; reached by holding the right key
  const appKey = () => { const c = child(); return frameId && c && !c.atHome() ? c.pageKey() : null; };
  function renderKeys() {
    const back = $('#k-back'), hm = $('#k-home'), right = $('#k-right');
    if (!back.dataset.icon) { back.innerHTML = keySvg('back'); hm.innerHTML = keySvg('home'); right.innerHTML = keySvg('turn'); back.dataset.icon = hm.dataset.icon = '1'; }
    back.disabled = !frameId && curRoute() === '/';
    const land = PH.store.get('orient') === 'landscape', k = appKey();
    right.setAttribute('aria-label', (land ? 'Portrait' : 'Landscape') + (k ? '. Hold for ' + (k.label || k.text) : '. Hold for the menu'));
    right.setAttribute('aria-pressed', String(land));
  }

  /* ---------- Orientation: the screen turns, the case doesn't ---------- */
  // Landscape means the phone is held a quarter turn to the left (top on the left) with rotation lock
  // on, so the screen's contents turn +90deg at its swapped size. If iOS really turns the page (lock off)
  // it's already wide, and nothing is turned.
  const slot = $('.screen-slot'), rootSt = document.documentElement.style;
  let turned = false;
  function applyOrient() {
    const r = slot.getBoundingClientRect();
    rootSt.setProperty('--slot-w', r.width + 'px'); rootSt.setProperty('--slot-h', r.height + 'px');
    rootSt.setProperty('--slot-cx', (r.left + r.width / 2) + 'px'); rootSt.setProperty('--slot-cy', (r.top + r.height / 2) + 'px');
    const was = turned;
    turned = PH.store.get('orient') === 'landscape' && innerHeight >= innerWidth;
    document.documentElement.classList.toggle('os-land', turned);
    document.documentElement.classList.toggle('os-wide', turned || r.width > r.height);
    if (was !== turned) dispatchEvent(new Event('resize'));   // canvases (sphere, rain) re-measure
    renderKeys();
  }
  new ResizeObserver(() => applyOrient()).observe(slot);
  PH.on('orient', applyOrient);
  // A finger's movement on the glass, turned back into the screen's own directions (for drags in the OS itself;
  // bundled apps get proper coordinates from the browser because their whole frame is turned)
  const toScreen = (dx, dy) => turned ? [dy, -dx] : [dx, dy];
  // Edge-swipe back from the screen's left edge, which is the top of the glass when turned
  let es = null;
  document.addEventListener('touchstart', e => {
    if (!turned) return; e.stopPropagation();   // the brand's own edge swipe watches the glass's left edge, which is wrong here
    const t = e.touches[0], top = $('.screen').getBoundingClientRect().top;
    es = t.clientY - top < 24 ? { x: t.clientX, y: t.clientY } : null;
  }, { passive: true });
  document.addEventListener('touchend', e => {
    if (!turned) return; e.stopPropagation();
    const t = e.changedTouches[0];
    if (es && !frameId && curRoute() !== '/' && t.clientY - es.y > 70 && Math.abs(t.clientX - es.x) < 60 && !$('dialog[open]')) PH.back();
    es = null;
  }, { passive: true });

  function closeDialogs() { $$('dialog[open]').forEach(d => d.close()); }
  $('#k-back').addEventListener('click', () => {
    if (!term.hidden) { closeTerm(); return; }
    if (frameId) {
      const c = child(); if (!c || c.atHome()) { PhosphorOS.closeApp(); return; }
      // if the app's own Back doesn't move it (its history can get out of step inside the frame), go to its first page
      const was = c.route(), dialog = !!(frame && frame.contentDocument && frame.contentDocument.querySelector('dialog[open]'));
      c.back();
      if (!dialog) setTimeout(() => { const k = child(); if (k === c && k.route() === was) k.home(); }, 400);
      return;
    }
    closeDialogs(); PH.back();
  });
  $('#k-home').addEventListener('click', () => {
    closeDialogs(); closeTerm();
    if (frameId) { PhosphorOS.closeApp(); return; }
    if (curRoute() !== '/') location.replace(home());
  });
  function openMenu() { if (term.hidden) openTerm(); else closeTerm(); }

  /* ---------- Menu: tap the ~/path line (or hold the right key) ---------- */
  // Kept short on purpose (Floyd, v1.24: the first version was far too busy): the open app's own
  // menu and Close, the apps that run inside the OS, and play/pause.
  const term = $('#term'), termBody = $('#term-body');
  const goTo = to => () => {
    if (frameId) { hideFrame(); location.replace(location.href.split('#')[0] + '#' + to); }
    else if (to === '/') location.replace(home()); else PH.go(to);
  };
  function termGroups() {
    const r = curRoute(), c = child(), groups = [];
    if (frameId && c) {
      const name = APPS[frameId][0], here = [];
      let hasMenu = false; try { hasMenu = !!frame.contentDocument.getElementById('menu'); } catch {}
      if (hasMenu) here.push([name + ' options', () => c.openMenu()]);
      here.push(['Close ' + name, () => PhosphorOS.closeApp()]);
      groups.push(['', here]);
    }
    // only apps that stay inside the OS; links and Shortcuts that leave it stay on the home screen
    const apps = [['Home', goTo('/'), !frameId && r === '/', keySvg('home')]];
    tiles.filter(t => t.kind === 'app' && APPS[t.target]).forEach(t => {
      const a = APPS[t.target], cur = a[2] ? frameId === t.target : !frameId && (r === a[1] || r.startsWith(a[1] + '/'));
      apps.push([t.label, a[2] ? () => launch(t) : goTo(a[1]), cur, svg(t.glyph)]);
    });
    if (!tiles.some(t => t.kind === 'app' && t.target === 'settings')) apps.push(['Settings', goTo('/settings'), !frameId && r.startsWith('/settings'), svg('gear')]);
    groups.push(['apps', apps]);
    if (window.PhosphorMusic) { const on = PhosphorMusic.playing(); groups.push(['', [[on ? 'Pause music' : 'Play music', () => PhosphorMusic.toggle()]]]); }
    return groups;
  }
  function renderTerm() {
    termBody.textContent = '';
    termGroups().forEach(([kind, items]) => {
      const chips = kind === 'apps';
      const g = document.createElement('div'); g.className = 'term-group' + (chips ? ' term-chips' : '');
      g.innerHTML = '<div class="tg-items"></div>';
      items.forEach(([label, fn, cur, ico]) => {
        const b = document.createElement('button'); b.className = chips ? 'term-chip' : 'term-line';
        b.innerHTML = chips ? `${ico || ''}<span class="tl-l"></span>` : '<span class="tl-l"></span>';
        b.querySelector('.tl-l').textContent = label;
        if (cur) b.setAttribute('aria-current', 'page');
        b.addEventListener('click', () => { closeTerm(); fn(); });
        g.lastChild.appendChild(b);
      });
      termBody.appendChild(g);
    });
  }
  function openTerm() { closeDialogs(); renderTerm(); term.hidden = false; termBody.scrollTop = 0; $('#path-cmd').setAttribute('aria-expanded', 'true'); }
  // A bundled app keeps keyboard focus, so a paired Bluetooth keyboard keeps typing into it after the case keys are used
  const focusFrame = () => setTimeout(() => { if (frame && term.hidden) try { frame.contentWindow.focus(); } catch {} }, 0);
  $('.app-controls').addEventListener('click', focusFrame);
  function closeTerm() { if (term.hidden) return; term.hidden = true; $('#path-cmd').setAttribute('aria-expanded', 'false'); focusFrame(); }
  $('#term-x').addEventListener('click', closeTerm);
  $('#path-cmd').addEventListener('click', openMenu);
  $('.path').addEventListener('click', e => { if (!e.target.closest('a')) openTerm(); });
  addEventListener('hashchange', closeTerm);
  addEventListener('keydown', e => { if (e.key === 'Escape') closeTerm(); });
  // Right key: tap turns the screen; hold opens the menu, or presses the app's own button when it shows one
  let rkHold = null, rkHeld = false;
  $('#k-right').addEventListener('pointerdown', () => { rkHeld = false; clearTimeout(rkHold); rkHold = setTimeout(() => {
    rkHeld = true;
    if (appKey()) child().pressKey(); else openMenu();
  }, 550); });
  ['pointerup', 'pointerleave', 'pointercancel'].forEach(n => $('#k-right').addEventListener(n, () => clearTimeout(rkHold)));
  $('#k-right').addEventListener('contextmenu', e => e.preventDefault());
  $('#k-right').addEventListener('click', () => {
    if (rkHeld) { rkHeld = false; return; }
    PH.store.set('orient', PH.store.get('orient') === 'landscape' ? 'portrait' : 'landscape');
  });
  addEventListener('hashchange', renderKeys);

  /* ---------- Routes ---------- */
  let prevRoute = null;
  PH.on('route', r => {
    if (prevRoute === '/notes/edit' && r !== '/notes/edit') closeEmptyNote();
    if (r === '/tiles/edit' && !editing) { location.replace('#/tiles'); return; }
    if (r === '/notes/edit') { if (!notes.some(n => n.id === openNote)) { location.replace('#/notes'); return; } loadNoteEditor(); }
    if (r === '/phone') renderDial();
    if (r === '/calc') renderCalc();
    // the title in the header follows the edit page's current name
    if (r === '/tiles/edit') $$('[data-view-title]').forEach(el => { el.textContent = $('.view[data-route="/tiles/edit"]').dataset.title; });
    prevRoute = r;
  });
  PH.on('cols', renderGrid);
  PH.on('home', applyHome);
  PH.on('route', applyHome);
  PH.on('clock', () => { tick(); renderNotes(); });

  // keyboard input for the calculator and dialer on desktop
  addEventListener('keydown', e => {
    if (e.target.matches('input, textarea, select')) return;
    const r = (location.hash.slice(1) || '/');
    const map = r === '/calc' ? { '*': '×', '/': '÷', '-': '−', '+': '+', Enter: '=', '=': '=', Backspace: '⌫', Escape: 'C', '.': '.', '%': '%' } : null;
    if (r === '/calc') { const c = map[e.key] || (/^\d$/.test(e.key) ? e.key : null); if (c) { $(`[data-calc="${c}"]`).click(); e.preventDefault(); } }
    if (r === '/phone') { if (/^[\d*#]$/.test(e.key)) $(`[data-dial="${e.key}"]`).click(); else if (e.key === 'Backspace') actions['dial-del'](); }
  });

  /* ---------- Matrix rain (brand reference implementation) ---------- */
  (() => {
    const c = document.querySelector('.rain');
    if (!c || matchMedia('(prefers-reduced-motion: reduce)').matches) return;
    const x = c.getContext('2d'), glyphs = 'ｱｲｳｴｵｶｷｸｹｺｻｼｽｾｿﾀﾁﾂﾃﾄ0123456789<>/=+*';
    let cols, drops, fs = 16, last = 0, raf;
    const size = () => { c.width = c.offsetWidth; c.height = c.offsetHeight;
      cols = Math.ceil(c.width / fs); drops = Array.from({ length: cols }, () => Math.random() * -50); };
    const tick = t => { raf = requestAnimationFrame(tick); if (c.hidden || t - last < 50) return; last = t;
      if (!c.width) size();
      x.fillStyle = 'rgba(3,7,3,.12)'; x.fillRect(0, 0, c.width, c.height);
      x.fillStyle = '#39FF6A'; x.font = fs + 'px ui-monospace, monospace';
      drops.forEach((y, i) => { x.fillText(glyphs[Math.random() * glyphs.length | 0], i * fs, y * fs);
        drops[i] = y * fs > c.height && Math.random() > .975 ? 0 : y + 1; }); };
    addEventListener('resize', size); size(); raf = requestAnimationFrame(tick);
    document.addEventListener('visibilitychange', () =>
      document.hidden ? cancelAnimationFrame(raf) : (raf = requestAnimationFrame(tick)));
  })();

  /* ---------- Start ---------- */
  renderGrid(); renderTileList(); renderNotes(); renderDial(); renderCalc();
  PH.init({ clock: '12', cols: '4', boot: true, home: 'sphere', orient: 'portrait' });
  renderGrid(); applyOrient();
  startClock();
  if (/^#\/app\//.test(location.hash)) syncFrame(); else boot();
  if ('serviceWorker' in navigator) navigator.serviceWorker.register('sw.js');
})();
