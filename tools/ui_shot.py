"""Render the real main window offscreen and save it as a PNG.

Useful for reviewing layout changes without a desktop session (CI, a remote
shell, or an agent), and for attaching before/after images to a change. It builds
the actual widget tree - not a mock - feeds it one composed frame and a fake
stats snapshot, then grabs the window.

    python tools/ui_shot.py [-o build/gui-screenshot.png] [--scene "Rig Stats"]
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("FITZLCD_HOME", str(ROOT / "build" / "apphome"))

from PySide6.QtGui import QFont, QFontDatabase  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import fitzlcd.render.layers  # noqa: E402, F401 - registers layer types
from fitzlcd.config import AppConfig, SceneLibrary  # noqa: E402
from fitzlcd.engine import EngineStats, RenderEngine  # noqa: E402
from fitzlcd.render.compositor import Compositor  # noqa: E402
from fitzlcd.render.context import RenderContext  # noqa: E402
from fitzlcd.sources.stats import StatsRegistry  # noqa: E402
from fitzlcd.ui.main_window import MainWindow  # noqa: E402

#: The offscreen Qt platform ships with no font database at all, so widget text
#: renders as tofu boxes unless a real font file is loaded by hand. This affects
#: only screenshots taken this way - the app on a desktop uses the normal
#: Windows platform plugin and its system fonts.
WINDOWS_FONTS = Path("C:/Windows/Fonts")
PREFERRED_FONTS = ("segoeui.ttf", "arial.ttf", "tahoma.ttf")


def _install_fonts(app: QApplication) -> None:
    if QFontDatabase.families():
        return
    for name in PREFERRED_FONTS:
        path = WINDOWS_FONTS / name
        if not path.exists():
            continue
        font_id = QFontDatabase.addApplicationFont(str(path))
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            app.setFont(QFont(families[0], 9))
            print(f"loaded font {families[0]} from {name}")
            return
    print("warning: no font could be loaded; text will render as boxes")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=str(ROOT / "build" / "gui-screenshot.png"))
    ap.add_argument("--scene", default="Rig Stats")
    ap.add_argument("--size", default="1180x760")
    args = ap.parse_args(argv)

    width, _, height = args.size.partition("x")
    app = QApplication(sys.argv[:1])
    _install_fonts(app)

    from fitzlcd.ui.app import DARK_QSS

    app.setStyleSheet(DARK_QSS)

    config = AppConfig.load()
    config.active_scene = args.scene
    library = SceneLibrary()
    library.ensure_defaults()

    engine = RenderEngine()  # never started: this is a layout shot, not a run
    window = MainWindow(engine, library, config)
    window.resize(int(width), int(height))

    # Feed one real composed frame plus a plausible stats snapshot so the
    # screenshot shows the window in its normal working state.
    stats = StatsRegistry.with_defaults()
    metrics = stats.poll_once()
    stats.stop()

    if window.current_scene is not None:
        frame = Compositor(1920, 462).compose(
            window.current_scene, RenderContext(1920, 462, metrics=metrics)
        )
        window.preview._on_frame(frame)

    shot_stats = EngineStats(
        connected=True,
        panel_label="Jonsbo DS916",
        address="COM5",
        model="D215-FL7707N-9.16inch-hor",
        firmware="2.2",
        width=1920,
        height=462,
        fps=10.0,
        bytes_per_second=0.57e6,
        frames_sent=128,
        frames_skipped=0,
        scene_name=args.scene,
    )
    if window.layer_list.count():
        window.layer_list.setCurrentRow(0)

    window.show()
    # Drain queued signals first: the window emits its own (disconnected)
    # stats while loading scenes, which would otherwise land after ours.
    app.processEvents()
    window._render_stats(shot_stats)
    app.processEvents()

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    window.grab().save(str(out))
    print(f"saved {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
