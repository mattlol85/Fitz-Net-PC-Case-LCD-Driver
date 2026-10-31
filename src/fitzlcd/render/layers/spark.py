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

Motion, all opt-in: ``spin`` turns the mark at a steady rate; ``breathe`` swells
and shrinks the arms while idle; and ``active_metric`` ties the spin speed and a
glow to a reading (for Claude, whether Claude Code is working right now). Only
the activity-linked path keeps state, because its spin rate changes and the
angle has to be integrated to avoid jumping.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import ClassVar

from PIL import Image, ImageDraw

from fitzlcd.render.colors import mix, parse_color
from fitzlcd.render.context import RenderContext
from fitzlcd.render.draw import overlay
from fitzlcd.render.geometry import ANCHORS, Length, parse_anchor, resolve_rect
from fitzlcd.render.motion import MAX_STEP, approach, as_finite_float, pulse_wave
from fitzlcd.render.scene import Field, Layer, layer_type

#: Draw this many times oversized, then LANCZOS down for smooth edges.
SUPERSAMPLE = 4

#: Seconds for the activity level to follow its reading. Short enough to feel
#: responsive, long enough that a reading flickering on and off does not stutter.
ACTIVITY_TAU = 0.6

#: Breathing rate of the arms while idle, in cycles per second.
BREATHE_HZ = 0.25


@dataclass
class _Activity:
    """Integrated motion for an activity-linked mark."""

    energy: float = 0.0
    phase: float = 0.0  # degrees, kept in [0, 360)
    last_time: float | None = None


@layer_type("spark")
@dataclass
class SparkLayer(Layer):
    """A tapered N-armed asterisk, optionally turning."""

    display_name: ClassVar[str] = "Starburst"
    summary: ClassVar[str] = "A decorative star mark that can spin"
    icon: ClassVar[str] = "sparkle"

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
            advanced=True,
        ),
        Field("inner", "number", "Inner hole", 0.0, minimum=0.0, maximum=0.9, advanced=True),
        Field("angle", "number", "Angle", 0.0, minimum=-360.0, maximum=360.0, advanced=True),
        Field(
            "spin",
            "number",
            "Spin (deg/sec)",
            0.0,
            minimum=-360.0,
            maximum=360.0,
            help="0 keeps the mark still, and the scene cheap to render",
        ),
        Field(
            "breathe",
            "number",
            "Breathe",
            0.0,
            minimum=0.0,
            maximum=0.5,
            section="animation",
            help="How far the arms swell and shrink while the mark is idle",
        ),
        Field(
            "active_metric",
            "metric",
            "Activity metric",
            "",
            section="animation",
            help="While this reads above 0, the mark spins faster and glows. "
            "Blank keeps it on its normal spin",
        ),
        Field(
            "active_spin",
            "number",
            "Spin when active (deg/sec)",
            90.0,
            minimum=-360.0,
            maximum=360.0,
            section="animation",
        ),
        Field(
            "active_glow",
            "number",
            "Glow when active",
            0.0,
            minimum=0.0,
            maximum=1.0,
            section="animation",
            help="How far the colour lifts toward white at full activity",
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
    breathe: float = 0.0
    active_metric: str = ""
    active_spin: float = 90.0
    active_glow: float = 0.0
    #: Integrated activity and angle. Not part of the scene document.
    _activity: _Activity = field(default_factory=_Activity, init=False, repr=False, compare=False)

    @property
    def is_dynamic(self) -> bool:
        # A still spark must not force the whole scene to re-encode every tick.
        return self.spin != 0 or self.breathe > 0 or bool(self.active_metric)

    def describe(self) -> str:
        return self.name or f"spark x{int(self.arms)}"

    def _breath(self, time: float) -> float:
        """Arm-length scale at this instant: 1.0 at full length, less at the trough."""
        if self.breathe <= 0:
            return 1.0
        return 1.0 - self.breathe * (1.0 - pulse_wave(time, BREATHE_HZ))

    def _advance(self, ctx: RenderContext) -> tuple[float, float]:
        """Eased activity level (0..1) and integrated spin angle in degrees.

        The angle is stepped rather than computed from ``ctx.time``: the spin rate
        changes with activity, and a closed-form angle would jump each time it did.
        A reading that is missing or unreadable idles the mark. It does not draw a
        number, so treating it as inactive is a motion choice, not a published value.
        """
        reading = as_finite_float(ctx.metrics.get(self.active_metric))
        target = 0.0 if reading is None else max(0.0, min(1.0, reading))
        state = self._activity

        if state.last_time is None or ctx.time < state.last_time:
            state.energy = target
            state.phase = 0.0
            state.last_time = ctx.time
            return state.energy, state.phase

        dt = min(max(ctx.time - state.last_time, 0.0), MAX_STEP)
        state.last_time = ctx.time
        state.energy = approach(state.energy, target, dt, ACTIVITY_TAU)
        speed = self.spin + (self.active_spin - self.spin) * state.energy
        state.phase = (state.phase + speed * dt) % 360.0
        return state.energy, state.phase

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

        if self.active_metric:
            energy, phase = self._advance(ctx)
            fill = mix(fill, (255, 255, 255, fill[3]), self.active_glow * energy)
            turn = self.angle + phase
        else:
            turn = self.angle + self.spin * ctx.time

        big = diameter * SUPERSAMPLE
        mark = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        mark_draw = ImageDraw.Draw(mark)

        centre = big / 2.0
        radius = centre * self._breath(ctx.time)
        base_r = centre * max(0.0, min(0.9, self.inner))
        half_base = max(1.0, self.arm_width * SUPERSAMPLE / 2.0)
        half_tip = half_base * max(0.0, min(1.0, self.taper))

        arms = max(3, min(12, int(self.arms)))

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
