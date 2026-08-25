"""Render core: tokens, colours, fit, scene round-trip, compositing, encoding."""

from __future__ import annotations

import json
import time

import pytest
from PIL import Image

import fitzlcd.render.layers  # noqa: F401 - registers the built-in layer types
from fitzlcd.render import tokens
from fitzlcd.render.colors import INVALID_COLOR, parse_color
from fitzlcd.render.compositor import Compositor
from fitzlcd.render.context import RenderContext
from fitzlcd.render.encode import Transform, apply_transform, encode_jpeg, to_image
from fitzlcd.render.fit import Fit, fit_image
from fitzlcd.render.scene import Layer, Scene, SceneError, layer_types

PANEL = (1920, 462)


class TestTokens:
    def test_substitutes_dotted_names(self):
        assert tokens.expand("{cpu.load}%", {"cpu.load": 42}) == "42%"

    def test_applies_format_spec(self):
        assert tokens.expand("{cpu.load:.1f}", {"cpu.load": 42.345}) == "42.3"

    def test_missing_metric_renders_placeholder(self):
        assert tokens.expand("{nope}", {}) == tokens.PLACEHOLDER

    def test_wrong_spec_for_type_falls_back_to_raw_value(self):
        # A numeric spec on a string metric must not raise mid-frame.
        assert tokens.expand("{gpu.name:.1f}", {"gpu.name": "RTX"}) == "RTX"

    def test_finds_referenced_metrics(self):
        assert tokens.find_tokens("{a.b} and {c:.0f}") == {"a.b", "c"}

    def test_dynamic_detection(self):
        assert tokens.is_dynamic("cpu {cpu.load}")
        assert not tokens.is_dynamic("static text")


class TestColors:
    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            ("#FF0000", (255, 0, 0, 255)),
            ("#FF000080", (255, 0, 0, 128)),
            ("red", (255, 0, 0, 255)),
            ([0, 255, 0], (0, 255, 0, 255)),
            ((0, 0, 255, 10), (0, 0, 255, 10)),
        ],
    )
    def test_parses_supported_forms(self, value, expected):
        assert parse_color(value) == expected

    def test_none_is_transparent(self):
        assert parse_color(None) == (0, 0, 0, 0)

    def test_garbage_is_loud_not_fatal(self):
        assert parse_color("not-a-colour") == INVALID_COLOR


class TestFit:
    def test_cover_fills_the_frame_and_crops(self):
        out = fit_image(Image.new("RGB", (1920, 1080)), PANEL, Fit.COVER)
        assert out.size == PANEL

    def test_contain_letterboxes_without_cropping(self):
        out = fit_image(Image.new("RGB", (1920, 1080), "red"), PANEL, Fit.CONTAIN)
        assert out.size == PANEL
        assert out.getpixel((5, 5))[3] == 0  # transparent letterbox

    def test_stretch_ignores_aspect(self):
        assert fit_image(Image.new("RGB", (100, 100)), PANEL, Fit.STRETCH).size == PANEL

    def test_tile_repeats(self):
        out = fit_image(Image.new("RGB", (64, 64), "blue"), PANEL, Fit.TILE)
        assert out.size == PANEL
        assert out.getpixel((1000, 200))[:3] == (0, 0, 255)

    def test_pan_selects_which_part_survives_the_crop(self):
        source = Image.new("RGB", (462, 1920))
        for y in range(1920):
            for x in range(0, 462, 461):
                source.putpixel((x, y), (y % 256, 0, 0))
        top = fit_image(source, PANEL, Fit.COVER, pan=0.0)
        bottom = fit_image(source, PANEL, Fit.COVER, pan=1.0)
        assert list(top.get_flattened_data()) != list(bottom.get_flattened_data())

    def test_rejects_zero_size(self):
        with pytest.raises(ValueError):
            fit_image(Image.new("RGB", (10, 10)), (0, 100))


class TestSceneModel:
    def test_round_trip_preserves_layers(self):
        original = Scene.from_dict(
            {
                "name": "Demo",
                "fps": 15,
                "background": "#123456",
                "layers": [
                    {"type": "text", "text": "hi", "size": 40},
                    {"type": "gauge", "metric": "cpu.load"},
                ],
            }
        )
        restored = Scene.from_dict(json.loads(json.dumps(original.to_dict())))
        assert restored.name == "Demo"
        assert restored.fps == 15
        assert [layer.type_name for layer in restored.layers] == ["text", "gauge"]
        assert restored.layers[0].text == "hi"

    def test_unknown_layer_type_is_reported_clearly(self):
        with pytest.raises(SceneError, match="unknown layer type"):
            Scene.from_dict({"layers": [{"type": "hologram"}]})

    def test_rejects_future_schema_version(self):
        with pytest.raises(SceneError, match="schema version"):
            Scene.from_dict({"version": 99, "layers": []})

    def test_rejects_nonsense_fps(self):
        with pytest.raises(SceneError, match="fps"):
            Scene.from_dict({"fps": 0, "layers": []})

    def test_saves_and_loads(self, tmp_path):
        scene = Scene(name="Saved", layers=[])
        path = scene.save(tmp_path / "saved.json")
        assert Scene.load(path).name == "Saved"

    def test_bad_json_is_reported_with_the_filename(self, tmp_path):
        path = tmp_path / "broken.json"
        path.write_text("{not json", encoding="utf-8")
        with pytest.raises(SceneError, match="broken.json"):
            Scene.load(path)

    def test_all_builtin_layer_types_are_registered(self):
        assert set(layer_types()) == {
            "text",
            "clock",
            "donut",
            "gauge",
            "sparkline",
            "media",
            "solid",
            "cs2_hit_timeline",
            "cs2_hit_flash",
        }

    def test_every_layer_type_round_trips_from_its_defaults(self):
        for name in layer_types():
            layer = Layer.from_dict({"type": name})
            data = layer.to_dict()
            assert data["type"] == name
            assert Layer.from_dict(data).type_name == name
            assert not any(key.startswith("_") for key in data), f"{name} leaks runtime state"

    def test_static_scene_is_not_dynamic(self):
        scene = Scene.from_dict({"layers": [{"type": "text", "text": "static"}]})
        assert not scene.is_dynamic

    def test_metric_text_makes_a_scene_dynamic(self):
        scene = Scene.from_dict({"layers": [{"type": "text", "text": "{cpu.load}"}]})
        assert scene.is_dynamic

    def test_hidden_layers_do_not_make_a_scene_dynamic(self):
        scene = Scene.from_dict(
            {"layers": [{"type": "clock", "visible": False}, {"type": "text", "text": "x"}]}
        )
        assert not scene.is_dynamic


class TestCompositor:
    def test_produces_an_rgb_frame_of_the_right_size(self):
        scene = Scene.from_dict({"background": "#102030", "layers": []})
        frame = Compositor(*PANEL).compose(scene)
        assert frame.size == PANEL
        assert frame.mode == "RGB"
        assert frame.getpixel((10, 10)) == (16, 32, 48)

    def test_translucent_layers_blend_rather_than_replace(self):
        scene = Scene.from_dict(
            {
                "background": "#000000",
                "layers": [
                    {"type": "solid", "color": "#FFFFFF"},
                    {
                        "type": "gauge",
                        "metric": "none",
                        "rect": [0, 0, 100, 100],
                        "track_color": "#00000080",
                        "radius": 0,
                    },
                ],
            }
        )
        frame = Compositor(*PANEL).compose(scene)
        pixel = frame.getpixel((50, 50))
        # Blended over white: mid grey. Replaced: near black.
        assert 100 < pixel[0] < 200, f"expected a blend, got {pixel}"

    def test_layer_opacity_is_applied(self):
        scene = Scene.from_dict(
            {
                "background": "#000000",
                "layers": [{"type": "solid", "color": "#FFFFFF", "opacity": 0.5}],
            }
        )
        pixel = Compositor(*PANEL).compose(scene).getpixel((100, 100))
        assert 100 < pixel[0] < 160

    def test_invisible_layers_are_skipped(self):
        scene = Scene.from_dict(
            {
                "background": "#000000",
                "layers": [{"type": "solid", "color": "#FFFFFF", "visible": False}],
            }
        )
        assert Compositor(*PANEL).compose(scene).getpixel((100, 100)) == (0, 0, 0)

    def test_a_failing_layer_does_not_kill_the_frame(self):
        class Exploding(Layer):
            type_name = "exploding"

            def draw(self, canvas, ctx):
                raise RuntimeError("boom")

        scene = Scene(background="#010203", layers=[Exploding()])
        frame = Compositor(*PANEL).compose(scene)
        assert frame.getpixel((0, 0)) == (1, 2, 3)

    def test_metrics_reach_text_layers(self):
        scene = Scene.from_dict(
            {
                "background": "#000000",
                "layers": [
                    {
                        "type": "text",
                        "text": "{cpu.load:.0f}",
                        "size": 200,
                        "pos": [10, 10],
                        "color": "#FFFFFF",
                    }
                ],
            }
        )
        ctx = RenderContext(*PANEL, metrics={"cpu.load": 88})
        frame = Compositor(*PANEL).compose(scene, ctx)
        assert any(p != (0, 0, 0) for p in frame.get_flattened_data()), "nothing was drawn"


class TestCs2Layers:
    def test_hit_timeline_draws_something_for_recent_events(self):
        scene = Scene.from_dict(
            {
                "background": "#000000",
                "layers": [
                    {
                        "type": "cs2_hit_timeline",
                        "rect": [0, 0, 400, 100],
                        "window_seconds": 10.0,
                    }
                ],
            }
        )
        events = [{"t": time.monotonic(), "amount": 40, "kind": "health"}]
        ctx = RenderContext(*PANEL, metrics={"cs2.player.hit_events": events})
        frame = Compositor(*PANEL).compose(scene, ctx)
        assert any(p != (0, 0, 0) for p in frame.get_flattened_data()), "nothing was drawn"

    def test_hit_timeline_ignores_events_outside_window(self):
        scene = Scene.from_dict(
            {
                "background": "#000000",
                "layers": [
                    {
                        "type": "cs2_hit_timeline",
                        "rect": [0, 0, 400, 100],
                        "window_seconds": 10.0,
                        "track_color": "#00000000",
                        "baseline_color": "#00000000",
                    }
                ],
            }
        )
        events = [{"t": time.monotonic() - 999, "amount": 40, "kind": "health"}]
        ctx = RenderContext(*PANEL, metrics={"cs2.player.hit_events": events})
        frame = Compositor(*PANEL).compose(scene, ctx)
        assert all(p == (0, 0, 0) for p in frame.get_flattened_data()), "a stale event was drawn"

    def test_hit_flash_only_draws_within_its_duration(self):
        scene = Scene.from_dict(
            {"background": "#000000", "layers": [{"type": "cs2_hit_flash", "duration": 0.5}]}
        )

        fresh = RenderContext(
            *PANEL,
            metrics={
                "cs2.player.last_hit_seconds_ago": 0.1,
                "cs2.player.last_hit_kind": "health",
            },
        )
        frame = Compositor(*PANEL).compose(scene, fresh)
        assert any(p != (0, 0, 0) for p in frame.get_flattened_data()), "nothing was drawn"

        stale = RenderContext(
            *PANEL,
            metrics={
                "cs2.player.last_hit_seconds_ago": 5.0,
                "cs2.player.last_hit_kind": "health",
            },
        )
        frame = Compositor(*PANEL).compose(scene, stale)
        assert all(p == (0, 0, 0) for p in frame.get_flattened_data()), "flash did not fade out"


class TestEncode:
    def test_rot270_swaps_axes(self):
        image = Image.new("RGB", PANEL)
        assert apply_transform(image, Transform.ROT_270).size == (462, 1920)

    def test_no_transform_keeps_orientation(self):
        image = Image.new("RGB", PANEL)
        assert apply_transform(image, Transform.NONE).size == PANEL

    def test_encodes_a_jpeg(self):
        frame = Image.new("RGB", PANEL, "red")
        data = encode_jpeg(frame, Transform.ROT_270, quality=80)
        assert data.startswith(b"\xff\xd8") and data.endswith(b"\xff\xd9")

    def test_higher_quality_is_larger(self):
        frame = Image.new("RGB", PANEL)
        for x in range(0, 1920, 7):
            frame.putpixel((x, x % 462), (x % 256, 128, 64))
        assert len(encode_jpeg(frame, quality=95)) > len(encode_jpeg(frame, quality=40))

    def test_rejects_non_rgb_arrays(self):
        import numpy as np

        with pytest.raises(ValueError, match="RGB"):
            to_image(np.zeros((10, 10), dtype=np.uint8))

    def test_rejects_wrong_dtype(self):
        import numpy as np

        with pytest.raises(ValueError, match="uint8"):
            to_image(np.zeros((10, 10, 3), dtype=np.float32))

    def test_from_angle_maps_to_transforms(self):
        assert Transform.from_angle(270) is Transform.ROT_270
        assert Transform.from_angle(0) is Transform.NONE
        assert Transform.from_angle(360) is Transform.NONE


class TestDonutLayer:
    """The ring gauge. Colours are neutralised per-test so each assertion is specific."""

    @staticmethod
    def _scene(**overrides):
        layer = {
            "type": "donut",
            "metric": "test.pct",
            "rect": [0, 0, 200, 200],
            "thickness": 20,
            "track_color": "#00000000",  # off, so "drawn" can only mean the value arc
        }
        layer.update(overrides)
        return Scene.from_dict({"background": "#000000", "layers": [layer]})

    def test_draws_an_arc_for_a_present_metric(self):
        ctx = RenderContext(*PANEL, metrics={"test.pct": 50})
        frame = Compositor(*PANEL).compose(self._scene(), ctx)
        assert any(p != (0, 0, 0) for p in frame.get_flattened_data()), "nothing was drawn"

    def test_missing_metric_draws_nothing(self):
        ctx = RenderContext(*PANEL, metrics={})
        frame = Compositor(*PANEL).compose(self._scene(), ctx)
        assert all(p == (0, 0, 0) for p in frame.get_flattened_data()), "drew without a value"

    def test_zero_draws_nothing_but_the_track(self):
        ctx = RenderContext(*PANEL, metrics={"test.pct": 0})
        frame = Compositor(*PANEL).compose(self._scene(), ctx)
        assert all(p == (0, 0, 0) for p in frame.get_flattened_data()), "drew a zero-width arc"

    def test_track_renders_even_with_no_value(self):
        scene = self._scene(track_color="#FFFFFFFF")
        ctx = RenderContext(*PANEL, metrics={})
        frame = Compositor(*PANEL).compose(scene, ctx)
        assert any(p != (0, 0, 0) for p in frame.get_flattened_data()), "track was not drawn"

    def test_thresholds_pick_the_fill_colour(self):
        scene = self._scene(
            color="#0000FF", warn_color="#00FF00", critical_color="#FF0000",
            warn_value=75.0, critical_value=90.0,
        )
        seen = {}
        for label, value in (("base", 10), ("warn", 80), ("crit", 95)):
            ctx = RenderContext(*PANEL, metrics={"test.pct": value})
            frame = Compositor(*PANEL).compose(scene, ctx)
            pixels = [p for p in frame.get_flattened_data() if p != (0, 0, 0)]
            # Antialiasing means the dominant channel identifies the colour.
            seen[label] = max(pixels, key=lambda p: max(p))
        assert seen["base"][2] > seen["base"][0], f"base should be blue-dominant, got {seen['base']}"
        assert seen["warn"][1] > seen["warn"][0], f"warn should be green-dominant, got {seen['warn']}"
        assert seen["crit"][0] > seen["crit"][1], f"crit should be red-dominant, got {seen['crit']}"

    def test_more_value_means_more_ink(self):
        def ink(value):
            ctx = RenderContext(*PANEL, metrics={"test.pct": value})
            frame = Compositor(*PANEL).compose(self._scene(), ctx)
            return sum(1 for p in frame.get_flattened_data() if p != (0, 0, 0))

        assert ink(25) < ink(75) < ink(100), "arc length should track the metric"

    def test_value_is_clamped_to_the_range(self):
        def ink(value):
            ctx = RenderContext(*PANEL, metrics={"test.pct": value})
            frame = Compositor(*PANEL).compose(self._scene(), ctx)
            return sum(1 for p in frame.get_flattened_data() if p != (0, 0, 0))

        assert ink(100) == ink(9999), "over-max should clamp to a full ring"
        assert ink(-50) == 0, "under-min should clamp to empty"

    def test_centre_text_expands_metric_tokens(self):
        plain = self._scene()
        labelled = self._scene(text="{test.pct:.0f}%", text_size=48)
        ctx = RenderContext(*PANEL, metrics={"test.pct": 50})
        bare = sum(1 for p in Compositor(*PANEL).compose(plain, ctx).get_flattened_data() if p != (0, 0, 0))
        with_text = sum(
            1 for p in Compositor(*PANEL).compose(labelled, ctx).get_flattened_data() if p != (0, 0, 0)
        )
        assert with_text > bare, "centre text did not render"

    def test_non_square_rect_stays_circular(self):
        """A wide box must centre a circle, not stretch an ellipse into the corners."""
        scene = self._scene(rect=[0, 0, 400, 200], track_color="#FFFFFFFF")
        ctx = RenderContext(*PANEL, metrics={"test.pct": 100})
        frame = Compositor(*PANEL).compose(scene, ctx)
        px = frame.load()
        lit = [(x, y) for y in range(210) for x in range(410) if px[x, y] != (0, 0, 0)]
        assert lit, "nothing was drawn"

        xs = [p[0] for p in lit]
        ys = [p[1] for p in lit]
        span_x, span_y = max(xs) - min(xs), max(ys) - min(ys)
        assert abs(span_x - span_y) <= 2, f"not circular: {span_x}x{span_y}"
        # 200-across circle centred in a 400-wide box.
        assert abs((min(xs) + max(xs)) / 2 - 200) <= 2, "circle is not centred in the rect"
        assert span_x >= 196, f"ring should fill the short axis, spans only {span_x}"
        assert frame.getpixel((200, 100)) == (0, 0, 0), "centre filled -- that's a pie, not a ring"

    def test_degenerate_rect_is_skipped(self):
        for rect in ([0, 0, 0, 0], [0, 0, 2, 2], [0, 0, 200, 1]):
            ctx = RenderContext(*PANEL, metrics={"test.pct": 50})
            frame = Compositor(*PANEL).compose(self._scene(rect=rect), ctx)
            assert all(p == (0, 0, 0) for p in frame.get_flattened_data()), f"drew into {rect}"

    def test_round_trips_through_json(self):
        scene = self._scene(text="{test.pct:.0f}%", clockwise=False)
        restored = Scene.from_dict(json.loads(json.dumps(scene.to_dict())))
        assert restored.layers[0].to_dict() == scene.layers[0].to_dict()

    def test_is_registered_for_the_gui(self):
        assert "donut" in layer_types()
