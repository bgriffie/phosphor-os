# Phosphor OS lights relay

Lets the **Lights** app in Phosphor OS control your Kasa bulbs and plugs. The relay runs on a
computer at home that stays on and talks to the bulbs over your Wi-Fi. Tailscale gives it a
private, secure address that only your own devices can reach, so the OS can call it from anywhere.

## Set up (Windows PC, about 15 minutes)

1. **Python.** Install it from <https://www.python.org/downloads/>. On the first screen, tick
   **Add python.exe to PATH**.
2. **Tailscale.** Install it on the PC (<https://tailscale.com/download>) and on your iPhone
   (App Store), and sign in to both with the same account. On the iPhone, leave it switched on.
3. **The relay.** Make a folder such as `Documents\PhosphorLights` and save these two files into it:
   - [kasa_bridge.py](https://raw.githubusercontent.com/bgriffie/phosphor-os/main/bridge/kasa_bridge.py)
   - [start-lights.bat](https://raw.githubusercontent.com/bgriffie/phosphor-os/main/bridge/start-lights.bat)

   (Right-click each link, then **Save link as**.)
4. **Secure address.** Open **Command Prompt** and run:

       tailscale serve --bg 7788

   If it shows a link to turn on HTTPS, open it, turn it on, and run the command again.
   You only do this once.
5. **Start.** Double-click `start-lights.bat`. The first time, it asks for your Kasa app login:
   newer bulbs need it, older ones don't (press Enter to skip). It's saved on this PC only, in
   `kasa_bridge.json` next to the script.
6. **Connect the phone.** The window prints a **Lights link** like
   `https://your-pc.tail1234.ts.net#abc123…`. Send it to your phone (email it to yourself), then
   paste it into **Phosphor OS › Lights › Setup** and tap Connect.

### Keep it running
- **Start with Windows:** press Win+R, type `shell:startup`, press Enter, and put a shortcut to
  `start-lights.bat` in the folder that opens.
- **No sleep:** Settings › System › Power › Screen and sleep › set sleep to **Never** when plugged in.

## TV (Phosphor TV on a Chromecast)

1. Install the Chromecast library once: `py -m pip install pychromecast`
2. Start the relay with `py kasa_bridge.py --cast`. Without `--cast`, the TV part stays off.
3. If Windows asks whether Python may use the network, allow **Private networks** only.
4. Open the TV app in Phosphor OS and turn the TV on from there.

## Chore alerts (lock-screen reminders from Chores)

The relay buzzes your iPhone when a chore is due, even when phosphor.os is closed. It needs iOS 16.4 or later and phosphor.os added to the home screen.

1. In the relay folder, run  `py -m pip install pywebpush`
2. Download `chores.py` and the new `kasa_bridge.py` into the relay folder, then restart the relay. It should print "Chore alerts: on".
3. In phosphor.os, open Chores › Settings, turn on Lock-screen alerts, allow notifications, and tap Send a test alert.

The relay keeps its alert keys in `chores.json` and `chores_vapid.pem` next to it. Don't share those files.

## Mac or Raspberry Pi
Same steps, but save [start-lights.sh](https://raw.githubusercontent.com/bgriffie/phosphor-os/main/bridge/start-lights.sh)
instead of the .bat file and run `./start-lights.sh` in Terminal. On a Pi, install Tailscale with
`curl -fsSL https://tailscale.com/install.sh | sh` then `sudo tailscale up`.

## Trouble
- **"Can't reach the lights relay"**: the PC is off or asleep, the relay window was closed, or
  Tailscale is off on the phone.
- **"Rejected the key"**: paste the Lights link again (it changes only if you run
  `kasa_bridge.py --new-key`).
- **A bulb is missing**: make sure the PC is on the same Wi-Fi as the bulbs, then tap
  **Rescan for lights** in Setup. Newer bulbs need your Kasa login: run `kasa_bridge.py --login`.
- **Try the app with no bulbs**: `kasa_bridge.py --demo` shows four pretend lights.
