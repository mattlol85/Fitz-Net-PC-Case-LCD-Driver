"""GUI bootstrap: wires config, stats, engine, window and tray together."""

from __future__ import annotations

import logging
import os
import sys

from PySide6.QtWidgets import QApplication, QSystemTrayIcon

import fitzlcd.render.layers  # noqa: F401 - registers the built-in layer types
from fitzlcd import __version__
from fitzlcd.config import AppConfig, SceneLibrary, ensure_cs2_gsi_token
from fitzlcd.engine import EngineConfig, RenderEngine
from fitzlcd.sources.claude_limits import ClaudeLimitsProvider
from fitzlcd.sources.cs2gsi import Cs2GsiProvider, write_cs2_gsi_cfg
from fitzlcd.sources.stats import StatsRegistry
from fitzlcd.ui.main_window import MainWindow
from fitzlcd.ui.tray import TrayIcon
from fitzlcd.ui.updates import UpdateController

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
    config = ensure_cs2_gsi_token(config)
    cfg_path = write_cs2_gsi_cfg(config)
    log.info(
        "CS2 GSI config written to %s - copy it into <Steam>/steamapps/common/"
        "Counter-Strike Global Offensive/game/csgo/cfg/ and restart CS2",
        cfg_path,
    )

    # CLI overrides are for this session only. They must never land on `config`
    # itself: MainWindow holds that same object and calls config.save() during
    # ordinary startup (selecting the initial scene alone triggers one), which
    # would silently bake a one-off `--panel COM5`-style debugging flag into
    # the user's permanent settings.
    panel = config.panel
    quality = config.quality
    max_fps = config.max_fps
    rotation = config.rotation
    if args is not None:
        if getattr(args, "panel", None):
            panel = args.panel
        if getattr(args, "quality", None):
            quality = args.quality
        if getattr(args, "max_fps", None):
            max_fps = args.max_fps
        if getattr(args, "scene", None):
            config.active_scene = args.scene
        if getattr(args, "rotation", None) is not None:
            rotation = args.rotation

    app = QApplication(sys.argv[:1])
    app.setApplicationName("FitzLCD")
    app.setApplicationVersion(__version__)
    app.setQuitOnLastWindowClosed(False)  # the tray keeps the app alive
    app.setStyleSheet(DARK_QSS)

    stats = StatsRegistry.with_defaults(clock_24_hour=config.clock_24_hour)
    try:
        stats.add(Cs2GsiProvider(port=config.cs2_gsi_port, token=config.cs2_gsi_token))
    except OSError as exc:
        log.warning("CS2 GSI listener unavailable: %s", exc)
    if config.claude_limits_enabled:
        stats.add(ClaudeLimitsProvider(poll_seconds=config.claude_limits_poll_seconds))
    log.info("metric providers: %s", ", ".join(stats.provider_names))
    stats.start()

    engine = RenderEngine(
        EngineConfig(
            panel=panel,
            rotation=rotation,
            quality=quality,
            max_fps=max_fps,
            clock_24_hour=config.clock_24_hour,
        ),
        metrics_provider=stats,
    )

    library = SceneLibrary()
    window = MainWindow(engine, library, config, stats=stats)

    tray = None
    if QSystemTrayIcon.isSystemTrayAvailable():
        tray = TrayIcon(
            on_show=lambda: (window.showNormal(), window.raise_(), window.activateWindow()),
            on_pause=window.pause_button.setChecked,
            on_quit=app.quit,
            on_next=window.next_scene,
            on_previous=window.previous_scene,
        )
        tray.set_scenes([s.name for s in window.scenes], _scene_switcher(window))
        tray.show()
    else:
        log.warning("no system tray available; closing the window will quit")
        app.setQuitOnLastWindowClosed(True)

    # After the tray exists, so an available update can raise a notification.
    updates = UpdateController(window.update_btn, config, parent=window, tray=tray)
    updates.start()

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
