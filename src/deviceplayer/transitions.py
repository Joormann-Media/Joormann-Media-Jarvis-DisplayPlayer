from __future__ import annotations

import pygame


def normalize_transition_name(value: str) -> str:
    raw = (value or '').strip().lower().replace('_', '-')
    if raw in {'', 'none', 'off', 'disabled'}:
        return 'none'
    if raw in {'fade', 'dissolve', 'cross-fade'}:
        return 'crossfade'
    if raw in {'slideleft', 'slide-left'}:
        return 'slide-left'
    if raw in {'slideright', 'slide-right'}:
        return 'slide-right'
    if raw in {'slideup', 'slide-up'}:
        return 'slide-up'
    if raw in {'slidedown', 'slide-down'}:
        return 'slide-down'
    if raw in {'crossfade'}:
        return 'crossfade'
    return raw


def crossfade(old_surface: pygame.Surface, new_surface: pygame.Surface, progress: float) -> pygame.Surface:
    p = max(0.0, min(1.0, float(progress)))
    frame = old_surface.copy()
    overlay = new_surface.copy()
    overlay.set_alpha(int(255 * p))
    frame.blit(overlay, (0, 0))
    return frame


def slide_left(old_surface: pygame.Surface, new_surface: pygame.Surface, progress: float) -> pygame.Surface:
    p = max(0.0, min(1.0, float(progress)))
    width = old_surface.get_width()
    offset = int(width * p)
    frame = pygame.Surface(old_surface.get_size()).convert()
    frame.blit(old_surface, (-offset, 0))
    frame.blit(new_surface, (width - offset, 0))
    return frame


def slide_right(old_surface: pygame.Surface, new_surface: pygame.Surface, progress: float) -> pygame.Surface:
    p = max(0.0, min(1.0, float(progress)))
    width = old_surface.get_width()
    offset = int(width * p)
    frame = pygame.Surface(old_surface.get_size()).convert()
    frame.blit(old_surface, (offset, 0))
    frame.blit(new_surface, (-width + offset, 0))
    return frame


def slide_up(old_surface: pygame.Surface, new_surface: pygame.Surface, progress: float) -> pygame.Surface:
    p = max(0.0, min(1.0, float(progress)))
    height = old_surface.get_height()
    offset = int(height * p)
    frame = pygame.Surface(old_surface.get_size()).convert()
    frame.blit(old_surface, (0, -offset))
    frame.blit(new_surface, (0, height - offset))
    return frame


def slide_down(old_surface: pygame.Surface, new_surface: pygame.Surface, progress: float) -> pygame.Surface:
    p = max(0.0, min(1.0, float(progress)))
    height = old_surface.get_height()
    offset = int(height * p)
    frame = pygame.Surface(old_surface.get_size()).convert()
    frame.blit(old_surface, (0, offset))
    frame.blit(new_surface, (0, -height + offset))
    return frame


def can_animate(name: str) -> bool:
    normalized = normalize_transition_name(name)
    return normalized in {'crossfade', 'slide-left', 'slide-right', 'slide-up', 'slide-down'}


def render_transition(name: str, old_surface: pygame.Surface, new_surface: pygame.Surface, progress: float) -> pygame.Surface:
    normalized = normalize_transition_name(name)
    if normalized == 'crossfade':
        return crossfade(old_surface, new_surface, progress)
    if normalized == 'slide-left':
        return slide_left(old_surface, new_surface, progress)
    if normalized == 'slide-right':
        return slide_right(old_surface, new_surface, progress)
    if normalized == 'slide-up':
        return slide_up(old_surface, new_surface, progress)
    if normalized == 'slide-down':
        return slide_down(old_surface, new_surface, progress)
    return new_surface
