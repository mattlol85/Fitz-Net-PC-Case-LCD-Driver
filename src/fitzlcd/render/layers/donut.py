"""Circular progress gauge: a ring showing one metric against a range.

The round counterpart to :class:`~fitzlcd.render.layers.gauge.GaugeLayer`. Reads
for percentages the way a bar does not - "62% of my weekly limit" lands faster as
a filled arc than as a bar, and two rings side by side compare at a glance.

Pillow's ``arc`` is not antialiased, and a jagged ring is very visible on a panel
this size, so the ring is drawn at :data:`SUPERSAMPLE` times the target size and
downscaled. The box is small, so that costs almost nothing.

Centre text is part of this layer rather than a separate text layer on purpose:
centring text inside the ring by hand means duplicating the ring's own geometry
in a second layer's coordinates, and the two drift apart the moment the panel is
rotated or the rect changes.

Motion is opt-in and off by default, so an existing scene renders exactly as it
did. ``sweep_in`` fills the ring from empty when the scene starts; ``glide``
eases to a new reading; ``gain_flash`` lights up the slice a rise has just added
before it cools to the fill colour; ``pulse`` dims the fill in a slow rhythm past
the warn threshold. The centre text always shows the true reading, never the
eased one. See :mod:`fitzlcd.render.motion` for the stepping rules.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import ClassVar

from PIL import Image, ImageDraw

from fitzlcd.render import tokens
from fitzlcd.render.colors import mix, parse_color
from fitzlcd.render.context import RenderContext
from fitzlcd.render.draw import overlay
from fitzlcd.render.geometry import ANCHORS, Length, parse_anchor, resolve_rect
from fitzlcd.render.motion import ValueGlide, as_finite_float, ease_out_cubic, pulse_wave
from fitzlcd.render.scene import Field, Layer, layer_type

#: Draw the ring this many times oversized, then LANCZOS down for smooth edges.
SUPERSAMPLE = 4

#: Pillow measures angles from 3 o'clock; scenes want to start at the top.
TOP_OF_CIRCLE = -90.0

#: How far a warning-level fill dims at the bottom of a pulse, as a fraction of
#: the way toward the track colour.
PULSE_DEPTH = 0.45


def _normalise(value: float, minimum: float, maximum: float) -> float:
    span = maximum - minimum
    if span == 0:
        return 0.0
    return max(0.0, min(1.0, (value - minimum) / span))


@layer_type("donut")
@dataclass
class DonutLayer(Layer):
    """Ring gauge with optional centred readout."""

    display_name: ClassVar[str] = "Ring"
    summary: ClassVar[str] = "A circular gauge with a readout in the middle"
    icon: ClassVar[str] = "ring"

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("metric", "metric", "Reading", "cpu.load"),
        Field(
            "rect",
            "rect",
            "Rect",
            [40, 40, 200, 200],
            help="x, y, w, h -- the ring is the largest circle that fits",
        ),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("minimum", "number", "Lowest value", 0.0),
        Field("maximum", "number", "Highest value", 100.0),
        Field(
            "thickness",
            "number",
            "Ring width (px)",
            18,
            minimum=1,
            maximum=200,
            section="appearance",
        ),
        Field(
            "start_angle",
            "number",
            "Start angle",
            TOP_OF_CIRCLE,
            minimum=-360,
            maximum=360,
            advanced=True,
        ),
        Field("clockwise", "bool", "Clockwise", True, advanced=True),
        Field("color", "color", "Fill", "#00E5FF"),
        Field("track_color", "color", "Track", "#FFFFFF1A"),
        Field("warn_value", "number", "Warn at", 75.0, section="appearance"),
        Field("warn_color", "color", "Warn fill", "#FFCC00"),
        Field("critical_value", "number", "Critical at", 90.0, section="appearance"),
        Field("critical_color", "color", "Critical fill", "#FF5F6D"),
        Field("text", "text", "Centre text", "", help="Use Insert live value to add readings"),
        Field(
            "text_size", "number", "Centre size", 44, minimum=1, maximum=400, section="appearance"
        ),
        Field("text_color", "color", "Centre colour", "#E8EDFB"),
        Field("sub_text", "text", "Sub text", ""),
        Field("sub_size", "number", "Sub size", 20, minimum=1, maximum=400, section="appearance"),
        Field("sub_color", "color", "Sub colour", "#8892B0"),
        Field("font", "font", "Font", "sans", section="appearance"),
        Field(
            "sweep_in",
            "number",
            "Sweep in (s)",
            0.0,
            minimum=0.0,
            maximum=5.0,
            section="animation",
            help="Fill from empty over this long when the scene starts. Keep at 1 s or "
            "less so gallery thumbnails show a finished ring.",
        ),
        Field(
            "glide",
            "number",
            "Glide (s)",
            0.0,
            minimum=0.0,
            maximum=10.0,
            section="animation",
            help="Ease to a new reading over this long instead of jumping",
        ),
        Field(
            "gain_flash",
            "number",
            "Added flash (s)",
            0.0,
            minimum=0.0,
            maximum=10.0,
            section="animation",
            help="When the reading rises, light up the slice it added and cool it to the "
            "fill colour over this long",
        ),
        Field(
            "gain_color",
            "color",
            "Added colour",
            "#FFFFFF",
            section="animation",
        ),
        Field(
            "pulse",
            "number",
            "Warn pulse (Hz)",
            0.0,
            minimum=0.0,
            maximum=4.0,
            section="animation",
            help="Pulse the fill past the warn threshold, twice as fast past critical. "
            "0 keeps it steady",
        ),
    )

    metric: str = "cpu.load"
    rect: tuple[Length, ...] | list[Length] = (40, 40, 200, 200)
    anchor: str = "top-left"
    minimum: float = 0.0
    maximum: float = 100.0
    thickness: int = 18
    start_angle: float = TOP_OF_CIRCLE
    clockwise: bool = True
    color: str = "#00E5FF"
    track_color: str = "#FFFFFF1A"
    warn_value: float = 75.0
    warn_color: str = "#FFCC00"
    critical_value: float = 90.0
    critical_color: str = "#FF5F6D"
    text: str = ""
    text_size: int = 44
    text_color: str = "#E8EDFB"
    sub_text: str = ""
    sub_size: int = 20
    sub_color: str = "#8892B0"
    font: str = "sans"
    sweep_in: float = 0.0
    glide: float = 0.0
    gain_flash: float = 0.0
    gain_color: str = "#FFFFFF"
    pulse: float = 0.0
    #: Eased reading and rise memory. Not part of the scene document.
    _motion: ValueGlide = field(default_factory=ValueGlide, init=False, repr=False, compare=False)

    @property
    def is_dynamic(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name or f"donut {self.metric}"

    def _fill_for(self, value: float) -> str:
        """Threshold colour. Thresholds above ``maximum`` are effectively off."""
        if value >= self.critical_value:
            return self.critical_color
        if value >= self.warn_value:
            return self.warn_color
        return self.color

    def _fraction(self, value: float) -> float:
        return _normalise(value, self.minimum, self.maximum)

    def _arc(self, start_fraction: float, end_fraction: float) -> tuple[float, float]:
        """Pillow's (start, end) angles for the stretch of sweep between two fractions."""
        direction = 1.0 if self.clockwise else -1.0
        a = self.start_angle + 360.0 * start_fraction * direction
        b = self.start_angle + 360.0 * end_fraction * direction
        return (min(a, b), max(a, b))

    def _pulsed(self, fill: tuple[int, int, int, int], value: float, now: float):
        """Dim the fill in a slow rhythm while the reading is past the warn threshold."""
        if self.pulse <= 0 or value < self.warn_value:
            return fill
        hz = self.pulse * (2.0 if value >= self.critical_value else 1.0)
        return mix(fill, parse_color(self.track_color), PULSE_DEPTH * pulse_wave(now, hz))

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w <= 1 or h <= 1:
            return

        diameter = min(w, h)
        if diameter < 4:
            return
        # Centre the circle in a non-square rect rather than distorting it.
        offset_x = (w - diameter) // 2
        offset_y = (h - diameter) // 2

        shown = self._motion.step(
            as_finite_float(ctx.metrics.get(self.metric)), ctx.time, self.glide
        )
        # The sweep scales the drawn fraction, not the value, so thresholds and the
        # centre text keep the real reading while the ring is still filling.
        sweep = ease_out_cubic(ctx.time / self.sweep_in) if self.sweep_in > 0 else 1.0
        rise_from = self._motion.gain_from
        rise_progress = self._motion.gain_progress(ctx.time, self.gain_flash)
        thickness = max(1, min(int(self.thickness), diameter // 2))

        big = diameter * SUPERSAMPLE
        ring = Image.new("RGBA", (big, big), (0, 0, 0, 0))
        ring_draw = ImageDraw.Draw(ring)
        # Pillow grows the stroke *inward* from the bounding box, so an un-inset
        # box puts the ring's outer edge exactly on the rect - insetting would
        # shrink the ring by the stroke width instead of centring it.
        box = [0, 0, big - 1, big - 1]
        stroke = thickness * SUPERSAMPLE

        track = parse_color(self.track_color)
        if track[3] > 0:
            ring_draw.arc(box, 0, 360, fill=track, width=stroke)

        if shown is not None:
            fill = self._pulsed(parse_color(self._fill_for(shown)), shown, ctx.time)
            end = self._fraction(shown) * sweep
            if end > 0:
                ring_draw.arc(box, *self._arc(0.0, end), fill=fill, width=stroke)

            if rise_progress is not None and rise_from is not None:
                start = self._fraction(rise_from) * sweep
                if end > start:
                    tint = mix(parse_color(self.gain_color), fill, ease_out_cubic(rise_progress))
                    ring_draw.arc(box, *self._arc(start, end), fill=tint, width=stroke)

        smooth = ring.resize((diameter, diameter), Image.Resampling.LANCZOS)

        with overlay(canvas, (x, y, w, h)) as (scratch, draw):
            scratch.alpha_composite(smooth, (offset_x, offset_y))
            self._draw_centre_text(draw, ctx, offset_x + diameter / 2, offset_y + diameter / 2)

    def _draw_centre_text(
        self, draw: ImageDraw.ImageDraw, ctx: RenderContext, cx: float, cy: float
    ) -> None:
        main = tokens.expand(self.text, ctx.metrics)
        sub = tokens.expand(self.sub_text, ctx.metrics)
        if not main and not sub:
            return

        if main and sub:
            # Lift the pair so the block as a whole reads as centred.
            main_y = cy - self.sub_size * 0.55
            sub_y = main_y + self.text_size * 0.62 + self.sub_size * 0.55
        else:
            main_y = sub_y = cy

        if main:
            draw.text(
                (cx, main_y),
                main,
                font=ctx.font(self.font, self.text_size),
                fill=parse_color(self.text_color),
                anchor="mm",
                align="center",
            )
        if sub:
            draw.text(
                (cx, sub_y),
                sub,
                font=ctx.font(self.font, self.sub_size),
                fill=parse_color(self.sub_color),
                anchor="mm",
                align="center",
            )
