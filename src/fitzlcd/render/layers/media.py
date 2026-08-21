"""Image, GIF and video layers, plus a plain solid/gradient fill."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import ClassVar

from PIL import Image, ImageDraw

from fitzlcd.render.colors import parse_color
from fitzlcd.render.context import RenderContext
from fitzlcd.render.fit import Fit, fit_image
from fitzlcd.render.geometry import ANCHORS, Length, parse_anchor, resolve_rect
from fitzlcd.render.scene import Field, Layer, layer_type
from fitzlcd.sources.media import MediaError, MediaSource, open_media

log = logging.getLogger(__name__)


@layer_type("media")
@dataclass
class MediaLayer(Layer):
    """Draws a still, animated GIF, or video.

    The underlying source is opened lazily and cached on the instance, so
    editing unrelated properties never re-decodes the file.
    """

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("source", "path", "File", "", help="image, GIF or video"),
        Field("fit", "choice", "Fit", "cover", choices=tuple(f.value for f in Fit)),
        Field("pan", "number", "Pan (vertical)", 0.5, minimum=0.0, maximum=1.0),
        Field("pan_x", "number", "Pan (horizontal)", 0.5, minimum=0.0, maximum=1.0),
        Field("speed", "number", "Speed", 1.0, minimum=0.05, maximum=10.0),
    )

    source: str = ""
    fit: str = "cover"
    pan: float = 0.5
    pan_x: float = 0.5
    speed: float = 1.0

    _media: MediaSource | None = field(default=None, repr=False, compare=False)
    _media_path: str = field(default="", repr=False, compare=False)
    _failed: bool = field(default=False, repr=False, compare=False)

    @property
    def is_dynamic(self) -> bool:
        media = self._ensure_media()
        return bool(media and media.is_animated)

    def describe(self) -> str:
        return self.name or (Path(self.source).name if self.source else "media")

    def to_dict(self):
        data = super().to_dict()
        for key in ("_media", "_media_path", "_failed"):
            data.pop(key, None)
        return data

    def _ensure_media(self) -> MediaSource | None:
        if self._media_path != self.source:
            self.close()
            self._media_path = self.source
            self._failed = False
        if self._media is not None or self._failed or not self.source:
            return self._media
        try:
            self._media = open_media(self.source)
        except MediaError as exc:
            # Remember the failure: retrying an unreadable file every frame would
            # stall the render loop on disk I/O.
            log.warning("media layer disabled: %s", exc)
            self._failed = True
        return self._media

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        media = self._ensure_media()
        if media is None:
            return
        frame = media.frame_at(ctx.time * max(0.05, self.speed))
        try:
            mode = Fit(self.fit)
        except ValueError:
            mode = Fit.COVER
        fitted = fit_image(frame, ctx.size, mode, pan=self.pan, pan_x=self.pan_x)
        canvas.alpha_composite(fitted)

    def close(self) -> None:
        if self._media is not None:
            self._media.close()
            self._media = None


@layer_type("solid")
@dataclass
class SolidLayer(Layer):
    """A flat or vertically-graded rectangle. Useful as a scrim behind text."""

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("color", "color", "Color", "#000000"),
        Field("color2", "color", "Gradient to", "#00000000", help="blank for a flat fill"),
        Field("rect", "rect", "Rect", [], help="empty = whole frame; px or percentages"),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("radius", "number", "Corner radius", 0, minimum=0, maximum=400),
    )

    color: str = "#000000"
    color2: str = "#00000000"
    rect: tuple[Length, ...] | list[Length] = ()
    anchor: str = "top-left"
    radius: int = 0

    def describe(self) -> str:
        return self.name or f"solid {self.color}"

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w <= 0 or h <= 0:
            return

        start = parse_color(self.color)
        end = parse_color(self.color2)

        if end[3] == 0 and start == parse_color(self.color):
            patch = Image.new("RGBA", (w, h), start)
        else:
            patch = Image.new("RGBA", (w, h))
            draw = ImageDraw.Draw(patch)
            for row in range(h):
                blend = row / max(1, h - 1)
                draw.line(
                    [(0, row), (w, row)],
                    fill=tuple(int(start[i] + (end[i] - start[i]) * blend) for i in range(4)),
                )

        if self.radius > 0:
            mask = Image.new("L", (w, h), 0)
            ImageDraw.Draw(mask).rounded_rectangle(
                [0, 0, w - 1, h - 1], radius=int(self.radius), fill=255
            )
            patch.putalpha(Image.composite(patch.getchannel("A"), Image.new("L", (w, h), 0), mask))

        canvas.alpha_composite(patch, (x, y))
