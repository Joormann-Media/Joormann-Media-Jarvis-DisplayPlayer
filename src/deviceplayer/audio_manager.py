from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
from pathlib import Path
from urllib.parse import urlparse

from .audio_backend import AudioBackendError, apply_system_volume, discover_audio_backend
from .audio_models import AudioStatus


class AudioManager:
    def __init__(
        self,
        *,
        default_volume: int = 65,
        default_output: str = "local",
        audio_root: Path | None = None,
        logger: logging.Logger | None = None,
    ):
        self.log = logger or logging.getLogger("deviceplayer")
        self.audio_root = audio_root.resolve() if isinstance(audio_root, Path) else None
        self._lock = threading.RLock()
        self._proc: subprocess.Popen | None = None
        self._status = AudioStatus(
            volume=max(0, min(100, int(default_volume))),
            output="local" if str(default_output).strip().lower() != "bluetooth" else "bluetooth",
        )
        self._backend = None
        self._shutdown = False

        try:
            self._backend = discover_audio_backend()
            self._status.backend = self._backend.name
            self.log.info("audio backend selected: %s (%s)", self._backend.name, self._backend.binary)
        except AudioBackendError as exc:
            self._status.state = "error"
            self._status.last_error = str(exc)
            self.log.warning("audio backend unavailable: %s", exc)

        ok, detail = apply_system_volume(self._status.volume)
        if not ok and detail:
            self.log.warning("initial volume apply failed: %s", detail)

    def shutdown(self) -> None:
        with self._lock:
            self._shutdown = True
        self.stop()

    def capabilities(self) -> dict[str, bool]:
        return self._status.capabilities.as_dict()

    def status(self) -> dict:
        with self._lock:
            self._sync_process_state_locked()
            return self._status.as_dict()

    def set_volume(self, volume: int) -> dict:
        with self._lock:
            self._status.volume = max(0, min(100, int(volume)))
            ok, detail = apply_system_volume(self._status.volume)
            if not ok:
                self._status.last_error = detail
                self.log.warning("volume set warning: %s", detail)
            else:
                self._status.last_error = ""
            return {
                "ok": ok,
                "volume": self._status.volume,
                "warning": "" if ok else detail,
                "status": self._status.as_dict(),
            }

    def play_file(self, path: str, output: str = "local") -> dict:
        source = str(path or "").strip()
        if not source:
            return self._error("missing file path")
        try:
            resolved = self._validate_file_path(source)
        except ValueError as exc:
            return self._error(str(exc))
        return self._start_playback(source=str(resolved), source_type="file", output=output)

    def play_stream(self, url: str, output: str = "local") -> dict:
        source = str(url or "").strip()
        if not source:
            return self._error("missing stream url")
        if not self._is_valid_stream_url(source):
            return self._error("invalid stream url (need http/https)")
        return self._start_playback(source=source, source_type="stream", output=output)

    def stop(self) -> dict:
        with self._lock:
            self._stop_process_locked()
            self._status.state = "stopped"
            self._status.source = ""
            self._status.source_type = "none"
            self._status.pid = 0
            return {"ok": True, "status": self._status.as_dict()}

    def pause(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return self._error("no active playback")
            if self._status.state == "paused":
                return {"ok": True, "status": self._status.as_dict()}
            try:
                os.kill(proc.pid, signal.SIGSTOP)
                self._status.state = "paused"
                return {"ok": True, "status": self._status.as_dict()}
            except Exception as exc:
                return self._error(f"pause failed: {exc}")

    def resume(self) -> dict:
        with self._lock:
            proc = self._proc
            if proc is None or proc.poll() is not None:
                return self._error("no active playback")
            try:
                os.kill(proc.pid, signal.SIGCONT)
                self._status.state = "playing"
                return {"ok": True, "status": self._status.as_dict()}
            except Exception as exc:
                return self._error(f"resume failed: {exc}")

    def _start_playback(self, *, source: str, source_type: str, output: str) -> dict:
        with self._lock:
            self._sync_process_state_locked()
            if self._shutdown:
                return self._error("audio manager is shutting down")
            if self._backend is None:
                return self._error("audio backend unavailable")

            output_value = str(output or "local").strip().lower()
            if output_value not in ("local", "bluetooth"):
                output_value = "local"
            if output_value == "bluetooth":
                return self._error("bluetooth output not implemented yet")

            self._stop_process_locked()

            try:
                cmd = self._backend.build_command(source=source, source_type=source_type, volume=self._status.volume)
                child_env = dict(os.environ)
                if child_env.get("SDL_AUDIODRIVER", "").strip().lower() == "dummy":
                    # Player render runtime may use dummy SDL audio, but audio backend must output real sound.
                    child_env.pop("SDL_AUDIODRIVER", None)
                proc = subprocess.Popen(
                    cmd,
                    env=child_env,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.PIPE,
                    text=True,
                )
            except Exception as exc:
                self._status.state = "error"
                self._status.last_error = str(exc)
                self.log.error("audio playback start failed: %s", exc)
                return self._error(f"playback start failed: {exc}")

            self._proc = proc
            self._status.state = "playing"
            self._status.source_type = "file" if source_type == "file" else "stream"
            self._status.source = source
            self._status.output = "local"
            self._status.pid = int(proc.pid or 0)
            self._status.last_error = ""
            self.log.info("audio playback started type=%s source=%s pid=%s", source_type, source, proc.pid)
            return {"ok": True, "status": self._status.as_dict()}

    def _stop_process_locked(self) -> None:
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=4.0)
            except Exception:
                try:
                    proc.kill()
                    proc.wait(timeout=2.0)
                except Exception:
                    pass
        self._proc = None

    def _sync_process_state_locked(self) -> None:
        proc = self._proc
        if proc is None:
            return
        rc = proc.poll()
        if rc is None:
            return
        stderr_text = ""
        try:
            if proc.stderr:
                stderr_text = (proc.stderr.read() or "").strip()
        except Exception:
            stderr_text = ""
        self._proc = None
        self._status.pid = 0
        if self._status.state == "paused":
            self._status.state = "stopped"
        elif rc == 0:
            self._status.state = "stopped"
        else:
            self._status.state = "error"
            detail = stderr_text or f"backend exited with code {rc}"
            self._status.last_error = detail
            self.log.warning("audio backend exited non-zero: %s", detail)

    def _validate_file_path(self, raw: str) -> Path:
        path = Path(raw).expanduser().resolve()
        if not path.exists() or not path.is_file():
            raise ValueError("audio file not found")
        if self.audio_root is not None:
            try:
                path.relative_to(self.audio_root)
            except ValueError as exc:
                raise ValueError(f"audio file must be inside {self.audio_root}") from exc
        return path

    @staticmethod
    def _is_valid_stream_url(url: str) -> bool:
        parsed = urlparse(url)
        return parsed.scheme in ("http", "https") and bool(parsed.netloc)

    def _error(self, message: str) -> dict:
        self.log.warning("audio operation rejected: %s", message)
        return {"ok": False, "error": message, "status": self.status()}
