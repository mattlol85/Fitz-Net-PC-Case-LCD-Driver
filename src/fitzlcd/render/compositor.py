"""Flattens a scene's layer stack into a single frame."""

from __future__ import annotations

import logging

from PIL import Image

from fitzlcd.render.colors import parse_color
from fitzlcd.render.context import RenderContext
from fitzlcd.render.scene import Layer, Scene

log = logging.getLogger(__name__)


class Compositor:
    """Renders scenes at a fixed size.

    Layers are drawn bottom-up onto one RGBA canvas. A layer that raises is
    logged once and skipped: a broken layer should cost you that layer, not the
    whole display.
    """

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height
        self._reported: set[int] = set()

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    def compose(self, scene: Scene, ctx: RenderContext | None = None) -> Image.Image:
        """Return the composed RGB frame for ``scene``."""
        context = ctx or RenderContext(self.width, self.height)
        canvas = Image.new("RGBA", self.size, parse_color(scene.background))

        for layer in scene.visible_layers:
            if layer.opacity <= 0:
                continue
            try:
                self._draw_layer(layer, canvas, context)
            except Exception as exc:  # noqa: BLE001 - one bad layer must not stop the frame
                self._report(layer, exc)

        return canvas.convert("RGB")

    def _draw_layer(self, layer: Layer, canvas: Image.Image, ctx: RenderContext) -> None:
        if layer.opacity >= 1.0:
            layer.draw(canvas, ctx)
            return
        # Partial opacity needs its own surface so the layer blends as a whole
        # rather than each of its own draw calls blending separately.
        scratch = Image.new("RGBA", self.size, (0, 0, 0, 0))
        layer.draw(scratch, ctx)
        alpha = scratch.getchannel("A").point(lambda a: int(a * layer.opacity))
        scratch.putalpha(alpha)
        canvas.alpha_composite(scratch)

    def _report(self, layer: Layer, exc: Exception) -> None:
        key = id(layer)
        if key not in self._reported:
            self._reported.add(key)
            log.exception("layer %r failed and will be skipped: %s", layer.describe(), exc)
