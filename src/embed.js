/* Phosphor OS embed shim: injected at the end of each bundled app's <body>.
   The app runs full-screen in an iframe over the OS, keeps its own data (same origin,
   same localStorage keys as the standalone version) and gets a way back to the OS. */
(() => {
  const os = window.parent !== window && window.parent.PhosphorOS;
  if (!os || typeof PH === 'undefined') return;
  const route = () => (location.hash.replace(/^#/, '') || '/').replace(/\/+$/, '') || '/';
  const atHome = () => route() === '/';
  const exit = () => os.closeApp();

  // Back on the app's home page returns to the OS instead of sitting disabled
  const fixBack = () => document.querySelectorAll('.app-controls [data-back]').forEach(b => {
    if (atHome()) { b.disabled = false; b.setAttribute('aria-label', 'Back to Phosphor OS'); }
    else b.setAttribute('aria-label', 'Back');
  });
  PH.on('route', r => { fixBack(); os.saveRoute(route()); });
  fixBack();
  addEventListener('click', e => {
    const b = e.target.closest('.app-controls [data-back]');
    if (b && atHome()) { e.preventDefault(); e.stopPropagation(); exit(); }
  }, true);

  // "Phosphor OS" at the top of the app's ☰ menu
  const sections = document.querySelector('#menu [data-sections]');
  if (sections) {
    const item = document.createElement('button');
    item.className = 'menu-item';
    item.innerHTML = '<span class="mi-glyph" aria-hidden="true">◂</span><span class="mi-label">Phosphor OS</span>';
    item.addEventListener('click', () => { item.closest('dialog')?.close(); exit(); });
    sections.prepend(item);
  }

  // Edge swipe right on the app's home page also returns to the OS
  let sx = null, sy = 0;
  addEventListener('touchstart', e => { const t = e.touches[0]; sx = t.clientX < 24 ? t.clientX : null; sy = t.clientY; }, { passive: true });
  addEventListener('touchend', e => {
    if (sx === null || !atHome() || document.querySelector('dialog[open]')) return;
    const t = e.changedTouches[0];
    if (t.clientX - sx > 70 && Math.abs(t.clientY - sy) < 60) exit();
    sx = null;
  }, { passive: true });
})();
