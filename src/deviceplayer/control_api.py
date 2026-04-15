from __future__ import annotations

import json
import logging
import threading
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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
        if self.path.startswith("/player/"):
            self._send(
                HTTPStatus.GONE,
                {
                    "ok": False,
                    "error": "endpoint_removed",
                    "detail": "Audio endpoints were removed from DisplayPlayer.",
                },
            )
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
        runtime_status: PlayerRuntimeStatus,
        log: logging.Logger,
    ):
        super().__init__((host, port), _Handler)
        self.runtime_status = runtime_status
        self.log = log

    def build_health(self) -> dict:
        return self.runtime_status.health().as_dict()

    def build_player_status(self) -> dict:
        health = self.build_health()
        return {
            "ok": True,
            "state": "render-only",
            "health": health,
            "runtime": self.runtime_status.runtime_meta(),
        }


class PlayerControlApi:
    def __init__(
        self,
        *,
        bind_host: str,
        bind_port: int,
        runtime_status: PlayerRuntimeStatus,
        logger: logging.Logger,
    ):
        self.log = logger
        self._server = _ControlApiServer(bind_host, bind_port, runtime_status=runtime_status, log=logger)
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
