"""Fitting source media into the frame.

This matters more than usual here: the DS916 is 1920x462, an aspect ratio of
about 4.16:1, while almost all source media is 16:9. Naive scaling makes every
photo and video look wrong, so the fit mode and the pan offset are first-class
scene properties.
"""

from __future__ import annotations

from enum import StrEnum

from PIL import Image

RESAMPLE = Image.Resampling.LANCZOS


class Fit(StrEnum):
    #: Fill the frame, cropping the overflowing axis. The usual choice.
    COVER = "cover"
    #: Fit entirely inside the frame, letterboxing the rest.
    CONTAIN = "contain"
    #: Ignore aspect ratio and stretch to the frame.
    STRETCH = "stretch"
    #: Repeat at native size.
    TILE = "tile"
    #: Draw at native size, centred, no scaling.
    NONE = "none"


def fit_image(
    image: Image.Image,
    size: tuple[int, int],
    mode: Fit = Fit.COVER,
    pan: float = 0.5,
    pan_x: float = 0.5,
) -> Image.Image:
    """Return ``image`` rendered into an RGBA canvas of ``size``.

    ``pan``/``pan_x`` choose which part survives cropping (0.0 = top/left,
    0.5 = centre, 1.0 = bottom/right) and are what makes a 16:9 source usable on
    a letterbox panel.
    """
    target_w, target_h = size
    if target_w <= 0 or target_h <= 0:
        raise ValueError(f"invalid target size {size}")

    canvas = Image.new("RGBA", (target_w, target_h), (0, 0, 0, 0))
    src_w, src_h = image.size
    if src_w <= 0 or src_h <= 0:
        return canvas
    if image.mode != "RGBA":
        image = image.convert("RGBA")

    pan = max(0.0, min(1.0, pan))
    pan_x = max(0.0, min(1.0, pan_x))

    if mode is Fit.STRETCH:
        return image.resize((target_w, target_h), RESAMPLE)

    if mode is Fit.TILE:
        for y in range(0, target_h, src_h):
            for x in range(0, target_w, src_w):
                canvas.paste(image, (x, y))
        return canvas

    if mode is Fit.NONE:
        canvas.paste(image, _centred_offset(image.size, size, pan_x, pan))
        return canvas

    scale_w = target_w / src_w
    scale_h = target_h / src_h
    scale = max(scale_w, scale_h) if mode is Fit.COVER else min(scale_w, scale_h)
    new_size = (max(1, round(src_w * scale)), max(1, round(src_h * scale)))
    scaled = image.resize(new_size, RESAMPLE)

    if mode is Fit.COVER:
        left = int((new_size[0] - target_w) * pan_x)
        top = int((new_size[1] - target_h) * pan)
        return scaled.crop((left, top, left + target_w, top + target_h))

    canvas.paste(scaled, _centred_offset(new_size, size, pan_x, pan))
    return canvas


def _centred_offset(
    src: tuple[int, int], target: tuple[int, int], pan_x: float, pan_y: float
) -> tuple[int, int]:
    return (
        int((target[0] - src[0]) * pan_x),
        int((target[1] - src[1]) * pan_y),
    )
