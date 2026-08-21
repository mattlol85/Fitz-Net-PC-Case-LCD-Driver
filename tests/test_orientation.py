"""Orientation and responsive-layout tests.

The panel can be mounted any way up, so the same scene has to lay out correctly
at 1920x462 and 462x1920. These tests pin down the rotation maths, the geometry
resolution, and the promise that no built-in scene draws outside its frame.
"""

from __future__ import annotations

import pytest
from PIL import Image

import fitzlcd.render.layers  # noqa: F401 - registers the built-in layer types
from fitzlcd.panels.base import PanelCaps
from fitzlcd.render.compositor import Compositor
from fitzlcd.render.context import RenderContext
from fitzlcd.render.encode import Transform, apply_transform
from fitzlcd.render.geometry import Anchor, parse_anchor, resolve, resolve_rect, resolve_size
from fitzlcd.render.layers.text import MIN_FONT_SIZE, TextLayer, _fitted_size
from fitzlcd.render.scene import Layer, Scene
from fitzlcd.scenes_builtin import DEFAULT_SCENES

LANDSCAPE = (1920, 462)
PORTRAIT = (462, 1920)
NATIVE = PanelCaps(1920, 462, Transform.ROT_270)

SAMPLE_METRICS = {
    "cpu.load": 42.0,
    "cpu.cores": 32,
    "cpu.freq": 3000.0,
    "mem.used_pct": 57.0,
    "mem.used_gb": 19.4,
    "mem.total_gb": 34.0,
    "gpu.load": 63.0,
    "gpu.temp": 71.0,
    "gpu.power": 220.0,
    "gpu.vram_pct": 44.0,
    "gpu.vram_used_gb": 7.5,
    "net.up": 1.1,
    "net.down": 4.2,
    "disk.used_pct": 69.0,
    "time.now": "19:41:02",
    "time.date": "2026-08-21",
    "time.uptime": "3h07m",
}


class TestTransformAlgebra:
    @pytest.mark.parametrize(
        ("transform", "angle"),
        [
            (Transform.NONE, 0),
            (Transform.ROT_90, 90),
            (Transform.ROT_180, 180),
            (Transform.ROT_270, 270),
        ],
    )
    def test_angles(self, transform, angle):
        assert transform.angle == angle

    def test_combining_rotations_adds_their_angles(self):
        assert Transform.ROT_90.combine(Transform.ROT_180) is Transform.ROT_270
        assert Transform.ROT_270.combine(90) is Transform.NONE
        assert Transform.ROT_180.combine(180) is Transform.NONE

    def test_inverse_cancels(self):
        for transform in Transform:
            assert transform.combine(transform.inverse()) is Transform.NONE

    def test_only_quarter_turns_swap_axes(self):
        assert Transform.ROT_90.swaps_axes
        assert Transform.ROT_270.swaps_axes
        assert not Transform.NONE.swaps_axes
        assert not Transform.ROT_180.swaps_axes

    def test_combination_matches_applying_both_rotations(self):
        """The whole scheme rests on this: two rotations really are one."""
        image = Image.new("RGB", (40, 10))
        for x in range(40):
            image.putpixel((x, 0), (x * 6 % 256, 0, 0))

        for first in Transform:
            for second in Transform:
                stepwise = apply_transform(apply_transform(image, first), second)
                combined = apply_transform(image, first.combine(second))
                assert stepwise.size == combined.size
                assert list(stepwise.getdata()) == list(combined.getdata())


class TestCapsRotation:
    @pytest.mark.parametrize(
        ("degrees", "size", "transform"),
        [
            (0, (1920, 462), Transform.ROT_270),
            (90, (462, 1920), Transform.NONE),
            (180, (1920, 462), Transform.ROT_90),
            (270, (462, 1920), Transform.ROT_180),
        ],
    )
    def test_geometry_and_transform(self, degrees, size, transform):
        caps = NATIVE.rotated(degrees)
        assert caps.size == size
        assert caps.transform is transform

    def test_full_turn_is_a_no_op(self):
        assert NATIVE.rotated(360) == NATIVE

    def test_rejects_non_quarter_turns(self):
        with pytest.raises(ValueError, match="multiple of 90"):
            NATIVE.rotated(45)

    def test_preserves_the_rest_of_the_caps(self):
        caps = NATIVE.rotated(90)
        assert caps.model == NATIVE.model
        assert caps.max_fps == NATIVE.max_fps

    def test_portrait_flag(self):
        assert NATIVE.rotated(90).is_portrait
        assert not NATIVE.rotated(0).is_portrait

    def test_encoded_output_is_always_the_panels_native_buffer(self):
        """Whatever the mounting, the bytes sent must suit the panel's scan-out."""
        for degrees in (0, 90, 180, 270):
            caps = NATIVE.rotated(degrees)
            composed = Image.new("RGB", caps.size)
            assert apply_transform(composed, caps.transform).size == (462, 1920)


class TestGeometry:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [(0, 0), (40, 40), ("50%", 50), ("0%", 0), ("100%", 100), ("center", 50)],
    )
    def test_resolves_lengths(self, value, expected):
        assert resolve(value, 100) == expected

    def test_negative_measures_from_the_far_edge(self):
        assert resolve(-10, 100) == 90
        assert resolve("-25%", 100) == 75

    def test_right_anchor_measures_inwards(self):
        assert resolve(10, 100, from_far_edge=True) == 90

    def test_garbage_resolves_to_zero_rather_than_raising(self):
        assert resolve("banana", 100) == 0
        assert resolve("%", 100) == 0

    def test_sizes_never_go_negative(self):
        assert resolve_size("-20", 100) == 80
        assert resolve_size("-200", 100) == 0
        assert resolve_size("50%", 100) == 50

    def test_empty_rect_is_the_whole_frame(self):
        assert resolve_rect([], (200, 100)) == (0, 0, 200, 100)

    def test_rect_percentages(self):
        assert resolve_rect(["10%", "20%", "50%", "30%"], (200, 100)) == (20, 20, 100, 30)

    def test_bottom_right_anchor_shifts_by_the_box_size(self):
        # 20 px in from each far edge, for a 40x10 box on a 200x100 frame.
        assert resolve_rect([20, 20, 40, 10], (200, 100), Anchor.BOTTOM_RIGHT) == (
            140,
            70,
            40,
            10,
        )

    def test_centre_anchor_centres_the_box(self):
        assert resolve_rect(["center", "center", 40, 10], (200, 100), Anchor.MIDDLE_CENTER) == (
            80,
            45,
            40,
            10,
        )

    def test_unknown_anchor_falls_back_to_top_left(self):
        assert parse_anchor("sideways") is Anchor.TOP_LEFT
        assert parse_anchor("bottom-right") is Anchor.BOTTOM_RIGHT


class TestTextFitting:
    def test_leaves_text_that_already_fits(self):
        ctx = RenderContext(*LANDSCAPE)
        assert _fitted_size("short", "mono", 40, 1800, ctx) == 40

    def test_shrinks_text_that_would_overflow(self):
        ctx = RenderContext(*PORTRAIT)
        fitted = _fitted_size("a very long line of terminal output", "mono", 40, 440, ctx)
        assert MIN_FONT_SIZE <= fitted < 40

    def test_never_goes_below_the_readable_floor(self):
        ctx = RenderContext(*PORTRAIT)
        assert _fitted_size("x" * 400, "mono", 40, 50, ctx) == MIN_FONT_SIZE

    def test_fitting_can_be_turned_off(self):
        layer = TextLayer(text="x" * 200, size=80, fit=False)
        canvas = Image.new("RGBA", PORTRAIT)
        layer.draw(canvas, RenderContext(*PORTRAIT))  # must not raise; may overflow

    def test_a_fitted_layer_stays_inside_the_frame(self):
        layer = TextLayer(text="a very long line indeed " * 3, size=80, pos=[10, 10])
        canvas = Image.new("RGBA", PORTRAIT, (0, 0, 0, 0))
        layer.draw(canvas, RenderContext(*PORTRAIT))
        assert _rightmost_ink(canvas) < PORTRAIT[0]


class TestOrientationFilter:
    def test_any_layer_draws_everywhere(self):
        assert Layer.from_dict({"type": "text"}).applies_to(LANDSCAPE)
        assert Layer.from_dict({"type": "text"}).applies_to(PORTRAIT)

    def test_tagged_layers_only_draw_in_their_orientation(self):
        wide = Layer.from_dict({"type": "text", "orientation": "landscape"})
        tall = Layer.from_dict({"type": "text", "orientation": "portrait"})
        assert wide.applies_to(LANDSCAPE) and not wide.applies_to(PORTRAIT)
        assert tall.applies_to(PORTRAIT) and not tall.applies_to(LANDSCAPE)

    def test_compositor_honours_the_filter(self):
        scene = Scene.from_dict(
            {
                "background": "#000000",
                "layers": [
                    {"type": "solid", "color": "#FFFFFF", "orientation": "portrait"},
                ],
            }
        )
        assert Compositor(*LANDSCAPE).compose(scene).getpixel((10, 10)) == (0, 0, 0)
        assert Compositor(*PORTRAIT).compose(scene).getpixel((10, 10)) == (255, 255, 255)

    def test_orientation_survives_a_round_trip(self):
        data = {"type": "gauge", "orientation": "portrait"}
        assert Layer.from_dict(Layer.from_dict(data).to_dict()).orientation == "portrait"


class TestBuiltinScenesInEveryOrientation:
    @pytest.mark.parametrize("name", sorted(DEFAULT_SCENES))
    @pytest.mark.parametrize("degrees", [0, 90, 180, 270])
    def test_scene_renders_at_every_orientation(self, name, degrees):
        caps = NATIVE.rotated(degrees)
        scene = Scene.from_dict(DEFAULT_SCENES[name])
        compositor = Compositor(caps.width, caps.height)
        frame = compositor.compose(
            scene, RenderContext(caps.width, caps.height, metrics=SAMPLE_METRICS)
        )
        assert frame.size == caps.size

    @pytest.mark.parametrize("name", sorted(DEFAULT_SCENES))
    @pytest.mark.parametrize("degrees", [0, 90])
    def test_scene_draws_something(self, name, degrees):
        if name == "wallpaper":
            pytest.skip("wallpaper is empty until the user points it at a file")
        caps = NATIVE.rotated(degrees)
        scene = Scene.from_dict(DEFAULT_SCENES[name])
        frame = Compositor(caps.width, caps.height).compose(
            scene, RenderContext(caps.width, caps.height, metrics=SAMPLE_METRICS)
        )
        colours = frame.convert("RGB").getcolors(maxcolors=1 << 20)
        assert colours is not None and len(colours) > 4, f"{name} at {degrees}° looks blank"

    @pytest.mark.parametrize("name", sorted(DEFAULT_SCENES))
    @pytest.mark.parametrize("degrees", [0, 90, 180, 270])
    def test_no_layer_overflows_its_frame(self, name, degrees):
        """The core promise of responsive layout: nothing spills off the panel.

        Each layer is drawn alone onto a deliberately oversized canvas while
        being told the real frame size. Anything that lands in the padding would
        have been silently clipped on the real panel.
        """
        caps = NATIVE.rotated(degrees)
        scene = Scene.from_dict(DEFAULT_SCENES[name])
        ctx = RenderContext(caps.width, caps.height, metrics=SAMPLE_METRICS)

        for layer in scene.visible_layers:
            if not layer.applies_to(caps.size):
                continue
            overflow = _overflow(layer, ctx)
            assert (
                not overflow
            ), f"{name} at {degrees}°: layer {layer.describe()!r} overflows by {overflow}"

    def test_the_overflow_detector_actually_detects_overflow(self):
        """Guards the test above from passing vacuously."""
        ctx = RenderContext(*PORTRAIT)
        long_line = {"type": "text", "text": "x" * 80, "size": 60, "pos": [10, 10]}

        unfitted = Layer.from_dict({**long_line, "fit": False})
        assert _overflow(unfitted, ctx).get("right", 0) > 100

        fitted = Layer.from_dict({**long_line, "fit": True})
        assert not _overflow(fitted, ctx)

        off_frame = Layer.from_dict({"type": "gauge", "rect": [400, 10, 300, 40]})
        assert _overflow(off_frame, ctx).get("right", 0) > 100

    def test_every_scene_is_valid_json_and_parses(self):
        for name, data in DEFAULT_SCENES.items():
            scene = Scene.from_dict(data)
            assert scene.layers, f"{name} has no layers"
            assert scene.fps >= 1

    def test_terminal_scene_has_both_layouts(self):
        scene = Scene.from_dict(DEFAULT_SCENES["terminal"])
        orientations = {layer.orientation for layer in scene.layers}
        assert {"landscape", "portrait"} <= orientations


def _rightmost_ink(canvas: Image.Image) -> int:
    """X of the right-most non-transparent pixel, or -1 when nothing was drawn."""
    box = canvas.getchannel("A").getbbox()
    return box[2] if box else -1


#: Slack around the nominal frame, so overflow has somewhere to land and be seen.
OVERFLOW_PAD = 400


def _overflow(layer, ctx: RenderContext) -> dict[str, int]:
    """How far ``layer`` draws outside the frame it was given, per edge."""
    canvas = Image.new("RGBA", (ctx.width + OVERFLOW_PAD, ctx.height + OVERFLOW_PAD), (0, 0, 0, 0))
    try:
        layer.draw(canvas, ctx)
    except Exception as exc:  # pragma: no cover - a drawing failure is its own bug
        raise AssertionError(f"layer {layer.describe()!r} failed to draw: {exc}") from exc

    box = canvas.getchannel("A").getbbox()
    if box is None:
        return {}
    left, top, right, bottom = box
    spill = {
        "left": max(0, -left),
        "top": max(0, -top),
        "right": max(0, right - ctx.width),
        "bottom": max(0, bottom - ctx.height),
    }
    # A pixel or two of antialiasing on an edge-hugging layer is not a layout bug.
    return {edge: amount for edge, amount in spill.items() if amount > 2}
