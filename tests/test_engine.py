"""Engine and configuration tests, driven against the virtual panel."""

from __future__ import annotations

import io
import time

import pytest

import fitzlcd.render.layers  # noqa: F401 - registers the built-in layer types
from fitzlcd.config import AppConfig, SceneLibrary
from fitzlcd.engine import EngineConfig, RenderEngine
from fitzlcd.panels import registry
from fitzlcd.panels.base import PanelUnavailableError
from fitzlcd.panels.virtual import VirtualPanel
from fitzlcd.render.scene import Scene


@pytest.fixture
def virtual_only(monkeypatch):
    """Force autodetect to see only the software panel."""
    monkeypatch.setattr("fitzlcd.panels.ds916.driver.list_ports.comports", lambda: [])
    return registry.resolve("virtual")


def wait_for(predicate, timeout=5.0, interval=0.02):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(interval)
    return False


def make_engine(**overrides) -> RenderEngine:
    config = EngineConfig(panel="virtual", **overrides)
    return RenderEngine(config, metrics_provider=lambda: {"cpu.load": 50.0})


class TestEngineLifecycle:
    def test_connects_and_sends_frames(self, virtual_only):
        engine = make_engine()
        engine.set_scene(Scene.from_dict({"fps": 30, "layers": [{"type": "clock"}]}))
        engine.start()
        try:
            assert wait_for(lambda: engine.stats.frames_sent > 2), engine.stats
            assert engine.stats.connected
            assert engine.stats.width == 1920
        finally:
            engine.stop()
        assert not engine.stats.connected

    def test_frame_rate_is_capped_by_the_panel(self, virtual_only):
        engine = make_engine()
        # The scene asks for 240; the virtual panel caps at 30.
        engine.set_scene(Scene.from_dict({"fps": 240, "layers": [{"type": "clock"}]}))
        engine.start()
        try:
            assert wait_for(lambda: engine.stats.target_fps > 0)
            assert engine.stats.target_fps == 30
        finally:
            engine.stop()

    def test_config_max_fps_lowers_the_cap_further(self, virtual_only):
        engine = make_engine(max_fps=5)
        engine.set_scene(Scene.from_dict({"fps": 60, "layers": [{"type": "clock"}]}))
        engine.start()
        try:
            assert wait_for(lambda: engine.stats.target_fps > 0)
            assert engine.stats.target_fps == 5
        finally:
            engine.stop()

    def test_static_scene_is_sent_once_then_skipped(self, virtual_only):
        engine = make_engine()
        engine.set_scene(
            Scene.from_dict({"fps": 60, "layers": [{"type": "solid", "color": "#123456"}]})
        )
        engine.start()
        try:
            assert wait_for(lambda: engine.stats.frames_skipped > 5, timeout=6.0)
            # The identical frame is encoded once and never re-sent.
            assert engine.stats.frames_sent == 1, engine.stats
        finally:
            engine.stop()

    def test_preview_callback_receives_frames(self, virtual_only):
        seen = []
        engine = make_engine()
        engine.on_frame = seen.append
        engine.set_scene(Scene.from_dict({"fps": 30, "layers": [{"type": "clock"}]}))
        engine.start()
        try:
            assert wait_for(lambda: len(seen) > 1)
            assert seen[0].size == (1920, 462)
        finally:
            engine.stop()

    def test_pause_stops_output_without_disconnecting(self, virtual_only):
        engine = make_engine()
        engine.set_scene(Scene.from_dict({"fps": 30, "layers": [{"type": "clock"}]}))
        engine.start()
        try:
            assert wait_for(lambda: engine.stats.frames_sent > 2)
            engine.set_paused(True)
            time.sleep(0.4)
            settled = engine.stats.frames_sent
            time.sleep(0.5)
            assert engine.stats.frames_sent == settled
            assert engine.stats.connected
        finally:
            engine.stop()

    def test_missing_panel_is_reported_not_raised(self, monkeypatch):
        monkeypatch.setattr("fitzlcd.panels.ds916.driver.list_ports.comports", lambda: [])
        engine = RenderEngine(EngineConfig(panel="auto", reconnect=False))
        engine.set_scene(Scene.from_dict({"layers": []}))
        engine.start()
        try:
            assert wait_for(lambda: "no panel detected" in engine.stats.last_error)
            assert not engine.stats.connected
        finally:
            engine.stop()

    def test_a_dropped_panel_triggers_a_reconnect(self, monkeypatch, virtual_only):
        failures = {"count": 0}
        original = VirtualPanel.push_frame

        def flaky(self, payload):
            if failures["count"] < 1:
                failures["count"] += 1
                raise PanelUnavailableError("simulated dropout")
            original(self, payload)

        monkeypatch.setattr(VirtualPanel, "push_frame", flaky)

        engine = make_engine()
        engine.set_scene(Scene.from_dict({"fps": 30, "layers": [{"type": "clock"}]}))
        engine.start()
        try:
            assert wait_for(lambda: engine.stats.frames_sent > 1, timeout=8.0)
            assert engine.stats.connected
            assert failures["count"] == 1
        finally:
            engine.stop()


class TestConfig:
    def test_round_trips(self, tmp_path):
        path = tmp_path / "config.json"
        AppConfig(panel="COM5", quality=75, autostart=True).save(path)
        loaded = AppConfig.load(path)
        assert (loaded.panel, loaded.quality, loaded.autostart) == ("COM5", 75, True)

    def test_missing_file_yields_defaults(self, tmp_path):
        assert AppConfig.load(tmp_path / "absent.json").panel == "auto"

    def test_corrupt_file_yields_defaults_instead_of_crashing(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("{{{", encoding="utf-8")
        assert AppConfig.load(path).quality == 90

    def test_unknown_keys_from_a_newer_build_are_ignored(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text('{"panel": "COM9", "future_option": 1}', encoding="utf-8")
        assert AppConfig.load(path).panel == "COM9"


class TestSceneLibrary:
    def test_seeds_defaults_on_first_run(self, tmp_path):
        library = SceneLibrary(tmp_path)
        library.ensure_defaults()
        names = {scene.name for scene in library.list()}
        assert {"Rig Stats", "Clock", "Wallpaper"} <= names

    def test_does_not_reseed_over_existing_scenes(self, tmp_path):
        library = SceneLibrary(tmp_path)
        library.ensure_defaults()
        for path in tmp_path.glob("*.json"):
            path.unlink()
        library.save(Scene(name="Only Mine"))
        library.ensure_defaults()
        assert [scene.name for scene in library.list()] == ["Only Mine"]

    def test_broken_scene_files_are_skipped_not_fatal(self, tmp_path):
        library = SceneLibrary(tmp_path)
        library.save(Scene(name="Good"))
        (tmp_path / "broken.json").write_text("{ nope", encoding="utf-8")
        assert [scene.name for scene in library.list()] == ["Good"]

    def test_save_and_delete(self, tmp_path):
        library = SceneLibrary(tmp_path)
        scene = Scene(name="Temp Scene")
        library.save(scene)
        assert library.get("Temp Scene") is not None
        library.delete(scene)
        assert library.get("Temp Scene") is None

    def test_default_scenes_all_parse(self, tmp_path):
        library = SceneLibrary(tmp_path)
        library.ensure_defaults()
        for scene in library.list():
            assert scene.fps >= 1
            assert isinstance(scene.layers, list)


class TestEngineRotation:
    def test_composes_at_the_rotated_geometry(self, virtual_only):
        seen: list[tuple[int, int]] = []
        engine = make_engine(rotation=90)
        engine.on_frame = lambda image: seen.append(image.size)
        engine.set_scene(Scene.from_dict({"fps": 30, "layers": [{"type": "clock"}]}))
        engine.start()
        try:
            assert wait_for(lambda: bool(seen))
            assert seen[0] == (462, 1920), "portrait mounting should compose portrait"
        finally:
            engine.stop()

    def test_rotating_at_runtime_changes_the_frame(self, virtual_only):
        seen: list[tuple[int, int]] = []
        engine = make_engine()
        engine.on_frame = lambda image: seen.append(image.size)
        engine.set_scene(Scene.from_dict({"fps": 30, "layers": [{"type": "clock"}]}))
        engine.start()
        try:
            assert wait_for(lambda: (1920, 462) in seen)
            engine.set_rotation(90)
            assert wait_for(lambda: (462, 1920) in seen)
            assert engine.stats.rotation == 90
            assert (engine.stats.width, engine.stats.height) == (462, 1920)
        finally:
            engine.stop()

    def test_output_is_always_the_panels_native_buffer(self, virtual_only):
        """Whatever the mounting, the panel must receive its own scan-out shape."""
        from PIL import Image

        for degrees in (0, 90, 180, 270):
            engine = make_engine(rotation=degrees)
            engine.set_scene(Scene.from_dict({"fps": 30, "layers": [{"type": "clock"}]}))
            engine.start()
            try:
                assert wait_for(lambda e=engine: e.stats.frames_sent > 0)
                payload = engine._panel.last_frame
            finally:
                engine.stop()
            with Image.open(io.BytesIO(payload)) as sent:
                assert sent.size == (462, 1920), f"wrong buffer shape at {degrees}°"

    def test_rejects_non_quarter_turns(self):
        engine = make_engine()
        with pytest.raises(ValueError, match="multiple of 90"):
            engine.set_rotation(45)

    def test_rotation_is_a_no_op_when_unchanged(self, virtual_only):
        engine = make_engine(rotation=90)
        engine.set_rotation(90)
        assert engine.config.rotation == 90
