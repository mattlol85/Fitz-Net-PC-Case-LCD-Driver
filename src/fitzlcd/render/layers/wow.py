"""World of Warcraft layers: the zone map with party dots, a party roster, and
the player unit frame (portrait, health and power, the way the game draws it).

All of them read structured metrics published by
:class:`fitzlcd.sources.wow.provider.WowProvider`: ``wow.party`` is a list of
dicts, ``wow.map.image`` the path of the zone's cached in-game art and
``wow.player.portrait`` a PIL image copied off the screen. Anything malformed
in them is skipped rather than drawn wrong.

The registry snapshot only changes once a second, so the map glides every dot
toward its latest reading (state on the instance, never in ``FIELDS``) rather
than hopping. Time going backwards means the scene restarted, and dots snap.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Any, ClassVar

from PIL import Image, ImageChops, ImageDraw, ImageOps

from fitzlcd.render.colors import parse_color, with_alpha
from fitzlcd.render.context import RenderContext
from fitzlcd.render.draw import overlay
from fitzlcd.render.geometry import ANCHORS, Length, parse_anchor, resolve_rect
from fitzlcd.render.motion import MAX_STEP, ValueGlide, as_finite_float, pulse_wave
from fitzlcd.render.scene import Field, Layer, layer_type

log = logging.getLogger(__name__)

SUPERSAMPLE = 4
VIEWS = ("party", "follow", "zone")
#: Map "pixels" used when there is no art to take a size from.
_NOMINAL_HEIGHT = 1000.0
_DEFAULT_ASPECT = 1.5
#: A jump further than this (fraction of the map) is a teleport, not a walk.
_SNAP_DISTANCE = 0.08


def _unit(value: object) -> float | None:
    number = as_finite_float(value)
    if number is None or not 0.0 <= number <= 1.0:
        return None
    return number


def _members(value: object) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [m for m in value[:8] if isinstance(m, dict)]


def _fit_text(draw: ImageDraw.ImageDraw, text: str, font, width: float) -> str:
    if width <= 0:
        return ""
    if draw.textlength(text, font=font) <= width:
        return text
    while text and draw.textlength(text + "…", font=font) > width:
        text = text[:-1]
    return text + "…" if text else ""


@dataclass
class _Glide:
    x: float
    y: float
    angle: float | None = None


@layer_type("wow_map")
@dataclass
class WowMapLayer(Layer):
    """The zone map, your facing arrow and your party's dots."""

    display_name: ClassVar[str] = "WoW map"
    summary: ClassVar[str] = "Your zone's map with you and your party on it"
    icon: ClassVar[str] = "map"

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field(
            "view",
            "choice",
            "View",
            "party",
            choices=VIEWS,
            help="party fits everyone in, follow centres on you, zone shows the whole map",
        ),
        Field("zoom", "number", "Zoom", 3.0, minimum=1.0, maximum=20.0),
        Field("show_names", "bool", "Show names", True),
        Field("rect", "rect", "Rect", [40, 40, 600, 380]),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("dot_size", "number", "Dot size", 10, minimum=3, maximum=40, section="appearance"),
        Field("player_color", "color", "You", "#FFD100"),
        Field("backdrop_color", "color", "Backdrop", "#0b1220"),
        Field(
            "backdrop_dim",
            "number",
            "Dim the map",
            0.25,
            minimum=0.0,
            maximum=1.0,
            section="appearance",
        ),
        Field("grid_color", "color", "Grid", "#9fb8ff26"),
        Field("name_color", "color", "Names", "#F5EBDD"),
        Field("border_color", "color", "Border", "#ffffff22"),
        Field(
            "corner_radius", "number", "Corner radius", 18, minimum=0, maximum=200, advanced=True
        ),
        Field("font", "font", "Font", "sans", section="appearance"),
        Field(
            "glide",
            "number",
            "Glide (s)",
            0.9,
            minimum=0.0,
            maximum=5.0,
            section="animation",
            help="Dots move smoothly between readings instead of hopping once a second",
        ),
        Field(
            "ping",
            "number",
            "Ping every (s)",
            0.0,
            minimum=0.0,
            maximum=10.0,
            section="animation",
            help="A ring that ripples out from you; 0 turns it off",
        ),
    )

    view: str = "party"
    zoom: float = 3.0
    show_names: bool = True
    rect: tuple[Length, ...] | list[Length] = (40, 40, 600, 380)
    anchor: str = "top-left"
    dot_size: float = 10
    player_color: str = "#FFD100"
    backdrop_color: str = "#0b1220"
    backdrop_dim: float = 0.25
    grid_color: str = "#9fb8ff26"
    name_color: str = "#F5EBDD"
    border_color: str = "#ffffff22"
    corner_radius: float = 18
    font: str = "sans"
    glide: float = 0.9
    ping: float = 0.0

    _art_path: str = field(default="", init=False, repr=False, compare=False)
    _art: Image.Image | None = field(default=None, init=False, repr=False, compare=False)
    _glides: dict[str, _Glide] = field(default_factory=dict, init=False, repr=False, compare=False)
    _map_id: object = field(default=None, init=False, repr=False, compare=False)
    _last_time: float | None = field(default=None, init=False, repr=False, compare=False)
    _sprites: dict[tuple, Image.Image] = field(
        default_factory=dict, init=False, repr=False, compare=False
    )

    @property
    def is_dynamic(self) -> bool:
        return True

    def to_dict(self):
        data = super().to_dict()
        for key in [k for k in data if k.startswith("_")]:
            data.pop(key)
        return data

    # ------------------------------------------------------------ backdrop

    def _load_art(self, path: object) -> Image.Image | None:
        if not isinstance(path, str) or not path:
            self._art_path, self._art = "", None
            return None
        if path != self._art_path:
            self._art_path, self._art = path, None
            try:
                with Image.open(path) as raw:
                    self._art = raw.convert("RGBA")
            except Exception as exc:  # noqa: BLE001 - Pillow raises odd types on bad files
                log.warning("map art %s could not be loaded: %s", path, exc)
        return self._art

    # --------------------------------------------------------------- glide

    def _step(self, key: str, x: float, y: float, angle: float | None, dt: float | None) -> _Glide:
        shown = self._glides.get(key)
        tau = max(0.0, float(self.glide)) / 3.0
        if (
            shown is None
            or dt is None
            or tau <= 0
            or math.hypot(x - shown.x, y - shown.y) > _SNAP_DISTANCE
        ):
            shown = _Glide(x, y, angle)
        else:
            k = 1.0 - math.exp(-dt / tau) if dt > 0 else 0.0
            shown.x += (x - shown.x) * k
            shown.y += (y - shown.y) * k
            if angle is None or shown.angle is None:
                shown.angle = angle
            else:
                turn = (angle - shown.angle + math.pi) % math.tau - math.pi
                shown.angle += turn * k
        self._glides[key] = shown
        return shown

    # ---------------------------------------------------------------- view

    def _viewport(
        self, w: int, h: int, map_w: float, map_h: float, points: list[tuple[float, float]]
    ) -> tuple[float, float, float]:
        """(centre x, centre y) in map pixels, and map pixels -> screen scale."""
        fit = min(w / map_w, h / map_h)
        zoom = max(1.0, as_finite_float(self.zoom) or 1.0)
        view = self.view if self.view in VIEWS else "party"
        if view == "zone" or not points:
            return map_w / 2, map_h / 2, fit
        if view == "follow" or len(points) == 1:
            cx, cy = points[0][0] * map_w, points[0][1] * map_h
            scale = fit * zoom
        else:
            xs = [p[0] * map_w for p in points]
            ys = [p[1] * map_h for p in points]
            cx, cy = (min(xs) + max(xs)) / 2, (min(ys) + max(ys)) / 2
            span_w = max(max(xs) - min(xs), 1e-6) * 1.4
            span_h = max(max(ys) - min(ys), 1e-6) * 1.4
            scale = max(fit, min(fit * zoom, w / span_w, h / span_h))
        # Never pan past the map's edge when it is bigger than the view.
        half_w, half_h = w / 2 / scale, h / 2 / scale
        cx = min(max(cx, half_w), map_w - half_w) if map_w > 2 * half_w else map_w / 2
        cy = min(max(cy, half_h), map_h - half_h) if map_h > 2 * half_h else map_h / 2
        return cx, cy, scale

    # ------------------------------------------------------------- sprites

    def _dot(self, radius: int, color, ring, cross: bool) -> Image.Image:
        key = ("dot", radius, color, ring, cross)
        sprite = self._sprites.get(key)
        if sprite is None:
            size = (radius * 2 + 4) * SUPERSAMPLE
            big = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(big)
            c = size / 2
            r = radius * SUPERSAMPLE
            d.ellipse((c - r, c - r, c + r, c + r), fill=ring)
            inner = r - 2 * SUPERSAMPLE
            d.ellipse((c - inner, c - inner, c + inner, c + inner), fill=color)
            if cross:
                arm = inner * 0.6
                width = max(1, int(1.6 * SUPERSAMPLE))
                d.line((c - arm, c - arm, c + arm, c + arm), fill=ring, width=width)
                d.line((c - arm, c + arm, c + arm, c - arm), fill=ring, width=width)
            sprite = big.resize((size // SUPERSAMPLE,) * 2, Image.Resampling.LANCZOS)
            self._remember(key, sprite)
        return sprite

    def _arrow(self, radius: int, color, angle: float) -> Image.Image:
        step = round(angle / math.tau * 128) % 128
        key = ("arrow", radius, color, step)
        sprite = self._sprites.get(key)
        if sprite is None:
            theta = step / 128 * math.tau
            size = (radius * 4 + 4) * SUPERSAMPLE
            big = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            d = ImageDraw.Draw(big)
            c = size / 2
            r = radius * SUPERSAMPLE
            # Facing is counter-clockwise from north; screen y grows downward.
            fx, fy = -math.sin(theta), -math.cos(theta)
            px, py = -fy, fx
            tip = (c + fx * r * 1.8, c + fy * r * 1.8)
            notch = (c - fx * r * 0.5, c - fy * r * 0.5)
            left = (c - fx * r + px * r * 1.1, c - fy * r + py * r * 1.1)
            right = (c - fx * r - px * r * 1.1, c - fy * r - py * r * 1.1)
            d.polygon([tip, left, notch, right], fill=(0, 0, 0, 200))
            shrink = 0.72
            inner = [
                (c + (x - c) * shrink, c + (y - c) * shrink) for x, y in (tip, left, notch, right)
            ]
            d.polygon(inner, fill=color)
            sprite = big.resize((size // SUPERSAMPLE,) * 2, Image.Resampling.LANCZOS)
            self._remember(key, sprite)
        return sprite

    def _remember(self, key: tuple, sprite: Image.Image) -> None:
        if len(self._sprites) > 256:
            self._sprites.clear()
        self._sprites[key] = sprite

    # ---------------------------------------------------------------- draw

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w < 8 or h < 8:
            return
        metrics = ctx.metrics
        dt = None
        if self._last_time is not None and ctx.time >= self._last_time:
            dt = min(ctx.time - self._last_time, MAX_STEP)
        self._last_time = ctx.time
        map_id = metrics.get("wow.map.id")
        if map_id != self._map_id:
            self._map_id, dt = map_id, None  # a new map: everything snaps

        art = self._load_art(metrics.get("wow.map.image"))
        aspect = as_finite_float(metrics.get("wow.map.aspect"))
        if art is not None:
            map_w, map_h = float(art.width), float(art.height)
        else:
            if aspect is None or not 0.1 < aspect < 10:
                aspect = _DEFAULT_ASPECT
            map_w, map_h = _NOMINAL_HEIGHT * aspect, _NOMINAL_HEIGHT

        connected = metrics.get("wow.connected") is not False
        player_map = metrics.get("wow.player.map_id")
        dots: list[tuple[str, _Glide, dict[str, Any] | None]] = []
        px, py = _unit(metrics.get("wow.player.x")), _unit(metrics.get("wow.player.y"))
        if px is not None and py is not None:
            facing = as_finite_float(metrics.get("wow.player.facing"))
            dots.append(("player", self._step("player", px, py, facing, dt), None))
        # Members the map cannot place: pinned to its edge, pointing their way.
        chips: list[tuple[dict[str, Any], float, float]] = []
        for member in _members(metrics.get("wow.party")):
            mx, my = _unit(member.get("x")), _unit(member.get("y"))
            if mx is None or my is None or member.get("map_id") != player_map:
                bearing = as_finite_float(member.get("bearing"))
                if bearing is not None:
                    chips.append((member, -math.sin(bearing), -math.cos(bearing)))
                continue
            key = f"slot{member.get('slot', len(dots))}"
            dots.append((key, self._step(key, mx, my, None, dt), member))
        live = {key for key, _, _ in dots}
        for stale in [k for k in self._glides if k not in live]:
            del self._glides[stale]

        points = [(g.x, g.y) for _, g, _ in dots]
        cx, cy, scale = self._viewport(w, h, map_w, map_h, points)

        def to_screen(gx: float, gy: float) -> tuple[float, float]:
            return w / 2 + (gx * map_w - cx) * scale, h / 2 + (gy * map_h - cy) * scale

        layer = Image.new("RGBA", (w, h), parse_color(self.backdrop_color))
        if art is not None:
            extent = (
                cx - w / 2 / scale,
                cy - h / 2 / scale,
                cx + w / 2 / scale,
                cy + h / 2 / scale,
            )
            region = art.transform(
                (w, h), Image.Transform.EXTENT, extent, Image.Resampling.BILINEAR
            )
            dim = min(1.0, max(0.0, as_finite_float(self.backdrop_dim) or 0.0))
            if dim > 0:
                shade = Image.new("RGBA", (w, h), (0, 0, 0, int(255 * dim)))
                region = Image.alpha_composite(region, shade)
            layer.alpha_composite(region)
        else:
            self._draw_grid(layer, to_screen)

        radius = max(3, int(as_finite_float(self.dot_size) or 10))
        ring = (12, 12, 16, 230)
        # ImageDraw replaces alpha instead of blending, so every translucent
        # mark goes on its own transparent pass and is composited in.
        marks = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(marks)
        font = ctx.font(self.font, max(10, int(radius * 1.7)))
        name_color = parse_color(self.name_color)
        # Party first, you on top.
        for _key, glide, member in sorted(dots, key=lambda d: d[2] is None):
            sx, sy = to_screen(glide.x, glide.y)
            if member is None:
                self._draw_player(layer, ctx, sx, sy, radius, glide.angle)
                continue
            if not (0 <= sx < w and 0 <= sy < h):  # off this view (follow mode)
                chips.append((member, sx - w / 2, sy - h / 2))
                continue
            color = parse_color(member.get("color") or "#cccccc")
            online = member.get("online") is not False
            dead = bool(member.get("dead"))
            if dead:
                color = (110, 110, 110, 255)
            if not online or not connected:
                color = with_alpha(color, 110)
            # A position the member's own addon shared gets a white ring.
            edge = (245, 245, 245, 235) if member.get("shared") else ring
            sprite = self._dot(radius, color, edge, dead)
            layer.alpha_composite(sprite, (int(sx - sprite.width / 2), int(sy - sprite.height / 2)))
            name = member.get("name")
            if self.show_names and isinstance(name, str) and name:
                draw.text(
                    (sx + radius + 4, sy),
                    name,
                    font=font,
                    fill=name_color,
                    anchor="lm",
                    stroke_width=2,
                    stroke_fill=(0, 0, 0, 200),
                )

        for member, dx, dy in chips:
            self._draw_chip(draw, font, w, h, member, dx, dy, radius, connected)

        if metrics.get("wow.player.instance") is True and px is None:
            self._caption(draw, ctx, w, h, "POSITIONS ARE HIDDEN IN DUNGEONS")
        elif not dots and art is None:
            zone = metrics.get("wow.zone")
            self._caption(draw, ctx, w, h, str(zone) if zone else "NO MAP YET")

        border = parse_color(self.border_color)
        if border[3]:
            draw.rounded_rectangle(
                (0, 0, w - 1, h - 1), radius=int(self.corner_radius), outline=border, width=2
            )
        layer.alpha_composite(marks)
        mask = Image.new("L", (w, h), 0)
        ImageDraw.Draw(mask).rounded_rectangle(
            (0, 0, w - 1, h - 1), radius=max(0, int(self.corner_radius)), fill=255
        )
        layer.putalpha(ImageChops.multiply(layer.getchannel("A"), mask))
        with overlay(canvas, (x, y, w, h)) as (scratch, _):
            scratch.alpha_composite(layer)

    def _draw_chip(self, draw, font, w, h, member, dx, dy, radius, connected) -> None:
        """An arrow on the map's edge toward a member it cannot show, and who it is."""
        length = math.hypot(dx, dy)
        if length < 1e-9:
            return
        ux, uy = dx / length, dy / length
        margin = radius * 1.8
        reach = min(
            (w / 2 - margin) / abs(ux) if abs(ux) > 1e-9 else math.inf,
            (h / 2 - margin) / abs(uy) if abs(uy) > 1e-9 else math.inf,
        )
        if not math.isfinite(reach) or reach <= 0:
            return
        tip_x, tip_y = w / 2 + ux * reach, h / 2 + uy * reach
        color = parse_color(member.get("color") or "#cccccc")
        if member.get("dead"):
            color = (110, 110, 110, 255)
        if member.get("online") is False or not connected:
            color = with_alpha(color, 110)
        size = radius * 1.5
        px, py = -uy, ux
        # Longer than wide, with a notch, so it plainly points one way.
        base_x, base_y = tip_x - ux * size * 2.0, tip_y - uy * size * 2.0
        notch = (tip_x - ux * size * 1.4, tip_y - uy * size * 1.4)
        arrow = [
            (tip_x, tip_y),
            (base_x + px * size * 0.75, base_y + py * size * 0.75),
            notch,
            (base_x - px * size * 0.75, base_y - py * size * 0.75),
        ]
        draw.polygon(arrow, fill=color, outline=(12, 12, 16, 230), width=2)
        parts = [member.get("name") if self.show_names else None, member.get("zone")]
        distance = as_finite_float(member.get("distance_yd"))
        if distance is not None:
            parts.append(f"{distance:,.0f} yd")
        label = " · ".join(str(p) for p in parts if isinstance(p, str | int | float) and p != "")
        if not label:
            return
        label = _fit_text(draw, label, font, w * 0.6)
        text_w = draw.textlength(label, font=font)
        lx = base_x - ux * size * 0.8
        ly = base_y - uy * size * 1.2
        lx = min(max(lx, text_w / 2 + 6), w - text_w / 2 - 6)
        ly = min(max(ly, radius + 6), h - radius - 6)
        draw.text(
            (lx, ly),
            label,
            font=font,
            fill=parse_color(self.name_color),
            anchor="mm",
            stroke_width=2,
            stroke_fill=(0, 0, 0, 220),
        )

    def _draw_grid(self, layer: Image.Image, to_screen) -> None:
        color = parse_color(self.grid_color)
        if not color[3]:
            return
        lines = Image.new("RGBA", layer.size, (0, 0, 0, 0))
        draw = ImageDraw.Draw(lines)
        for i in range(11):
            t = i / 10
            width = 2 if i == 5 else 1
            x0, y0 = to_screen(t, 0.0)
            x1, y1 = to_screen(t, 1.0)
            draw.line((x0, y0, x1, y1), fill=color, width=width)
            x0, y0 = to_screen(0.0, t)
            x1, y1 = to_screen(1.0, t)
            draw.line((x0, y0, x1, y1), fill=color, width=width)
        layer.alpha_composite(lines)

    def _draw_player(self, layer, ctx: RenderContext, sx, sy, radius, angle) -> None:
        color = parse_color(self.player_color)
        period = as_finite_float(self.ping) or 0.0
        if period > 0:
            phase = (ctx.time % period) / period
            reach = radius * (1.5 + 4.0 * phase)
            alpha = int(color[3] * (1.0 - phase) * 0.8)
            ripple = Image.new("RGBA", layer.size, (0, 0, 0, 0))
            ImageDraw.Draw(ripple).ellipse(
                (sx - reach, sy - reach, sx + reach, sy + reach),
                outline=with_alpha(color, alpha),
                width=2,
            )
            layer.alpha_composite(ripple)
        if angle is None:
            sprite = self._dot(radius + 1, color, (255, 255, 255, 235), False)
        else:
            sprite = self._arrow(radius, color, angle)
        layer.alpha_composite(sprite, (int(sx - sprite.width / 2), int(sy - sprite.height / 2)))

    def _caption(self, draw, ctx: RenderContext, w: int, h: int, text: str) -> None:
        font = ctx.font(self.font, max(12, min(34, h // 12)))
        text = _fit_text(draw, text, font, w - 24)
        draw.text(
            (w / 2, h / 2),
            text,
            font=font,
            fill=parse_color(self.name_color),
            anchor="mm",
            stroke_width=2,
            stroke_fill=(0, 0, 0, 220),
        )


@layer_type("wow_party")
@dataclass
class WowPartyLayer(Layer):
    """One row per party member: class colour, name, level, health and distance."""

    display_name: ClassVar[str] = "WoW party"
    summary: ClassVar[str] = "Your party with health bars and how far away each one is"
    icon: ClassVar[str] = "users"

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field("empty_text", "text", "When alone", "Not in a group"),
        Field("rect", "rect", "Rect", [40, 40, 520, 300]),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("rows", "number", "Rows", 4, minimum=1, maximum=8, advanced=True),
        Field("size", "number", "Text size", 30, minimum=8, maximum=120, section="appearance"),
        Field("font", "font", "Font", "sans", section="appearance"),
        Field("text_color", "color", "Text", "#F5EBDD"),
        Field("dim_color", "color", "Detail", "#c9b99a99"),
        Field("track_color", "color", "Bar track", "#ffffff18"),
        Field("bar_height", "number", "Bar height", 8, minimum=2, maximum=40, advanced=True),
    )

    empty_text: str = "Not in a group"
    rect: tuple[Length, ...] | list[Length] = (40, 40, 520, 300)
    anchor: str = "top-left"
    rows: int = 4
    size: float = 30
    font: str = "sans"
    text_color: str = "#F5EBDD"
    dim_color: str = "#c9b99a99"
    track_color: str = "#ffffff18"
    bar_height: float = 8

    @property
    def is_dynamic(self) -> bool:
        return True

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w < 8 or h < 8:
            return
        rows = max(1, int(as_finite_float(self.rows) or 4))
        row_h = h / rows
        size = int(min(as_finite_float(self.size) or 30, row_h * 0.5))
        if size < 6:
            return
        font = ctx.font(self.font, size)
        small = ctx.font(self.font, max(6, int(size * 0.7)))
        text, dim = parse_color(self.text_color), parse_color(self.dim_color)
        track = parse_color(self.track_color)
        bar_h = max(2, min(int(as_finite_float(self.bar_height) or 8), int(row_h * 0.2)))
        members = _members(ctx.metrics.get("wow.party"))
        connected = ctx.metrics.get("wow.connected") is not False

        with overlay(canvas, (x, y, w, h)) as (_, draw):
            if not members:
                if self.empty_text:
                    message = _fit_text(draw, self.empty_text, font, w)
                    draw.text((0, row_h / 2), message, font=font, fill=dim, anchor="lm")
                return
            for i, member in enumerate(members[:rows]):
                top = i * row_h
                color = parse_color(member.get("color") or "#cccccc")
                if not connected or member.get("online") is False:
                    color = with_alpha(color, 110)
                swatch = int(size * 0.8)
                mid = top + row_h * 0.38
                draw.rounded_rectangle(
                    (0, mid - swatch / 2, swatch, mid + swatch / 2),
                    radius=max(1, swatch // 4),
                    fill=color,
                )
                right = self._status(member)
                right_w = draw.textlength(right, font=small) if right else 0
                level = member.get("level")
                level_text = f"  {level}" if isinstance(level, int) else ""
                label_x = swatch + size * 0.45
                name = member.get("name") or member.get("class") or f"Party {i + 1}"
                room = w - label_x - right_w - size - draw.textlength(level_text, font=small)
                name = _fit_text(draw, str(name), font, room)
                draw.text((label_x, mid), name, font=font, fill=text, anchor="lm")
                name_w = draw.textlength(name, font=font)
                if level_text:
                    draw.text(
                        (label_x + name_w, mid), level_text, font=small, fill=dim, anchor="lm"
                    )
                if right:
                    draw.text((w, mid), right, font=small, fill=dim, anchor="rm")
                bar_top = top + row_h * 0.72
                draw.rounded_rectangle(
                    (label_x, bar_top, w, bar_top + bar_h), radius=bar_h // 2, fill=track
                )
                health = as_finite_float(member.get("health_pct"))
                if health is not None and not member.get("dead"):
                    fill_w = (w - label_x) * max(0.0, min(1.0, health / 100))
                    if fill_w >= 1:
                        draw.rounded_rectangle(
                            (label_x, bar_top, label_x + fill_w, bar_top + bar_h),
                            radius=bar_h // 2,
                            fill=color,
                        )

    @staticmethod
    def _status(member: dict[str, Any]) -> str:
        if member.get("online") is False:
            return "OFFLINE"
        if member.get("dead"):
            return "DEAD"
        distance = as_finite_float(member.get("distance_yd"))
        yards = f"{distance:,.0f} yd" if distance is not None else ""
        zone = member.get("zone")
        if isinstance(zone, str) and zone:
            return f"{zone} · {yards}" if yards else zone
        if yards:
            return yards
        if "x" not in member:
            return "far away"
        return ""


PORTRAIT_SOURCES = ("auto", "live", "class", "none")
BAR_TEXTS = ("value", "percent", "both", "none")
_PLACEHOLDER = "—"


def _whole(value: object) -> int | None:
    number = as_finite_float(value)
    return None if number is None or number < 0 else int(number)


def _bar_label(mode: str, current: object, top: object, pct: object) -> str:
    """The text on a bar, like the game's: ``812 / 900``, ``90%`` or both."""
    if mode == "none":
        return ""
    cur, cap, share = _whole(current), _whole(top), as_finite_float(pct)
    if share is None and cur is not None and cap:
        share = 100.0 * cur / cap
    value = f"{cur:,} / {cap:,}" if cur is not None and cap is not None else ""
    percent = f"{share:.0f}%" if share is not None else ""
    if mode == "value":
        return value or percent or _PLACEHOLDER
    if mode == "percent":
        return percent or _PLACEHOLDER
    if value and percent:
        return f"{value}  ({percent})"
    return value or percent or _PLACEHOLDER


def _fit_font(draw: ImageDraw.ImageDraw, ctx: RenderContext, name: str, text: str, size, width):
    """The largest font up to ``size`` that fits ``text`` in ``width``, never below 7 px."""
    size = max(7, int(size))
    font = ctx.font(name, size)
    while size > 7 and draw.textlength(text, font=font) > width:
        size = max(7, int(size * 0.9))
        font = ctx.font(name, size)
    return font


def _lighten(color: tuple[int, int, int, int], amount: float) -> tuple[int, int, int, int]:
    r, g, b, a = color
    return (
        int(r + (255 - r) * amount),
        int(g + (255 - g) * amount),
        int(b + (255 - b) * amount),
        a,
    )


@layer_type("wow_unit_frame")
@dataclass
class WowUnitFrameLayer(Layer):
    """Your character's unit frame: portrait, level, name, health and power bars."""

    display_name: ClassVar[str] = "WoW unit frame"
    summary: ClassVar[str] = "Your portrait with health and mana bars, like the game's own frame"
    icon: ClassVar[str] = "user"

    FIELDS: ClassVar[tuple[Field, ...]] = (
        *Layer.FIELDS,
        Field(
            "portrait",
            "choice",
            "Portrait",
            "auto",
            choices=PORTRAIT_SOURCES,
            help="live copies the game's portrait off the screen; auto falls back to the "
            "class icon when it cannot be seen",
        ),
        Field("bar_text", "choice", "Bar text", "value", choices=BAR_TEXTS),
        Field("show_name", "bool", "Show name", True),
        Field("show_level", "bool", "Show level", True),
        Field("rect", "rect", "Rect", [40, 40, 900, 300]),
        Field("anchor", "choice", "Anchor", "top-left", choices=ANCHORS),
        Field("font", "font", "Font", "sans", section="appearance"),
        Field("name_color", "color", "Name", "#FFD100"),
        Field("class_colored_name", "bool", "Name in class colour", False, section="appearance"),
        Field("text_color", "color", "Bar text", "#FFFFFF"),
        Field("health_color", "color", "Health", "#1FC01F"),
        Field(
            "power_by_type",
            "bool",
            "Power colour by type",
            True,
            section="appearance",
            help="Mana blue, rage red, energy yellow, as the game colours them",
        ),
        Field("power_color", "color", "Power", "#0070DD"),
        Field("ring_color", "color", "Ring", "#C9A343"),
        Field("panel_color", "color", "Panel", "#000000b4"),
        Field("track_color", "color", "Bar track", "#ffffff14"),
        Field("loss_color", "color", "Damage taken", "#ff5a4ad0", section="appearance"),
        Field(
            "glide",
            "number",
            "Glide (s)",
            0.45,
            minimum=0.0,
            maximum=3.0,
            section="animation",
            help="Bars ease toward a new reading; what you just lost lingers as a red slice",
        ),
        Field(
            "combat_glow",
            "bool",
            "Combat glow",
            True,
            section="animation",
            help="The portrait ring pulses red in combat, gold while resting",
        ),
    )

    portrait: str = "auto"
    bar_text: str = "value"
    show_name: bool = True
    show_level: bool = True
    rect: tuple[Length, ...] | list[Length] = (40, 40, 900, 300)
    anchor: str = "top-left"
    font: str = "sans"
    name_color: str = "#FFD100"
    class_colored_name: bool = False
    text_color: str = "#FFFFFF"
    health_color: str = "#1FC01F"
    power_by_type: bool = True
    power_color: str = "#0070DD"
    ring_color: str = "#C9A343"
    panel_color: str = "#000000b4"
    track_color: str = "#ffffff14"
    loss_color: str = "#ff5a4ad0"
    glide: float = 0.45
    combat_glow: bool = True

    _health: ValueGlide = field(default_factory=ValueGlide, init=False, repr=False, compare=False)
    _power: ValueGlide = field(default_factory=ValueGlide, init=False, repr=False, compare=False)
    _icon_path: str = field(default="", init=False, repr=False, compare=False)
    _icon: Image.Image | None = field(default=None, init=False, repr=False, compare=False)
    _face_key: tuple = field(default=(), init=False, repr=False, compare=False)
    _face: Image.Image | None = field(default=None, init=False, repr=False, compare=False)

    @property
    def is_dynamic(self) -> bool:
        return True

    def to_dict(self):
        data = super().to_dict()
        for key in [k for k in data if k.startswith("_")]:
            data.pop(key)
        return data

    # ------------------------------------------------------------ portrait

    def _load_icon(self, path: object) -> Image.Image | None:
        if not isinstance(path, str) or not path:
            self._icon_path, self._icon = "", None
            return None
        if path != self._icon_path:
            self._icon_path, self._icon = path, None
            try:
                with Image.open(path) as raw:
                    self._icon = raw.convert("RGBA")
            except Exception as exc:  # noqa: BLE001 - Pillow raises odd types on bad files
                log.warning("class icon %s could not be loaded: %s", path, exc)
        return self._icon

    def _source_image(self, metrics: dict[str, Any]) -> Image.Image | None:
        mode = self.portrait if self.portrait in PORTRAIT_SOURCES else "auto"
        if mode == "none":
            return None
        live = metrics.get("wow.player.portrait")
        if mode in ("auto", "live") and isinstance(live, Image.Image) and live.width > 0:
            return live
        if mode in ("auto", "class"):
            return self._load_icon(metrics.get("wow.player.class_icon"))
        return None

    def _face_disc(
        self, source: Image.Image | None, size: int, grey: bool, fallback
    ) -> Image.Image:
        """The portrait cut to a circle ``size`` across, cached until it changes."""
        key = (id(source), source.size if source else None, size, grey, fallback)
        if self._face is not None and self._face_key == key:
            return self._face
        if source is not None:
            # Square it up from the centre, then drop the outer rim: the live crop's
            # corners hold the game's own frame art.
            side = min(source.size)
            left, top = (source.width - side) // 2, (source.height - side) // 2
            square = source.crop((left, top, left + side, top + side)).convert("RGBA")
            inset = int(side * 0.04)
            if side - 2 * inset > 4:
                square = square.crop((inset, inset, side - inset, side - inset))
            face = square.resize((size, size), Image.Resampling.LANCZOS)
            if grey:
                alpha = face.getchannel("A")
                face = ImageOps.grayscale(face.convert("RGB")).convert("RGBA")
                face.putalpha(alpha)
        else:
            face = Image.new("RGBA", (size, size), fallback)
            shade = Image.new("RGBA", (size, size), (0, 0, 0, 0))
            ImageDraw.Draw(shade).ellipse(
                (size * 0.18, size * 0.12, size * 0.82, size * 0.76), fill=(255, 255, 255, 46)
            )
            face.alpha_composite(shade)
        big = Image.new("L", (size * SUPERSAMPLE, size * SUPERSAMPLE), 0)
        ImageDraw.Draw(big).ellipse((0, 0, big.width - 1, big.height - 1), fill=255)
        mask = big.resize((size, size), Image.Resampling.LANCZOS)
        face.putalpha(ImageChops.multiply(face.getchannel("A"), mask))
        self._face_key, self._face = key, face
        return face

    # ---------------------------------------------------------------- draw

    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        x, y, w, h = resolve_rect(self.rect, ctx.size, parse_anchor(self.anchor))
        if w < 40 or h < 20:
            return
        m = ctx.metrics
        connected = m.get("wow.connected") is not False
        dead = m.get("wow.player.dead") is True or m.get("wow.player.ghost") is True
        glide = max(0.0, as_finite_float(self.glide) or 0.0)

        health = as_finite_float(m.get("wow.player.health_pct"))
        if health is None:
            cur, cap = _whole(m.get("wow.player.hp")), _whole(m.get("wow.player.hp_max"))
            health = 100.0 * cur / cap if cur is not None and cap else None
        power = as_finite_float(m.get("wow.player.power_pct"))
        if power is None:
            cur, cap = _whole(m.get("wow.player.power")), _whole(m.get("wow.player.power_max"))
            power = 100.0 * cur / cap if cur is not None and cap else None
        if dead:
            health = 0.0
        health = None if health is None else max(0.0, min(100.0, health))
        power = None if power is None else max(0.0, min(100.0, power))
        shown_health = self._health.step(health, ctx.time, glide)
        shown_power = self._power.step(power, ctx.time, glide)

        layer = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        marks = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(marks)

        # Portrait on the left, as tall as the frame (bars tuck in behind it).
        d = int(min(h, w * 0.42))
        ring_w = max(2, d // 22)
        cx, cy = d / 2, h / 2
        bars_x = int(d * 0.82)
        bars_w = w - bars_x
        name_h = h * 0.24
        hp_h = h * 0.30
        pw_h = h * 0.20
        block = name_h + hp_h + pw_h + h * 0.04
        top = (h - block) / 2

        panel = parse_color(self.panel_color)
        if panel[3]:
            radius = int(min(hp_h, pw_h) * 0.45)
            draw.rounded_rectangle(
                (bars_x - d * 0.1, top + name_h - h * 0.02, w - 1, top + block),
                radius=radius,
                fill=panel,
            )
        layer.alpha_composite(marks)
        marks = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(marks)

        text = parse_color(self.text_color)
        class_color = m.get("wow.player.class_color")
        name_fill = parse_color(
            class_color
            if self.class_colored_name and isinstance(class_color, str)
            else self.name_color
        )
        pad = max(4, int(bars_w * 0.025))
        inner_x = bars_x + d * 0.12 + pad

        if self.show_name:
            name = m.get("wow.player.name")
            label = str(name) if isinstance(name, str) and name else _PLACEHOLDER
            state = m.get("wow.player.state_text")
            room = w - inner_x - pad
            name_font = _fit_font(draw, ctx, self.font, label, int(name_h * 0.72), room)
            state_font = ctx.font(self.font, max(7, int(name_h * 0.46)))
            if isinstance(state, str) and state:
                state_w = draw.textlength(state, font=state_font)
                if draw.textlength(label, font=name_font) + state_w + pad > room:
                    state = ""  # the name matters more; the ring already glows in combat
            if isinstance(state, str) and state:
                draw.text(
                    (w - pad, top + name_h * 0.55),
                    state,
                    font=state_font,
                    fill=(255, 122, 102, 255) if state != "RESTING" else name_fill,
                    anchor="rm",
                )
                room -= state_w + pad
            draw.text(
                (inner_x, top + name_h * 0.55),
                _fit_text(draw, label, name_font, room),
                font=name_font,
                fill=name_fill,
                anchor="lm",
                stroke_width=max(1, int(name_h * 0.05)),
                stroke_fill=(0, 0, 0, 200),
            )

        track = parse_color(self.track_color)
        loss = parse_color(self.loss_color)
        bar_left, bar_right = bars_x + d * 0.08, w - pad
        hp_top = top + name_h + h * 0.01
        pw_top = hp_top + hp_h + h * 0.02
        health_fill = parse_color(self.health_color)
        power_value = m.get("wow.player.power_color")
        power_fill = parse_color(
            power_value
            if self.power_by_type and isinstance(power_value, str) and power_value
            else self.power_color
        )
        for bar_top, bar_h, target, shown, fill in (
            (hp_top, hp_h - h * 0.02, health, shown_health, health_fill),
            (pw_top, pw_h, power, shown_power, power_fill),
        ):
            box = (bar_left, bar_top, bar_right, bar_top + bar_h)
            draw.rectangle(box, fill=track)
            span = bar_right - bar_left
            if target is not None and shown is not None:
                now_x = bar_left + span * target / 100
                was_x = bar_left + span * shown / 100
                if was_x > now_x + 1 and loss[3]:
                    draw.rectangle((now_x, bar_top, was_x, bar_top + bar_h), fill=loss)
                fill_x = min(now_x, was_x)
                if fill_x > bar_left + 1:
                    draw.rectangle((bar_left, bar_top, fill_x, bar_top + bar_h), fill=fill)
                    # A lighter band along the top, like the game's bar texture. Mixed
                    # by hand: ImageDraw replaces alpha instead of blending.
                    draw.rectangle(
                        (bar_left, bar_top, fill_x, bar_top + bar_h * 0.3),
                        fill=_lighten(fill, 0.22),
                    )
        layer.alpha_composite(marks)
        marks = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        draw = ImageDraw.Draw(marks)

        mode = self.bar_text if self.bar_text in BAR_TEXTS else "value"
        visible_left = max(bar_left, d + pad)  # the portrait covers the bars' left ends
        mid_x = (visible_left + bar_right) / 2
        text_room = bar_right - visible_left - 2 * pad
        if dead:
            hp_label = "GHOST" if m.get("wow.player.ghost") is True else "DEAD"
        else:
            hp_label = _bar_label(mode, m.get("wow.player.hp"), m.get("wow.player.hp_max"), health)
        pw_label = _bar_label(mode, m.get("wow.player.power"), m.get("wow.player.power_max"), power)
        for label, bar_top, bar_h in ((hp_label, hp_top, hp_h), (pw_label, pw_top, pw_h)):
            if not label:
                continue
            # Shrink to fit: an ellipsis would turn "1,548 / 1,800" into a wrong number.
            font = _fit_font(draw, ctx, self.font, label, int(bar_h * 0.5), text_room)
            draw.text(
                (mid_x, bar_top + bar_h / 2),
                _fit_text(draw, label, font, text_room),
                font=font,
                fill=text,
                anchor="mm",
                stroke_width=max(1, int(bar_h * 0.06)),
                stroke_fill=(0, 0, 0, 220),
            )
        layer.alpha_composite(marks)

        # The portrait sits on top of the bars' left ends.
        ring = parse_color(self.ring_color)
        fallback = parse_color(class_color if isinstance(class_color, str) else "#555555")
        face = self._face_disc(self._source_image(m), max(4, d - 2 * ring_w), dead, fallback)
        backing = Image.new("RGBA", (w, h), (0, 0, 0, 0))
        ImageDraw.Draw(backing).ellipse(
            (cx - d / 2, cy - d / 2, cx + d / 2, cy + d / 2), fill=(8, 8, 10, 255)
        )
        layer.alpha_composite(backing)
        layer.alpha_composite(face, (int(cx - face.width / 2), int(cy - face.height / 2)))

        glow = None
        if self.combat_glow and connected:
            if m.get("wow.player.combat") is True:
                glow = (230, 40, 30)
            elif m.get("wow.player.resting") is True:
                glow = (255, 209, 0)
        rings = Image.new("RGBA", (w * SUPERSAMPLE, h * SUPERSAMPLE), (0, 0, 0, 0))
        rd = ImageDraw.Draw(rings)
        s = SUPERSAMPLE
        r = d / 2 * s
        if glow is not None:
            strength = 0.45 + 0.55 * pulse_wave(ctx.time, 1.0)
            for step in range(4):
                spread = (step + 1) * ring_w * 0.6 * s
                alpha = int(150 * strength * (1 - step / 4))
                rd.ellipse(
                    (
                        cx * s - r - spread,
                        cy * s - r - spread,
                        cx * s + r + spread,
                        cy * s + r + spread,
                    ),
                    outline=(*glow, alpha),
                    width=int(ring_w * 0.7 * s),
                )
        rd.ellipse(
            (cx * s - r, cy * s - r, cx * s + r, cy * s + r),
            outline=ring,
            width=int(ring_w * s),
        )
        if self.show_level:
            br = d * 0.17 * s
            bx, by = (cx + d * 0.33) * s, (cy + d * 0.33) * s
            rd.ellipse((bx - br, by - br, bx + br, by + br), fill=(14, 12, 10, 255))
            rd.ellipse(
                (bx - br, by - br, bx + br, by + br), outline=ring, width=int(ring_w * 0.8 * s)
            )
        rings = rings.resize((w, h), Image.Resampling.LANCZOS)
        layer.alpha_composite(rings)

        if self.show_level:
            level = m.get("wow.player.level")
            label = str(level) if isinstance(level, int) and level > 0 else _PLACEHOLDER
            font = ctx.font(self.font, max(7, int(d * 0.17)))
            ImageDraw.Draw(layer).text(
                (cx + d * 0.33, cy + d * 0.33),
                label,
                font=font,
                fill=parse_color(self.name_color),
                anchor="mm",
            )

        if not connected:
            layer.putalpha(layer.getchannel("A").point(lambda v: v * 55 // 100))
        with overlay(canvas, (x, y, w, h)) as (scratch, _):
            scratch.alpha_composite(layer)
