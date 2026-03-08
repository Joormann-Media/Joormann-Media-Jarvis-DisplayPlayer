from __future__ import annotations

from pathlib import Path

import pygame


class FrameRenderer:
    def __init__(self, screen_size: tuple[int, int]):
        self.screen_w, self.screen_h = screen_size
        self._cache: dict[str, pygame.Surface] = {}
        self._fit_cache: dict[tuple[int, int, int], pygame.Surface] = {}

    def clear_caches(self) -> None:
        self._cache.clear()
        self._fit_cache.clear()

    def resolve_asset_path(self, manifest_dir: Path, asset_ref: str) -> Path:
        raw = Path(str(asset_ref))
        if raw.is_absolute() and raw.exists():
            return raw
        return (manifest_dir / raw).resolve()

    def load_image(self, path: Path) -> pygame.Surface:
        key = str(path)
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        loaded = pygame.image.load(key)
        if loaded.get_alpha() is not None:
            image = loaded.convert_alpha()
        else:
            image = loaded.convert()
        self._cache[key] = image
        return image

    def _fit(self, image: pygame.Surface, target_size: tuple[int, int]) -> pygame.Surface:
        tw, th = target_size
        cache_key = (id(image), tw, th)
        cached = self._fit_cache.get(cache_key)
        if cached is not None:
            return cached

        iw, ih = image.get_size()
        if iw <= 0 or ih <= 0 or tw <= 0 or th <= 0:
            empty = pygame.Surface((max(tw, 1), max(th, 1))).convert()
            self._fit_cache[cache_key] = empty
            return empty

        scale = min(tw / iw, th / ih)
        nw = max(1, int(iw * scale))
        nh = max(1, int(ih * scale))
        scaled = pygame.transform.smoothscale(image, (nw, nh))
        canvas = pygame.Surface((tw, th)).convert()
        canvas.fill((0, 0, 0))
        canvas.blit(scaled, ((tw - nw) // 2, (th - nh) // 2))
        self._fit_cache[cache_key] = canvas
        return canvas

    def fit_image(self, image: pygame.Surface, target_size: tuple[int, int]) -> pygame.Surface:
        return self._fit(image, target_size)

    def orient_frame(self, frame: pygame.Surface, orientation: str | None = None) -> pygame.Surface:
        return frame

    def render_full(self, image: pygame.Surface, orientation: str | None = None) -> pygame.Surface:
        return self._fit(image, (self.screen_w, self.screen_h))

    def render_split(self, image_a: pygame.Surface | None, image_b: pygame.Surface | None, direction: str, ratio_a: int, orientation: str | None = None) -> pygame.Surface:
        frame = pygame.Surface((self.screen_w, self.screen_h)).convert()
        frame.fill((0, 0, 0))

        if direction == 'vertical':
            a_h = int(self.screen_h * (ratio_a / 100.0))
            b_h = self.screen_h - a_h
            if image_a is not None and a_h > 0:
                part = self._fit(image_a, (self.screen_w, a_h))
                frame.blit(part, (0, 0))
            if image_b is not None and b_h > 0:
                part = self._fit(image_b, (self.screen_w, b_h))
                frame.blit(part, (0, a_h))
        else:
            a_w = int(self.screen_w * (ratio_a / 100.0))
            b_w = self.screen_w - a_w
            if image_a is not None and a_w > 0:
                part = self._fit(image_a, (a_w, self.screen_h))
                frame.blit(part, (0, 0))
            if image_b is not None and b_w > 0:
                part = self._fit(image_b, (b_w, self.screen_h))
                frame.blit(part, (a_w, 0))

        return frame
