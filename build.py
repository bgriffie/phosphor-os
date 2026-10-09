"""Build Phosphor OS.
index.html     = src/app.html with the shared PHOSPHOR CSS/JS and src/app.js inlined.
apps/<id>/     = Floyd's other PHOSPHOR tools, bundled so they open inside the OS:
                 copied from their built index.html, minus manifest/icons/service worker
                 (the OS's sw.js caches them), plus src/embed.js for the way back."""
import os, re
B = '/mnt/project-files/brand/'
T = '/mnt/project-files/tools/'
APPS = {'arcade': 'phosphor-arcade', 'workout': 'gymlog', 'kitchen': 'kitchen-log'}

h = open('src/app.html').read()
h = h.replace('/*@@PHOSPHOR_CSS@@*/', open(B + 'phosphor.css').read()).replace('/*@@PHOSPHOR_JS@@*/', open(B + 'phosphor.js').read()).replace('/*@@APP_JS@@*/', open('src/music.js').read() + '\n' + open('src/app.js').read())
open('index.html', 'w').write(h)
print('index.html', len(h))

HEAD = ('<script>/* Phosphor OS: iframes report no safe areas on iOS, so use the OS\'s */'
        '(()=>{const os=window.parent!==window&&window.parent.PhosphorOS;if(!os)return;'
        'const i=os.insets(),s=document.documentElement.style;for(const k in i)s.setProperty("--safe-"+k,i[k]+"px");})();</script>')
embed = open('src/embed.js').read()
for app_id, src in APPS.items():
    a = open(T + src + '/index.html').read()
    n0 = len(a)
    a = re.sub(r'\s*<link rel="(manifest|apple-touch-icon|icon)"[^>]*>', '', a)
    a = re.sub(r'if \(["\']serviceWorker["\'] in navigator\) navigator\.serviceWorker\.register\([^;]*;', '/* service worker: provided by Phosphor OS */', a)
    a = a.replace('<meta charset="utf-8">', '<meta charset="utf-8">\n  ' + HEAD, 1)
    i = a.rindex('</body>')
    a = a[:i] + '<script>\n' + embed + '</script>\n' + a[i:]
    assert HEAD in a and 'serviceWorker.register' not in a, app_id
    os.makedirs('apps/' + app_id, exist_ok=True)
    open('apps/' + app_id + '/index.html', 'w').write(a)
    print('apps/' + app_id, n0, '->', len(a))
