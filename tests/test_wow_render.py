"""The WoW map and party layers."""

from __future__ import annotations

import logging

import pytest
from PIL import Image

import fitzlcd.render.layers  # noqa: F401 - registers layer types
from fitzlcd.render.compositor import Compositor
from fitzlcd.render.context import RenderContext
from fitzlcd.render.layers.wow import WowPartyLayer
from fitzlcd.render.scene import Scene
from fitzlcd.sources.wow.demo import demo_metrics

SIZE = (800, 400)


def render(layer: dict, metrics: dict, time: float = 1.0) -> Image.Image:
    scene = Scene.from_dict({"background": "#000000", "layers": [layer]})
    ctx = RenderContext(*SIZE, time=time, metrics=metrics)
    return Compositor(*SIZE).compose(scene, ctx)


def lit(image: Image.Image) -> int:
    return sum(1 for p in image.get_flattened_data() if p != (0, 0, 0))


MAP = {"type": "wow_map", "rect": [0, 0, 800, 400], "border_color": "#00000000", "ping": 0}
PARTY = {"type": "wow_party", "rect": [0, 0, 800, 400]}


class TestMap:
    def test_draws_dots_on_the_grid_without_art(self):
        frame = render({**MAP, "backdrop_color": "#000000"}, demo_metrics())
        gold = sum(1 for r, g, b in frame.get_flattened_data() if r > 200 and g > 170 and b < 80)
        assert gold > 20, "the player's arrow is missing"

    def test_uses_the_map_art(self, tmp_path):
        art = tmp_path / "1429.png"
        Image.new("RGB", (300, 200), (0, 0, 255)).save(art)
        metrics = {**demo_metrics(), "wow.map.image": str(art)}
        frame = render({**MAP, "backdrop_dim": 0.0, "view": "zone"}, metrics)
        blue = sum(1 for r, g, b in frame.get_flattened_data() if b > 200 and r < 50)
        assert blue > 800 * 400 * 0.5

    def test_missing_art_file_falls_back_to_the_grid(self, tmp_path, caplog):
        metrics = {**demo_metrics(), "wow.map.image": str(tmp_path / "gone.png")}
        with caplog.at_level(logging.WARNING):
            frame = render(MAP, metrics)
        assert lit(frame) > 1000
        assert "will be skipped" not in caplog.text

    @pytest.mark.parametrize(
        "party",
        [None, "lots", 7, [None, 3, "x"], [{"x": "far", "y": None, "map_id": 1429}]],
    )
    def test_garbage_party_is_ignored(self, party, caplog):
        metrics = {**demo_metrics(), "wow.party": party}
        with caplog.at_level(logging.WARNING):
            render(MAP, metrics)
        assert "will be skipped" not in caplog.text

    @pytest.mark.parametrize("view", ["party", "follow", "zone", "bogus"])
    def test_every_view_renders(self, view):
        assert lit(render({**MAP, "view": view}, demo_metrics())) > 1000

    def test_dungeon_says_positions_are_hidden(self):
        metrics = {
            k: v for k, v in demo_metrics().items() if k not in ("wow.player.x", "wow.player.y")
        }
        metrics["wow.player.instance"] = True
        assert lit(render(MAP, metrics)) > 1000

    def test_dots_glide_rather_than_jump(self):
        scene = Scene.from_dict({"background": "#000000", "layers": [{**MAP, "glide": 2.0}]})
        layer = scene.layers[0]
        comp = Compositor(*SIZE)
        metrics = demo_metrics()
        comp.compose(scene, RenderContext(*SIZE, time=0.0, metrics=metrics))
        moved = {**metrics, "wow.player.x": metrics["wow.player.x"] + 0.02}
        comp.compose(scene, RenderContext(*SIZE, time=0.1, metrics=moved))
        shown = layer._glides["player"].x
        assert metrics["wow.player.x"] < shown < moved["wow.player.x"]

    def test_scene_restart_snaps(self):
        scene = Scene.from_dict({"background": "#000000", "layers": [{**MAP, "glide": 2.0}]})
        layer = scene.layers[0]
        comp = Compositor(*SIZE)
        metrics = demo_metrics()
        comp.compose(scene, RenderContext(*SIZE, time=5.0, metrics=metrics))
        moved = {**metrics, "wow.player.x": metrics["wow.player.x"] + 0.02}
        comp.compose(scene, RenderContext(*SIZE, time=0.0, metrics=moved))
        assert layer._glides["player"].x == pytest.approx(moved["wow.player.x"])

    def test_runtime_state_is_not_serialised(self):
        scene = Scene.from_dict({"layers": [MAP]})
        render(MAP, demo_metrics())
        assert not any(k.startswith("_") for k in scene.layers[0].to_dict())


class TestParty:
    def test_draws_rows(self):
        assert lit(render(PARTY, demo_metrics())) > 2000

    def test_alone_shows_the_empty_text(self):
        assert lit(render(PARTY, {"wow.party": []})) > 50
        assert lit(render({**PARTY, "empty_text": ""}, {"wow.party": []})) == 0

    @pytest.mark.parametrize("party", [None, {"a": 1}, [1, 2], [{"health_pct": "full"}]])
    def test_garbage_party_is_ignored(self, party, caplog):
        with caplog.at_level(logging.WARNING):
            render(PARTY, {"wow.party": party})
        assert "will be skipped" not in caplog.text

    def test_member_in_another_zone_shows_its_zone(self):
        layer = WowPartyLayer()
        member = {"zone": "Westfall", "distance_yd": 1240.0}
        assert layer._status(member) == "Westfall · 1,240 yd"
        assert layer._status({"zone": "Westfall"}) == "Westfall"
        assert layer._status({}) == "far away"


class TestMapEdgeChips:
    def test_a_member_in_another_zone_gets_an_edge_arrow(self):
        away = {"slot": 1, "name": "Far", "color": "#00FF00", "map_id": 7, "bearing": 0.0}
        metrics = {**demo_metrics(), "wow.party": [away]}
        frame = render({**MAP, "backdrop_color": "#000000", "show_names": False}, metrics)
        top = frame.crop((0, 0, SIZE[0], SIZE[1] // 4))
        green = sum(1 for r, g, b in top.get_flattened_data() if g > 200 and r < 80 and b < 80)
        assert green > 20, "a bearing of 0 is due north: the arrow belongs on the top edge"

    def test_no_bearing_no_arrow(self):
        away = {"slot": 1, "color": "#00FF00", "map_id": 7}
        metrics = {**demo_metrics(), "wow.party": [away]}
        frame = render({**MAP, "backdrop_color": "#000000"}, metrics)
        green = sum(1 for r, g, b in frame.get_flattened_data() if g > 200 and r < 80 and b < 80)
        assert green == 0

    @pytest.mark.parametrize("bearing", ["nan", None, "x", float("inf")])
    def test_bad_bearings_are_ignored(self, bearing):
        away = {"slot": 1, "color": "#00FF00", "map_id": 7, "bearing": bearing}
        render(MAP, {**demo_metrics(), "wow.party": [away]})


UNIT = {"type": "wow_unit_frame", "rect": [0, 0, 800, 400], "combat_glow": False}


def colour_count(image: Image.Image, test) -> int:
    return sum(1 for px in image.get_flattened_data() if test(*px))


class TestUnitFrame:
    def test_draws_portrait_and_bars(self):
        frame = render(UNIT, demo_metrics())
        assert colour_count(frame, lambda r, g, b: g > 150 and r < 80 and b < 80) > 500, "health"
        assert colour_count(frame, lambda r, g, b: b > 180 and r < 40 and 80 < g < 150) > 300
        assert colour_count(frame, lambda r, g, b: r > 200 and 150 < g < 210 and b > 120) > 50

    def test_power_colour_follows_the_power_type(self):
        metrics = {**demo_metrics(), "wow.player.power_color": "#FF0000"}
        frame = render(UNIT, metrics)
        assert colour_count(frame, lambda r, g, b: r > 220 and g < 30 and b < 30) > 300

    def test_nothing_known_still_renders(self, caplog):
        with caplog.at_level(logging.WARNING):
            frame = render(UNIT, {})
        assert lit(frame) > 0
        assert "failed" not in caplog.text.lower()

    @pytest.mark.parametrize(
        "metrics",
        [
            {"wow.player.portrait": "not an image", "wow.player.health_pct": "x"},
            {"wow.player.hp": -5, "wow.player.hp_max": 0, "wow.player.power_pct": float("nan")},
            {"wow.player.class_icon": "C:/does/not/exist.png", "wow.player.level": "high"},
            {"wow.player.health_pct": 1e9, "wow.player.power_pct": -40},
        ],
    )
    def test_garbage_metrics_are_survived(self, metrics):
        render(UNIT, metrics)

    def test_class_icon_is_the_fallback(self, tmp_path):
        icon = tmp_path / "icon.png"
        Image.new("RGBA", (64, 64), (255, 0, 255, 255)).save(icon)
        metrics = {**demo_metrics(), "wow.player.class_icon": str(icon)}
        metrics.pop("wow.player.portrait")
        frame = render(UNIT, metrics)
        assert colour_count(frame, lambda r, g, b: r > 230 and g < 30 and b > 230) > 1000
        live = render(
            {**UNIT, "portrait": "class"}, demo_metrics() | {"wow.player.class_icon": str(icon)}
        )
        assert colour_count(live, lambda r, g, b: r > 230 and g < 30 and b > 230) > 1000

    def test_damage_leaves_a_red_slice_that_fades(self):
        layer = {**UNIT, "glide": 1.0, "loss_color": "#FF0000"}
        metrics = {**demo_metrics(), "wow.player.health_pct": 90.0}
        scene = Scene.from_dict({"background": "#000000", "layers": [layer]})
        comp = Compositor(*SIZE)
        red = lambda r, g, b: r > 230 and g < 40 and b < 40  # noqa: E731
        comp.compose(scene, RenderContext(*SIZE, time=1.0, metrics=metrics))
        hit = {**metrics, "wow.player.health_pct": 30.0}
        soon = comp.compose(scene, RenderContext(*SIZE, time=1.1, metrics=hit))
        for step in range(2, 40):  # the layer advances at most a quarter second a frame
            later = comp.compose(scene, RenderContext(*SIZE, time=1.0 + step / 10, metrics=hit))
        assert colour_count(soon, red) > 300
        assert colour_count(later, red) < colour_count(soon, red) / 10

    def test_dead_says_so_and_empties_health(self):
        metrics = {**demo_metrics(), "wow.player.dead": True}
        frame = render(UNIT, metrics)
        assert colour_count(frame, lambda r, g, b: g > 150 and r < 80 and b < 80) < 50

    def test_runtime_state_is_not_serialised(self):
        scene = Scene.from_dict({"layers": [UNIT]})
        data = scene.layers[0].to_dict()
        assert not [k for k in data if k.startswith("_")]
