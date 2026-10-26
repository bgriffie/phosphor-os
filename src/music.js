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
  const SCOPES = 'user-read-playback-state user-modify-playback-state user-read-currently-playing playlist-read-private playlist-read-collaborative user-library-read user-read-playback-position user-read-recently-played';
  const RECENT_SCOPE = 'user-read-recently-played';   // added in v1.10: older logins must reconnect once for Recently played
  const RESUME_SCOPE = 'user-read-playback-position';   // added in v1.3: older logins can't see which episodes were played
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
    auth = { clientId: auth.clientId }; saveAuth(); state = null; eps = null;
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
  const wait = ms => new Promise(r => setTimeout(r, ms));
  let backoffUntil = 0;   // Spotify said "slow down" (429): no polling until then
  async function api(path, { method = 'GET', body, query } = {}, retried, attempt = 0) {
    if (!connected()) throw new Error('Not connected');
    if (!auth.access || Date.now() > auth.expires - 60000) await refresh();
    const url = 'https://api.spotify.com/v1' + path + (query ? '?' + new URLSearchParams(query) : '');
    const safe = method !== 'POST';   // next/previous must not be sent twice
    let r;
    try { r = await fetch(url, { method, headers: { Authorization: 'Bearer ' + auth.access, ...(body ? { 'Content-Type': 'application/json' } : {}) }, body: body ? JSON.stringify(body) : undefined }); }
    catch (e) {   // offline or a dropped connection: one quiet retry
      if (safe && attempt < 1) { await wait(800); return api(path, { method, body, query }, retried, attempt + 1); }
      throw Object.assign(new Error(navigator.onLine === false ? 'No internet connection.' : 'Couldn\'t reach Spotify.'), { offline: true });
    }
    if (r.status === 401 && !retried) { await refresh(); return api(path, { method, body, query }, true, attempt); }
    if (r.status === 429) { backoffUntil = Date.now() + 1000 * (+r.headers.get('Retry-After') || 5); }
    if (r.status >= 500 && safe && attempt < 2) { await wait(600 * (attempt + 1)); return api(path, { method, body, query }, retried, attempt + 1); }
    if (r.status === 204 || r.status === 202) return null;
    const d = await r.json().catch(() => null);
    if (!r.ok) throw Object.assign(new Error((d && d.error && d.error.message) || 'Spotify error ' + r.status), { status: r.status, reason: d && d.error && d.error.reason });
    return d;
  }
  const say = e => {
    if (e.offline) { PH.toast(e.message, 'warn'); return; }
    if (e.reason === 'NO_ACTIVE_DEVICE' || e.status === 404) PH.toast('Spotify isn\'t playing on any device. Open Spotify on your phone, start any song, then come back.', 'warn');
    else if (e.reason === 'PREMIUM_REQUIRED') PH.toast('Spotify only allows remote control with Premium.', 'warn');
    else if (e.status === 403) PH.toast('Spotify refused that: ' + (e.message || 'not allowed') + (e.reason ? ' (' + e.reason + ')' : ''), 'warn');
    else if (e.message !== 'Not connected') PH.toast(e.message, 'error');
  };

  /* ---------- Player state ---------- */
  let state = null, stamp = 0, devices = [], misses = 0;
  // Stuff You Should Know episodes: { items: [], next, loaded, loading }
  let eps = null, epFilter = load('mu-ep-filter', 'new'), epSort = load('mu-ep-sort', 'new');
  const fmt = ms => { const s = Math.max(0, Math.floor(ms / 1000)); return Math.floor(s / 60) + ':' + String(s % 60).padStart(2, '0'); };
  const isEpisode = () => !!(state && state.item && state.item.type === 'episode');
  const playing = () => !!(state && state.is_playing);
  const changed = () => dispatchEvent(new CustomEvent('os-music', { detail: { playing: playing() } }));

  async function poll() {
    if (!connected() || Date.now() < backoffUntil) return;
    try {
      state = await api('/me/player', { query: { additional_types: 'episode' } }); stamp = Date.now(); misses = 0;
      if (state && state.device && state.device.id) save('mu-last-device', state.device.id);
      renderNow(); note('');
    } catch (e) {
      if (e.status === 429) return;
      // keep showing the last item through a blip; only clear after a few misses in a row
      if (++misses >= 3) { state = null; renderNow(); }
      note(e.offline ? 'Offline. Retrying…' : 'Reconnecting to Spotify…');
    }
  }
  const note = t => { const el = $('#mu-note'); if (el) { el.textContent = t; el.hidden = !t; } };
  const later = () => setTimeout(poll, 450);
  async function cmd(fn) { try { await fn(); later(); } catch (e) { say(e); } }

  async function pickDevice() {   // when nothing is active: wake this phone (or the best device Spotify knows)
    const d = await api('/me/player/devices'); devices = (d && d.devices) || [];
    const last = load('mu-last-device', null), ok = devices.filter(x => !x.is_restricted);
    return ok.find(x => x.is_active) || ok.find(x => x.id === last) || ok.find(x => x.type === 'Smartphone') || ok[0] || null;
  }
  const pos = () => state && state.item ? Math.min(state.item.duration_ms, state.progress_ms + (state.is_playing ? Date.now() - stamp : 0)) : 0;
  const seekBy = ms => cmd(async () => {
    const to = Math.max(0, Math.min(state.item.duration_ms - 1000, pos() + ms));
    await api('/me/player/seek', { method: 'PUT', query: { position_ms: Math.round(to) } });
    state.progress_ms = to; stamp = Date.now(); tickBar();
  });
  const actions = {
    toggle: () => cmd(async () => {
      if (state && state.device) {
        try { await api('/me/player/' + (state.is_playing ? 'pause' : 'play'), { method: 'PUT' }); if (state) { state.progress_ms = pos(); stamp = Date.now(); state.is_playing = !state.is_playing; renderNow(); } return; }
        catch (e) {
          if (e.status === 403 && e.reason !== 'PREMIUM_REQUIRED') { later(); return; }   // already paused/playing: just refresh
          if (!(e.reason === 'NO_ACTIVE_DEVICE' || e.status === 404)) throw e;   // fall through and wake a device
        }
      }
      const dev = await pickDevice(); if (!dev) throw Object.assign(new Error(''), { reason: 'NO_ACTIVE_DEVICE' });
      await api('/me/player', { method: 'PUT', body: { device_ids: [dev.id], play: true } });
    }),
    // Episodes skip 15 s back / 30 s ahead; songs go to the previous / next track
    left: () => isEpisode() ? seekBy(-15000)
      : cmd(() => (state && state.progress_ms > 3000) ? api('/me/player/seek', { method: 'PUT', query: { position_ms: 0 } }) : api('/me/player/previous', { method: 'POST' })),
    right: () => isEpisode() ? seekBy(30000) : cmd(() => api('/me/player/next', { method: 'POST' })),
  };
  // body: { uris, position_ms }
  async function play(body) {
    try {
      let device_id;
      if (!state || !state.device) { const dev = await pickDevice(); if (!dev) throw Object.assign(new Error(''), { reason: 'NO_ACTIVE_DEVICE' }); device_id = dev.id; }
      try {
        try { await api('/me/player/play', { method: 'PUT', body, query: device_id ? { device_id } : undefined }); }
        catch (e) {   // Spotify won't start some playlists (its own mixes) for apps like this one: play just the song
          if (!(body.context_uri && body.offset && body.offset.uri && (e.status === 403 || e.status === 404) && e.reason !== 'NO_ACTIVE_DEVICE')) throw e;
          body = { uris: [body.offset.uri] };
          await api('/me/player/play', { method: 'PUT', body, query: device_id ? { device_id } : undefined });
        }
      }
      catch (e) {   // the device we thought was active went to sleep: wake the best one and try once more
        if (!(e.reason === 'NO_ACTIVE_DEVICE' || e.status === 404) || device_id) throw e;
        const dev = await pickDevice(); if (!dev) throw e;
        await api('/me/player/play', { method: 'PUT', body, query: { device_id: dev.id } });
      }
      later(); PH.go('/music/now');
    } catch (e) { say(e); }
  }

  /* Stuff You Should Know: find the show once, then page through its episodes (newest first) */
  const hasResume = () => !auth.scope || auth.scope.split(' ').includes(RESUME_SCOPE);
  const SYSK_ID = '0ofXAdFIQQRsCYj9754UFx';   // the show's Spotify id, used if search comes back empty
  let searchNote = 'saved id';
  async function syskId() {
    searchNote = 'saved id';
    let id = load('mu-sysk', null); if (id) return id;
    let found = [];
    for (const q of [{ market: 'from_token' }, {}]) {   // some accounts get nothing back with a market
      try {
        const d = await api('/search', { query: { q: 'Stuff You Should Know', type: 'show', limit: 5, ...q } });
        found = ((d && d.shows && d.shows.items) || []).filter(Boolean);
      } catch (e) { if (e.status === 401 || e.offline) throw e; }
      if (found.length) break;
    }
    const s = found.find(x => /^stuff you should know$/i.test(x.name)) || found.find(x => /stuff you should know/i.test(x.name));
    id = s ? s.id : SYSK_ID; searchNote = s ? 'search found the show' : `search found ${found.length} shows, using built-in id`;
    save('mu-sysk', id); return id;
  }
  // Episodes, newest first. Tries the episodes list, then the show itself (which carries its first
  // page of episodes), with and without a market, since Spotify answers these differently per account.
  let diag = [], pageForm = 0;
  async function episodePage(id, offset) {
    const tries = [
      () => api(`/shows/${id}/episodes`, { query: { limit: 50, offset, market: 'from_token' } }),
      () => api(`/shows/${id}/episodes`, { query: { limit: 20, offset } }),
      ...(offset ? [] : [() => api(`/shows/${id}`, { query: { market: 'from_token' } }).then(s => s && s.episodes)]),
    ];
    let last;
    for (const [i, t] of tries.entries()) {
      try { const d = await t(); const n = ((d && d.items) || []).filter(Boolean).length; diag.push(`try ${i + 1}: ${n} episodes`); if (n) { pageForm = i; return d; } last = last || new Error('Spotify sent no episodes.'); }
      catch (e) { diag.push(`try ${i + 1}: ${e.status || ''} ${e.message}`); if (e.status === 401 || e.status === 429 || e.offline) throw e; last = e; }
    }
    throw last;
  }
  const day = d => { if (!d) return ''; const t = new Date(d + 'T12:00:00'); return t.toLocaleDateString('en-US', { month: 'short', day: 'numeric', ...(t.getFullYear() !== new Date().getFullYear() ? { year: 'numeric' } : {}) }); };
  // The whole catalog is fetched (50 at a time) so it can be sorted and scrubbed by date; it's kept on the
  // phone, so the next visit shows it at once while a fresh copy (with what's been played) comes in.
  const epOf = ep => { const rp = ep.resume_point || {};
    return { uri: ep.uri, name: ep.name, date: ep.release_date || '', dur: ep.duration_ms || 0, played: !!rp.fully_played, resume: rp.fully_played ? 0 : rp.resume_position_ms || 0 }; };
  async function loadEpisodes() {
    if (eps && eps.loading) return;
    const cached = load('mu-ep-cache', null);
    if (!eps || eps.error) eps = { items: cached && cached.items ? cached.items : [], loaded: !!(cached && cached.items), total: 0, got: 0 };
    eps.loading = true; eps.error = ''; renderEpisodes();
    let lastDraw = 0;
    try {
      const id = await syskId(); diag = [searchNote + ' (' + id + ')'];
      let d;
      try { d = await episodePage(id, 0); }
      catch (e) { drop('mu-sysk'); throw e; }
      const fresh = [], seen = new Set();
      eps.total = (d && d.total) || 0;
      for (;;) {
        ((d && d.items) || []).filter(Boolean).forEach(ep => { if (!seen.has(ep.uri)) { seen.add(ep.uri); fresh.push(epOf(ep)); } });
        eps.got = fresh.length;
        // show what's in so far, with the older part of the saved copy underneath
        eps.items = fresh.concat(eps.items.filter(x => !seen.has(x.uri))); eps.loaded = true;
        if (Date.now() - lastDraw > 700) { lastDraw = Date.now(); renderEpisodes(); }
        if (!d || !d.next || pageForm === 2) break;
        d = await api(`/shows/${id}/episodes`, { query: pageForm === 0 ? { limit: 50, offset: fresh.length, market: 'from_token' } : { limit: 20, offset: fresh.length } });
      }
      eps.items = fresh; save('mu-ep-cache', { id, at: Date.now(), items: fresh });
    } catch (e) { if (!eps.items.length) eps.error = e.message || 'Spotify didn\'t send the episodes.'; else eps.stale = true; if (e.offline) say(e); }
    finally { eps.loading = false; eps.loaded = true; renderEpisodes(); }
  }
  /* Recently played songs (Spotify only lists music here, not podcast episodes) */
  let recent = null;   // { items: [], loaded, error }
  const hasRecent = () => !auth.scope || auth.scope.split(' ').includes(RECENT_SCOPE);
  const ago = t => { const m = Math.round((Date.now() - new Date(t)) / 60000);
    return m < 1 ? 'just now' : m < 60 ? m + ' min ago' : m < 1440 ? Math.round(m / 60) + ' h ago' : Math.round(m / 1440) + ' d ago'; };
  async function loadRecent() {
    if (recent && recent.loading) return;
    recent = { items: (recent && recent.items) || [], loaded: !!recent, loading: true };
    renderRecent();
    try {
      const d = await api('/me/player/recently-played', { query: { limit: 50 } });
      const seen = new Set();
      recent.items = ((d && d.items) || []).filter(x => x && x.track && x.track.uri && !seen.has(x.track.uri) && seen.add(x.track.uri)).map(x => ({
        uri: x.track.uri, name: x.track.name, sub: (x.track.artists || []).map(a => a.name).join(', ') + ' · ' + ago(x.played_at),
        images: x.track.album && x.track.album.images, context: x.context && /^spotify:(album|playlist|artist|show):/.test(x.context.uri) ? x.context.uri : null }));
      recent.error = '';
    } catch (e) {
      recent.error = e.status === 403 || e.status === 401 ? 'scope' : (e.message || 'Spotify didn\'t send your history.');
      if (e.offline) say(e);
    }
    recent.loading = false; recent.loaded = true; renderRecent();
  }
  async function loadDevices() {
    try { const d = await api('/me/player/devices'); devices = (d && d.devices) || []; renderDevices(); } catch (e) { say(e); }
  }

  /* ---------- Rendering ---------- */
  const ICON = {
    play: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><path d="M7 4.5v15a1 1 0 0 0 1.5.86l12.2-7.5a1 1 0 0 0 0-1.72L8.5 3.64A1 1 0 0 0 7 4.5z"/></svg>',
    pause: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="5.5" y="4" width="4.5" height="16" rx="1"/><rect x="14" y="4" width="4.5" height="16" rx="1"/></svg>',
    prev: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="4" y="5" width="2.5" height="14" rx="1"/><path d="M20 5.7v12.6a.8.8 0 0 1-1.2.7L8.6 12.7a.8.8 0 0 1 0-1.4L18.8 5a.8.8 0 0 1 1.2.7z"/></svg>',
    next: '<svg viewBox="0 0 24 24" fill="currentColor" aria-hidden="true"><rect x="17.5" y="5" width="2.5" height="14" rx="1"/><path d="M4 5.7v12.6a.8.8 0 0 0 1.2.7l10.2-6.3a.8.8 0 0 0 0-1.4L5.2 5A.8.8 0 0 0 4 5.7z"/></svg>',
    back15: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 12a8 8 0 1 0 2.4-5.7"/><path d="M4 3v4h4"/><text x="12" y="15.5" text-anchor="middle" font-size="7.5" font-family="inherit" font-weight="700" fill="currentColor" stroke="none">15</text></svg>',
    fwd30: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M20 12a8 8 0 1 1-2.4-5.7"/><path d="M20 3v4h-4"/><text x="12" y="15.5" text-anchor="middle" font-size="7.5" font-family="inherit" font-weight="700" fill="currentColor" stroke="none">30</text></svg>',
  };
  const art = (imgs, size) => { if (!imgs || !imgs.length) return ''; const s = [...imgs].sort((a, b) => (a.width || 0) - (b.width || 0)); return (s.find(i => (i.width || 0) >= size) || s[s.length - 1]).url; };
  function renderNow() {
    const it = state && state.item, ep = isEpisode();
    $('#mu-track').textContent = it ? it.name : 'Nothing playing';
    $('#mu-artist').textContent = it ? (it.artists ? it.artists.map(a => a.name).join(', ') : it.show ? it.show.name : '') : 'Pick a song or an episode, or start something in Spotify.';
    const src = it ? art((it.album && it.album.images) || it.images || (it.show && it.show.images), 300) : '';
    const cover = $('#mu-art');
    if (cover.dataset.src !== src) { cover.dataset.src = src; cover.hidden = !src; if (src) cover.src = src; else cover.removeAttribute('src'); }
    $('#mu-devname').textContent = state && state.device ? state.device.name : 'None';
    // the Now playing card on the Spotify home page
    $('#mu-mini-title').textContent = it ? it.name : 'Nothing playing';
    $('#mu-mini-sub').textContent = it ? (it.artists ? it.artists.map(a => a.name).join(', ') : it.show ? it.show.name : '') : 'Pick something below';
    const mini = $('#mu-mini-art'), msrc = it ? art((it.album && it.album.images) || it.images || (it.show && it.show.images), 64) : '';
    if (mini.dataset.src !== msrc) { mini.dataset.src = msrc; mini.classList.toggle('none', !msrc); if (msrc) mini.src = msrc; else mini.removeAttribute('src'); }
    document.querySelectorAll('#rp-items [data-uri]').forEach(b => b.classList.toggle('is-current', !!it && b.dataset.uri === it.uri));
    const set = (b, html, label) => { if (b.dataset.icon !== html) { b.innerHTML = html; b.dataset.icon = html; } b.setAttribute('aria-label', label); };
    set($('#mu-left'), ep ? ICON.back15 : ICON.prev, ep ? 'Back 15 seconds' : 'Previous');
    set($('#mu-right'), ep ? ICON.fwd30 : ICON.next, ep ? 'Forward 30 seconds' : 'Next');
    document.querySelectorAll('[data-mu="toggle"]').forEach(b => set(b, playing() ? ICON.pause : ICON.play, playing() ? 'Pause' : 'Play'));
    document.querySelectorAll('#ep-items [data-uri]').forEach(b => b.classList.toggle('is-current', !!it && b.dataset.uri === it.uri));
    tickBar(); changed();
  }
  function tickBar() {
    const it = state && state.item;
    if (!it) { $('#mu-bar').style.setProperty('--value', '0%'); $('#mu-pos').textContent = $('#mu-dur').textContent = '0:00'; return; }
    const p = pos();
    $('#mu-bar').style.setProperty('--value', (100 * p / it.duration_ms).toFixed(1) + '%');
    $('#mu-bar').parentElement.setAttribute('aria-valuenow', Math.round(100 * p / it.duration_ms));
    $('#mu-pos').textContent = fmt(p); $('#mu-dur').textContent = fmt(it.duration_ms);
    if (state.is_playing && p >= it.duration_ms && Date.now() - stamp > 1500) { stamp = Date.now(); later(); }
  }
  function renderRecent() {
    const box = $('#rp-items'); box.textContent = '';
    const needScope = !hasRecent() || (recent && recent.error === 'scope');
    $('#rp-scope').hidden = !needScope;
    const msg = t => { const p = document.createElement('p'); p.className = 'menu-note'; p.textContent = t; box.appendChild(p); };
    if (needScope) return;
    if (!recent || (!recent.loaded && !recent.items.length)) { msg('Loading what you played…'); return; }
    if (recent.error && !recent.items.length) { msg('Couldn\'t load your history. Spotify said: ' + recent.error); return; }
    if (!recent.items.length) { msg('Nothing played lately. Play something in Spotify and it shows up here.'); return; }
    const cur = state && state.item && state.item.uri;
    recent.items.forEach((x, i) => {
      const b = document.createElement('button'); b.className = 'menu-item mu-row mu-rp'; b.dataset.i = i; b.dataset.uri = x.uri;
      const im = document.createElement('img'); im.className = 'mi-thumb'; im.alt = ''; im.loading = 'lazy'; im.referrerPolicy = 'no-referrer';
      const src = art(x.images, 64); if (src) im.src = src; else im.classList.add('none'); b.appendChild(im);
      const l = document.createElement('span'); l.className = 'mi-label'; l.textContent = x.name;
      const sm = document.createElement('small'); sm.textContent = x.sub; l.appendChild(sm); b.appendChild(l);
      b.classList.toggle('is-current', x.uri === cur);
      box.appendChild(b);
    });
  }
  const MONTHS = ['January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'];
  const monthOf = x => { const m = /^(\d{4})(?:-(\d{2}))?/.exec(x.date); return m ? (m[2] ? MONTHS[+m[2] - 1] + ' ' : '') + m[1] : 'Undated'; };
  const mins = x => Math.max(1, Math.round(x.dur / 60000));
  // what the scrubber shows while you drag: the month for date sorts, the length for length sorts
  const epLabel = x => epSort === 'short' || epSort === 'long' ? mins(x) + ' min' : monthOf(x).replace(/^(\w{3})\w*/, '$1');
  const SORTS = { new: (a, b) => b.date.localeCompare(a.date), old: (a, b) => a.date.localeCompare(b.date), short: (a, b) => a.dur - b.dur, long: (a, b) => b.dur - a.dur };
  let epRows = [];
  function renderEpisodes() {
    document.querySelectorAll('[data-ep-filter]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.epFilter === epFilter)));
    document.querySelectorAll('[data-ep-sort]').forEach(b => b.setAttribute('aria-checked', String(b.dataset.epSort === epSort)));
    $('#ep-scope').hidden = hasResume();
    const box = $('#ep-items'); box.textContent = ''; epRows = [];
    const msg = t => { const p = document.createElement('p'); p.className = 'menu-note'; p.textContent = t; box.appendChild(p); };
    const prog = $('#ep-prog');
    prog.hidden = !(eps && eps.loading && eps.total && eps.got < eps.total);
    if (!prog.hidden) prog.textContent = `Loading the whole catalog… ${eps.got} of ${eps.total}`;
    if (!eps || !eps.loaded) { msg('Loading episodes…'); syncScrub(); return; }
    const cur = state && state.item && state.item.uri;
    const rows = eps.items.filter(x => epFilter === 'all' || !x.played).slice().sort(SORTS[epSort] || SORTS.new);
    if (!rows.length) msg(eps.error ? 'Couldn\'t load episodes. Spotify said: ' + eps.error : eps.loading ? 'Loading episodes…' : eps.items.length ? 'You\'ve played every episode. Tap All to see them.' : 'No episodes yet.');
    const byDate = epSort === 'new' || epSort === 'old';
    let lastMonth = '';
    const frag = document.createDocumentFragment();
    rows.forEach(x => {
      if (byDate) { const m = monthOf(x); if (m !== lastMonth) { lastMonth = m; const h = document.createElement('div'); h.className = 'ep-month'; h.textContent = m; frag.appendChild(h); } }
      const b = document.createElement('button'); b.className = 'menu-item mu-ep'; b.dataset.uri = x.uri;
      const l = document.createElement('span'); l.className = 'mi-label'; l.textContent = x.name;
      const sm = document.createElement('small');
      sm.textContent = day(x.date) + ' · ' + (x.played ? 'Played' : x.resume > 0 ? Math.max(1, Math.round((x.dur - x.resume) / 60000)) + ' min left' : mins(x) + ' min');
      l.appendChild(sm); b.appendChild(l);
      b.classList.toggle('is-played', x.played); b.classList.toggle('is-current', x.uri === cur);
      frag.appendChild(b); epRows.push([x, b]);
    });
    box.appendChild(frag);
    $('#ep-diag').textContent = eps.error ? 'Details: ' + diag.join(' · ') : '';
    const fresh = eps.items.filter(x => !x.played).length;
    $('#mu-sysk-sub').textContent = hasResume() && eps.items.length ? fresh + ' unplayed' : 'Episodes';
    syncScrub();
  }

  /* Episode scrubber: hold the strip on the right edge and slide to jump through the catalog by month */
  const scrub = $('#ep-scrub'), bubble = $('#ep-bubble'), body = () => document.querySelector('.screen-body');
  const onEpisodes = () => /^#\/music\/episodes/.test(location.hash);
  function syncScrub() {
    scrub.hidden = !onEpisodes() || epRows.length < 30;
    if (scrub.hidden) return;
    const b = body(), max = b.scrollHeight - b.clientHeight;
    scrub.style.setProperty('--at', max > 0 ? Math.min(1, b.scrollTop / max) : 0);
  }
  // where on the strip a finger is, 0 at the top to 1 at the bottom (the screen may be turned sideways)
  function stripFrac(e) {
    const r = scrub.getBoundingClientRect(), p = e.touches ? e.touches[0] : e;
    const f = document.documentElement.classList.contains('os-land') ? (r.right - p.clientX) / r.width : (p.clientY - r.top) / r.height;
    return Math.min(1, Math.max(0, f));
  }
  let scrubbing = false;
  const pageTop = el => { let y = 0; for (; el; el = el.offsetParent) y += el.offsetTop; return y; };   // unaffected by the turned screen
  function scrubTo(f) {
    if (!epRows.length) return;
    const [x] = epRows[Math.min(epRows.length - 1, Math.floor(f * epRows.length))];
    // jump to the first episode of that month (or that length), with its month heading in view
    const lab = epLabel(x), first = epRows.find(([y]) => epLabel(y) === lab)[1];
    const head = first.previousElementSibling && first.previousElementSibling.classList.contains('ep-month') ? first.previousElementSibling : first;
    body().scrollTop = pageTop(head) - pageTop(body());
    bubble.textContent = lab; bubble.style.setProperty('--y', f);
    scrub.style.setProperty('--at', f);
  }
  const scrubStart = e => { e.preventDefault(); scrubbing = true; scrub.classList.add('is-on'); bubble.hidden = false; scrubTo(stripFrac(e)); };
  const scrubMove = e => { if (!scrubbing) return; e.preventDefault(); scrubTo(stripFrac(e)); };
  const scrubEnd = () => { if (!scrubbing) return; scrubbing = false; scrub.classList.remove('is-on'); setTimeout(() => { if (!scrubbing) bubble.hidden = true; }, 600); };
  scrub.addEventListener('touchstart', scrubStart, { passive: false });
  scrub.addEventListener('touchmove', scrubMove, { passive: false });
  scrub.addEventListener('touchend', scrubEnd); scrub.addEventListener('touchcancel', scrubEnd);
  scrub.addEventListener('mousedown', e => { scrubStart(e); const mv = ev => scrubMove(ev), up = () => { scrubEnd(); removeEventListener('mousemove', mv); removeEventListener('mouseup', up); }; addEventListener('mousemove', mv); addEventListener('mouseup', up); });
  document.querySelector('.screen-body').addEventListener('scroll', () => { if (!scrubbing && onEpisodes()) syncScrub(); }, { passive: true });
  addEventListener('hashchange', () => { syncScrub(); bubble.hidden = true; });

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
    $('#mu-connect').textContent = connected() ? 'Reconnect' : 'Connect Spotify';
    $('#set-spotify').textContent = connected() ? 'Connected' : 'Off';
  }
  function renderAll() {
    $('#mu-off').hidden = connected(); $('#mu-on').hidden = !connected();
    renderSetup(); renderNow(); renderEpisodes();
  }

  /* ---------- Wiring ---------- */
  document.addEventListener('click', e => {
    const m = e.target.closest('[data-mu]'); if (m && actions[m.dataset.mu]) { e.preventDefault(); actions[m.dataset.mu](); return; }
    const row = e.target.closest('#ep-items .mu-ep');
    if (row) { const ep = eps.items.find(x => x.uri === row.dataset.uri); if (ep) play({ uris: [ep.uri], ...(ep.resume ? { position_ms: ep.resume } : {}) }); return; }
    const rp = e.target.closest('#rp-items .mu-rp');
    if (rp) { const x = recent.items[+rp.dataset.i]; play(x.context && !/^spotify:artist:/.test(x.context) ? { context_uri: x.context, offset: { uri: x.uri } } : { uris: [x.uri] }); return; }
    if (e.target.closest('[data-mu-act="reconnect"]')) { login(); return; }
    const f = e.target.closest('[data-ep-filter]');
    if (f) { epFilter = f.dataset.epFilter; save('mu-ep-filter', epFilter); renderEpisodes(); return; }
    const so = e.target.closest('[data-ep-sort]');
    if (so) { epSort = so.dataset.epSort; save('mu-ep-sort', epSort); renderEpisodes(); body().scrollTop = 0; return; }
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

  // For the OS right key: play/pause from anywhere
  window.PhosphorMusic = {
    toggle: async () => { if (!connected()) { PH.toast('Set up Spotify first.', 'warn'); return; } if (!onMusic()) await poll(); actions.toggle(); },
    playing,
  };

  // Poll only while a music page is on screen and the app is visible
  let pollT = null, barT = null;
  const onMusic = () => /^#\/music(\/|$)/.test(location.hash);
  if (/^#\/music\/recent/.test(location.hash)) location.replace('#/music');   // v1.11: recent plays moved to the Spotify home page
  function sync() {
    clearInterval(pollT); clearInterval(barT); pollT = barT = null;
    if (!onMusic() || document.hidden) return;
    renderAll();
    if (!connected()) return;
    poll(); pollT = setInterval(poll, 4000); barT = setInterval(tickBar, 1000);
    const h = location.hash;
    if (h.startsWith('#/music/devices')) loadDevices();
    else if (/^#\/music\/?$/.test(h)) { if (hasRecent()) loadRecent(); else renderRecent(); if (!eps || eps.error) loadEpisodes(); }
    else if (h.startsWith('#/music/episodes') && (!eps || eps.error || !eps.loading)) loadEpisodes();
  }
  addEventListener('hashchange', sync);
  document.addEventListener('visibilitychange', sync);
  addEventListener('online', sync);
  addEventListener('pageshow', e => { if (e.persisted) sync(); });

  renderSetup();
  finishLogin().finally(() => setTimeout(sync, 0));
})();
