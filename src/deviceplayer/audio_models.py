from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


AudioSourceType = Literal["none", "file", "stream", "tts"]
AudioState = Literal["idle", "playing", "paused", "stopped", "error"]
AudioOutput = Literal["local", "bluetooth"]


@dataclass
class AudioCapabilities:
    file: bool = True
    stream: bool = True
    tts: bool = False
    bluetooth: bool = False
    spotify: bool = False

    def as_dict(self) -> dict[str, bool]:
        return {
            "file": self.file,
            "stream": self.stream,
            "tts": self.tts,
            "bluetooth": self.bluetooth,
            "spotify": self.spotify,
        }


@dataclass
class AudioStatus:
    state: AudioState = "idle"
    source_type: AudioSourceType = "none"
    source: str = ""
    output: AudioOutput = "local"
    volume: int = 65
    backend: str = ""
    pid: int = 0
    last_error: str = ""
    capabilities: AudioCapabilities = field(default_factory=AudioCapabilities)

    def as_dict(self) -> dict:
        return {
            "state": self.state,
            "source_type": self.source_type,
            "source": self.source,
            "output": self.output,
            "volume": self.volume,
            "backend": self.backend,
            "pid": self.pid,
            "last_error": self.last_error,
            "capabilities": self.capabilities.as_dict(),
        }

