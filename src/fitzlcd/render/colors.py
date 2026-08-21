"""Colour parsing shared by every layer.

Accepts ``#RGB``, ``#RRGGBB``, ``#RRGGBBAA``, ``rgb()``/``rgba()``, CSS names and
raw tuples, and never raises: an unparseable colour renders as opaque magenta so
a typo is obvious on the panel rather than crashing the render loop.
"""

from __future__ import annotations

import logging
from typing import Any

from PIL import ImageColor

log = logging.getLogger(__name__)

RGBA = tuple[int, int, int, int]

#: Deliberately loud, so a bad colour value is visible at a glance.
INVALID_COLOR: RGBA = (255, 0, 255, 255)


def parse_color(value: Any, default_alpha: int = 255) -> RGBA:
    """Coerce ``value`` to an RGBA tuple."""
    if value is None:
        return (0, 0, 0, 0)

    if isinstance(value, (tuple, list)):
        parts = [int(v) for v in value[:4]]
        if len(parts) == 3:
            parts.append(default_alpha)
        if len(parts) == 4:
            return tuple(max(0, min(255, p)) for p in parts)  # type: ignore[return-value]
        log.warning("bad colour tuple %r", value)
        return INVALID_COLOR

    if isinstance(value, str):
        text = value.strip()
        try:
            rgba = ImageColor.getcolor(text, "RGBA")
        except ValueError:
            log.warning("unparseable colour %r", value)
            return INVALID_COLOR
        return rgba  # type: ignore[return-value]

    log.warning("unsupported colour value %r", value)
    return INVALID_COLOR


def with_alpha(color: RGBA, alpha: float) -> RGBA:
    """Scale a colour's alpha by ``alpha`` (0.0-1.0)."""
    r, g, b, a = color
    return (r, g, b, max(0, min(255, int(a * max(0.0, min(1.0, alpha))))))
