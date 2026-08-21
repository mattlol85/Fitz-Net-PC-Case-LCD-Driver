"""Render core: tokens, colours, fit, scene round-trip, compositing, encoding."""

from __future__ import annotations

import json

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
        assert set(layer_types()) == {"text", "clock", "gauge", "sparkline", "media", "solid"}

    def test_every_layer_type_round_trips_from_its_defaults(self):
        for name, cls in layer_types().items():
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
