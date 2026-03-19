from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone


def _ts() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass
class RuntimeHealthSnapshot:
    service: str
    status: str
    render_runtime: str
    audio_runtime: str
    player_check_available: bool
    timestamp: str

    def as_dict(self) -> dict:
        return {
            "service": self.service,
            "status": self.status,
            "render_runtime": self.render_runtime,
            "audio_runtime": self.audio_runtime,
            "player_check_available": self.player_check_available,
            "timestamp": self.timestamp,
        }


class PlayerRuntimeStatus:
    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._started_at = time.monotonic()
        self._last_render_tick = 0.0
        self._last_render_error = ""

    def mark_render_tick(self) -> None:
        with self._lock:
            self._last_render_tick = time.monotonic()

    def mark_render_error(self, detail: str) -> None:
        with self._lock:
            self._last_render_error = str(detail or "")

    def health(self, *, audio_ok: bool) -> RuntimeHealthSnapshot:
        with self._lock:
            now = time.monotonic()
            render_alive = (now - self._last_render_tick) < 10.0 if self._last_render_tick > 0 else True
            render_runtime = "ok" if render_alive else "stale"
            if self._last_render_error:
                render_runtime = "error"
            audio_runtime = "ok" if audio_ok else "degraded"
            overall = "healthy" if render_runtime == "ok" and audio_runtime == "ok" else "degraded"
            return RuntimeHealthSnapshot(
                service="deviceplayer",
                status=overall,
                render_runtime=render_runtime,
                audio_runtime=audio_runtime,
                player_check_available=render_runtime == "ok",
                timestamp=_ts(),
            )

    def runtime_meta(self) -> dict:
        with self._lock:
            return {
                "uptime_seconds": int(max(0.0, time.monotonic() - self._started_at)),
                "last_render_error": self._last_render_error,
            }

