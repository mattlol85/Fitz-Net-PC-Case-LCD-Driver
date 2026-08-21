"""Resolving layer coordinates against whatever frame we are actually drawing.

A scene has to survive being shown on a 1920x462 strip *and*, when the panel is
mounted on its side, a 462x1920 column. Fixed pixel coordinates cannot do that,
so every position and size in a layer is a :data:`Length`, which may be:

``40``          40 pixels from the left/top edge
``-40``         40 pixels from the right/bottom edge (like CSS ``right: 40px``)
``"50%"``       half way across the relevant axis
``"-10%"``      10% in from the far edge
``"center"``    centred on that axis

Anchors do the rest: a layer anchored ``top-right`` measures its x from the right
edge, so ``pos: ["4%", "8%"], anchor: "top-right"`` sits in the same visual place
in either orientation.

Font sizes deliberately stay in pixels. The panel is one physical device being
rotated, so a 92 px glyph is the same physical size whichever way round it is;
only positions and extents need to adapt.
"""

from __future__ import annotations

from enum import StrEnum

Length = int | float | str

#: Positions given as plain numbers below this are still treated as pixels; the
#: fraction form must be an explicit "50%" string so there is never any doubt
#: about whether 1 means "one pixel" or "the whole width".
CENTER_KEYWORDS = frozenset({"center", "centre", "middle"})


class Anchor(StrEnum):
    """Which point of the frame a layer's position is measured from."""

    TOP_LEFT = "top-left"
    TOP_CENTER = "top-center"
    TOP_RIGHT = "top-right"
    MIDDLE_LEFT = "middle-left"
    MIDDLE_CENTER = "middle-center"
    MIDDLE_RIGHT = "middle-right"
    BOTTOM_LEFT = "bottom-left"
    BOTTOM_CENTER = "bottom-center"
    BOTTOM_RIGHT = "bottom-right"

    @property
    def horizontal(self) -> str:
        return self.value.split("-")[1]

    @property
    def vertical(self) -> str:
        return self.value.split("-")[0]

    @property
    def from_right(self) -> bool:
        return self.horizontal == "right"

    @property
    def from_bottom(self) -> bool:
        return self.vertical == "bottom"


ANCHORS = tuple(a.value for a in Anchor)

#: Maps an anchor to the Pillow text anchor with matching horizontal alignment
#: and a top baseline, which is what the text layers draw with.
_TEXT_ANCHORS = {"left": "la", "center": "ma", "right": "ra"}


def parse_anchor(value: object) -> Anchor:
    """Coerce ``value`` to an :class:`Anchor`, defaulting to top-left."""
    if isinstance(value, Anchor):
        return value
    try:
        return Anchor(str(value))
    except ValueError:
        return Anchor.TOP_LEFT


def text_anchor(anchor: Anchor) -> str:
    return _TEXT_ANCHORS[anchor.horizontal]


def resolve(value: Length, extent: int, *, from_far_edge: bool = False) -> int:
    """Resolve one length against an axis of ``extent`` pixels.

    ``from_far_edge`` measures from the right/bottom instead of the left/top,
    which is what a right- or bottom-anchored layer wants.
    """
    if isinstance(value, str):
        text = value.strip().lower()
        if text in CENTER_KEYWORDS:
            return extent // 2
        if text.endswith("%"):
            try:
                fraction = float(text[:-1]) / 100.0
            except ValueError:
                return 0
            return _apply_edge(round(fraction * extent), extent, from_far_edge, fraction < 0)
        try:
            value = float(text)
        except ValueError:
            return 0

    pixels = round(float(value))
    return _apply_edge(pixels, extent, from_far_edge, pixels < 0)


def _apply_edge(pixels: int, extent: int, from_far_edge: bool, negative: bool) -> int:
    # A negative length always means "in from the far edge", regardless of
    # anchor -- that is the least surprising reading of pos: [-40, 20].
    if negative:
        return extent + pixels if not from_far_edge else extent - abs(pixels)
    return extent - pixels if from_far_edge else pixels


def resolve_size(value: Length, extent: int) -> int:
    """Resolve a width/height. Never measured from the far edge."""
    if isinstance(value, str):
        text = value.strip().lower()
        if text.endswith("%"):
            try:
                return max(0, round(float(text[:-1]) / 100.0 * extent))
            except ValueError:
                return 0
        try:
            value = float(text)
        except ValueError:
            return 0
    pixels = round(float(value))
    # A negative size means "leave this much off the far end", so a rect of
    # width "-40" fills the frame bar a 40 px margin.
    return max(0, extent + pixels) if pixels < 0 else pixels


def resolve_point(
    point: object,
    size: tuple[int, int],
    anchor: Anchor = Anchor.TOP_LEFT,
) -> tuple[int, int]:
    """Resolve an ``[x, y]`` pair into absolute pixels."""
    width, height = size
    if not point or len(tuple(point)) < 2:  # type: ignore[arg-type]
        return 0, 0
    x_value, y_value = tuple(point)[:2]  # type: ignore[index]
    return (
        resolve(x_value, width, from_far_edge=anchor.from_right),
        resolve(y_value, height, from_far_edge=anchor.from_bottom),
    )


def resolve_rect(
    rect: object,
    size: tuple[int, int],
    anchor: Anchor = Anchor.TOP_LEFT,
) -> tuple[int, int, int, int]:
    """Resolve an ``[x, y, w, h]`` rect into absolute pixels.

    An empty rect means the whole frame, which is what a full-bleed backdrop
    wants and keeps scenes terse.
    """
    width, height = size
    values = tuple(rect) if rect else ()
    if len(values) < 4:
        return 0, 0, width, height

    x_value, y_value, w_value, h_value = values[:4]
    w = resolve_size(w_value, width)
    h = resolve_size(h_value, height)
    x = resolve(x_value, width, from_far_edge=anchor.from_right)
    y = resolve(y_value, height, from_far_edge=anchor.from_bottom)

    # For a right/bottom anchor the resolved coordinate is the far edge of the
    # box, so shift back by its own size to get the top-left origin.
    if anchor.from_right:
        x -= w
    if anchor.from_bottom:
        y -= h
    if anchor.horizontal == "center":
        x -= w // 2
    if anchor.vertical == "middle":
        y -= h // 2
    return x, y, w, h
