"""Per-frame state handed to every layer.

Holds the things layers need but should not own: frame geometry, the clock, the
current metric values, and a font cache (loading a TrueType face per frame would
dominate the render budget).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

from PIL import ImageFont

log = logging.getLogger(__name__)

#: Fonts to try, in order, when a layer asks for one we cannot find.
FALLBACK_FONTS = ("segoeui.ttf", "arial.ttf", "DejaVuSans.ttf")

_WINDOWS_FONT_DIRS = (
    Path("C:/Windows/Fonts"),
    Path.home() / "AppData/Local/Microsoft/Windows/Fonts",
)


@lru_cache(maxsize=256)
def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font by family name, file name, or path, with graceful fallback.

    Cached because layers ask for the same face every frame.
    """
    candidates: list[str] = []
    if name:
        candidates.append(name)
        if not name.lower().endswith((".ttf", ".otf", ".ttc")):
            candidates.append(f"{name}.ttf")
            candidates.append(f"{name.replace(' ', '')}.ttf")
    candidates.extend(FALLBACK_FONTS)

    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            pass
        for directory in _WINDOWS_FONT_DIRS:
            path = directory / candidate
            if path.exists():
                try:
                    return ImageFont.truetype(str(path), size)
                except OSError:
                    continue

    log.warning("no usable font for %r at size %d; falling back to bitmap default", name, size)
    return ImageFont.load_default(size)


@dataclass
class RenderContext:
    """Everything a layer needs to draw one frame."""

    width: int
    height: int
    time: float = 0.0  # seconds since the scene started
    frame_index: int = 0
    metrics: dict[str, Any] = field(default_factory=dict)

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def font(self, name: str, size: int) -> ImageFont.FreeTypeFont:
        return load_font(name, max(1, int(size)))
