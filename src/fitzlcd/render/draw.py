"""Alpha-correct drawing helpers.

``ImageDraw`` *replaces* pixels on an RGBA image, alpha channel included, so
drawing a translucent shape punches a hole in whatever is underneath instead of
blending with it. Every layer that uses a colour with alpha < 255 must therefore
draw onto a scratch surface and composite it, which is what :func:`overlay` does.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from PIL import Image, ImageDraw


@contextmanager
def overlay(
    canvas: Image.Image,
    box: tuple[int, int, int, int] | None = None,
) -> Iterator[tuple[Image.Image, ImageDraw.ImageDraw]]:
    """Draw onto a scratch surface, then alpha-composite it onto ``canvas``.

    ``box`` is ``(x, y, w, h)``; the scratch surface is limited to it and drawing
    coordinates become relative to its origin. Omit it to work in full-frame
    coordinates.
    """
    if box is None:
        origin = (0, 0)
        size = canvas.size
    else:
        x, y, w, h = (int(v) for v in box)
        origin = (x, y)
        size = (max(1, w), max(1, h))

    scratch = Image.new("RGBA", size, (0, 0, 0, 0))
    try:
        yield scratch, ImageDraw.Draw(scratch)
    finally:
        canvas.alpha_composite(scratch, origin)
