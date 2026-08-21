"""GUI bootstrap: wires config, stats, engine, window and tray together."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import fitzlcd.render.layers  # noqa: F401 - registers the built-in layer types
from fitzlcd.config import AppConfig, SceneLibrary
from fitzlcd.engine import EngineConfig, RenderEngine
from fitzlcd.sources.stats import StatsRegistry
from fitzlcd.ui.main_window import MainWindow
from fitzlcd.ui.tray import TrayIcon

log = logging.getLogger(__name__)

DARK_QSS = """
QWidget { background: #10131c; color: #dbe1ef; font-size: 12px; }
QListWidget, QScrollArea, QLineEdit, QTextEdit, QComboBox, QSpinBox, QDoubleSpinBox {
    background: #161a26; border: 1px solid #232838; border-radius: 4px;
}
QListWidget::item:selected { background: #263048; }
QPushButton {
    background: #1d2334; border: 1px solid #2c3348; border-radius: 4px; padding: 4px 10px;
}
QPushButton:hover { background: #263048; }
QPushButton:checked { background: #3a2f10; border-color: #6d5714; }
QFrame { border: 1px solid #232838; border-radius: 4px; }
QSplitter::handle { background: #1b2030; }
"""


def run_gui(args=None) -> int:
    """Start the application. Returns the Qt exit code."""
    config = AppConfig.load()
    if args is not None:
        if getattr(args, "panel", None):
            config.panel = args.panel
        if getattr(args, "quality", None):
            config.quality = args.quality
        if getattr(args, "max_fps", None):
            config.max_fps = args.max_fps
        if getattr(args, "scene", None):
            config.active_scene = args.scene

    app = QApplication(sys.argv[:1])
    app.setApplicationName("FitzLCD")
    app.setQuitOnLastWindowClosed(False)  # the tray keeps the app alive
    app.setStyleSheet(DARK_QSS)

    stats = StatsRegistry.with_defaults()
    stats.start()

    engine = RenderEngine(
        EngineConfig(panel=config.panel, quality=config.quality, max_fps=config.max_fps),
        metrics_provider=stats,
    )

    library = SceneLibrary()
    window = MainWindow(engine, library, config)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayIcon(
            on_show=lambda: (window.showNormal(), window.raise_(), window.activateWindow()),
            on_pause=window.pause_button.setChecked,
            on_quit=app.quit,
        )
        tray.set_scenes([s.name for s in window.scenes], _scene_switcher(window))
        tray.show()
    else:
        log.warning("no system tray available; closing the window will quit")
        app.setQuitOnLastWindowClosed(True)

    engine.start()

    if not (config.start_minimised and tray is not None):
        window.show()

    def shutdown() -> None:
        engine.stop()
        stats.stop()
        if tray is not None:
            tray.hide()

    app.aboutToQuit.connect(shutdown)

    code = app.exec()

    # A wedged panel can leave the guarded open thread blocked inside the OS
    # serial driver, which stops the interpreter from finalising. The user asked
    # to quit; honour that rather than hanging on a stuck device.
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(code)


def _scene_switcher(window: MainWindow):
    def switch(name: str) -> None:
        for row, scene in enumerate(window.scenes):
            if scene.name == name:
                window.scene_list.setCurrentRow(row)
                return

    return switch
