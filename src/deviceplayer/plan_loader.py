from __future__ import annotations

import json
from pathlib import Path


class ManifestError(RuntimeError):
    pass


def _as_dict(value, field: str) -> dict:
    if not isinstance(value, dict):
        raise ManifestError(f'{field} must be an object')
    return value


def _as_list(value, field: str) -> list:
    if not isinstance(value, list):
        raise ManifestError(f'{field} must be a list')
    return value


def _normalize_transition_obj(value, fallback_type: str, fallback_ms: int) -> dict:
    obj = value if isinstance(value, dict) else {}
    t = str(obj.get('type') or fallback_type).strip().lower()
    ms = max(0, int(obj.get('ms') or fallback_ms))
    return {'type': t, 'ms': ms}


def load_manifest(path: Path) -> dict:
    if not path.exists():
        raise ManifestError(f'manifest not found: {path}')

    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except Exception as exc:
        raise ManifestError(f'invalid JSON: {exc}') from exc

    data = _as_dict(raw, 'manifest')
    layout = _as_dict(data.get('layout'), 'layout')
    defaults = _as_dict(data.get('defaults'), 'defaults')
    assets = _as_dict(data.get('assets'), 'assets')
    playlist = _as_list(data.get('playlist'), 'playlist')

    mode = str(layout.get('mode') or '').strip().lower()
    if mode not in {'full', 'split'}:
        raise ManifestError('layout.mode must be full or split')

    direction = str(layout.get('direction') or 'horizontal').strip().lower()
    if direction not in {'vertical', 'horizontal'}:
        direction = 'horizontal'

    ratio_a = int(layout.get('ratioA') or 50)
    ratio_a = max(1, min(99, ratio_a))

    duration_ms = int(defaults.get('durationMs') or 10000)
    duration_ms = max(1000, duration_ms)

    transition_obj = defaults.get('transition') if isinstance(defaults.get('transition'), dict) else {}
    transition_type = str(transition_obj.get('type') or 'none').strip().lower()
    transition_ms = int(transition_obj.get('ms') or 0)
    transition_ms = max(0, transition_ms)

    normalized_playlist: list[dict] = []
    for idx, item in enumerate(playlist):
        row = _as_dict(item, f'playlist[{idx}]')
        row_duration = int(row.get('durationMs') or duration_ms)
        row_duration = max(1000, row_duration)

        row_transition = _normalize_transition_obj(row.get('transition'), transition_type, transition_ms)

        if mode == 'full':
            asset_key = str(row.get('asset') or '').strip()
            if not asset_key:
                raise ManifestError(f'playlist[{idx}].asset missing for full mode')
            normalized_playlist.append({
                'asset': asset_key,
                'title': str(row.get('title') or ''),
                'durationMs': row_duration,
                'transition': row_transition,
            })
        else:
            zones = _as_dict(row.get('zones'), f'playlist[{idx}].zones')
            zone_a = _as_dict(zones.get('A') or {}, f'playlist[{idx}].zones.A')
            zone_b = _as_dict(zones.get('B') or {}, f'playlist[{idx}].zones.B')
            asset_a = str(zone_a.get('asset') or '').strip()
            asset_b = str(zone_b.get('asset') or '').strip()
            if not asset_a and not asset_b:
                raise ManifestError(f'playlist[{idx}] requires zones.A.asset or zones.B.asset')
            zone_a_transition = _normalize_transition_obj(zone_a.get('transition'), row_transition['type'], row_transition['ms'])
            zone_b_transition = _normalize_transition_obj(zone_b.get('transition'), row_transition['type'], row_transition['ms'])
            normalized_playlist.append({
                'zones': {
                    'A': {'asset': asset_a, 'title': str(zone_a.get('title') or ''), 'transition': zone_a_transition},
                    'B': {'asset': asset_b, 'title': str(zone_b.get('title') or ''), 'transition': zone_b_transition},
                },
                'durationMs': row_duration,
                'transition': row_transition,
            })

    for key, value in assets.items():
        if not str(key).strip() or not str(value).strip():
            raise ManifestError('assets must map non-empty keys to non-empty paths')

    return {
        'layout': {
            'mode': mode,
            # Orientation is pre-applied in admin/runtime assets; player always renders landscape.
            'orientation': 'landscape',
            'direction': direction,
            'ratioA': ratio_a,
        },
        'defaults': {
            'durationMs': duration_ms,
            'transition': {'type': transition_type, 'ms': transition_ms},
        },
        'assets': {str(k): str(v) for k, v in assets.items()},
        'playlist': normalized_playlist,
        'version': str(data.get('version') or ''),
    }
