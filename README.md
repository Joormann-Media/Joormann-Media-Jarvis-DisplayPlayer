# Joormann-Media-DevicePlayer

Eigenständiger HDMI-Player für kompakte Runtime-Manifeste aus dem Adminpanel.

## Unterstütztes Manifest

- `layout.mode`: `full` | `split`
- `layout.orientation`: `landscape` | `portrait`
- `layout.direction`: `horizontal` | `vertical` (bei split)
- `layout.ratioA`: 1..99 (bei split)
- `defaults.durationMs`
- `defaults.transition.type`:
  - nativ: `none` | `crossfade` | `slide-left` | `slide-right` | `slide-up` | `slide-down`
  - Aliasse: `fade`, `dissolve`, `cross-fade` -> `crossfade`
- `defaults.transition.ms`
- `assets` Mapping
- `playlist`:
  - full: `[{ "asset": "assetX" }]`
  - split: `[{ "zones": { "A": {"asset":"..."}, "B": {"asset":"..."} } }]`
  - optional bei split pro Zone:
    - `zones.A.transition`: `{ "type": "...", "ms": ... }`
    - `zones.B.transition`: `{ "type": "...", "ms": ... }`
    - falls nicht gesetzt, wird `playlist[].transition` bzw. `defaults.transition` verwendet

## Start

```bash
cd /home/djanebmb/projects/Joormann-Media-DevicePlayer
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
python run.py --manifest runtime/plan.json
```

## Service

```bash
sudo cp systemd/joormann-media-deviceplayer.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now joormann-media-deviceplayer.service
sudo systemctl status joormann-media-deviceplayer.service
```

Manifest-Auflösung (Reihenfolge):
1. `DEVICEPLAYER_PORTAL_PLAYER_SOURCE` (Portal-Handover-Datei mit `manifest.path`)
2. `DEVICEPLAYER_PORTAL_STORAGE_CONFIG` (Storage-SSOT)
3. `DEVICEPLAYER_STORAGE_ROOT`
4. `DEVICEPLAYER_MANIFEST_PATH` (Legacy-Fallback)
5. Fallback: `/mnt/deviceportal/media/stream/current/manifest.json`

Empfehlung: `DEVICEPLAYER_MANIFEST_PATH` und `DEVICEPLAYER_STORAGE_ROOT` leer lassen,
`DEVICEPLAYER_PORTAL_PLAYER_SOURCE` plus `DEVICEPLAYER_PORTAL_STORAGE_CONFIG` setzen.

Empfohlene Runtime-Parameter (für Signage-Dauerbetrieb):
- `DEVICEPLAYER_TRANSITION_FPS=30`
- `DEVICEPLAYER_IDLE_SLEEP_MS=200`
- `DEVICEPLAYER_RELOAD_POLL_SECONDS=1.0`
- optional: `DEVICEPLAYER_VIDEO_DRIVERS=kmsdrm,fbcon,wayland,x11`

## Lokaler Dateiaufbau

Der Player erwartet lokal:
- `manifest.json`
- `assets/<dateien>`

Das Device Portal schreibt atomar:
- `<storage>/stream/staging/build-*/...`
- danach Umschalten auf `<storage>/stream/current/...`

## Performance-Strategie

Der Player ist auf niedrige Last im Dauerbetrieb optimiert:

- Idle statt Dauer-Redraw:
  - Bei statischen Slides wird nicht permanent neu gezeichnet.
  - `blit+flip` passiert nur bei Zustandswechseln (Slide-Wechsel, Reload, Transition-Frame).
  - Zwischenzustände nutzen einen kurzen Idle-Wait statt Busy-Loop.
- Getrennte Render-Modi:
  - Transition aktiv: begrenzte Framerate (`DEVICEPLAYER_TRANSITION_FPS`, default 30).
  - Statische Anzeige: sparsamer Schlafzyklus (`DEVICEPLAYER_IDLE_SLEEP_MS`, default 200ms).
- Caching:
  - Bild-Decoding nur einmal pro Datei (`renderer`-Asset-Cache).
  - Skalierte Teilflächen werden wiederverwendet (`_fit`-Cache).
  - Voll gerenderte Slide-Frames (full/split) werden per Item-Key gecacht und über Playlist-Zyklen wiederverwendet.
- Reload nur bei Änderung:
  - Manifest wird per mtime überwacht.
  - Bei echter Änderung werden Caches gezielt invalidiert und neu aufgebaut.
