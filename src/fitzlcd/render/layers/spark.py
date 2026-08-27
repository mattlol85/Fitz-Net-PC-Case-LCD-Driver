"""The Claude spark: a tapered N-armed asterisk, drawn as vector geometry.

Deliberately not a text layer. The obvious way to put a mark like this on the
panel is a glyph - ``✻`` U+273B - but the fonts this app actually reaches for do
not have it: Consolas (what the ``mono`` alias resolves to on Windows) and
Segoe UI (``sans``) both render it as ``.notdef``, and only Segoe UI Symbol
covers it. Naming that face directly would leave a brand mark at the mercy of
whatever is installed on the machine the build lands on, and a missing glyph
shows up as a tofu box rather than as nothing. Drawing the arms as polygons
takes the font out of the question entirely, and scales to any size.

Same two constraints as :mod:`fitzlcd.render.layers.donut`, for the same
reasons: Pillow does not antialias polygon edges, so the mark is drawn
:data:`SUPERSAMPLE` times oversized and downscaled; and it is composited through
:func:`fitzlcd.render.draw.overlay` because ``ImageDraw`` writes alpha rather
than blending it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import ClassVar

from PIL import Image, ImageDraw

from fitzlcd.render.colors import parse_color
from fitzlcd.render.context import RenderContext
from fitzlcd.render.draw import overlay
from fitzlcd.render.geometry import ANCHORS, Length, parse_anchor, resolve_rect
from fitzlcd.render.scene import Field, Layer, layer_type

#: Draw this many times oversized, then LANCZOS down for smooth edges.
SUPERSAMPLE = 4


@layer_type("spark")
@dataclass
class SparkLayer(Layer):
    """A tapered N-armed asterisk, optionally turning."""

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field(
            "rect",
            "rect",
            "Rect",
            [40, 40, 120, 120],
            help="x, y, w, h -- the mark is the largest circle that fits",
        ),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("color", "color", "Colour", "#D97757"),
        Field("arms", "number", "Arms", 6, minimum=3, maximum=12),
        Field("arm_width", "number", "Arm width (px)", 10, minimum=1, maximum=200),
        Field(
            "taper",
            "number",
            "Tip width",
            0.3,
            minimum=0.0,
            maximum=1.0,
            help="tip width as a fraction of the base",
        ),
        Field("inner", "number", "Inner hole", 0.0, minimum=0.0, maximum=0.9),
        Field("angle", "number", "Angle", 0.0, minimum=-360.0, maximum=360.0),
        Field(
            "spin",
            "number",
            "Spin (deg/sec)",
            0.0,
            minimum=-360.0,
            maximum=360.0,
            help="0 keeps the mark still, and the scene cheap to render",
        ),
    )

    rect: tuple[Length, ...] | list[Length] = (40, 40, 120, 120)
    anchor: str = "top-left"
    color: str = "#D97757"
    arms: int = 6
    arm_width: int = 10
    taper: float = 0.3
    inner: float = 0.0
    angle: float = 0.0
    spin: float = 0.0

    @property
    def is_dynamic(self) -> bool:
        # A still spark must not force the whole scene to re-encode every tick.
        return self.spin != 0

    def describe(self) -> str:
        return self.name or f"spark x{int(self.arms)}"

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w <= 1 or h <= 1:
            return

        diameter = min(w, h)
        if diameter < 4:
            return
        # Centre the mark in a non-square rect rather than distorting it.
        offset_x = (w - diameter) // 2
        offset_y = (h - diameter) // 2

        fill = parse_color(self.color)
        if fill[3] == 0:
            return

        big = diameter * SUPERSAMPLE
        mark = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        mark_draw = ImageDraw.Draw(mark)

        centre = big / 2.0
        radius = centre
        base_r = radius * max(0.0, min(0.9, self.inner))
        half_base = max(1.0, self.arm_width * SUPERSAMPLE / 2.0)
        half_tip = half_base * max(0.0, min(1.0, self.taper))

        arms = max(3, min(12, int(self.arms)))
        turn = self.angle + self.spin * ctx.time

        for index in range(arms):
            theta = math.radians(turn + index * 360.0 / arms)
            dx, dy = math.cos(theta), math.sin(theta)
            # Perpendicular to the arm, so width is measured across it.
            px, py = -dy, dx
            bx, by = centre + dx * base_r, centre + dy * base_r
            tx, ty = centre + dx * radius, centre + dy * radius
            mark_draw.polygon(
                [
                    (bx + px * half_base, by + py * half_base),
                    (tx + px * half_tip, ty + py * half_tip),
                    (tx - px * half_tip, ty - py * half_tip),
                    (bx - px * half_base, by - py * half_base),
                ],
                fill=fill,
            )

        smooth = mark.resize((diameter, diameter), Image.Resampling.LANCZOS)
        with overlay(canvas, (x, y, w, h)) as (scratch, _draw):
            scratch.alpha_composite(smooth, (offset_x, offset_y))
