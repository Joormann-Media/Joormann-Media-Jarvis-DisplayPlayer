from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable

from .audio_manager import AudioManager
from .player_status import PlayerRuntimeStatus


def _json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


class _Handler(BaseHTTPRequestHandler):
    server: "_ControlApiServer"

    def _send(self, status: int, payload: dict) -> None:
        raw = _json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def _read_json(self) -> dict:
        try:
            size = int(self.headers.get("Content-Length", "0"))
        except Exception:
            size = 0
        if size <= 0:
            return {}
        body = self.rfile.read(size)
        try:
            payload = json.loads(body.decode("utf-8"))
        except Exception:
            return {}
        return payload if isinstance(payload, dict) else {}

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/health":
            payload = self.server.build_health()
            self._send(HTTPStatus.OK, payload)
            return
        if self.path == "/player/status":
            payload = self.server.build_player_status()
            self._send(HTTPStatus.OK, payload)
            return
        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def do_POST(self) -> None:  # noqa: N802
        data = self._read_json()
        if self.path == "/player/play-file":
            result = self.server.audio.play_file(path=str(data.get("path") or ""), output=str(data.get("output") or "local"))
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send(status, result)
            return
        if self.path == "/player/play-stream":
            result = self.server.audio.play_stream(url=str(data.get("url") or ""), output=str(data.get("output") or "local"))
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send(status, result)
            return
        if self.path == "/player/stop":
            self._send(HTTPStatus.OK, self.server.audio.stop())
            return
        if self.path == "/player/pause":
            result = self.server.audio.pause()
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send(status, result)
            return
        if self.path == "/player/resume":
            result = self.server.audio.resume()
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send(status, result)
            return
        if self.path == "/player/volume":
            if "volume" not in data:
                self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "missing volume"})
                return
            try:
                volume = int(data.get("volume"))
            except Exception:
                self._send(HTTPStatus.BAD_REQUEST, {"ok": False, "error": "volume must be integer"})
                return
            result = self.server.audio.set_volume(volume)
            status = HTTPStatus.OK if result.get("ok") else HTTPStatus.BAD_REQUEST
            self._send(status, result)
            return
        self._send(HTTPStatus.NOT_FOUND, {"ok": False, "error": "not_found"})

    def log_message(self, fmt: str, *args) -> None:
        self.server.log.debug("control-api %s - " + fmt, self.address_string(), *args)


class _ControlApiServer(ThreadingHTTPServer):
    def __init__(
        self,
        host: str,
        port: int,
        *,
        audio: AudioManager,
        runtime_status: PlayerRuntimeStatus,
        log: logging.Logger,
    ):
        super().__init__((host, port), _Handler)
        self.audio = audio
        self.runtime_status = runtime_status
        self.log = log

    def build_health(self) -> dict:
        st = self.audio.status()
        audio_ok = st.get("state") != "error"
        return self.runtime_status.health(audio_ok=audio_ok).as_dict()

    def build_player_status(self) -> dict:
        health = self.build_health()
        return {
            "ok": True,
            **self.audio.status(),
            "health": health,
            "runtime": self.runtime_status.runtime_meta(),
        }


class PlayerControlApi:
    def __init__(
        self,
        *,
        bind_host: str,
        bind_port: int,
        audio: AudioManager,
        runtime_status: PlayerRuntimeStatus,
        logger: logging.Logger,
    ):
        self.log = logger
        self._server = _ControlApiServer(bind_host, bind_port, audio=audio, runtime_status=runtime_status, log=logger)
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True, name="deviceplayer-control-api")
        self._thread.start()
        host, port = self._server.server_address
        self.log.info("control API listening on http://%s:%s", host, port)

    def stop(self) -> None:
        if self._thread is None:
            return
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=3.0)
        self._thread = None

