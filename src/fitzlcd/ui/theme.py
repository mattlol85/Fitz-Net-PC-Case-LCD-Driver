"""The desktop app's single dark palette and stylesheet.

Every colour lives in :data:`COLORS`, so a widget that needs a status colour
(connected/searching/error, muted text, the accent) reaches in here instead of
retyping a hex string. :func:`apply_theme` is the only entry point: the app and
``tools/ui_shot.py`` both call it, so screenshots show what users see.

Qt's Fusion style is used underneath the stylesheet. It looks the same on every
Windows version, and anything the stylesheet leaves alone is still drawn from
the same palette rather than falling back to the light native theme.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from PySide6.QtGui import QColor, QFont, QPalette
from PySide6.QtWidgets import QApplication

COLORS = {
    "bg": "#131519",
    "surface": "#1b1e24",
    "surface_alt": "#23272f",
    "surface_hover": "#2c313b",
    "border": "#2a2e37",
    "border_strong": "#3a3f4b",
    "text": "#e7e9ee",
    "muted": "#9aa1b0",
    "muted_dim": "#666d7c",
    "accent": "#4c8dff",
    "accent_hover": "#6aa0ff",
    "accent_soft": "#1e2b44",
    "success": "#3ccf6e",
    "warning": "#f0a53a",
    "danger": "#ff5f57",
    "preview_bg": "#0c0d10",
}

#: Keyed by ``EngineStats.status``; also doubles as a general
#: connected/attention/error vocabulary (e.g. the CS2 integration state).
STATUS_COLORS = {
    "connected": COLORS["success"],
    "searching": COLORS["warning"],
    "error": COLORS["danger"],
}

#: The base UI font. Segoe UI is what Windows itself uses.
FONT_FAMILY = "Segoe UI"
FONT_POINT_SIZE = 9


def apply_theme(app: QApplication) -> None:
    """Style the whole application. Call once, before any window is shown."""
    app.setStyle("Fusion")
    app.setPalette(build_palette())
    font = QFont(FONT_FAMILY, FONT_POINT_SIZE)
    # The offscreen screenshot platform may have loaded a different family;
    # keep whatever it chose rather than asking for one that isn't there.
    if QFont(FONT_FAMILY).exactMatch():
        app.setFont(font)
    app.setStyleSheet(build_stylesheet(_asset_dir()))


def build_palette() -> QPalette:
    c = {k: QColor(v) for k, v in COLORS.items()}
    palette = QPalette()
    role = QPalette.ColorRole
    palette.setColor(role.Window, c["bg"])
    palette.setColor(role.WindowText, c["text"])
    palette.setColor(role.Base, c["surface"])
    palette.setColor(role.AlternateBase, c["surface_alt"])
    palette.setColor(role.Text, c["text"])
    palette.setColor(role.Button, c["surface_alt"])
    palette.setColor(role.ButtonText, c["text"])
    palette.setColor(role.Highlight, c["accent"])
    palette.setColor(role.HighlightedText, QColor("#ffffff"))
    palette.setColor(role.ToolTipBase, c["surface_alt"])
    palette.setColor(role.ToolTipText, c["text"])
    palette.setColor(role.PlaceholderText, c["muted_dim"])
    palette.setColor(role.Link, c["accent"])
    palette.setColor(role.Mid, c["border"])
    palette.setColor(role.Dark, c["bg"])
    palette.setColor(role.Light, c["border_strong"])
    disabled = QPalette.ColorGroup.Disabled
    for r in (role.WindowText, role.Text, role.ButtonText):
        palette.setColor(disabled, r, c["muted_dim"])
    return palette


def _asset_dir() -> Path:
    """Stylesheet images (checkmarks, arrows) written once per run.

    QSS can only reference images by file path, and the icons are generated in
    code, so they are written out to a temporary folder.
    """
    from fitzlcd.ui import icons  # noqa: PLC0415 - icons imports this module

    target = Path(tempfile.gettempdir()) / "fitzlcd-theme"
    target.mkdir(parents=True, exist_ok=True)
    icons.write_svg("check", "#ffffff", target / "check.svg")
    icons.write_svg("chevron-down", COLORS["muted"], target / "down.svg")
    icons.write_svg("chevron-up", COLORS["muted"], target / "up.svg")
    return target


def build_stylesheet(assets: Path | None = None) -> str:
    c = COLORS
    a = (assets or Path(".")).as_posix()
    return f"""
QWidget {{ color: {c['text']}; }}
QMainWindow, QDialog {{ background: {c['bg']}; }}
QLabel {{ background: transparent; border: none; }}
QToolTip {{
    background: {c['surface_alt']}; color: {c['text']};
    border: 1px solid {c['border_strong']}; border-radius: 6px; padding: 6px 8px;
}}

/* Surfaces */
QFrame#card {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
}}
QFrame#card QLabel, QFrame#card QCheckBox {{ background: transparent; }}
QFrame#separator {{ background: {c['border']}; border: none; max-height: 1px; }}
QLabel#title {{ font-size: 15pt; font-weight: 600; }}
QLabel#heading {{ font-size: 11pt; font-weight: 600; }}
QLabel#section {{
    color: {c['muted']}; font-size: 8pt; font-weight: 600; letter-spacing: 0.5px;
}}
QLabel#muted {{ color: {c['muted']}; }}
QLabel#hint {{ color: {c['muted_dim']}; font-size: 8pt; }}

/* Inputs */
QLineEdit, QTextEdit, QPlainTextEdit, QSpinBox, QDoubleSpinBox, QComboBox, QFontComboBox {{
    background: {c['surface_alt']}; border: 1px solid {c['border']}; border-radius: 6px;
    padding: 4px 8px; selection-background-color: {c['accent']};
}}
/* min-height on a QTextEdit would override its fixed height, so single-line only. */
QLineEdit, QSpinBox, QDoubleSpinBox, QComboBox {{ min-height: 18px; }}
QLineEdit:hover, QTextEdit:hover, QSpinBox:hover, QDoubleSpinBox:hover, QComboBox:hover {{
    border-color: {c['border_strong']};
}}
QLineEdit:focus, QTextEdit:focus, QPlainTextEdit:focus, QSpinBox:focus,
QDoubleSpinBox:focus, QComboBox:focus {{
    border-color: {c['accent']};
}}
QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QComboBox:disabled {{
    color: {c['muted_dim']}; background: {c['surface']};
}}
QLineEdit#inlineTitle {{
    background: transparent; border: 1px solid transparent; font-size: 13pt; font-weight: 600;
    padding: 2px 6px;
}}
QLineEdit#inlineTitle:hover {{ border-color: {c['border']}; }}
QLineEdit#inlineTitle:focus {{ border-color: {c['accent']}; background: {c['surface_alt']}; }}
QComboBox::drop-down {{ border: none; width: 22px; }}
QComboBox::down-arrow {{ image: url({a}/down.svg); width: 10px; height: 10px; }}
QComboBox QAbstractItemView {{
    background: {c['surface_alt']}; border: 1px solid {c['border_strong']};
    selection-background-color: {c['accent']}; outline: 0; padding: 4px;
}}
QSpinBox, QDoubleSpinBox {{ padding-right: 20px; }}
QSpinBox::up-button, QDoubleSpinBox::up-button,
QSpinBox::down-button, QDoubleSpinBox::down-button {{
    subcontrol-origin: border; width: 18px; border: none; background: transparent;
}}
QSpinBox::up-button, QDoubleSpinBox::up-button {{ subcontrol-position: top right; }}
QSpinBox::down-button, QDoubleSpinBox::down-button {{ subcontrol-position: bottom right; }}
QSpinBox::up-arrow, QDoubleSpinBox::up-arrow {{
    image: url({a}/up.svg); width: 8px; height: 8px;
}}
QSpinBox::down-arrow, QDoubleSpinBox::down-arrow {{
    image: url({a}/down.svg); width: 8px; height: 8px;
}}
QCheckBox {{ spacing: 8px; background: transparent; }}
QCheckBox::indicator {{
    width: 16px; height: 16px; border-radius: 4px;
    border: 1px solid {c['border_strong']}; background: {c['surface_alt']};
}}
QCheckBox::indicator:hover {{ border-color: {c['accent']}; }}
QCheckBox::indicator:checked {{
    background: {c['accent']}; border-color: {c['accent']}; image: url({a}/check.svg);
}}
QSlider::groove:horizontal {{ height: 4px; background: {c['border_strong']}; border-radius: 2px; }}
QSlider::sub-page:horizontal {{ background: {c['accent']}; border-radius: 2px; }}
QSlider::handle:horizontal {{
    background: #ffffff; width: 14px; height: 14px; margin: -5px 0; border-radius: 7px;
}}
QSlider::groove:horizontal:disabled, QSlider::sub-page:horizontal:disabled {{
    background: {c['border']};
}}

/* Buttons */
QPushButton {{
    background: {c['surface_alt']}; border: 1px solid {c['border_strong']}; border-radius: 6px;
    padding: 5px 14px; min-height: 18px;
}}
QPushButton:hover {{ background: {c['surface_hover']}; }}
QPushButton:pressed {{ background: {c['border']}; }}
QPushButton:focus {{ border-color: {c['accent']}; }}
QPushButton:disabled {{ color: {c['muted_dim']}; background: {c['surface']}; }}
QPushButton:checked {{ background: {c['accent_soft']}; border-color: {c['accent']}; }}
QPushButton#primary {{ background: {c['accent']}; border-color: {c['accent']}; color: #ffffff; }}
QPushButton#primary:hover {{ background: {c['accent_hover']}; }}
QPushButton#danger {{ color: {c['danger']}; }}
QPushButton::menu-indicator {{ image: none; width: 0; }}
QToolButton {{
    background: transparent; border: 1px solid transparent; border-radius: 6px; padding: 4px;
}}
QToolButton:hover {{ background: {c['surface_hover']}; }}
QToolButton:pressed {{ background: {c['border']}; }}
QToolButton:checked {{ background: {c['accent_soft']}; }}
QToolButton:focus {{ border-color: {c['accent']}; }}
QToolButton::menu-indicator {{ image: none; width: 0; }}

/* Lists */
QListWidget, QListView, QTreeView, QScrollArea {{
    background: transparent; border: none; outline: 0;
}}
QListWidget::item {{ padding: 6px 8px; border-radius: 6px; }}
QListWidget::item:hover {{ background: {c['surface_hover']}; }}
QListWidget::item:selected {{ background: {c['accent_soft']}; color: {c['text']}; }}
QListWidget#gallery::item {{
    padding: 8px; margin: 4px; border: 2px solid transparent; border-radius: 10px;
}}
QListWidget#gallery::item:selected {{
    border-color: {c['accent']}; background: {c['accent_soft']};
}}
QScrollArea > QWidget > QWidget {{ background: transparent; }}

/* Scrollbars: slim, no arrow buttons */
QScrollBar:vertical {{ background: transparent; width: 10px; margin: 2px; }}
QScrollBar:horizontal {{ background: transparent; height: 10px; margin: 2px; }}
QScrollBar::handle:vertical, QScrollBar::handle:horizontal {{
    background: {c['border_strong']}; border-radius: 3px; min-height: 24px; min-width: 24px;
}}
QScrollBar::handle:hover {{ background: {c['muted_dim']}; }}
QScrollBar::add-line, QScrollBar::sub-line {{ width: 0; height: 0; }}
QScrollBar::add-page, QScrollBar::sub-page {{ background: transparent; }}

/* Group boxes (Settings) */
QGroupBox {{
    background: {c['surface']}; border: 1px solid {c['border']}; border-radius: 10px;
    margin-top: 22px; padding: 14px 12px 10px 12px;
}}
QGroupBox::title {{
    subcontrol-origin: margin; left: 4px; top: 0; padding: 0 2px;
    color: {c['muted']}; font-size: 8pt; font-weight: 600;
}}

/* Menus (also the tray menu) */
QMenu {{
    background: {c['surface_alt']}; border: 1px solid {c['border_strong']};
    border-radius: 8px; padding: 4px;
}}
QMenu::item {{ padding: 6px 22px 6px 10px; border-radius: 5px; }}
QMenu::item:selected {{ background: {c['accent']}; color: #ffffff; }}
QMenu::item:disabled {{ color: {c['muted_dim']}; }}
QMenu::separator {{ height: 1px; background: {c['border']}; margin: 4px 6px; }}
QMenu::icon {{ padding-left: 6px; }}

QSplitter::handle {{ background: transparent; }}
QStatusBar {{ background: {c['bg']}; color: {c['muted']}; }}
QStatusBar::item {{ border: none; }}
QProgressBar {{
    background: {c['surface_alt']}; border: none; border-radius: 4px; height: 8px;
    text-align: center;
}}
QProgressBar::chunk {{ background: {c['accent']}; border-radius: 4px; }}
"""


def dot_style(status: str) -> str:
    """The small round status indicator beside the connection sentence."""
    color = STATUS_COLORS.get(status, COLORS["muted"])
    return (
        f"background: {color}; border-radius: 5px;"
        " min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px;"
    )


def text_color(color: str) -> str:
    return f"color: {color};"
