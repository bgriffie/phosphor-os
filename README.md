# Phosphor OS

A phone-style launcher in the PHOSPHOR theme: a grid of tiles that open iOS apps (app links and Shortcuts), built-in Phone, Notes and Calculator apps, and Floyd's Arcade, Workout Log and Kitchen Log bundled under `apps/` so they open full-screen inside the OS. Works offline.

Live: https://bgriffie.github.io/phosphor-os/

Edit `src/app.html` and `src/app.js`, then run `python3 build.py`: it inlines the shared PHOSPHOR CSS/JS into `index.html` and re-copies the bundled apps from their project sources (adding `src/embed.js`). Bump the cache version in `sw.js` on every change.
