# Joormann-Media-DevicePlayer

Eigenständiger HDMI-Player für kompakte Runtime-Manifeste aus dem Adminpanel.

## Unterstütztes Manifest

- `layout.mode`: `full` | `split`
- `layout.orientation`: wird vom Player ignoriert (Assets sind serverseitig bereits vorbereitet)
- `layout.direction`: wird vom Player ignoriert (Split ist fix links/rechts)
- `layout.ratioA`: wird vom Player ignoriert (Split ist fix 50/50)
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

Overlay-State-Auflösung (Reihenfolge):
1. `DEVICEPLAYER_OVERLAY_STATE_PATH`
2. Neben Manifest: `<manifest-dir>/overlay-state.json`

Empfehlung: `DEVICEPLAYER_MANIFEST_PATH` und `DEVICEPLAYER_STORAGE_ROOT` leer lassen,
`DEVICEPLAYER_PORTAL_PLAYER_SOURCE` plus `DEVICEPLAYER_PORTAL_STORAGE_CONFIG` setzen.

Empfohlene Runtime-Parameter (für Signage-Dauerbetrieb):
- `DEVICEPLAYER_TRANSITION_FPS=30`
- `DEVICEPLAYER_OVERLAY_FPS=24`
- `DEVICEPLAYER_IDLE_SLEEP_MS=200`
- `DEVICEPLAYER_RELOAD_POLL_SECONDS=1.0`
- `DEVICEPLAYER_OVERLAY_RELOAD_POLL_SECONDS=1.0`
- optional: `DEVICEPLAYER_VIDEO_DRIVERS=kmsdrm,fbcon,wayland,x11`

## Lokaler Dateiaufbau

Der Player erwartet lokal:
- `manifest.json`
- `assets/<dateien>`
- optional: `overlay-state.json` (separates Runtime-Overlay)

Das Device Portal schreibt atomar:
- `<storage>/stream/staging/build-*/...`
- danach Umschalten auf `<storage>/stream/current/...`
- Overlay separat und atomar:
  - `<storage>/stream/current/overlay-state.json`

## Runtime Overlays

Overlays laufen getrennt vom Manifest und blockieren die Playlist nicht.

Unterstützt:
- `flashMessages`: nacheinander rotierende Flash-Boxen
- `tickers`: dauerhaft scrollende Ticker (top/bottom)
- `popups`: nacheinander rotierende Popups

Dateiformat (`overlay-state.json`, gekürzt):

```json
{
  "updatedAt": "2026-03-10T17:00:00+01:00",
  "flashMessages": [],
  "tickers": [],
  "popups": []
}
```

Hinweise:
- `enabled=false` wird ignoriert
- ungültige Einträge werden defensiv übersprungen
- kaputtes Overlay-JSON stoppt den Player nicht (letzter gültiger Zustand bleibt aktiv)
- Overlay-Reload ist mtime-basiert und unabhängig vom Manifest-Reload

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

## Audio Node (MVP)

Der Player enthält zusätzlich eine lokale Audio-Laufzeit mit Control-API.
Bild/Manifest/Overlay bleiben unabhängig und laufen weiter wie bisher.

Unterstützt:
- lokale Audio-Datei (`mp3`, `wav`, `ogg`, abhängig vom Backend)
- Webstream (`http`/`https`)

Nicht in diesem Schritt:
- Spotify
- Bluetooth-Output-Routing
- TTS-Quelle
- Adminpanel-Integration

### Audio-Abhängigkeiten

Mindestens **eines** der folgenden Backends muss installiert sein:
- `cvlc` (bevorzugt)
- `ffplay`
- `mpg123`

Optional für Lautstärke-Steuerung:
- `amixer` (alsa-utils) oder
- `pactl`

### Control API

Default: `127.0.0.1:5081`

Endpoints:
- `GET /health`
- `GET /player/status`
- `POST /player/play-file`
- `POST /player/play-stream`
- `POST /player/stop`
- `POST /player/pause`
- `POST /player/resume`
- `POST /player/volume`

Beispiel:

```bash
curl -s http://127.0.0.1:5081/health | jq
curl -s http://127.0.0.1:5081/player/status | jq

curl -s -X POST http://127.0.0.1:5081/player/play-file \
  -H 'Content-Type: application/json' \
  -d '{"path":"/home/djanebmb/projects/Joormann-Media-DevicePlayer/runtime/audio/test.mp3"}' | jq

curl -s -X POST http://127.0.0.1:5081/player/play-stream \
  -H 'Content-Type: application/json' \
  -d '{"url":"https://example.com/stream.mp3"}' | jq

curl -s -X POST http://127.0.0.1:5081/player/volume \
  -H 'Content-Type: application/json' \
  -d '{"volume":65}' | jq
```

### Neue Env-Variablen

- `DEVICEPLAYER_CONTROL_API_HOST` (default `127.0.0.1`)
- `DEVICEPLAYER_CONTROL_API_PORT` (default `5081`)
- `DEVICEPLAYER_AUDIO_DEFAULT_OUTPUT` (default `local`)
- `DEVICEPLAYER_AUDIO_DEFAULT_VOLUME` (default `65`)
- `DEVICEPLAYER_AUDIO_ALLOWED_ROOT` (default `<manifest-dir>/audio`)

`play-file` ist auf `DEVICEPLAYER_AUDIO_ALLOWED_ROOT` beschränkt.
