"""Build Phosphor OS.
index.html     = src/app.html with the shared PHOSPHOR CSS/JS and src/app.js inlined.
apps/<id>/     = Floyd's other PHOSPHOR tools, bundled so they open inside the OS:
                 copied from their built index.html, minus manifest/icons/service worker
                 (the OS's sw.js caches them), plus src/embed.js for the way back."""
import os, re
B = '/mnt/project-files/brand/'
T = '/mnt/project-files/tools/'
APPS = {'arcade': 'phosphor-arcade', 'workout': 'gymlog', 'kitchen': 'kitchen-log', 'lights': 'kasa-lights', 'pc': 'pc-remote'}

h = open('src/app.html').read()
h = h.replace('/*@@PHOSPHOR_CSS@@*/', open(B + 'phosphor.css').read()).replace('/*@@PHOSPHOR_JS@@*/', open(B + 'phosphor.js').read()).replace('/*@@APP_JS@@*/', open('src/music.js').read() + '\n' + open('src/app.js').read())
open('index.html', 'w').write(h)
print('index.html', len(h))

HEAD = ('<script>/* Phosphor OS: this app runs inside the OS screen, which already clears the notch */'
        '(()=>{if(window.parent===window||!window.parent.PhosphorOS)return;const d=document.documentElement;d.classList.add("os-embed");'
        'for(const k of["top","right","bottom","left"])d.style.setProperty("--safe-"+k,"0px");})();</script>')
# Fill the OS screen: no case, header or keys of its own, no slide between pages (Floyd, OS v1.5)
EMBED_CSS = """<style>/* Phosphor OS */
html.os-embed { --app-gap: 0px !important; --app-h: 100% !important; }
html.os-embed, html.os-embed body { background: var(--bg) !important; height: 100% !important; }
html.os-embed .app { padding: 0 !important; max-width: none; gap: 0; height: 100% !important; min-height: 0 !important; }
html.os-embed .app-header, html.os-embed .app-controls { display: none !important; }
html.os-embed .screen { margin: 0; border-radius: 0; box-shadow: none; }
html.os-embed .screen::before, html.os-embed .screen::after { display: none; }
html.os-embed .view.enter-fwd, html.os-embed .view.enter-back { animation: none; }
html.os-embed .screen > .path { padding-right: 84px; cursor: pointer; }
html.os-embed .os-path-cmd { position: absolute; top: 0; right: var(--sp-3); z-index: 2; height: 32px; display: flex; align-items: center; gap: 4px; appearance: none; -webkit-appearance: none; background: none; border: 0; padding: 0 var(--sp-2); color: var(--accent); font-size: var(--fs-xs); letter-spacing: var(--tracking-label); text-transform: uppercase; cursor: pointer; }
html.os-embed .os-path-cmd svg { width: 12px; height: 12px; }
</style>"""
embed = open('src/embed.js').read()
for app_id, src in APPS.items():
    a = open(T + src + '/index.html').read()
    n0 = len(a)
    a = re.sub(r'\s*<link rel="(manifest|apple-touch-icon|icon)"[^>]*>', '', a)
    a = re.sub(r'if \(["\']serviceWorker["\'] in navigator\) navigator\.serviceWorker\.register\([^;]*;', '/* service worker: provided by Phosphor OS */', a)
    a = a.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  ' + HEAD, 1)
    a = a.replace('</head>', EMBED_CSS + '\n</head>', 1)
    i = a.rindex('</body>')
    a = a[:i] + '<script>\n' + embed + '</script>\n' + a[i:]
    assert HEAD in a and 'serviceWorker.register' not in a, app_id
    os.makedirs('apps/' + app_id, exist_ok=True)
    open('apps/' + app_id + '/index.html', 'w').write(a)
    print('apps/' + app_id, n0, '->', len(a))
