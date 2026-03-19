# Audio Node Implementation

## Überblick

Der DevicePlayer wurde um eine lokale Audio-Schicht erweitert, ohne die bestehende Bild-/Manifest-/Overlay-Renderpipeline umzubauen.

Bausteine:
- `audio_backend.py`: Backend-Discovery (`cvlc`, `ffplay`, `mpg123`) + Lautstärke-Helper
- `audio_manager.py`: Playback-Steuerung (play/stop/pause/resume/volume/status)
- `control_api.py`: lokale JSON-HTTP-API
- `player_status.py`: Runtime-Health (Render + Audio)
- Integration in `app.py`: Start/Stop der Audio- und API-Komponenten neben dem Render-Loop

## Designentscheidungen

- Subprocess-basierte Audio-Wiedergabe (robust für headless/systemd)
- Keine Busy-Loops
- Defensive Fehlerbehandlung (ungültige URL/Pfad, fehlendes Backend, Backend-Exit)
- ThreadingHTTPServer für lokale API (klein, stabil, keine zusätzliche Dependency)
- Audio-Output aktuell nur `local`; `bluetooth` als vorbereiteter Platzhalter

## API

- `GET /health`
- `GET /player/status`
- `POST /player/play-file` `{ "path": "..." }`
- `POST /player/play-stream` `{ "url": "..." }`
- `POST /player/stop`
- `POST /player/pause`
- `POST /player/resume`
- `POST /player/volume` `{ "volume": 65 }`

Alle Responses sind JSON.

## Betrieb

Wichtige Env-Variablen:
- `DEVICEPLAYER_CONTROL_API_HOST`
- `DEVICEPLAYER_CONTROL_API_PORT`
- `DEVICEPLAYER_AUDIO_DEFAULT_OUTPUT`
- `DEVICEPLAYER_AUDIO_DEFAULT_VOLUME`
- `DEVICEPLAYER_AUDIO_ALLOWED_ROOT`

Datei-Wiedergabe ist auf `DEVICEPLAYER_AUDIO_ALLOWED_ROOT` begrenzt.

## Abhängigkeiten

Audio-Backend (mind. eins):
- `cvlc` oder
- `ffplay` oder
- `mpg123`

Optional für Lautstärke:
- `amixer` oder
- `pactl`

## Lokaltest (kurz)

```bash
curl -s http://127.0.0.1:5081/health | jq
curl -s http://127.0.0.1:5081/player/status | jq
curl -s -X POST http://127.0.0.1:5081/player/play-file -H 'Content-Type: application/json' -d '{"path":"/home/djanebmb/projects/Joormann-Media-DevicePlayer/runtime/audio/test.mp3"}' | jq
curl -s -X POST http://127.0.0.1:5081/player/pause | jq
curl -s -X POST http://127.0.0.1:5081/player/resume | jq
curl -s -X POST http://127.0.0.1:5081/player/stop | jq
```

## Bewusst offen für spätere Schritte

- Spotify Connect
- Bluetooth-Gerätemanagement / echtes Output-Routing
- TTS-Quelle
- Adminpanel-Steuerintegration
