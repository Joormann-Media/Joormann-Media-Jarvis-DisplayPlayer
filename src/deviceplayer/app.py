from __future__ import annotations

import os
import signal
import time
from pathlib import Path

import pygame

from .config import PlayerConfig
from .logger import configure_logger
from .plan_loader import ManifestError, load_manifest
from .playlist import PlaylistCursor
from .renderer import FrameRenderer
from .transitions import crossfade, slide_left
from .utils import clamp_transition_ms


class DevicePlayerApp:
    def __init__(self, config: PlayerConfig):
        self.config = config
        self.log = configure_logger(config.log_level)
        self.running = True
        self._last_manifest_mtime = 0.0
        self._last_reload_at = 0.0

        signal.signal(signal.SIGINT, self._on_signal)
        signal.signal(signal.SIGTERM, self._on_signal)

    def _on_signal(self, signum, _frame):
        self.log.info('received signal %s -> shutdown', signum)
        self.running = False

    def _init_screen(self) -> pygame.Surface:
        forced = os.getenv('SDL_VIDEODRIVER', '').strip()
        configured = os.getenv('DEVICEPLAYER_VIDEO_DRIVERS', 'kmsdrm,fbcon,wayland,x11').strip()
        candidates: list[str] = []
        if forced:
            candidates.append(forced)
        for item in configured.split(','):
            driver = item.strip()
            if driver and driver not in candidates:
                candidates.append(driver)
        if not candidates:
            candidates = ['kmsdrm', 'fbcon', 'wayland', 'x11']

        flags = pygame.FULLSCREEN if self.config.fullscreen else 0
        last_error: Exception | None = None

        for driver in candidates:
            try:
                os.environ['SDL_VIDEODRIVER'] = driver
                pygame.quit()
                pygame.init()
                if not pygame.display.get_init():
                    pygame.display.init()

                if self.config.fullscreen:
                    info = pygame.display.Info()
                    width = max(1, int(getattr(info, 'current_w', 0) or self.config.window_width))
                    height = max(1, int(getattr(info, 'current_h', 0) or self.config.window_height))
                    size = (width, height)
                else:
                    size = (self.config.window_width, self.config.window_height)

                screen = pygame.display.set_mode(size, flags)
                pygame.display.set_caption('Joormann Media DevicePlayer')
                self.log.info('video backend initialized: requested=%s active=%s size=%sx%s', driver, pygame.display.get_driver(), size[0], size[1])
                return screen
            except Exception as exc:
                last_error = exc
                self.log.warning('video backend init failed for %s: %s', driver, exc)

        raise RuntimeError(f'failed to initialize SDL video backend ({candidates}): {last_error}')

    def run(self) -> int:
        screen = self._init_screen()
        clock = pygame.time.Clock()

        renderer = FrameRenderer(screen.get_size())
        plan: dict | None = None
        cursor: PlaylistCursor | None = None

        current_frame = None
        next_switch_at = 0.0
        transition = {'type': 'none', 'ms': 0}
        transition_start = 0.0
        transition_from = None

        while self.running:
            now = time.monotonic()
            for event in pygame.event.get():
                if event.type == pygame.QUIT:
                    self.running = False

            if now >= self._last_reload_at + self.config.poll_reload_seconds:
                self._last_reload_at = now
                should_reload = plan is None
                try:
                    stat = self.config.manifest_path.stat()
                    if stat.st_mtime > self._last_manifest_mtime:
                        should_reload = True
                except FileNotFoundError:
                    should_reload = True

                if should_reload:
                    try:
                        if plan is not None:
                            self.log.info('manifest changed -> reload')
                        plan = self._load_plan_or_raise(self.config.manifest_path)
                        cursor = PlaylistCursor(plan['playlist'])
                        current_frame = None
                        next_switch_at = 0.0
                    except ManifestError as exc:
                        if plan is not None:
                            self.log.error('manifest reload failed: %s', exc)
                        plan = None
                        cursor = None

            if plan is None or cursor is None:
                if current_frame is None:
                    current_frame = pygame.Surface((renderer.screen_w, renderer.screen_h)).convert()
                    current_frame.fill((0, 0, 0))
                screen.blit(current_frame, (0, 0))
                pygame.display.flip()
                clock.tick(self.config.fps)
                continue

            if current_frame is None or now >= next_switch_at:
                try:
                    item = cursor.next()
                except Exception as exc:
                    self.log.error('playlist error: %s', exc)
                    time.sleep(1.0)
                    continue

                new_frame = self._render_item(renderer, plan, item)
                duration_ms = int(item.get('durationMs') or plan['defaults']['durationMs'])
                next_switch_at = now + (duration_ms / 1000.0)

                tr = item.get('transition') if isinstance(item.get('transition'), dict) else plan['defaults']['transition']
                transition = {'type': str(tr.get('type') or 'none').lower(), 'ms': clamp_transition_ms(duration_ms, int(tr.get('ms') or 0))}

                if current_frame is not None and transition['type'] in {'crossfade', 'slide-left'} and transition['ms'] > 0:
                    transition_from = current_frame
                    transition_start = now
                    current_frame = new_frame
                else:
                    transition_from = None
                    current_frame = new_frame

            frame_to_show = current_frame
            if transition_from is not None:
                progress = (now - transition_start) / max(transition['ms'] / 1000.0, 0.001)
                if progress >= 1.0:
                    transition_from = None
                    frame_to_show = current_frame
                elif transition['type'] == 'crossfade':
                    frame_to_show = crossfade(transition_from, current_frame, progress)
                elif transition['type'] == 'slide-left':
                    frame_to_show = slide_left(transition_from, current_frame, progress)

            if frame_to_show is not None:
                screen.blit(frame_to_show, (0, 0))
                pygame.display.flip()

            clock.tick(self.config.fps)

        pygame.quit()
        return 0

    def _load_plan_or_raise(self, manifest_path: Path) -> dict:
        plan = load_manifest(manifest_path)
        self._last_manifest_mtime = manifest_path.stat().st_mtime
        self.log.info('loaded manifest %s version=%s items=%s', manifest_path, plan.get('version', ''), len(plan.get('playlist', [])))
        return plan

    def _asset_surface(self, renderer: FrameRenderer, manifest_dir: Path, asset_key: str, assets_map: dict) -> pygame.Surface | None:
        if not asset_key:
            return None
        asset_ref = str(assets_map.get(asset_key) or '').strip()
        if not asset_ref:
            self.log.warning('asset key missing in assets map: %s', asset_key)
            return None
        asset_path = renderer.resolve_asset_path(manifest_dir, asset_ref)
        if not asset_path.exists():
            self.log.warning('asset file missing: %s', asset_path)
            return None
        try:
            return renderer.load_image(asset_path)
        except Exception as exc:
            self.log.error('asset load failed %s: %s', asset_path, exc)
            return None

    def _render_item(self, renderer: FrameRenderer, plan: dict, item: dict) -> pygame.Surface:
        manifest_dir = self.config.manifest_path.parent
        assets = plan['assets']
        layout = plan['layout']
        mode = layout.get('mode', 'full')
        orientation = layout.get('orientation', 'landscape')

        if mode == 'split':
            zones = item.get('zones') if isinstance(item.get('zones'), dict) else {}
            zone_a = zones.get('A') if isinstance(zones.get('A'), dict) else {}
            zone_b = zones.get('B') if isinstance(zones.get('B'), dict) else {}
            img_a = self._asset_surface(renderer, manifest_dir, str(zone_a.get('asset') or ''), assets)
            img_b = self._asset_surface(renderer, manifest_dir, str(zone_b.get('asset') or ''), assets)
            return renderer.render_split(
                img_a,
                img_b,
                str(layout.get('direction') or 'horizontal').lower(),
                int(layout.get('ratioA') or 50),
                orientation,
            )

        img = self._asset_surface(renderer, manifest_dir, str(item.get('asset') or ''), assets)
        if img is None:
            empty = pygame.Surface((renderer.screen_w, renderer.screen_h)).convert()
            empty.fill((0, 0, 0))
            return empty
        return renderer.render_full(img, orientation)
