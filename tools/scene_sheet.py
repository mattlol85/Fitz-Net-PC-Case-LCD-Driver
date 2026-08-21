"""Render every scene at every orientation into one contact sheet.

This is how orientation support is checked: a layout regression is obvious at a
glance, and it needs no hardware.

    python tools/scene_sheet.py [-o build/scene-sheet.png] [--rotations 0,90]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from PIL import Image, ImageDraw  # noqa: E402

import fitzlcd.render.layers  # noqa: E402, F401 - registers layer types
from fitzlcd.panels.base import PanelCaps  # noqa: E402
from fitzlcd.render.compositor import Compositor  # noqa: E402
from fitzlcd.render.context import RenderContext, load_font  # noqa: E402
from fitzlcd.render.encode import Transform  # noqa: E402
from fitzlcd.render.scene import Scene  # noqa: E402
from fitzlcd.scenes_builtin import DEFAULT_SCENES  # noqa: E402
from fitzlcd.sources.stats import StatsRegistry  # noqa: E402

NATIVE = PanelCaps(1920, 462, Transform.ROT_270)
MARGIN = 16
LABEL_H = 26


def warm_up(scene: Scene, compositor: Compositor, metrics: dict, frames: int = 30):
    """Sparklines need history before they draw anything."""
    frame = None
    for i in range(frames):
        ctx = RenderContext(
            compositor.width, compositor.height, time=i / 10, frame_index=i, metrics=metrics
        )
        frame = compositor.compose(scene, ctx)
    return frame


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("-o", "--out", default=str(ROOT / "build" / "scene-sheet.png"))
    ap.add_argument("--rotations", default="0,90,180,270")
    ap.add_argument("--scale", type=float, default=0.5)
    ap.add_argument("--scene", help="only this scene")
    ap.add_argument(
        "--columns",
        type=int,
        default=0,
        help="scenes per row (default: 2 for wide tiles, all for tall ones)",
    )
    args = ap.parse_args(argv)

    rotations = [int(r) for r in args.rotations.split(",")]
    stats = StatsRegistry.with_defaults()
    metrics = stats.poll_once()
    stats.stop()
    # Plausible non-zero values so gauges and graphs are not all empty.
    metrics.setdefault("cpu.load", 37.0)
    metrics["net.down"] = max(metrics.get("net.down") or 0, 4.2)
    metrics["net.up"] = max(metrics.get("net.up") or 0, 1.1)

    names = [args.scene] if args.scene else list(DEFAULT_SCENES)
    tiles: dict[tuple[str, int], Image.Image] = {}
    for name in names:
        scene = Scene.from_dict(DEFAULT_SCENES[name])
        for rotation in rotations:
            caps = NATIVE.rotated(rotation)
            compositor = Compositor(caps.width, caps.height)
            frame = warm_up(scene, compositor, metrics)
            tiles[(name, rotation)] = frame.resize(
                (int(frame.width * args.scale), int(frame.height * args.scale)),
                Image.LANCZOS,
            )

    col_w = max(t.width for t in tiles.values())
    tile_h = max(t.height for t in tiles.values())
    # A row of 1920-wide strips is unreadable at any sane sheet width, so wide
    # tiles wrap onto multiple rows while tall ones sit side by side.
    columns = args.columns or (2 if col_w > tile_h else len(names))
    columns = max(1, min(columns, len(names)))

    blocks: list[tuple[int, list[str]]] = []
    for rotation in rotations:
        for start in range(0, len(names), columns):
            blocks.append((rotation, names[start : start + columns]))

    row_h = {
        rotation: max(t.height for (_, r), t in tiles.items() if r == rotation)
        for rotation in rotations
    }
    sheet_w = MARGIN + columns * (col_w + MARGIN)
    sheet_h = MARGIN + sum(row_h[r] + 2 * LABEL_H + MARGIN for r, _ in blocks)

    sheet = Image.new("RGB", (sheet_w, sheet_h), (18, 20, 28))
    draw = ImageDraw.Draw(sheet)
    label_font = load_font("sans", 18)
    caption_font = load_font("sans", 15)

    y = MARGIN
    for rotation, group in blocks:
        caps = NATIVE.rotated(rotation)
        draw.text(
            (MARGIN, y),
            f"{rotation}°   {caps.width}x{caps.height}   transform {caps.transform.value}",
            font=label_font,
            fill="#8892b0",
        )
        y += LABEL_H
        for column, name in enumerate(group):
            tile = tiles[(name, rotation)]
            x = MARGIN + column * (col_w + MARGIN) + (col_w - tile.width) // 2
            draw.text((x, y), DEFAULT_SCENES[name]["name"], font=caption_font, fill="#e8edfb")
            sheet.paste(tile, (x, y + LABEL_H))
            draw.rectangle(
                [x - 1, y + LABEL_H - 1, x + tile.width, y + LABEL_H + tile.height],
                outline="#2c3348",
            )
        y += row_h[rotation] + LABEL_H + MARGIN

    out = Path(args.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    print(f"saved {out}  ({sheet.width}x{sheet.height}, {len(tiles)} tiles)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
