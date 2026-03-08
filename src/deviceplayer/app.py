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
from .transitions import can_animate, normalize_transition_name, render_transition
from .utils import clamp_transition_ms


class DevicePlayerApp:
    def __init__(self, config: PlayerConfig):
        self.config = config
        self.log = configure_logger(config.log_level)
        self.running = True
        self._last_manifest_mtime = 0.0
        self._last_reload_at = 0.0
        self._frame_cache: dict[str, pygame.Surface] = {}
        self._black_frame: pygame.Surface | None = None

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
                pygame.mouse.set_visible(False)
                pygame.event.set_allowed([pygame.QUIT])
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
        current_item = None
        next_switch_at = 0.0
        transition = {'type': 'none', 'ms': 0}
        transition_start = 0.0
        transition_from = None
        transition_context = None
        frame_dirty = True

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
                        renderer.clear_caches()
                        self._frame_cache.clear()
                        self._black_frame = None
                        cursor = PlaylistCursor(plan['playlist'])
                        current_frame = None
                        current_item = None
                        next_switch_at = 0.0
                        transition_from = None
                        transition_context = None
                        frame_dirty = True
                    except ManifestError as exc:
                        if plan is not None:
                            self.log.error('manifest reload failed: %s', exc)
                        plan = None
                        cursor = None

            if plan is None or cursor is None:
                if current_frame is None:
                    current_frame = self._get_black_frame(renderer)
                    frame_dirty = True
                if frame_dirty:
                    screen.blit(current_frame, (0, 0))
                    pygame.display.flip()
                    frame_dirty = False
                self._idle_wait(now, next_switch_at, in_transition=False)
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

                transition = self._resolve_transition(item, plan, duration_ms)
                transition_context = self._build_transition_context(plan, current_item, item, transition)

                if current_frame is not None and self._has_active_transition(transition_context):
                    transition_from = current_frame
                    transition_start = now
                    current_frame = new_frame
                else:
                    transition_from = None
                    transition_context = None
                    current_frame = new_frame
                current_item = item
                frame_dirty = True

            frame_to_show = current_frame
            in_transition = False
            if transition_from is not None:
                in_transition = True
                progress = (now - transition_start) / max(transition['ms'] / 1000.0, 0.001)
                if progress >= 1.0:
                    transition_from = None
                    frame_to_show = current_frame
                    frame_dirty = True
                else:
                    if (
                        transition_context is not None
                        and bool(transition_context.get('split_per_zone'))
                    ):
                        frame_to_show = self._render_split_zone_transition(
                            renderer=renderer,
                            plan=plan,
                            old_item=transition_context.get('old_item') if isinstance(transition_context.get('old_item'), dict) else {},
                            new_item=transition_context.get('new_item') if isinstance(transition_context.get('new_item'), dict) else {},
                            elapsed_s=max(0.0, now - transition_start),
                            zones=transition_context.get('zones') if isinstance(transition_context.get('zones'), dict) else {},
                        )
                    else:
                        frame_to_show = render_transition(str(transition['type']), transition_from, current_frame, progress)

            if frame_to_show is not None and (frame_dirty or in_transition):
                screen.blit(frame_to_show, (0, 0))
                pygame.display.flip()
                if not in_transition:
                    frame_dirty = False

            if in_transition:
                clock.tick(self.config.transition_fps)
            else:
                self._idle_wait(now, next_switch_at, in_transition=False)

        pygame.quit()
        return 0

    def _load_plan_or_raise(self, manifest_path: Path) -> dict:
        plan = load_manifest(manifest_path)
        self._last_manifest_mtime = manifest_path.stat().st_mtime
        self.log.info('loaded manifest %s version=%s items=%s', manifest_path, plan.get('version', ''), len(plan.get('playlist', [])))
        return plan

    def _get_black_frame(self, renderer: FrameRenderer) -> pygame.Surface:
        if self._black_frame is None:
            frame = pygame.Surface((renderer.screen_w, renderer.screen_h)).convert()
            frame.fill((0, 0, 0))
            self._black_frame = frame
        return self._black_frame

    def _idle_wait(self, now: float, next_switch_at: float, in_transition: bool) -> None:
        if in_transition:
            return
        reload_due = self._last_reload_at + self.config.poll_reload_seconds
        next_due = min(reload_due, next_switch_at if next_switch_at > now else reload_due)
        sleep_s = max(0.0, next_due - now)
        sleep_ms = int(min(self.config.idle_sleep_ms, max(1, int(sleep_s * 1000))))
        pygame.time.wait(sleep_ms)

    def _item_cache_key(self, plan: dict, item: dict) -> str:
        layout = plan['layout']
        assets = plan['assets']
        mode = str(layout.get('mode') or 'full')
        if mode == 'split':
            zones = item.get('zones') if isinstance(item.get('zones'), dict) else {}
            zone_a = zones.get('A') if isinstance(zones.get('A'), dict) else {}
            zone_b = zones.get('B') if isinstance(zones.get('B'), dict) else {}
            key_a = str(zone_a.get('asset') or '')
            key_b = str(zone_b.get('asset') or '')
            ref_a = str(assets.get(key_a) or '')
            ref_b = str(assets.get(key_b) or '')
            direction = str(layout.get('direction') or 'horizontal').lower()
            ratio = int(layout.get('ratioA') or 50)
            return f'split|{direction}|{ratio}|{ref_a}|{ref_b}'

        asset_key = str(item.get('asset') or '')
        asset_ref = str(assets.get(asset_key) or '')
        return f'full|{asset_ref}'

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
        cache_key = self._item_cache_key(plan, item)
        cached = self._frame_cache.get(cache_key)
        if cached is not None:
            return cached

        manifest_dir = self.config.manifest_path.parent
        assets = plan['assets']
        layout = plan['layout']
        mode = layout.get('mode', 'full')

        if mode == 'split':
            zones = item.get('zones') if isinstance(item.get('zones'), dict) else {}
            zone_a = zones.get('A') if isinstance(zones.get('A'), dict) else {}
            zone_b = zones.get('B') if isinstance(zones.get('B'), dict) else {}
            img_a = self._asset_surface(renderer, manifest_dir, str(zone_a.get('asset') or ''), assets)
            img_b = self._asset_surface(renderer, manifest_dir, str(zone_b.get('asset') or ''), assets)
            frame = renderer.render_split(
                img_a,
                img_b,
                str(layout.get('direction') or 'horizontal').lower(),
                int(layout.get('ratioA') or 50),
            )
            self._frame_cache[cache_key] = frame
            return frame

        img = self._asset_surface(renderer, manifest_dir, str(item.get('asset') or ''), assets)
        if img is None:
            empty = self._get_black_frame(renderer)
            self._frame_cache[cache_key] = empty
            return empty
        frame = renderer.render_full(img)
        self._frame_cache[cache_key] = frame
        return frame

    def _resolve_transition(self, item: dict, plan: dict, duration_ms: int) -> dict:
        tr = item.get('transition') if isinstance(item.get('transition'), dict) else plan['defaults']['transition']
        return {
            'type': normalize_transition_name(str(tr.get('type') or 'none')),
            'ms': clamp_transition_ms(duration_ms, int(tr.get('ms') or 0)),
        }

    def _build_transition_context(self, plan: dict, old_item: dict | None, new_item: dict | None, fallback_transition: dict) -> dict | None:
        if not isinstance(old_item, dict) or not isinstance(new_item, dict):
            return None

        layout = plan.get('layout') if isinstance(plan.get('layout'), dict) else {}
        mode = str(layout.get('mode') or 'full')
        if mode != 'split':
            return {
                'split_per_zone': False,
                'old_item': old_item,
                'new_item': new_item,
                'zones': {},
                'type': str(fallback_transition.get('type') or 'none'),
                'ms': int(fallback_transition.get('ms') or 0),
            }

        old_zones = old_item.get('zones') if isinstance(old_item.get('zones'), dict) else {}
        new_zones = new_item.get('zones') if isinstance(new_item.get('zones'), dict) else {}
        zones_cfg = {}
        differs = False
        has_active = False
        default_type = str(fallback_transition.get('type') or 'none')
        default_ms = int(fallback_transition.get('ms') or 0)

        for key in ('A', 'B'):
            zone_new = new_zones.get(key) if isinstance(new_zones.get(key), dict) else {}
            transition = zone_new.get('transition') if isinstance(zone_new.get('transition'), dict) else {}
            t = normalize_transition_name(str(transition.get('type') or default_type))
            ms = int(transition.get('ms') or default_ms)
            ms = max(0, ms)
            zones_cfg[key] = {'type': t, 'ms': ms}
            has_active = has_active or (can_animate(t) and ms > 0)

        differs = zones_cfg['A'] != zones_cfg['B']
        split_per_zone = differs or (zones_cfg['A']['type'] == 'none' and zones_cfg['B']['type'] != 'none') or (zones_cfg['B']['type'] == 'none' and zones_cfg['A']['type'] != 'none')
        if not has_active and not (can_animate(default_type) and default_ms > 0):
            return None

        return {
            'split_per_zone': split_per_zone,
            'old_item': old_item,
            'new_item': new_item,
            'zones': zones_cfg,
            'type': default_type,
            'ms': default_ms,
        }

    def _has_active_transition(self, transition_context: dict | None) -> bool:
        if transition_context is None:
            return False

        if bool(transition_context.get('split_per_zone')):
            zones = transition_context.get('zones') if isinstance(transition_context.get('zones'), dict) else {}
            for key in ('A', 'B'):
                cfg = zones.get(key) if isinstance(zones.get(key), dict) else {}
                t = str(cfg.get('type') or 'none')
                ms = int(cfg.get('ms') or 0)
                if can_animate(t) and ms > 0:
                    return True
            return False

        t = str(transition_context.get('type') or 'none')
        ms = int(transition_context.get('ms') or 0)
        return can_animate(t) and ms > 0

    def _render_split_zone_transition(self, renderer: FrameRenderer, plan: dict, old_item: dict, new_item: dict, elapsed_s: float, zones: dict) -> pygame.Surface:
        layout = plan.get('layout') if isinstance(plan.get('layout'), dict) else {}
        direction = str(layout.get('direction') or 'horizontal').lower()
        ratio = max(1, min(99, int(layout.get('ratioA') or 50)))
        manifest_dir = self.config.manifest_path.parent
        assets = plan.get('assets') if isinstance(plan.get('assets'), dict) else {}

        old_zones = old_item.get('zones') if isinstance(old_item.get('zones'), dict) else {}
        new_zones = new_item.get('zones') if isinstance(new_item.get('zones'), dict) else {}

        if direction == 'vertical':
            a_size = (renderer.screen_w, int(renderer.screen_h * (ratio / 100.0)))
            b_size = (renderer.screen_w, renderer.screen_h - a_size[1])
            a_pos = (0, 0)
            b_pos = (0, a_size[1])
        else:
            a_size = (int(renderer.screen_w * (ratio / 100.0)), renderer.screen_h)
            b_size = (renderer.screen_w - a_size[0], renderer.screen_h)
            a_pos = (0, 0)
            b_pos = (a_size[0], 0)

        frame = pygame.Surface((renderer.screen_w, renderer.screen_h)).convert()
        frame.fill((0, 0, 0))

        zone_a = self._render_split_zone(renderer, manifest_dir, assets, old_zones.get('A'), new_zones.get('A'), zones.get('A'), a_size, elapsed_s)
        zone_b = self._render_split_zone(renderer, manifest_dir, assets, old_zones.get('B'), new_zones.get('B'), zones.get('B'), b_size, elapsed_s)

        if zone_a is not None:
            frame.blit(zone_a, a_pos)
        if zone_b is not None:
            frame.blit(zone_b, b_pos)

        return frame

    def _render_split_zone(self, renderer: FrameRenderer, manifest_dir: Path, assets: dict, old_zone_raw, new_zone_raw, zone_transition_raw, target_size: tuple[int, int], elapsed_s: float) -> pygame.Surface | None:
        old_zone = old_zone_raw if isinstance(old_zone_raw, dict) else {}
        new_zone = new_zone_raw if isinstance(new_zone_raw, dict) else {}
        zone_transition = zone_transition_raw if isinstance(zone_transition_raw, dict) else {}

        old_asset = str(old_zone.get('asset') or '')
        new_asset = str(new_zone.get('asset') or '')
        old_img = self._asset_surface(renderer, manifest_dir, old_asset, assets)
        new_img = self._asset_surface(renderer, manifest_dir, new_asset, assets)

        if old_img is None and new_img is None:
            return None

        old_fit = renderer.fit_image(old_img, target_size) if old_img is not None else None
        new_fit = renderer.fit_image(new_img, target_size) if new_img is not None else old_fit
        if new_fit is None:
            return old_fit

        t = normalize_transition_name(str(zone_transition.get('type') or 'none'))
        ms = max(0, int(zone_transition.get('ms') or 0))
        if old_fit is not None and can_animate(t) and ms > 0:
            progress = min(1.0, max(0.0, elapsed_s / max(ms / 1000.0, 0.001)))
            if progress < 1.0:
                return render_transition(t, old_fit, new_fit, progress)

        return new_fit
