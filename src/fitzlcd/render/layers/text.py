"""Text and clock layers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from PIL import Image

from fitzlcd.render import tokens
from fitzlcd.render.colors import parse_color
from fitzlcd.render.context import RenderContext
from fitzlcd.render.draw import overlay
from fitzlcd.render.scene import Field, Layer, layer_type

ANCHORS = ("left", "center", "right")


@layer_type("text")
@dataclass
class TextLayer(Layer):
    """A line (or block) of text, optionally containing ``{metric}`` tokens."""

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("text", "multiline", "Text", "", help="Supports {cpu.load:.0f} style tokens"),
        Field("font", "text", "Font", "Segoe UI"),
        Field("size", "number", "Size", 48, minimum=6, maximum=400),
        Field("color", "color", "Color", "#FFFFFF"),
        Field("pos", "point", "Position", [40, 40]),
        Field("align", "choice", "Align", "left", choices=ANCHORS),
        Field("shadow", "bool", "Shadow", False),
    )

    text: str = ""
    font: str = "Segoe UI"
    size: int = 48
    color: str = "#FFFFFF"
    pos: tuple[int, int] | list[int] = (40, 40)
    align: str = "left"
    shadow: bool = False

    @property
    def is_dynamic(self) -> bool:
        return tokens.is_dynamic(self.text)

    def describe(self) -> str:
        return self.name or (self.text[:28] or "text")

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        rendered = tokens.expand(self.text, ctx.metrics)
        if not rendered:
            return
        face = ctx.font(self.font, self.size)
        x, y = int(self.pos[0]), int(self.pos[1])
        anchor = {"left": "la", "center": "ma", "right": "ra"}.get(self.align, "la")
        with overlay(canvas) as (_, draw):
            if self.shadow:
                nudge = max(2, self.size // 24)
                draw.multiline_text(
                    (x + nudge, y + nudge),
                    rendered,
                    font=face,
                    fill=(0, 0, 0, 160),
                    anchor=anchor,
                )
            draw.multiline_text(
                (x, y), rendered, font=face, fill=parse_color(self.color), anchor=anchor
            )


@layer_type("clock")
@dataclass
class ClockLayer(Layer):
    """Wall clock. Separate from a text layer so it needs no metric plumbing."""

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("format", "text", "Format", "%H:%M:%S", help="strftime format"),
        Field("font", "text", "Font", "Segoe UI"),
        Field("size", "number", "Size", 72, minimum=6, maximum=400),
        Field("color", "color", "Color", "#FFFFFF"),
        Field("pos", "point", "Position", [40, 40]),
        Field("align", "choice", "Align", "left", choices=ANCHORS),
    )

    format: str = "%H:%M:%S"
    font: str = "Segoe UI"
    size: int = 72
    color: str = "#FFFFFF"
    pos: tuple[int, int] | list[int] = (40, 40)
    align: str = "left"

    @property
    def is_dynamic(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name or f"clock {self.format}"

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        text = time.strftime(self.format)
        anchor = {"left": "la", "center": "ma", "right": "ra"}.get(self.align, "la")
        with overlay(canvas) as (_, draw):
            draw.text(
                (int(self.pos[0]), int(self.pos[1])),
                text,
                font=ctx.font(self.font, self.size),
                fill=parse_color(self.color),
                anchor=anchor,
            )
