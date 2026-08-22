"""CS2 GSI visualisation layers: a fading history of hits, and a hit flash.

Both read metrics published by :class:`fitzlcd.sources.cs2gsi.Cs2GsiProvider`.
Neither carries a token in a string template -- they draw shapes directly from
structured metric values (a list of hit-event dicts, or a "how long ago" float)
rather than going through :mod:`fitzlcd.render.tokens`.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from PIL import Image

from fitzlcd.render.colors import parse_color, with_alpha
from fitzlcd.render.context import RenderContext
from fitzlcd.render.draw import overlay
from fitzlcd.render.geometry import ANCHORS, Length, parse_anchor, resolve_rect
from fitzlcd.render.scene import Field, Layer, layer_type


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _normalise(value: float, minimum: float, maximum: float) -> float:
    span = maximum - minimum
    if span == 0:
        return 0.0
    return max(0.0, min(1.0, (value - minimum) / span))


@layer_type("cs2_hit_timeline")
@dataclass
class Cs2HitTimelineLayer(Layer):
    """A scrolling history of recent hits to health/armor.

    Stateless by design: the provider already keeps the rolling event buffer,
    so this layer just re-reads the whole list every frame rather than
    accumulating its own history like :class:`~fitzlcd.render.layers.gauge.SparklineLayer`.
    """

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("metric", "text", "Hit events metric", "cs2.player.hit_events"),
        Field("rect", "rect", "Rect", [40, 40, 600, 120]),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("window_seconds", "number", "Window (s)", 10.0, minimum=1.0, maximum=60.0),
        Field("health_color", "color", "Health hit color", "#ff5f6d"),
        Field("armor_color", "color", "Armor hit color", "#ffcc00"),
        Field("min_amount", "number", "Min dmg drawn", 1.0, minimum=0.0, maximum=100.0),
        Field("max_amount", "number", "Max dmg (scale)", 60.0, minimum=1.0, maximum=200.0),
        Field("track_color", "color", "Track", "#ffffff14"),
        Field("baseline_color", "color", "Baseline", "#ffffff33"),
    )

    metric: str = "cs2.player.hit_events"
    rect: tuple[Length, ...] | list[Length] = (40, 40, 600, 120)
    anchor: str = "top-left"
    window_seconds: float = 10.0
    health_color: str = "#ff5f6d"
    armor_color: str = "#ffcc00"
    min_amount: float = 1.0
    max_amount: float = 60.0
    track_color: str = "#ffffff14"
    baseline_color: str = "#ffffff33"

    @property
    def is_dynamic(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name or "hit timeline"

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w <= 1 or h <= 1:
            return

        events = ctx.metrics.get(self.metric)
        if not isinstance(events, list):
            events = []

        now = time.monotonic()
        window = max(0.1, float(self.window_seconds))
        baseline_margin = max(2, h // 12)
        bar_width = max(3, w // 40)

        with overlay(canvas, (x, y, w, h)) as (_, draw):
            draw.rounded_rectangle(
                [0, 0, w - 1, h - 1],
                radius=max(2, baseline_margin),
                fill=parse_color(self.track_color),
            )
            baseline_y = h - 1 - baseline_margin
            draw.line([(0, baseline_y), (w - 1, baseline_y)], fill=parse_color(self.baseline_color))

            for event in events:
                age = now - float(event.get("t", now))
                if age < 0 or age > window:
                    continue
                amount = _as_float(event.get("amount"))
                if amount is None or amount < self.min_amount:
                    continue

                fraction_from_now = 1.0 - (age / window)
                bar_x = int(fraction_from_now * (w - bar_width))
                magnitude = _normalise(amount, self.min_amount, self.max_amount)
                bar_height = max(3, int(magnitude * (h - baseline_margin - 2)))
                fade = max(0.15, 1.0 - age / window)

                base_color = (
                    self.health_color if event.get("kind") == "health" else self.armor_color
                )
                color = with_alpha(parse_color(base_color), fade)
                draw.rounded_rectangle(
                    [bar_x, baseline_y - bar_height, bar_x + bar_width, baseline_y],
                    radius=min(3, bar_width // 2),
                    fill=color,
                )


@layer_type("cs2_hit_flash")
@dataclass
class Cs2HitFlashLayer(Layer):
    """A brief full-frame border flash on the most recent hit."""

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field(
            "seconds_ago_metric", "text", "Seconds-ago metric", "cs2.player.last_hit_seconds_ago"
        ),
        Field("kind_metric", "text", "Kind metric", "cs2.player.last_hit_kind"),
        Field("health_color", "color", "Health flash", "#ff5f6d"),
        Field("armor_color", "color", "Armor flash", "#ffcc00"),
        Field("duration", "number", "Fade duration (s)", 0.6, minimum=0.1, maximum=5.0),
        Field("thickness", "number", "Border thickness (px)", 24, minimum=1, maximum=200),
    )

    seconds_ago_metric: str = "cs2.player.last_hit_seconds_ago"
    kind_metric: str = "cs2.player.last_hit_kind"
    health_color: str = "#ff5f6d"
    armor_color: str = "#ffcc00"
    duration: float = 0.6
    thickness: int = 24

    @property
    def is_dynamic(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name or "hit flash"

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        seconds_ago = _as_float(ctx.metrics.get(self.seconds_ago_metric))
        duration = max(0.05, float(self.duration))
        if seconds_ago is None or seconds_ago < 0 or seconds_ago > duration:
            return

        kind = ctx.metrics.get(self.kind_metric)
        base_color = self.health_color if kind == "health" else self.armor_color
        fade = 1.0 - seconds_ago / duration
        thickness = max(1, int(self.thickness * fade))
        color = with_alpha(parse_color(base_color), 0.7 * fade)

        w, h = ctx.size
        with overlay(canvas) as (_, draw):
            draw.rectangle([0, 0, w - 1, thickness - 1], fill=color)
            draw.rectangle([0, h - thickness, w - 1, h - 1], fill=color)
            draw.rectangle([0, 0, thickness - 1, h - 1], fill=color)
            draw.rectangle([w - thickness, 0, w - 1, h - 1], fill=color)
