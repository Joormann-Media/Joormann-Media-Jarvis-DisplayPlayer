from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass


class AudioBackendError(Exception):
    pass


@dataclass(frozen=True)
class AudioBackend:
    name: str
    binary: str

    def build_command(self, source: str, source_type: str, volume: int) -> list[str]:
        if self.name == "cvlc":
            return [
                self.binary,
                "--intf",
                "dummy",
                "--no-video",
                "--play-and-exit",
                "--quiet",
                source,
            ]
        if self.name == "ffplay":
            return [
                self.binary,
                "-nodisp",
                "-autoexit",
                "-loglevel",
                "error",
                "-volume",
                str(max(0, min(100, int(volume)))),
                source,
            ]
        if self.name == "mpg123":
            return [self.binary, "-q", source]
        raise AudioBackendError(f"unsupported backend: {self.name}")


def discover_audio_backend() -> AudioBackend:
    candidates = ("cvlc", "ffplay", "mpg123")
    for name in candidates:
        binary = shutil.which(name)
        if binary:
            return AudioBackend(name=name, binary=binary)
    raise AudioBackendError("No supported audio backend found (need one of: cvlc, ffplay, mpg123)")


def apply_system_volume(volume: int) -> tuple[bool, str]:
    target = max(0, min(100, int(volume)))

    amixer = shutil.which("amixer")
    if amixer:
        proc = subprocess.run(
            [amixer, "sset", "Master", f"{target}%"],
            capture_output=True,
            text=True,
            timeout=6,
        )
        if proc.returncode == 0:
            return True, ""
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, f"amixer failed: {detail}"

    pactl = shutil.which("pactl")
    if pactl:
        proc = subprocess.run(
            [pactl, "set-sink-volume", "@DEFAULT_SINK@", f"{target}%"],
            capture_output=True,
            text=True,
            timeout=6,
        )
        if proc.returncode == 0:
            return True, ""
        detail = (proc.stderr or proc.stdout or "").strip()
        return False, f"pactl failed: {detail}"

    return False, "no volume command available (amixer/pactl missing)"

