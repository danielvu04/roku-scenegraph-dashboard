# roku-couch bridge + phone remote

A tiny Python server (standard library only, Python 3.11+) that runs on an
always-on computer on your home network (laptop, Raspberry Pi, NAS) and

- serves a phone remote web app (`static/`) you can add to your home screen, and
- forwards its button presses to your Roku over ECP (`http://<roku>:8060`).

The bridge exists because a web page on your phone can't talk to the Roku
directly (no CORS, mixed content).

## Run it

```sh
cd bridge
python3 couch_bridge.py
```

It prints something like:

```
Open the remote on your phone (same Wi-Fi):
    http://192.168.1.20:8787/
Roku: discovering via SSDP...
```

Open that URL on your phone. To install it like an app:

- **iPhone (Safari):** Share › Add to Home Screen.
- **Android (Chrome):** ⋮ menu › Add to Home screen / Install app.

### Options

| Flag | Meaning |
| --- | --- |
| `--roku-ip 192.168.1.50` | Use this Roku and skip SSDP discovery (`ip` or `ip:port`). |
| `--port 8787` | Port to listen on (default: `bridge_port` from config, else 8787). |
| `--config PATH` | Config file. Default: `../config.json`, then `../config.example.json`, then built-in defaults. |
| `--host 0.0.0.0` | Bind address. |
| `-v` | Debug logging. |

Config fields the bridge uses (see `../config.example.json`):

- `roku_ip`: empty means auto-discover via SSDP. Set it if discovery doesn't work for you.
- `bridge_port`: the port to listen on.
- `favorites`: Roku app IDs to show first in the app grid, e.g. `["12", "837"]`
  (Netflix, YouTube). To find an app's ID, open `/api/apps` in a browser.

The **Dashboard** button opens the sideloaded roku-couch channel (`/launch/dev`).

## Run it as a service (Raspberry Pi / Linux)

`couch-bridge.service` is an example systemd unit. Edit `User=` and the paths, then:

```sh
sudo cp couch-bridge.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now couch-bridge
journalctl -u couch-bridge -f
```

## Security warning

**There is no authentication.** Anyone who can reach the bridge can control
your Roku: change channels, type text, launch apps, turn it off.

- Run it on your home LAN only.
- **Do not port-forward it** or expose it to the internet in any other way (and don't
  put it behind a public reverse proxy).
- Put visitors on a **guest Wi-Fi network** that can't reach your main LAN if you
  don't want them using the remote.

Roku's ECP has no authentication either, so anything on your LAN can already
control the Roku. The bridge doesn't make that worse as long as it stays on the LAN.

## API

All responses are JSON. Errors look like `{"error": "...", "hint": "..."}`.

| Method & path | What it does |
| --- | --- |
| `GET /api/status` | `{roku_ip, reachable, device: {name, model, power_mode}}`. Always 200; when the Roku can't be reached it returns `reachable: false` plus `error` and `hint`. |
| `POST /api/key/{key}` | Sends a keypress. Allowed keys: Home, Back, Select, Up, Down, Left, Right, Play, Rev, Fwd, InstantReplay, Info, Search, Enter, Backspace, VolumeUp, VolumeDown, VolumeMute, PowerOff, PowerOn, Power. Any other key returns 400. |
| `POST /api/text` `{"text": "..."}` | Types text: one `Lit_<char>` keypress per character (max 500 characters). |
| `GET /api/apps` | `{apps: [{id, name, type, version, favorite}]}`, favorites first. |
| `GET /api/apps/{id}/icon` | The app icon, proxied and cached in memory. |
| `POST /api/launch/{id}` `{"contentId"?, "mediaType"?}` | Launches an app, optionally deep-linking into content. |
| `POST /api/dashboard` | Launches the sideloaded dev channel. |
| `POST /api/search` `{"keyword", "providerId"?, "launch"?}` | Roku global search (`/search/browse`). |
| `GET /api/active-app` | `{app: {...}, screensaver: {...} or null}`. |

Status codes: 400 for bad input, 404 for an unknown route, 405 for the wrong
method, and 502 when the Roku is unreachable or ECP returns an error. The 502
body includes `hint`, and `ecp_status` when the Roku answered with an error code.

## Troubleshooting

- **The remote shows "403" or keys do nothing, with a hint about Permissive.** Newer
  Roku OS versions limit ECP. On the Roku, go to **Settings › System › Advanced system
  settings › Control by mobile apps › Network access** and set it to **Permissive**.
- **"No Roku found via SSDP discovery."** Some routers, mesh systems and Wi-Fi
  "client isolation" settings block multicast. Find the Roku's IP under **Settings ›
  Network › About**, then set `roku_ip` in `config.json` or pass `--roku-ip`.
- **It worked yesterday but now says unreachable.** The Roku probably got a new IP.
  Give it a **DHCP reservation** (static lease) in your router. A discovered IP is
  re-discovered on its own when it stops answering, but a configured `roku_ip` is not.
- **Volume buttons do nothing.** They only work when the Roku controls the TV's
  volume over HDMI-CEC or IR, which a Roku Express usually can't do.
- **The phone can't open the URL.** Make sure the phone is on the same network (not
  guest Wi-Fi or mobile data), and check that the computer's firewall allows the port
  (for example `sudo ufw allow 8787/tcp`).
- **Power does nothing.** Streaming sticks and boxes may ignore `Power`/`PowerOff`.
  Roku TVs support them.

## Development

```sh
cd bridge
python3 -m unittest -v          # bridge tests against a fake ECP server
node --check static/app.js      # syntax-check the remote UI
python3 tools/make_icons.py     # regenerate PNG icons (needs Pillow; dev-only)
```
