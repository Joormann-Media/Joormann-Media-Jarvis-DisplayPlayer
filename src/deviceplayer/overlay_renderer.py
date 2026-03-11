from __future__ import annotations

from pathlib import Path

import pygame

from .overlay_runtime import ActiveTicker, OverlayFrame


class OverlayRenderer:
    def __init__(self, screen_size: tuple[int, int]):
        self.screen_w, self.screen_h = screen_size
        self._font_cache: dict[tuple[str, int, bool], pygame.font.Font] = {}
        self._text_cache: dict[tuple[str, int, tuple[int, int, int], bool], pygame.Surface] = {}
        self._image_cache: dict[str, pygame.Surface] = {}

    def clear_caches(self) -> None:
        self._font_cache.clear()
        self._text_cache.clear()
        self._image_cache.clear()

    def _hex_to_rgb(self, raw: str, fallback: tuple[int, int, int]) -> tuple[int, int, int]:
        text = str(raw or "").strip()
        if len(text) == 7 and text.startswith("#"):
            try:
                return int(text[1:3], 16), int(text[3:5], 16), int(text[5:7], 16)
            except Exception:
                return fallback
        return fallback

    def _font(self, size: int, bold: bool = False) -> pygame.font.Font:
        key = ("default", max(12, int(size)), bold)
        cached = self._font_cache.get(key)
        if cached is not None:
            return cached
        font = pygame.font.SysFont("DejaVu Sans", key[1], bold=bold)
        self._font_cache[key] = font
        return font

    def _text(self, content: str, size: int, color: tuple[int, int, int], bold: bool = False) -> pygame.Surface:
        key = (content, max(12, int(size)), color, bold)
        cached = self._text_cache.get(key)
        if cached is not None:
            return cached
        surface = self._font(size, bold=bold).render(content, True, color)
        self._text_cache[key] = surface
        return surface

    def _set_alpha_fill(self, target: pygame.Surface, rect: pygame.Rect, color: tuple[int, int, int], opacity: float) -> None:
        alpha = max(0, min(255, int(float(opacity) * 255)))
        box = pygame.Surface((max(1, rect.width), max(1, rect.height)), pygame.SRCALPHA)
        box.fill((color[0], color[1], color[2], alpha))
        target.blit(box, rect.topleft)

    def _normalize_rotation(self, value: int) -> int:
        try:
            raw = int(value)
        except Exception:
            return 0
        raw %= 360
        if raw < 0:
            raw += 360
        if raw >= 315 or raw < 45:
            return 0
        if raw >= 45 and raw < 135:
            return 90
        if raw >= 135 and raw < 225:
            return 180
        return 270

    def _draw_flash(self, surface: pygame.Surface, flash) -> None:
        title = str(flash.title or "").strip()
        message = str(flash.message or "").strip()
        if not title and not message:
            return

        bg = self._hex_to_rgb(flash.background_color, (17, 17, 17))
        fg = self._hex_to_rgb(flash.text_color, (255, 255, 255))
        accent = self._hex_to_rgb(flash.accent_color, (13, 110, 253))

        title_s = self._text(title, flash.font_size, fg, bold=True) if title else None
        msg_s = self._text(message, max(12, flash.font_size - 4), fg, bold=False) if message else None

        padding = int(flash.padding)
        content_w = 0
        content_h = 0
        if title_s is not None:
            content_w = max(content_w, title_s.get_width())
            content_h += title_s.get_height()
        if msg_s is not None:
            content_w = max(content_w, msg_s.get_width())
            content_h += msg_s.get_height() + (6 if title_s is not None else 0)

        box_w = min(self.screen_w - 20, content_w + (padding * 2))
        box_h = min(self.screen_h - 20, content_h + (padding * 2))
        x = (self.screen_w - box_w) // 2
        if flash.position == "top":
            y = 24
        elif flash.position == "bottom":
            y = self.screen_h - box_h - 24
        else:
            y = (self.screen_h - box_h) // 2

        panel = pygame.Surface((max(1, box_w), max(1, box_h)), pygame.SRCALPHA)
        self._set_alpha_fill(panel, pygame.Rect(0, 0, box_w, box_h), bg, flash.opacity)
        pygame.draw.rect(panel, accent, pygame.Rect(0, 0, box_w, box_h), width=2, border_radius=10)

        cursor_y = padding
        if title_s is not None:
            panel.blit(title_s, (padding, cursor_y))
            cursor_y += title_s.get_height() + 6
        if msg_s is not None:
            panel.blit(msg_s, (padding, cursor_y))

        rotation = self._normalize_rotation(getattr(flash, "rotation", 0))
        if rotation in (90, 270):
            rotated = pygame.transform.rotate(panel, -rotation)
            rw, rh = rotated.get_size()
            y = (self.screen_h - rh) // 2
            if flash.position == "top":
                x = 24
            elif flash.position == "bottom":
                x = self.screen_w - rw - 24
            else:
                x = (self.screen_w - rw) // 2
            surface.blit(rotated, (x, y))
            return

        if rotation == 180:
            panel = pygame.transform.rotate(panel, -rotation)

        surface.blit(panel, (x, y))

    def _draw_popup(self, surface: pygame.Surface, popup) -> None:
        title = str(popup.title or "").strip()
        message = str(popup.message or "").strip()

        max_w = max(180, self.screen_w - 24)
        max_h = max(120, self.screen_h - 24)
        box_w = min(max_w, int(popup.width))
        box_h = min(max_h, int(popup.height))

        if popup.position == "top-left":
            x, y = 12, 12
        elif popup.position == "top-right":
            x, y = self.screen_w - box_w - 12, 12
        elif popup.position == "bottom-left":
            x, y = 12, self.screen_h - box_h - 12
        elif popup.position == "bottom-right":
            x, y = self.screen_w - box_w - 12, self.screen_h - box_h - 12
        else:
            x, y = (self.screen_w - box_w) // 2, (self.screen_h - box_h) // 2

        rect = pygame.Rect(x, y, box_w, box_h)
        bg = self._hex_to_rgb(popup.background_color, (255, 255, 255))
        fg = self._hex_to_rgb(popup.text_color, (17, 17, 17))
        accent = self._hex_to_rgb(popup.accent_color, (220, 53, 69))
        self._set_alpha_fill(surface, rect, bg, popup.opacity)
        pygame.draw.rect(surface, accent, rect, width=2, border_radius=10)

        padding = int(popup.padding)
        text_x = x + padding
        text_y = y + padding

        if title:
            t = self._text(title, 34, fg, bold=True)
            surface.blit(t, (text_x, text_y))
            text_y += t.get_height() + 8
        if message:
            m = self._text(message, 28, fg, bold=False)
            surface.blit(m, (text_x, text_y))

        img_ref = str(popup.image_path or "").strip()
        if img_ref:
            key = img_ref
            cached = self._image_cache.get(key)
            if cached is None:
                try:
                    img_path = Path(img_ref).expanduser()
                    if img_path.exists():
                        loaded = pygame.image.load(str(img_path))
                        cached = loaded.convert_alpha() if loaded.get_alpha() is not None else loaded.convert()
                        self._image_cache[key] = cached
                except Exception:
                    cached = None
            if cached is not None:
                max_img_w = max(40, box_w // 3)
                max_img_h = max(40, box_h // 2)
                iw, ih = cached.get_size()
                if iw > 0 and ih > 0:
                    scale = min(max_img_w / iw, max_img_h / ih)
                    nw = max(1, int(iw * scale))
                    nh = max(1, int(ih * scale))
                    scaled = pygame.transform.smoothscale(cached, (nw, nh))
                    surface.blit(scaled, (x + box_w - nw - padding, y + padding))

    def _draw_ticker(self, surface: pygame.Surface, active: ActiveTicker) -> None:
        ticker = active.ticker
        bg = self._hex_to_rgb(ticker.background_color, (0, 0, 0))
        fg = self._hex_to_rgb(ticker.text_color, (255, 255, 255))

        height = max(24, min(self.screen_h, int(ticker.height)))
        strip = pygame.Surface((self.screen_w, height), pygame.SRCALPHA)
        self._set_alpha_fill(strip, pygame.Rect(0, 0, self.screen_w, height), bg, ticker.opacity)

        text_surface = self._text(str(ticker.text), ticker.font_size, fg, bold=True)
        if text_surface.get_width() <= 0:
            return

        padding = max(0, int(ticker.padding_x))
        step = text_surface.get_width() + max(24, padding)
        offset = active.offset_px % float(step)
        x = -int(offset)
        y_text = max(0, (height - text_surface.get_height()) // 2)
        while x < self.screen_w + step:
            strip.blit(text_surface, (x + padding, y_text))
            x += step

        rotation = self._normalize_rotation(getattr(ticker, "rotation", 0))
        if rotation in (90, 270):
            rendered = pygame.transform.rotate(strip, -rotation)
            rw, rh = rendered.get_size()
            y = (self.screen_h - rh) // 2
            x = 0 if ticker.position == "top" else self.screen_w - rw
            surface.blit(rendered, (x, y))
            return

        if rotation == 180:
            strip = pygame.transform.rotate(strip, -rotation)
            rw, rh = strip.get_size()
            y = 0 if ticker.position == "top" else self.screen_h - rh
            surface.blit(strip, (0, y))
            return

        y = 0 if ticker.position == "top" else self.screen_h - height
        surface.blit(strip, (0, y))

    def compose(self, base_frame: pygame.Surface, overlay_frame: OverlayFrame) -> pygame.Surface:
        if overlay_frame.flash is None and overlay_frame.popup is None and not overlay_frame.tickers:
            return base_frame

        out = base_frame.copy()
        for ticker in overlay_frame.tickers:
            self._draw_ticker(out, ticker)
        if overlay_frame.flash is not None:
            self._draw_flash(out, overlay_frame.flash)
        if overlay_frame.popup is not None:
            self._draw_popup(out, overlay_frame.popup)
        return out
