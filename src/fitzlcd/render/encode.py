"""Turn a composed landscape frame into the byte stream a panel expects.

Panels in this family compose in one orientation and scan out in another. The
DS916 reports 1920x462 landscape but wants a 462x1920 portrait JPEG, so the
transform is a property of the panel, applied here at the last moment.
"""

from __future__ import annotations

import io
from enum import StrEnum

import numpy as np
from PIL import Image


class Transform(StrEnum):
    """Rotation applied to a composed frame before encoding."""

    NONE = "none"
    ROT_90 = "rot90"  # counter-clockwise
    ROT_180 = "rot180"
    ROT_270 = "rot270"  # clockwise -- what the DS916 needs

    @classmethod
    def from_angle(cls, angle: int) -> Transform:
        return {0: cls.NONE, 90: cls.ROT_90, 180: cls.ROT_180, 270: cls.ROT_270}[angle % 360]


_PIL_ROTATION = {
    Transform.NONE: None,
    Transform.ROT_90: Image.Transpose.ROTATE_90,
    Transform.ROT_180: Image.Transpose.ROTATE_180,
    Transform.ROT_270: Image.Transpose.ROTATE_270,
}


def apply_transform(image: Image.Image, transform: Transform) -> Image.Image:
    """Rotate ``image`` for scan-out. ``ROT_270`` maps 1920x462 -> 462x1920."""
    op = _PIL_ROTATION[transform]
    return image if op is None else image.transpose(op)


def to_image(frame: np.ndarray) -> Image.Image:
    """Wrap an (H, W, 3) uint8 array as a PIL image without copying pixel data."""
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError(f"expected (H, W, 3) RGB frame, got shape {frame.shape}")
    if frame.dtype != np.uint8:
        raise ValueError(f"expected uint8 frame, got {frame.dtype}")
    return Image.fromarray(frame, mode="RGB")


def encode_jpeg(
    frame: np.ndarray | Image.Image,
    transform: Transform = Transform.NONE,
    quality: int = 85,
) -> bytes:
    """Transform and JPEG-encode a frame into the bytes to push to the panel."""
    image = frame if isinstance(frame, Image.Image) else to_image(frame)
    image = apply_transform(image, transform)
    buf = io.BytesIO()
    image.save(buf, format="JPEG", quality=quality, subsampling=0 if quality >= 95 else 2)
    return buf.getvalue()
