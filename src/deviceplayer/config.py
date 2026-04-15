from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class PlayerConfig:
    manifest_path: Path
    overlay_state_path: Path
    fullscreen: bool
    window_width: int
    window_height: int
    transition_fps: int
    overlay_fps: int
    idle_sleep_ms: int
    poll_reload_seconds: float
    overlay_poll_seconds: float
    log_level: str
    display_rotation_degrees: int
    control_api_host: str
    control_api_port: int


def _manifest_from_portal_storage_config(config_path_raw: str) -> Path | None:
    config_path = Path(config_path_raw).expanduser()
    try:
        exists = config_path.exists()
    except Exception:
        return None
    if not exists:
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


def _manifest_from_player_source(config_path_raw: str) -> Path | None:
    config_path = Path(config_path_raw).expanduser()
    try:
        exists = config_path.exists()
    except Exception:
        return None
    if not exists:
        return None
    try:
        payload = json.loads(config_path.read_text(encoding='utf-8'))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    manifest_info = payload.get('manifest') if isinstance(payload.get('manifest'), dict) else {}
    manifest_path = str(manifest_info.get('path') or payload.get('manifest_path') or '').strip()
    if not manifest_path:
        return None
    return Path(manifest_path)


def _rotation_from_player_source(config_path_raw: str) -> int | None:
    config_path = Path(config_path_raw).expanduser()
    try:
        exists = config_path.exists()
    except Exception:
        return None
    if not exists:
        return None
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None

    display = payload.get("display") if isinstance(payload.get("display"), dict) else {}
    raw = display.get("rotation_degrees")
    if raw is None:
        primary = display.get("primary_display") if isinstance(display.get("primary_display"), dict) else {}
        raw = primary.get("rotation_degrees")
    if raw is None:
        return None
    try:
        return int(raw)
    except Exception:
        return None


def _portal_data_candidates(filename: str) -> list[str]:
    candidates: list[Path] = []
    env_path = os.getenv('DEVICEPLAYER_PORTAL_PLAYER_SOURCE' if filename == 'player-source.json' else 'DEVICEPLAYER_PORTAL_STORAGE_CONFIG', '').strip()
    if env_path:
        candidates.append(Path(env_path).expanduser())

    home = Path.home()
    candidates.extend(
        [
            home / 'projects' / 'Joormann-Media-Deviceportal' / 'var' / 'data' / filename,
            home / 'projects' / 'Joormann-Media-DevicePortal' / 'var' / 'data' / filename,
            home / 'projects' / 'Joormann-Media-JarvisPortal-Rsp' / 'var' / 'data' / filename,
            Path('/home/djanebmb/projects/Joormann-Media-Deviceportal/var/data') / filename,
            Path('/home/djanebmb/projects/Joormann-Media-JarvisPortal-Rsp/var/data') / filename,
        ]
    )

    uniq: list[str] = []
    seen: set[str] = set()
    for item in candidates:
        key = str(item)
        if key in seen:
            continue
        seen.add(key)
        uniq.append(key)
    return uniq


def _resolve_manifest_path(manifest_path: str | None = None) -> Path:
    if manifest_path:
        return Path(manifest_path).expanduser().resolve()

    # SSOT handover file from DevicePortal.
    for portal_source in _portal_data_candidates('player-source.json'):
        resolved = _manifest_from_player_source(portal_source)
        if resolved is not None:
            return resolved.expanduser().resolve()

    # SSOT: the DevicePortal storage config defines where stream/current lives.
    for portal_storage_cfg in _portal_data_candidates('config-storage.json'):
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
    overlay_explicit = os.getenv('DEVICEPLAYER_OVERLAY_STATE_PATH', '').strip()
    if overlay_explicit:
        overlay_path = Path(overlay_explicit).expanduser().resolve()
    else:
        overlay_path = (path.parent / 'overlay-state.json').resolve()
    fullscreen = os.getenv('DEVICEPLAYER_FULLSCREEN', '1').strip().lower() in {'1', 'true', 'yes', 'on'}
    width = int(os.getenv('DEVICEPLAYER_WIDTH', '1920'))
    height = int(os.getenv('DEVICEPLAYER_HEIGHT', '1080'))
    transition_fps = max(12, min(60, int(os.getenv('DEVICEPLAYER_TRANSITION_FPS', os.getenv('DEVICEPLAYER_FPS', '30')))))
    overlay_fps = max(8, min(60, int(os.getenv('DEVICEPLAYER_OVERLAY_FPS', '24'))))
    idle_sleep_ms = max(20, min(2000, int(os.getenv('DEVICEPLAYER_IDLE_SLEEP_MS', '200'))))
    poll = float(os.getenv('DEVICEPLAYER_RELOAD_POLL_SECONDS', '1.0'))
    overlay_poll = float(os.getenv('DEVICEPLAYER_OVERLAY_RELOAD_POLL_SECONDS', str(poll)))
    level = os.getenv('DEVICEPLAYER_LOG_LEVEL', 'INFO')
    rotation_raw = os.getenv('DEVICEPLAYER_DISPLAY_ROTATION_DEGREES', '').strip()
    rotation_degrees = 0
    if rotation_raw != '':
        try:
            rotation_degrees = int(rotation_raw)
        except Exception:
            rotation_degrees = 0
    else:
        for portal_source in _portal_data_candidates('player-source.json'):
            derived = _rotation_from_player_source(portal_source)
            if derived is not None:
                rotation_degrees = derived
                break

    control_api_host = os.getenv("DEVICEPLAYER_CONTROL_API_HOST", "127.0.0.1").strip() or "127.0.0.1"
    try:
        control_api_port = int(os.getenv("DEVICEPLAYER_CONTROL_API_PORT", "5081"))
    except Exception:
        control_api_port = 5081
    control_api_port = max(1, min(65535, control_api_port))

    return PlayerConfig(
        manifest_path=path,
        overlay_state_path=overlay_path,
        fullscreen=fullscreen,
        window_width=width,
        window_height=height,
        transition_fps=transition_fps,
        overlay_fps=overlay_fps,
        idle_sleep_ms=idle_sleep_ms,
        poll_reload_seconds=max(0.2, poll),
        overlay_poll_seconds=max(0.2, overlay_poll),
        log_level=level,
        display_rotation_degrees=rotation_degrees,
        control_api_host=control_api_host,
        control_api_port=control_api_port,
    )
