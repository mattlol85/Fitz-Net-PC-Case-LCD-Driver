"""Text and clock layers."""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import ClassVar

from PIL import Image

from fitzlcd.render import tokens
from fitzlcd.render.colors import parse_color
from fitzlcd.render.context import RenderContext, format_clock
from fitzlcd.render.draw import overlay
from fitzlcd.render.geometry import ANCHORS, Length, parse_anchor, resolve_point, text_anchor
from fitzlcd.render.scene import Field, Layer, layer_type

ALIGNMENTS = ("left", "center", "right")

#: Line spacing as a multiple of the font size. Pillow's default is tight for
#: the block-of-text look the terminal scenes want.
LINE_SPACING = 0.35


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
        Field(
            "pos",
            "point",
            "Position",
            [40, 40],
            help="px, -px from the far edge, or '50%' / 'center'",
        ),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("align", "choice", "Align", "left", choices=ALIGNMENTS),
        Field(
            "fit",
            "bool",
            "Shrink to fit",
            True,
            help="Reduce the size until the text fits the frame",
        ),
        Field("shadow", "bool", "Shadow", False),
    )

    text: str = ""
    font: str = "Segoe UI"
    size: int = 48
    color: str = "#FFFFFF"
    pos: tuple[Length, Length] | list[Length] = (40, 40)
    anchor: str = "top-left"
    align: str = "left"
    fit: bool = True
    shadow: bool = False

    @property
    def is_dynamic(self) -> bool:
        return tokens.is_dynamic(self.text)

    def describe(self) -> str:
        return self.name or (self.text.splitlines()[0][:28] if self.text else "text")

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        rendered = tokens.expand(self.text, ctx.metrics)
        if not rendered:
            return

        anchor = parse_anchor(self.anchor)
        x, y = resolve_point(self.pos, ctx.size, anchor)

        size = self.size
        if self.fit:
            size = _fitted_size(rendered, self.font, size, _available_width(x, anchor, ctx), ctx)
        face = ctx.font(self.font, size)
        # Vertical anchors position the text block by its own extent, so a
        # bottom-anchored block sits above the edge rather than off the frame.
        if anchor.vertical != "top":
            lines = rendered.count("\n") + 1
            block = lines * size + (lines - 1) * int(size * LINE_SPACING)
            y -= block if anchor.from_bottom else block // 2

        pil_anchor = _pil_anchor(self.align, anchor)
        spacing = int(size * LINE_SPACING)

        with overlay(canvas) as (_, draw):
            if self.shadow:
                nudge = max(2, size // 24)
                draw.multiline_text(
                    (x + nudge, y + nudge),
                    rendered,
                    font=face,
                    fill=(0, 0, 0, 160),
                    anchor=pil_anchor,
                    align=self.align,
                    spacing=spacing,
                )
            draw.multiline_text(
                (x, y),
                rendered,
                font=face,
                fill=parse_color(self.color),
                anchor=pil_anchor,
                align=self.align,
                spacing=spacing,
            )


@layer_type("clock")
@dataclass
class ClockLayer(Layer):
    """Wall clock. Separate from a text layer so it needs no metric plumbing."""

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field(
            "format",
            "text",
            "Format",
            "",
            help="strftime format; blank follows the app's 12/24-hour setting",
        ),
        Field("font", "text", "Font", "Segoe UI"),
        Field("size", "number", "Size", 72, minimum=6, maximum=400),
        Field("color", "color", "Color", "#FFFFFF"),
        Field("pos", "point", "Position", [40, 40]),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("align", "choice", "Align", "left", choices=ALIGNMENTS),
        Field("fit", "bool", "Shrink to fit", True),
    )

    #: Blank means "follow the app's 12/24-hour preference". An explicit pattern
    #: is a deliberate per-scene choice and always wins.
    format: str = ""
    font: str = "Segoe UI"
    size: int = 72
    color: str = "#FFFFFF"
    pos: tuple[Length, Length] | list[Length] = (40, 40)
    anchor: str = "top-left"
    align: str = "left"
    fit: bool = True

    @property
    def is_dynamic(self) -> bool:
        return True

    def describe(self) -> str:
        return self.name or f"clock {self.format or 'auto'}"

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        if self.format:
            text = time.strftime(self.format)
        else:
            text = format_clock(time.localtime(), ctx.clock_24_hour)
        anchor = parse_anchor(self.anchor)
        x, y = resolve_point(self.pos, ctx.size, anchor)

        size = self.size
        if self.fit:
            size = _fitted_size(text, self.font, size, _available_width(x, anchor, ctx), ctx)
        if anchor.vertical != "top":
            y -= size if anchor.from_bottom else size // 2

        with overlay(canvas) as (_, draw):
            draw.text(
                (x, y),
                text,
                font=ctx.font(self.font, size),
                fill=parse_color(self.color),
                anchor=_pil_anchor(self.align, anchor),
            )


#: Never shrink text below this; past it nothing is readable on a panel this
#: size, and a caller is better off seeing the overflow and fixing the scene.
MIN_FONT_SIZE = 10


def _available_width(x: int, anchor, ctx: RenderContext) -> int:
    """How much horizontal room the text has from its own origin."""
    if anchor.horizontal == "right":
        return max(1, x)
    if anchor.horizontal == "center":
        return max(1, 2 * min(x, ctx.width - x))
    return max(1, ctx.width - x)


def _fitted_size(text: str, font: str, size: int, available: int, ctx: RenderContext) -> int:
    """Largest size <= ``size`` whose longest line fits ``available`` pixels.

    This is what lets one scene work in both orientations: a line that is
    comfortable across 1920 px would run off a 462 px wide frame, so it scales
    down instead of being clipped.
    """
    lines = text.splitlines() or [text]
    face = ctx.font(font, size)
    widest = max((face.getlength(line) for line in lines), default=0)
    if widest <= available or widest <= 0:
        return size

    # Glyph advance is very close to linear in size, so one scaled guess lands
    # within a pixel or two; a short walk downwards settles the remainder.
    guess = max(MIN_FONT_SIZE, min(size, int(size * available / widest)))
    for candidate in range(guess, MIN_FONT_SIZE - 1, -1):
        face = ctx.font(font, candidate)
        if max((face.getlength(line) for line in lines), default=0) <= available:
            return candidate
    return MIN_FONT_SIZE


def _pil_anchor(align: str, anchor) -> str:
    """Pillow anchor for the requested alignment.

    ``align`` wins when it is set explicitly; otherwise the layer's frame anchor
    decides, so a ``top-right`` layer right-aligns its text without the scene
    having to say so twice.
    """
    if align in ALIGNMENTS and align != "left":
        return {"center": "ma", "right": "ra"}[align]
    return text_anchor(anchor) if align == "left" else "la"
