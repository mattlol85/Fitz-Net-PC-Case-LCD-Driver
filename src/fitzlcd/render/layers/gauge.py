"""Metric-driven layers: bar gauges and rolling sparklines."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import ClassVar

from PIL import Image

from fitzlcd.render.colors import parse_color
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


@layer_type("gauge")
@dataclass
class GaugeLayer(Layer):
    """Horizontal or vertical bar showing one metric against a range."""

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("metric", "text", "Metric", "cpu.load"),
        Field(
            "rect",
            "rect",
            "Rect",
            [40, 40, 400, 48],
            help="x, y, w, h -- px, -px from the far edge, or percentages",
        ),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("minimum", "number", "Min", 0.0),
        Field("maximum", "number", "Max", 100.0),
        Field("color", "color", "Fill", "#FFCC00"),
        Field("track_color", "color", "Track", "#FFFFFF22"),
        Field("border_color", "color", "Border", "#00000000"),
        Field("vertical", "bool", "Vertical", False),
        Field("radius", "number", "Corner radius", 6, minimum=0, maximum=200),
    )

    metric: str = "cpu.load"
    rect: tuple[Length, ...] | list[Length] = (40, 40, 400, 48)
    anchor: str = "top-left"
    minimum: float = 0.0
    maximum: float = 100.0
    color: str = "#FFCC00"
    track_color: str = "#FFFFFF22"
    border_color: str = "#00000000"
    vertical: bool = False
    radius: int = 6

    @property
    def is_dynamic(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name or f"gauge {self.metric}"

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w <= 0 or h <= 0:
            return
        radius = max(0, min(int(self.radius), min(w, h) // 2))
        value = _as_float(ctx.metrics.get(self.metric))

        with overlay(canvas, (x, y, w, h)) as (_, draw):
            draw.rounded_rectangle(
                [0, 0, w - 1, h - 1],
                radius=radius,
                fill=parse_color(self.track_color),
                outline=parse_color(self.border_color),
            )

            if value is None:
                return  # unknown metric: an empty track beats a wrong reading
            fraction = _normalise(value, self.minimum, self.maximum)
            if fraction <= 0:
                return

            if self.vertical:
                filled = max(1, int(h * fraction))
                box = [0, h - filled, w - 1, h - 1]
            else:
                filled = max(1, int(w * fraction))
                box = [0, 0, filled - 1, h - 1]
            draw.rounded_rectangle(box, radius=radius, fill=parse_color(self.color))


@layer_type("sparkline")
@dataclass
class SparklineLayer(Layer):
    """Rolling history of one metric.

    History lives on the layer instance rather than in the scene document: it is
    runtime state, not something to serialise.
    """

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("metric", "text", "Metric", "cpu.load"),
        Field("rect", "rect", "Rect", [40, 40, 400, 80]),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("minimum", "number", "Min", 0.0),
        Field("maximum", "number", "Max", 100.0),
        Field("color", "color", "Line", "#00E5FF"),
        Field("fill_color", "color", "Fill", "#00E5FF33"),
        Field("samples", "number", "Samples", 60, minimum=2, maximum=1000),
        Field("width", "number", "Line width", 3, minimum=1, maximum=20),
    )

    metric: str = "cpu.load"
    rect: tuple[Length, ...] | list[Length] = (40, 40, 400, 80)
    anchor: str = "top-left"
    minimum: float = 0.0
    maximum: float = 100.0
    color: str = "#00E5FF"
    fill_color: str = "#00E5FF33"
    samples: int = 60
    width: int = 3

    _history: deque[float] = field(default_factory=deque, repr=False, compare=False)

    @property
    def is_dynamic(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name or f"sparkline {self.metric}"

    def to_dict(self):  # history is runtime state, never serialised
        data = super().to_dict()
        data.pop("_history", None)
        return data

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w <= 1 or h <= 1:
            return

        capacity = max(2, int(self.samples))
        if self._history.maxlen != capacity:
            self._history = deque(self._history, maxlen=capacity)

        value = _as_float(ctx.metrics.get(self.metric))
        if value is not None:
            self._history.append(_normalise(value, self.minimum, self.maximum))
        if len(self._history) < 2:
            return

        step = w / (capacity - 1)
        offset = capacity - len(self._history)
        points = [
            (x + (offset + i) * step, y + h - 1 - v * (h - 1)) for i, v in enumerate(self._history)
        ]

        with overlay(canvas, (x, y, w, h)) as (_, draw):
            local = [(px - x, py - y) for px, py in points]
            fill = parse_color(self.fill_color)
            if fill[3] > 0:
                draw.polygon([(local[0][0], h), *local, (local[-1][0], h)], fill=fill)
            draw.line(
                local,
                fill=parse_color(self.color),
                width=max(1, int(self.width)),
                joint="curve",
            )
