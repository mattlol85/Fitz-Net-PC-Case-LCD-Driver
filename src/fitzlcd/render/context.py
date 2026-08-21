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


#: Families every scene can rely on, in preference order. The monospace entry
#: matters most: the terminal scenes are unreadable without a fixed pitch face.
MONOSPACE_PREFERENCES = ("Cascadia Mono", "Consolas", "Courier New", "DejaVu Sans Mono")


@lru_cache(maxsize=1)
def _windows_font_index() -> dict[str, str]:
    """Map lowercased family name -> font file, from the Windows font registry.

    Pillow resolves *file* names ("consola.ttf"), not families ("Consolas"), so
    a scene asking for a font by its real name would silently fall back. Windows
    already keeps the mapping; read it once rather than sniffing every file in
    the fonts directory.
    """
    index: dict[str, str] = {}
    try:
        import winreg
    except ImportError:  # pragma: no cover - non-Windows
        return index

    keys = (
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion\Fonts"),
    )
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as key:
                for i in range(winreg.QueryInfoKey(key)[1]):
                    try:
                        raw_name, filename, _ = winreg.EnumValue(key, i)
                    except OSError:
                        continue
                    if not isinstance(filename, str):
                        continue
                    # "Consolas (TrueType)" -> "consolas"; some entries list
                    # several families separated by "&".
                    family = raw_name.split("(")[0].strip()
                    for part in family.split("&"):
                        cleaned = part.strip().lower()
                        if cleaned:
                            index.setdefault(cleaned, filename)
        except OSError as exc:  # pragma: no cover - registry access denied
            log.debug("could not read font registry %s: %s", path, exc)
    return index


#: Scenes ask for "mono" rather than naming a face, so a scene stays readable on
#: a machine that has a different set of fonts installed.
FAMILY_ALIASES = {
    "mono": MONOSPACE_PREFERENCES,
    "monospace": MONOSPACE_PREFERENCES,
    "sans": ("Segoe UI", "Arial", "DejaVu Sans"),
    "sans-serif": ("Segoe UI", "Arial", "DejaVu Sans"),
}


def _installed(family: str) -> str | None:
    """Return a font file for ``family`` only if it is genuinely installed."""
    registered = _windows_font_index().get(family.strip().lower())
    if registered:
        return registered
    for directory in _WINDOWS_FONT_DIRS:
        for suffix in (".ttf", ".otf", ".ttc"):
            candidate = directory / f"{family.replace(' ', '')}{suffix}"
            if candidate.exists():
                return str(candidate)
    return None


def resolve_alias(name: str) -> str:
    """Turn an alias like ``mono`` into the best family actually installed."""
    options = FAMILY_ALIASES.get(name.strip().lower())
    if not options:
        return name
    for family in options:
        if _installed(family):
            return family
    return options[-1]


@lru_cache(maxsize=256)
def load_font(name: str, size: int) -> ImageFont.FreeTypeFont:
    """Load a font by family name, alias, file name, or path, with fallback.

    Cached because layers ask for the same face every frame.
    """
    name = resolve_alias(name) if name else name
    candidates: list[str] = []
    if name:
        candidates.append(name)
        registered = _windows_font_index().get(name.strip().lower())
        if registered:
            candidates.append(registered)
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
