"""Icons drawn from inline SVG, tinted from the theme palette.

There are no binary assets to ship or keep in step with the PyInstaller spec:
each icon is a Lucide-style 24x24 stroke path, rendered on demand in whatever
colour the caller needs and cached. Layer classes name their icon (see
``Layer.icon``) so a new layer type picks one without touching UI code.
"""

from __future__ import annotations

from functools import cache
from pathlib import Path

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtSvg import QSvgRenderer

from fitzlcd.ui.theme import COLORS

#: Inner SVG markup for each icon, drawn on a 24x24 canvas.
PATHS: dict[str, str] = {
    "play": '<polygon points="6 3 20 12 6 21 6 3"/>',
    "pause": (
        '<rect x="14" y="4" width="4" height="16" rx="1"/>'
        '<rect x="6" y="4" width="4" height="16" rx="1"/>'
    ),
    "settings": (
        '<path d="M20 7h-9"/><path d="M14 17H5"/>'
        '<circle cx="17" cy="17" r="3"/><circle cx="7" cy="7" r="3"/>'
    ),
    "chevron-left": '<path d="m15 18-6-6 6-6"/>',
    "chevron-right": '<path d="m9 18 6-6-6-6"/>',
    "chevron-down": '<path d="m6 9 6 6 6-6"/>',
    "chevron-up": '<path d="m18 15-6-6-6 6"/>',
    "plus": '<path d="M5 12h14"/><path d="M12 5v14"/>',
    "copy": (
        '<rect width="14" height="14" x="8" y="8" rx="2"/>'
        '<path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2"/>'
    ),
    "trash": (
        '<path d="M3 6h18"/><path d="M19 6v14c0 1-1 2-2 2H7c-1 0-2-1-2-2V6"/>'
        '<path d="M8 6V4c0-1 1-2 2-2h4c1 0 2 1 2 2v2"/>'
    ),
    "eye": (
        '<path d="M2 12s3-7 10-7 10 7 10 7-3 7-10 7-10-7-10-7Z"/>' '<circle cx="12" cy="12" r="3"/>'
    ),
    "eye-off": (
        '<path d="M9.88 9.88a3 3 0 1 0 4.24 4.24"/>'
        '<path d="M10.73 5.08A10.43 10.43 0 0 1 12 5c7 0 10 7 10 7a13.16 13.16 0 0 1-1.67 2.68"/>'
        '<path d="M6.61 6.61A13.53 13.53 0 0 0 2 12s3 7 10 7a9.74 9.74 0 0 0 5.39-1.61"/>'
        '<path d="M2 2l20 20"/>'
    ),
    "undo": '<path d="M9 14 4 9l5-5"/><path d="M4 9h10.5a5.5 5.5 0 0 1 0 11H11"/>',
    "redo": '<path d="m15 14 5-5-5-5"/><path d="M20 9H9.5a5.5 5.5 0 0 0 0 11H13"/>',
    "check": '<path d="M20 6 9 17l-5-5"/>',
    "refresh": (
        '<path d="M3 12a9 9 0 0 1 9-9 9.75 9.75 0 0 1 6.74 2.74L21 8"/><path d="M21 3v5h-5"/>'
        '<path d="M21 12a9 9 0 0 1-9 9 9.75 9.75 0 0 1-6.74-2.74L3 16"/><path d="M8 16H3v5"/>'
    ),
    "info": '<circle cx="12" cy="12" r="10"/><path d="M12 16v-4"/><path d="M12 8h.01"/>',
    "arrow-up": '<path d="m5 12 7-7 7 7"/><path d="M12 19V5"/>',
    "arrow-down": '<path d="M12 5v14"/><path d="m19 12-7 7-7-7"/>',
    "pencil": '<path d="M17 3a2.85 2.83 0 1 1 4 4L7.5 20.5 2 22l1.5-5.5Z"/>',
    "monitor": (
        '<rect width="20" height="14" x="2" y="3" rx="2"/>'
        '<path d="M8 21h8"/><path d="M12 17v4"/>'
    ),
    # Layer type icons.
    "type": '<path d="M4 7V4h16v3"/><path d="M9 20h6"/><path d="M12 4v16"/>',
    "clock": '<circle cx="12" cy="12" r="10"/><path d="M12 6v6l4 2"/>',
    "bar": '<rect x="2" y="8" width="20" height="8" rx="2"/><path d="M6 12h7"/>',
    "graph": '<path d="M22 12h-4l-3 9L9 3l-3 9H2"/>',
    "ring": '<path d="M21 12a9 9 0 1 1-9-9"/><path d="M12 3a9 9 0 0 1 9 9" opacity=".35"/>',
    "image": (
        '<rect width="18" height="18" x="3" y="3" rx="2"/><circle cx="9" cy="9" r="2"/>'
        '<path d="m21 15-3.1-3.1a2 2 0 0 0-2.8 0L6 21"/>'
    ),
    "square": '<rect width="18" height="18" x="3" y="3" rx="2"/>',
    "sparkle": (
        '<path d="M12 3v18"/><path d="M3 12h18"/>'
        '<path d="m5.6 5.6 12.8 12.8"/><path d="m18.4 5.6-12.8 12.8"/>'
    ),
    "crosshair": (
        '<circle cx="12" cy="12" r="10"/><path d="M22 12h-4"/><path d="M6 12H2"/>'
        '<path d="M12 6V2"/><path d="M12 22v-4"/>'
    ),
}


def svg(name: str, color: str = COLORS["text"], stroke: float = 2.0) -> str:
    """The complete SVG document for one icon."""
    body = PATHS.get(name, PATHS["square"])
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" width="24" height="24" viewBox="0 0 24 24" '
        f'fill="none" stroke="{color}" stroke-width="{stroke}" '
        f'stroke-linecap="round" stroke-linejoin="round">{body}</svg>'
    )


def pixmap(name: str, color: str = COLORS["text"], size: int = 16, scale: float = 1.0) -> QPixmap:
    renderer = QSvgRenderer(QByteArray(svg(name, color).encode("utf-8")))
    px = QPixmap(int(size * scale), int(size * scale))
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, px.width(), px.height()))
    painter.end()
    px.setDevicePixelRatio(scale)
    return px


@cache
def icon(name: str, color: str = COLORS["text"], disabled: str = COLORS["muted_dim"]) -> QIcon:
    """A crisp icon at 1x and 2x, with a dimmed variant for disabled buttons."""
    result = QIcon()
    for scale in (1.0, 2.0):
        result.addPixmap(pixmap(name, color, 16, scale), QIcon.Mode.Normal)
        result.addPixmap(pixmap(name, disabled, 16, scale), QIcon.Mode.Disabled)
    return result


def write_svg(name: str, color: str, target: Path) -> Path:
    """Save one icon as a file, for stylesheet ``url()`` references."""
    target.write_text(svg(name, color, stroke=3.0), encoding="utf-8")
    return target


def app_icon() -> QIcon:
    """The app's panel-shaped mark: window, taskbar and tray."""
    result = QIcon()
    for size in (16, 24, 32, 48, 64, 128, 256):
        result.addPixmap(_app_pixmap(size))
    return result


def _app_pixmap(size: int) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(QColor(0, 0, 0, 0))
    s = size / 64
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(QColor("#1b2233"))
    painter.drawRoundedRect(QRectF(2 * s, 16 * s, 60 * s, 32 * s), 7 * s, 7 * s)
    painter.setBrush(QColor(COLORS["accent"]))
    painter.drawRoundedRect(QRectF(9 * s, 23 * s, 22 * s, 18 * s), 3 * s, 3 * s)
    painter.setBrush(QColor("#00e5ff"))
    painter.drawRoundedRect(QRectF(34 * s, 23 * s, 21 * s, 18 * s), 3 * s, 3 * s)
    painter.end()
    return px
