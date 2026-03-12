from __future__ import annotations

import json
from pathlib import Path

from .overlay_models import EMPTY_OVERLAY_STATE, FlashMessage, OverlayState, PopupMessage, TickerMessage


class OverlayError(RuntimeError):
    pass


def _to_bool(value, default: bool = True) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return int(value) != 0
    if isinstance(value, str):
        return value.strip().lower() in {"1", "true", "yes", "on"}
    return default


def _to_int(value, default: int, minimum: int, maximum: int) -> int:
    try:
        num = int(value)
    except Exception:
        num = default
    return max(minimum, min(maximum, num))


def _to_float(value, default: float, minimum: float, maximum: float) -> float:
    try:
        num = float(value)
    except Exception:
        num = default
    return max(minimum, min(maximum, num))


def _to_str(value, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _color(value: str, fallback: str) -> str:
    raw = _to_str(value, fallback)
    if len(raw) == 7 and raw.startswith("#"):
        hex_part = raw[1:]
        if all(ch in "0123456789abcdefABCDEF" for ch in hex_part):
            return raw
    return fallback


def _norm_position(value: str, allowed: set[str], fallback: str) -> str:
    pos = _to_str(value, fallback).lower()
    if pos in allowed:
        return pos
    return fallback


def _parse_flash(row: dict, idx: int) -> FlashMessage | None:
    title = _to_str(row.get("title"), "")
    message = _to_str(row.get("message"), "")
    if not title and not message:
        return None
    return FlashMessage(
        id=_to_str(row.get("id"), f"flash-{idx}"),
        enabled=_to_bool(row.get("enabled"), True),
        title=title,
        message=message,
        duration_ms=_to_int(row.get("durationMs"), 5000, 500, 120000),
        position=_norm_position(_to_str(row.get("position"), "top"), {"top", "center", "bottom"}, "top"),
        rotation=_to_int(row.get("rotation"), 0, -360, 360),
        background_color=_color(_to_str(row.get("backgroundColor"), "#111111"), "#111111"),
        text_color=_color(_to_str(row.get("textColor"), "#ffffff"), "#ffffff"),
        accent_color=_color(_to_str(row.get("accentColor"), "#0d6efd"), "#0d6efd"),
        font_size=_to_int(row.get("fontSize"), 32, 12, 140),
        padding=_to_int(row.get("padding"), 24, 0, 240),
        opacity=_to_float(row.get("opacity"), 0.95, 0.05, 1.0),
    )


def _parse_ticker(row: dict, idx: int) -> TickerMessage | None:
    text = _to_str(row.get("text"), "")
    if not text:
        return None
    return TickerMessage(
        id=_to_str(row.get("id"), f"ticker-{idx}"),
        enabled=_to_bool(row.get("enabled"), True),
        text=text,
        position=_norm_position(_to_str(row.get("position"), "bottom"), {"top", "bottom"}, "bottom"),
        rotation=_to_int(row.get("rotation"), 0, -360, 360),
        speed_px_per_second=_to_float(row.get("speedPxPerSecond"), 120.0, 10.0, 800.0),
        height=_to_int(row.get("height"), 72, 24, 320),
        padding_x=_to_int(row.get("paddingX"), 24, 0, 240),
        background_color=_color(_to_str(row.get("backgroundColor"), "#000000"), "#000000"),
        text_color=_color(_to_str(row.get("textColor"), "#ffffff"), "#ffffff"),
        font_size=_to_int(row.get("fontSize"), 34, 12, 160),
        opacity=_to_float(row.get("opacity"), 0.9, 0.05, 1.0),
    )


def _parse_popup(row: dict, idx: int) -> PopupMessage | None:
    title = _to_str(row.get("title"), "")
    message = _to_str(row.get("message"), "")
    image = _to_str(row.get("imagePath"), "")
    if not image:
        image = _to_str(row.get("imageUrl"), "")
    if not image:
        image = _to_str(row.get("imageData"), "")
    if not title and not message and not image:
        return None
    return PopupMessage(
        id=_to_str(row.get("id"), f"popup-{idx}"),
        enabled=_to_bool(row.get("enabled"), True),
        title=title,
        message=message,
        duration_ms=_to_int(row.get("durationMs"), 8000, 500, 120000),
        position=_norm_position(
            _to_str(row.get("position"), "center"),
            {"center", "top-left", "top-right", "bottom-left", "bottom-right"},
            "center",
        ),
        image_path=image,
        background_color=_color(_to_str(row.get("backgroundColor"), "#ffffff"), "#ffffff"),
        text_color=_color(_to_str(row.get("textColor"), "#111111"), "#111111"),
        accent_color=_color(_to_str(row.get("accentColor"), "#dc3545"), "#dc3545"),
        width=_to_int(row.get("width"), 800, 180, 3840),
        height=_to_int(row.get("height"), 420, 120, 2160),
        padding=_to_int(row.get("padding"), 24, 0, 240),
        opacity=_to_float(row.get("opacity"), 1.0, 0.05, 1.0),
    )


def load_overlay_state(path: Path) -> OverlayState:
    if not path.exists():
        return EMPTY_OVERLAY_STATE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise OverlayError(f"invalid overlay json: {exc}") from exc

    if not isinstance(raw, dict):
        raise OverlayError("overlay json must be an object")

    flashes: list[FlashMessage] = []
    for idx, row in enumerate(raw.get("flashMessages") if isinstance(raw.get("flashMessages"), list) else []):
        if not isinstance(row, dict):
            continue
        parsed = _parse_flash(row, idx)
        if parsed is not None and parsed.enabled:
            flashes.append(parsed)

    tickers: list[TickerMessage] = []
    for idx, row in enumerate(raw.get("tickers") if isinstance(raw.get("tickers"), list) else []):
        if not isinstance(row, dict):
            continue
        parsed = _parse_ticker(row, idx)
        if parsed is not None and parsed.enabled:
            tickers.append(parsed)

    popups: list[PopupMessage] = []
    for idx, row in enumerate(raw.get("popups") if isinstance(raw.get("popups"), list) else []):
        if not isinstance(row, dict):
            continue
        parsed = _parse_popup(row, idx)
        if parsed is not None and parsed.enabled:
            popups.append(parsed)

    return OverlayState(
        updated_at=_to_str(raw.get("updatedAt"), ""),
        flash_messages=tuple(flashes),
        tickers=tuple(tickers),
        popups=tuple(popups),
    )
