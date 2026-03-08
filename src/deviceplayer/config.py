from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlayerConfig:
    manifest_path: Path
    fullscreen: bool
    window_width: int
    window_height: int
    transition_fps: int
    idle_sleep_ms: int
    poll_reload_seconds: float
    log_level: str


def _manifest_from_portal_storage_config(config_path_raw: str) -> Path | None:
    config_path = Path(config_path_raw).expanduser()
    if not config_path.exists():
        return None
    try:
        payload = json.loads(config_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    rel = Path('stream/current/manifest.json')
    internal = payload.get('internal') if isinstance(payload.get('internal'), dict) else {}
    if bool(internal.get('allow_media_storage', True)):
        mount_path = str(internal.get('mount_path') or '').strip()
        if mount_path:
            return Path(mount_path) / rel

    devices = payload.get('devices') if isinstance(payload.get('devices'), list) else []
    for item in devices:
        if not isinstance(item, dict):
            continue
        if not bool(item.get('allow_media_storage', False)):
            continue
        mount_path = str(item.get('mount_path') or '').strip()
        if mount_path:
            return Path(mount_path) / rel
    return None


def _resolve_manifest_path(manifest_path: str | None = None) -> Path:
    if manifest_path:
        return Path(manifest_path).expanduser().resolve()

    # SSOT: the DevicePortal storage config defines where stream/current lives.
    portal_storage_cfg = os.getenv('DEVICEPLAYER_PORTAL_STORAGE_CONFIG', '').strip()
    if portal_storage_cfg:
        resolved = _manifest_from_portal_storage_config(portal_storage_cfg)
        if resolved is not None:
            return resolved.expanduser().resolve()

    storage_root = os.getenv('DEVICEPLAYER_STORAGE_ROOT', '').strip()
    if storage_root:
        return (Path(storage_root).expanduser() / 'stream/current/manifest.json').resolve()

    # Legacy/manual override fallback only if SSOT config is not available.
    explicit = os.getenv('DEVICEPLAYER_MANIFEST_PATH', '').strip()
    if explicit:
        return Path(explicit).expanduser().resolve()

    return Path('/mnt/deviceportal/media/stream/current/manifest.json').resolve()


def build_config(manifest_path: str | None = None) -> PlayerConfig:
    path = _resolve_manifest_path(manifest_path)
    fullscreen = os.getenv('DEVICEPLAYER_FULLSCREEN', '1').strip().lower() in {'1', 'true', 'yes', 'on'}
    width = int(os.getenv('DEVICEPLAYER_WIDTH', '1920'))
    height = int(os.getenv('DEVICEPLAYER_HEIGHT', '1080'))
    transition_fps = max(12, min(60, int(os.getenv('DEVICEPLAYER_TRANSITION_FPS', os.getenv('DEVICEPLAYER_FPS', '30')))))
    idle_sleep_ms = max(20, min(2000, int(os.getenv('DEVICEPLAYER_IDLE_SLEEP_MS', '200'))))
    poll = float(os.getenv('DEVICEPLAYER_RELOAD_POLL_SECONDS', '1.0'))
    level = os.getenv('DEVICEPLAYER_LOG_LEVEL', 'INFO')

    return PlayerConfig(
        manifest_path=path,
        fullscreen=fullscreen,
        window_width=width,
        window_height=height,
        transition_fps=transition_fps,
        idle_sleep_ms=idle_sleep_ms,
        poll_reload_seconds=max(0.2, poll),
        log_level=level,
    )
