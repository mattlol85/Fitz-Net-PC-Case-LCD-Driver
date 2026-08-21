"""Entry point: ``python -m fitzlcd`` (or the ``fitzlcd`` console script).

Runs the GUI by default. ``--headless`` drives the panel with no window, which is
what you want on a machine with no desktop session or when debugging the render
loop without Qt in the picture.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time


def _configure_logging(level: str | None = None) -> None:
    logging.basicConfig(
        level=(level or os.environ.get("FITZLCD_LOG") or "INFO").upper(),
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="fitzlcd", description=__doc__)
    parser.add_argument(
        "--panel",
        default=None,
        help="panel selector: 'auto', 'virtual', or an address like COM5",
    )
    parser.add_argument("--scene", help="name of the scene to activate on start")
    parser.add_argument(
        "--rotation",
        type=int,
        choices=(0, 90, 180, 270),
        help="how the panel is mounted, in degrees counter-clockwise",
    )
    parser.add_argument("--headless", action="store_true", help="run without the GUI")
    parser.add_argument("--quality", type=int, help="JPEG quality (1-100)")
    parser.add_argument("--max-fps", type=int, help="clamp the frame rate")
    parser.add_argument("--log", help="log level (DEBUG, INFO, WARNING)")
    parser.add_argument("--list-panels", action="store_true", help="print detected panels and exit")
    return parser


def _list_panels() -> int:
    from fitzlcd.panels import registry

    handles = registry.autodetect(include_virtual=True)
    if not handles:
        print("no panels detected")
        return 1
    for handle in handles:
        print(f"{handle.label:<16} {handle.address}")
    return 0


def run_headless(args: argparse.Namespace) -> int:
    import fitzlcd.render.layers  # noqa: F401 - registers the built-in layer types
    from fitzlcd.config import AppConfig, SceneLibrary
    from fitzlcd.engine import EngineConfig, RenderEngine
    from fitzlcd.sources.stats import StatsRegistry

    config = AppConfig.load()
    library = SceneLibrary()
    library.ensure_defaults()

    scenes = library.list()
    wanted = args.scene or config.active_scene
    scene = next((s for s in scenes if s.name == wanted), None) or (scenes[0] if scenes else None)
    if scene is None:
        print("no scenes available", file=sys.stderr)
        return 1

    stats = StatsRegistry.with_defaults()
    stats.start()

    engine = RenderEngine(
        EngineConfig(
            panel=args.panel or config.panel,
            rotation=args.rotation if args.rotation is not None else config.rotation,
            quality=args.quality or config.quality,
            max_fps=args.max_fps or config.max_fps,
        ),
        metrics_provider=stats,
    )
    engine.set_scene(scene)
    engine.on_stats = lambda s: print(
        f"[{s.status}] {s.panel_label or '-'} {s.address} "
        f"{s.fps:.1f} fps {s.bytes_per_second / 1e6:.2f} MB/s "
        f"{s.last_error}".rstrip()
    )
    engine.start()
    print(f"running scene {scene.name!r}; Ctrl+C to stop")

    try:
        while True:
            time.sleep(1.0)
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        engine.stop()
        stats.stop()
    return 0


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    _configure_logging(args.log)

    if args.list_panels:
        return _list_panels()
    if args.headless:
        return run_headless(args)

    from fitzlcd.ui.app import run_gui

    return run_gui(args)


if __name__ == "__main__":
    raise SystemExit(main())
