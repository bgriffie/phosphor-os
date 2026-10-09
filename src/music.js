/* Phosphor OS: Spotify remote (#/music). Controls the Spotify app playing on this phone (or any
   device) through the Spotify Web API. Login is PKCE, so no server or secret is needed; Floyd's own
   Spotify developer app supplies the Client ID. Playback control needs Spotify Premium. */
(() => {
  const $ = s => document.querySelector(s);
  const KEY = 'phosphor-os:data:';
  const load = (k, d) => { try { const v = localStorage.getItem(KEY + k); return v === null ? d : JSON.parse(v); } catch { return d; } };
  const save = (k, v) => { try { localStorage.setItem(KEY + k, JSON.stringify(v)); } catch {} };
  const drop = k => { try { localStorage.removeItem(KEY + k); } catch {} };

  const REDIRECT = new URL('./', location.href.split(/[?#]/)[0]).href;   // https://bgriffie.github.io/phosphor-os/
  const SCOPES = 'user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private playlist-read-collaborative user-library-read user-read-playback-position';
  const PODCAST_SCOPE = 'user-library-read';   // added in v1.3: logins from v1.2 need a reconnect for followed shows
  const standalone = () => navigator.standalone || matchMedia('(display-mode: standalone)').matches;
  let auth = load('spotify', {});          // { clientId, access, expires, refresh, user }
  const saveAuth = () => save('spotify', auth);
  const connected = () => !!(auth.clientId && auth.refresh);

  /* ---------- PKCE login ---------- */
  const rand = n => { const a = new Uint8Array(n); crypto.getRandomValues(a); return [...a].map(b => 'ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~'[b % 66]).join(''); };
  const b64url = buf => btoa(String.fromCharCode(...new Uint8Array(buf))).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  async function login() {
    const clientId = $('#mu-client').value.trim() || auth.clientId || '';
    if (!/^[0-9a-f]{32}$/i.test(clientId)) { $('#mu-client').setAttribute('aria-invalid', 'true'); PH.toast('Paste the 32-character Client ID from your Spotify app.', 'warn'); return; }
    $('#mu-client').removeAttribute('aria-invalid');
    auth.clientId = clientId; saveAuth();
    const verifier = rand(64), state = rand(16);
    save('spotify-pkce', { verifier, state, clientId });
    const challenge = b64url(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(verifier)));
    location.href = 'https://accounts.spotify.com/authorize?' + new URLSearchParams({
      response_type: 'code', client_id: clientId, scope: SCOPES, redirect_uri: REDIRECT,
      code_challenge_method: 'S256', code_challenge: challenge, state,
    });
  }
  async function token(body) {
    const r = await fetch('https://accounts.spotify.com/api/token', { method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' }, body: new URLSearchParams(body) });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) throw Object.assign(new Error(d.error_description || d.error || 'Spotify login failed'), { code: d.error });
    auth.access = d.access_token; auth.expires = Date.now() + (d.expires_in || 3600) * 1000;
    if (d.refresh_token) auth.refresh = d.refresh_token;   // refresh tokens rotate: keep the newest
    if (d.scope != null) auth.scope = d.scope;
    saveAuth();
  }
  let refreshing = null;
  function refresh() {
    return refreshing ||= token({ grant_type: 'refresh_token', refresh_token: auth.refresh, client_id: auth.clientId })
      .catch(e => { if (e.code === 'invalid_grant' || e.code === 'invalid_client') { disconnect(true); PH.toast('Spotify signed you out. Connect again in setup.', 'error'); } throw e; })
      .finally(() => { refreshing = null; });
  }
  // Back from accounts.spotify.com with ?code=…
  async function finishLogin() {
    const q = new URLSearchParams(location.search);
    if (!q.has('code') && !q.has('error')) return;
    history.replaceState(null, '', location.pathname + '#/music/setup');
    const pkce = load('spotify-pkce', null); drop('spotify-pkce');
    if (q.has('error')) { PH.toast(q.get('error') === 'access_denied' ? 'Spotify connection cancelled.' : 'Spotify said: ' + q.get('error'), 'warn'); return; }
    if (!pkce || pkce.state !== q.get('state')) {
      // iOS opened the login in a separate browser that can't see this app's storage
      PH.toast('This window lost the login. Tap Connect again here.', 'warn'); return;
    }
    try {
      auth.clientId = pkce.clientId;
      await token({ grant_type: 'authorization_code', code: q.get('code'), redirect_uri: REDIRECT, client_id: pkce.clientId, code_verifier: pkce.verifier });
      const me = await api('/me'); auth.user = me && (me.display_name || me.id); saveAuth();
      if (standalone()) { PH.toast('Spotify connected.'); location.replace('#/music'); }
      else showPairing();   // Safari: hand the login over to the home-screen app
      renderSetup();
    } catch (e) { PH.toast('Couldn\'t connect: ' + e.message, 'error'); }
  }
  function disconnect(quiet) {
    auth = { clientId: auth.clientId }; saveAuth(); state = null; lists = null; shows = null;
    renderAll(); if (!quiet) PH.toast('Spotify disconnected.', 'warn');
  }

  /* Pairing code: moves a login made in Safari into the home-screen app (separate storage on iOS) */
  const pairingCode = () => 'PHOS-' + btoa(JSON.stringify({ c: auth.clientId, r: auth.refresh }));
  function showPairing() { $('#mu-pair-out').hidden = false; $('#mu-pair-code').value = pairingCode(); }
  async function usePairing() {
    const raw = $('#mu-pair-in').value.trim().replace(/^PHOS-/, '');
    try {
      const d = JSON.parse(atob(raw)); if (!d.c || !d.r) throw 0;
      auth = { clientId: d.c, refresh: d.r }; saveAuth();
      await refresh();
      const me = await api('/me'); auth.user = me && (me.display_name || me.id); saveAuth();
      $('#mu-pair-in').value = ''; PH.toast('Spotify connected.'); renderAll(); PH.go('/music');
    } catch { PH.toast('That pairing code didn\'t work. Make a new one in Safari.', 'error'); }
  }

  /* ---------- Web API ---------- */
  async function api(path, { method = 'GET', body, query } = {}, retried) {
    if (!connected()) throw new Error('Not connected');
    if (!auth.access || Date.now() > auth.expires - 60000) await refresh();
    const url = 'https://api.spotify.com/v1' + path + (query ? '?' + new URLSearchParams(query) : '');
    const r = await fetch(url, { method, headers: { Authorization: 'Bearer ' + auth.access, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined });
    if (r.status === 401 && !retried) { await refresh(); return api(path, { method, body, query }, true); }
    if (r.status === 204 || r.status === 202) return null;
    const d = await r.json().catch(() => null);
    if (!r.ok) throw Object.assign(new Error((d && d.error && d.error.message) || 'Spotify error ' + r.status), { status: r.status, reason: d && d.error && d.error.reason });
    return d;
  }
  const say = e => {
    if (e.reason === 'NO_ACTIVE_DEVICE' || e.status === 404) PH.toast('Spotify isn\'t playing on any device. Open Spotify on your phone, start any song, then come back.', 'warn');
    else if (e.reason === 'PREMIUM_REQUIRED' || e.status === 403) PH.toast('Spotify only allows remote control with Premium.', 'warn');
    else if (e.message !== 'Not connected') PH.toast(e.message, 'error');
  };

  /* ---------- Player state ---------- */
  let state = null, stamp = 0, lists = null, devices = [], shows = null;
  // Open playlist / podcast show: { id, uri, name, sub, images, items: [], total, next }
  let pl = load('mu-pl', null), sh = load('mu-sh', null);
  const fmt = ms => { const s = Math.max(0, Math.floor(ms / 1000)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  async function poll() {
    if (!connected()) return;
    try { state = await api('/me/player', { query: { additional_types: 'episode' } }); stamp = Date.now(); renderNow(); }
    catch (e) { if (e.status !== 429) { state = null; renderNow(); } }
  }
  const later = () => setTimeout(poll, 450);
  async function cmd(fn) { try { await fn(); later(); } catch (e) { say(e); } }

  async function pickDevice() {   // when nothing is active: wake this phone (or the first device Spotify knows)
    const d = await api('/me/player/devices'); devices = (d && d.devices) || [];
    return devices.find(x => x.is_active) || devices.find(x => x.type === 'Smartphone') || devices[0] || null;
  }
  const actions = {
    toggle: () => cmd(async () => {
      if (state && state.device) { await api('/me/player/' + (state.is_playing ? 'pause' : 'play'), { method: 'PUT' }); if (state) { state.is_playing = !state.is_playing; renderNow(); } return; }
      const dev = await pickDevice(); if (!dev) throw Object.assign(new Error(''), { reason: 'NO_ACTIVE_DEVICE' });
      await api('/me/player', { method: 'PUT', body: { device_ids: [dev.id], play: true } });
    }),
    next: () => cmd(() => api('/me/player/next', { method: 'POST' })),
    prev: () => cmd(() => (state && state.progress_ms > 3000) ? api('/me/player/seek', { method: 'PUT', query: { position_ms: 0 } }) : api('/me/player/previous', { method: 'POST' })),
    shuffle: () => cmd(() => api('/me/player/shuffle', { method: 'PUT', query: { state: !(state && state.shuffle_state) } })),
    repeat: () => cmd(() => api('/me/player/repeat', { method: 'PUT', query: { state: { off: 'context', context: 'track', track: 'off' }[(state && state.repeat_state) || 'off'] } })),
  };
  // body: { context_uri, offset } or { uris, position_ms }
  async function play(body, { shuffle } = {}) {
    try {
      let device_id;
      if (!state || !state.device) { const dev = await pickDevice(); if (!dev) throw Object.assign(new Error(''), { reason: 'NO_ACTIVE_DEVICE' }); device_id = dev.id; }
      const query = device_id ? { device_id } : undefined;
      if (shuffle != null) await api('/me/player/shuffle', { method: 'PUT', query: { state: shuffle, ...(query || {}) } }).catch(() => {});
      await api('/me/player/play', { method: 'PUT', body, query });
      later(); PH.go('/music');
    } catch (e) { say(e); }
  }

  /* Playlist songs. Spotify renamed /tracks to /items for newer apps, so try both. */
  let itemsPath = load('mu-items-path', 'items');
  async function playlistPage(id, offset) {
    const q = { query: { limit: 100, offset, additional_types: 'track,episode' } };
    const tryPath = itemsPath;
    try { return await api(`/playlists/${id}/${tryPath}`, q); }
    catch (e) {
      if (e.status === 401 || e.status === 429) throw e;
      const alt = tryPath === 'items' ? 'tracks' : 'items';
      const d = await api(`/playlists/${id}/${alt}`, q);
      itemsPath = alt; save('mu-items-path', alt);
      return d;
    }
  }
  async function loadPlaylist(more) {
    if (!pl || pl.loading) return;
    pl.loading = true;
    try {
      const d = await playlistPage(pl.id, more ? pl.items.length : 0);
      const got = ((d && d.items) || []).map(x => x && (x.track || x.item)).filter(t => t && t.uri && !t.is_local).map(t => ({
        uri: t.uri, name: t.name, sub: t.artists ? t.artists.map(a => a.name).join(', ') : t.show ? t.show.name : '',
        ms: t.duration_ms, images: (t.album && t.album.images) || t.images }));
      pl.items = more ? pl.items.concat(got) : got;
      pl.total = d ? d.total : pl.items.length; pl.next = !!(d && d.next); pl.loaded = true;
      renderCollection('pl', pl);
    } catch (e) { say(e); pl.loaded = true; renderCollection('pl', pl); }
    finally { pl.loading = false; }
  }
  function openPlaylist(p) {
    pl = { id: p.id, uri: p.uri, name: p.name, images: p.images, sub: ((p.tracks && p.tracks.total) ?? (p.items && p.items.total) ?? '') + ' songs', items: [], loaded: false };
    save('mu-pl', { ...pl, items: [] }); renderCollection('pl', pl); PH.go('/music/list'); loadPlaylist();
  }

  /* Podcasts: followed shows, search, episodes */
  const hasPodcastScope = () => !auth.scope || auth.scope.split(' ').includes(PODCAST_SCOPE);
  const showRow = s => ({ id: s.id, uri: s.uri, name: s.name, sub: s.publisher, images: s.images });
  async function loadShows() {
    if (auth.scope && !hasPodcastScope()) { shows = []; renderShows(); return; }
    try { const d = await api('/me/shows', { query: { limit: 50 } }); shows = ((d && d.items) || []).map(x => x && x.show).filter(Boolean).map(showRow); }
    catch (e) { shows = []; if (e.status === 403 || e.status === 401) { auth.scope = (auth.scope || 'x').replace(PODCAST_SCOPE, ''); saveAuth(); } else say(e); }
    renderShows();
  }
  let searchT = null, searchSeq = 0;
  async function searchShows(q, open) {
    const seq = ++searchSeq;
    if (!q) { $('#mp-results').textContent = ''; $('#mp-results-group').hidden = true; return; }
    try {
      const d = await api('/search', { query: { q, type: 'show', limit: open ? 1 : 20, market: 'from_token' } });
      if (seq !== searchSeq) return;
      const found = ((d && d.shows && d.shows.items) || []).filter(Boolean).map(showRow);
      if (open) { if (found[0]) openShow(found[0]); else PH.toast('Couldn\'t find that show.', 'warn'); return; }
      lastResults = found; fillRows($('#mp-results'), found, 'show', 'No shows found.'); $('#mp-results-group').hidden = false;
    } catch (e) { say(e); }
  }
  const ago = d => { if (!d) return ''; const t = new Date(d + 'T12:00:00'); return t.toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(t.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) }); };
  async function loadShow(more) {
    if (!sh || sh.loading) return;
    sh.loading = true;
    try {
      const d = await api(`/shows/${sh.id}/episodes`, { query: { limit: 50, offset: more ? sh.items.length : 0, market: 'from_token' } });
      const got = ((d && d.items) || []).filter(Boolean).map(ep => {
        const rp = ep.resume_point || {}, left = ep.duration_ms - (rp.resume_position_ms || 0);
        const status = rp.fully_played ? 'Played' : rp.resume_position_ms > 0 ? Math.max(1, Math.round(left / 60000)) + ' min left' : Math.round(ep.duration_ms / 60000) + ' min';
        return { uri: ep.uri, name: ep.name, sub: ago(ep.release_date) + ' · ' + status, ms: ep.duration_ms, images: ep.images,
          resume: rp.fully_played ? 0 : (rp.resume_position_ms || 0), played: !!rp.fully_played };
      });
      sh.items = more ? sh.items.concat(got) : got;
      sh.total = d ? d.total : sh.items.length; sh.next = !!(d && d.next); sh.loaded = true;
      renderCollection('sh', sh);
    } catch (e) { say(e); sh.loaded = true; renderCollection('sh', sh); }
    finally { sh.loading = false; }
  }
  function openShow(s) {
    sh = { ...s, items: [], loaded: false }; save('mu-sh', { ...sh, items: [] });
    renderCollection('sh', sh); PH.go('/music/podcasts/show'); loadShow();
  }
  async function loadLists() {
    try { const d = await api('/me/playlists', { query: { limit: 50 } }); lists = (d && d.items || []).filter(Boolean); renderLists(); }
    catch (e) { say(e); }
  }
  async function loadDevices() {
    try { const d = await api('/me/player/devices'); devices = (d && d.devices) || []; renderDevices(); } catch (e) { say(e); }
  }

  /* ---------- Rendering ---------- */
  // Album art: smallest image at least `size` px, else the biggest there is
  const art = (imgs, size) => { if (!imgs || !imgs.length) return ''; const s = [...imgs].sort((a, b) => (a.width || 0) - (b.width || 0)); return (s.find(i => (i.width || 0) >= size) || s[s.length - 1]).url; };
  const img = (src, cls) => { const i = document.createElement('img'); i.className = cls; i.alt = ''; i.loading = 'lazy'; i.decoding = 'async'; i.referrerPolicy = 'no-referrer'; if (src) i.src = src; else i.classList.add('none'); return i; };
  // rows: [{ uri|id, name, sub, images }] rendered as menu rows with a thumbnail
  function fillRows(box, rows, kind, empty) {
    box.textContent = '';
    if (!rows.length) { const p = document.createElement('p'); p.className = 'menu-note'; p.textContent = empty; box.appendChild(p); return; }
    rows.forEach((r, i) => {
      const b = document.createElement('button'); b.className = 'menu-item mu-row'; b.dataset.kind = kind; b.dataset.i = i;
      if (r.uri) b.dataset.uri = r.uri;
      b.appendChild(img(art(r.images, 64), 'mi-thumb'));
      const l = document.createElement('span'); l.className = 'mi-label'; l.textContent = r.name;
      if (r.sub) { const sm = document.createElement('small'); sm.textContent = r.sub; l.appendChild(sm); }
      b.appendChild(l);
      if (r.ms && kind !== 'episode') { const v = document.createElement('span'); v.className = 'mi-value'; v.textContent = fmt(r.ms); b.appendChild(v); }
      if (r.played) b.classList.add('is-played');
      box.appendChild(b);
    });
    markCurrent();
  }
  function markCurrent() {
    const cur = state && state.item && state.item.uri;
    document.querySelectorAll('.mu-row[data-uri]').forEach(b => b.classList.toggle('is-current', !!cur && b.dataset.uri === cur));
  }
  function renderCollection(px, c) {
    const head = $(`#${px}-head`);
    if (!c) { $(`#${px}-name`).textContent = 'Nothing open'; $(`#${px}-items`).textContent = ''; return; }
    head.querySelector('.mu-cover').replaceWith(img(art(c.images, 300), 'mu-cover'));
    $(`#${px}-name`).textContent = c.name; $(`#${px}-sub`).textContent = c.sub || '';
    const box = $(`#${px}-items`);
    if (!c.loaded) { box.innerHTML = '<p class="menu-note">Loading…</p>'; $(`#${px}-more`).hidden = true; return; }
    fillRows(box, c.items, px === 'pl' ? 'track' : 'episode', px === 'pl' ? 'This playlist is empty, or Spotify won\'t share its songs.' : 'No episodes.');
    $(`#${px}-more`).hidden = !c.next;
  }
  function renderShows() {
    const box = $('#mp-shows');
    $('#mp-reconnect').hidden = hasPodcastScope();
    if (!hasPodcastScope()) { box.innerHTML = '<p class="menu-note">Reconnect Spotify once so Phosphor OS can see the shows you follow. Search works already.</p>'; return; }
    if (!shows) { box.innerHTML = '<p class="menu-note">Loading your shows…</p>'; return; }
    fillRows(box, shows, 'show', 'You don\'t follow any shows yet. Search above.');
  }
  function renderNow() {
    const it = state && state.item, playing = !!(state && state.is_playing);
    $('#mu-track').textContent = it ? it.name : 'Nothing playing';
    const src = it ? art((it.album && it.album.images) || it.images || (it.show && it.show.images), 300) : '';
    const cover = $('#mu-art');
    if (cover.dataset.src !== src) { cover.dataset.src = src; cover.hidden = !src; if (src) cover.src = src; else cover.removeAttribute('src'); }
    markCurrent();
    $('#mu-artist').textContent = it ? (it.artists ? it.artists.map(a => a.name).join(', ') : it.show ? it.show.name : '') : 'Start a song in Spotify, or pick a playlist below.';
    $('#mu-dev').textContent = state && state.device ? state.device.name : 'No device';
    $('#mu-devname').textContent = state && state.device ? state.device.name : 'None';
    $('#mu-shuffle').setAttribute('aria-pressed', String(!!(state && state.shuffle_state)));
    const rep = (state && state.repeat_state) || 'off';
    $('#mu-repeat').setAttribute('aria-pressed', String(rep !== 'off'));
    $('#mu-repeat').querySelector('i').textContent = rep === 'track' ? '1' : '';
    document.querySelectorAll('[data-mu="toggle"]').forEach(b => {
      b.setAttribute('aria-label', playing ? 'Pause' : 'Play');
      if (b.closest('.page-keys')) b.textContent = playing ? 'II' : '▸';
      else b.innerHTML = playing ? ICON.pause : ICON.play;
    });
    tickBar();
  }
  function tickBar() {
    const it = state && state.item; if (!it) { $('#mu-bar').style.setProperty('--value', '0%'); $('#mu-pos').textContent = $('#mu-dur').textContent = '0:00'; return; }
    const pos = Math.min(it.duration_ms, state.progress_ms + (state.is_playing ? Date.now() - stamp : 0));
    $('#mu-bar').style.setProperty('--value', (100 * pos / it.duration_ms).toFixed(1) + '%');
    $('#mu-bar').parentElement.setAttribute('aria-valuenow', Math.round(100 * pos / it.duration_ms));
    $('#mu-pos').textContent = fmt(pos); $('#mu-dur').textContent = fmt(it.duration_ms);
    if (state.is_playing && pos >= it.duration_ms && Date.now() - stamp > 1500) { stamp = Date.now(); later(); }
  }
  function renderLists() {
    const box = $('#mu-lists');
    if (!lists) { box.innerHTML = '<p class="menu-note">Loading playlists…</p>'; return; }
    fillRows(box, lists.map(p => ({ id: p.id, name: p.name, images: p.images,
      sub: (((p.tracks && p.tracks.total) ?? (p.items && p.items.total)) ?? '') + ' songs' })), 'playlist', 'No playlists in your library yet.');
  }
  function renderDevices() {
    const box = $('#mu-devices'); box.textContent = '';
    if (!devices.length) { box.innerHTML = '<p class="menu-note">No devices. Open Spotify on your phone and play something, then tap Refresh.</p>'; return; }
    devices.forEach(d => {
      const b = document.createElement('button'); b.className = 'menu-item'; b.dataset.device = d.id;
      b.innerHTML = '<span class="mi-glyph" aria-hidden="true"></span><span class="mi-label"></span><span class="mi-value"></span>';
      b.querySelector('.mi-glyph').textContent = d.is_active ? '●' : '○';
      b.querySelector('.mi-label').textContent = d.name;
      b.querySelector('.mi-value').textContent = d.is_active ? 'Playing here' : d.type;
      box.appendChild(b);
    });
  }
  function renderSetup() {
    $('#mu-client').value = auth.clientId || '';
    $('#mu-redirect').textContent = REDIRECT;
    $('#mu-status').textContent = connected() ? (auth.user ? 'Connected as ' + auth.user : 'Connected') : 'Not connected';
    $('#mu-status').className = connected() ? 't-accent' : '';
    $('#mu-disconnect').hidden = !connected();
    $('#mu-connect').textContent = connected() ? 'Reconnect' : 'Connect Spotify';    $('#set-spotify').textContent = connected() ? 'Connected' : 'Off';
  }
  function renderAll() {
    $('#mu-off').hidden = connected(); $('#mu-on').hidden = !connected();
    renderSetup(); renderNow(); renderLists(); renderShows();
    if (pl && !pl.loaded) renderCollection('pl', pl);
    if (sh && !sh.loaded) renderCollection('sh', sh);
  }

  const ICON = {
    play: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4v16l13-8z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M6 4h4v16H6zM14 4h4v16h-4z"/></svg>',
  };

  /* ---------- Wiring ---------- */
  document.addEventListener('click', e => {
    const m = e.target.closest('[data-mu]'); if (m && actions[m.dataset.mu]) { e.preventDefault(); actions[m.dataset.mu](); return; }
    const row = e.target.closest('.mu-row');
    if (row) {
      const i = +row.dataset.i, k = row.dataset.kind;
      if (k === 'playlist') openPlaylist(lists[i]);
      else if (k === 'show') openShow((row.closest('#mp-results') ? null : shows && shows[i]) || readShow(row));
      else if (k === 'track') play({ context_uri: pl.uri, offset: { uri: pl.items[i].uri } });
      else if (k === 'episode') { const ep = sh.items[i]; play({ uris: [ep.uri], ...(ep.resume ? { position_ms: ep.resume } : {}) }); }
      return;
    }
    const a = e.target.closest('[data-mu-act]');
    if (a) {
      const k = a.dataset.muAct;
      if (k === 'pl-play' && pl) play({ context_uri: pl.uri }, { shuffle: false });
      if (k === 'pl-shuffle' && pl) play({ context_uri: pl.uri }, { shuffle: true });
      if (k === 'pl-more') loadPlaylist(true);
      if (k === 'sh-play' && sh && sh.items.length) { const ep = sh.items.find(x => !x.played) || sh.items[0]; play({ uris: [ep.uri], ...(ep.resume ? { position_ms: ep.resume } : {}) }); }
      if (k === 'sh-more') loadShow(true);
      if (k === 'sysk') searchShows('Stuff You Should Know', true);
      if (k === 'reconnect') login();
      return;
    }
    const d = e.target.closest('#mu-devices [data-device]');
    if (d) cmd(async () => { await api('/me/player', { method: 'PUT', body: { device_ids: [d.dataset.device], play: true } }); setTimeout(loadDevices, 600); });
  });
  $('#mu-connect').addEventListener('click', login);
  $('#mu-disconnect').addEventListener('click', async () => {
    if (await PH.confirm('Disconnect Spotify from Phosphor OS?', { title: 'Disconnect', ok: 'Disconnect', danger: true })) disconnect();
  });
  $('#mu-pair-use').addEventListener('click', usePairing);
  document.querySelectorAll('[data-copy]').forEach(b => b.addEventListener('click', async () => {
    const src = document.getElementById(b.dataset.copy), text = src.value ?? src.textContent;
    try { await navigator.clipboard.writeText(text); PH.toast('Copied.'); }
    catch { if (src.select) { src.select(); document.execCommand('copy'); PH.toast('Copied.'); } }
  }));
  $('#mu-dev-refresh').addEventListener('click', loadDevices);
  let lastResults = [];
  const readShow = row => lastResults[+row.dataset.i];
  $('#mp-q').addEventListener('input', () => { clearTimeout(searchT); searchT = setTimeout(() => searchShows($('#mp-q').value.trim()), 400); });
  $('#mp-q').addEventListener('keydown', e => { if (e.key === 'Enter') { e.preventDefault(); clearTimeout(searchT); searchShows($('#mp-q').value.trim()); e.target.blur(); } });

  // Poll only while a music page is on screen and the app is visible
  let pollT = null, barT = null;
  const onMusic = () => /^#\/music(\/|$)/.test(location.hash);
  function sync() {
    clearInterval(pollT); clearInterval(barT); pollT = barT = null;
    if (!onMusic() || document.hidden) return;
    renderAll();
    if (!connected()) return;
    poll(); pollT = setInterval(poll, 4000); barT = setInterval(tickBar, 1000);
    const h = location.hash;
    if (h.startsWith('#/music/devices')) loadDevices();
    else if (h.startsWith('#/music/list')) { if (pl && !pl.loaded) loadPlaylist(); }
    else if (h.startsWith('#/music/podcasts/show')) { if (sh && !sh.loaded) loadShow(); }
    else if (h.startsWith('#/music/podcasts')) { if (!shows) loadShows(); }
    else if (!lists) loadLists();
  }
  addEventListener('hashchange', sync);
  document.addEventListener('visibilitychange', sync);

  renderSetup();
  finishLogin().finally(() => setTimeout(sync, 0));
})();
