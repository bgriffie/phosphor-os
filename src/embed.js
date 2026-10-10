/* Phosphor OS embed shim: injected at the end of each bundled app's <body>.
   The app runs inside the OS's screen in an iframe, keeps its own data (same origin,
   same localStorage keys as the standalone version), and is driven by the OS's three keys. */
(() => {
  const os = window.parent !== window && window.parent.PhosphorOS;
  if (!os || typeof PH === 'undefined') return;
  const route = () => (location.hash.replace(/^#/, '') || '/').replace(/\/+$/, '') || '/';
  const atHome = () => route() === '/';
  const exit = () => os.closeApp();
  const openDialog = () => document.querySelector('dialog[open]');
  // The app's own page key (Pause, Undo, Add…), shown on the OS's right key when Settings allows
  const keyEl = () => [...document.querySelectorAll('.app-controls .page-keys:not([hidden]) button')].find(b => !b.disabled);

  window.OSChild = {
    atHome: () => atHome() && !openDialog(),
    back: () => { const d = openDialog(); if (d) d.close(); else PH.back(); },
    openMenu: () => { const m = document.getElementById('menu'); if (!m) return; if (m.open) m.close(); else m.showModal(); },
    pageKey: () => { const b = keyEl(); if (!b) return null;
      return { text: b.textContent.trim(), svg: b.querySelector('svg')?.outerHTML || '', label: b.getAttribute('aria-label') || b.textContent.trim(), pressed: b.getAttribute('aria-pressed') }; },
    pressKey: () => { const b = keyEl(); if (b) b.click(); },
  };

  PH.on('route', () => { os.saveRoute(route()); os.keysChanged(); });
  // Pause ↔ play and similar key changes happen without a route change
  const ctl = document.querySelector('.app-controls');
  if (ctl) new MutationObserver(() => os.keysChanged()).observe(ctl, { subtree: true, childList: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'aria-pressed', 'disabled'] });

  // Edge swipe right on the app's home page returns to the OS
  let sx = null, sy = 0;
  addEventListener('touchstart', e => { const t = e.touches[0]; sx = t.clientX < 24 ? t.clientX : null; sy = t.clientY; }, { passive: true });
  addEventListener('touchend', e => {
    if (sx === null || !atHome() || openDialog()) return;
    const t = e.changedTouches[0];
    if (t.clientX - sx > 70 && Math.abs(t.clientY - sy) < 60) exit();
    sx = null;
  }, { passive: true });
})();
