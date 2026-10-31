"""Motion for the animated layers, and the Claude activity signal that drives the spark.

Frames are compared as pixels, the same way the render tests do it. Where a
motion is subtle the test checks an ordering or a difference rather than an
exact value, so it survives antialiasing changes.
"""

from __future__ import annotations

import json
import math
import os
import time
from pathlib import Path

import pytest

import fitzlcd.render.layers  # noqa: F401 - registers the built-in layer types
from fitzlcd.render.colors import mix
from fitzlcd.render.compositor import Compositor
from fitzlcd.render.context import RenderContext
from fitzlcd.render.motion import (
    MAX_STEP,
    ValueGlide,
    approach,
    as_finite_float,
    ease_out_cubic,
    pulse_wave,
)
from fitzlcd.render.scene import Scene
from fitzlcd.scenes_builtin import DEFAULT_SCENES
from fitzlcd.sources.claude_usage import ACTIVE_WINDOW_SECONDS, ClaudeUsageProvider

PANEL = (1920, 462)


# ------------------------------------------------------------------ helpers


def _frame(scene: Scene, time: float, metrics: dict):
    return Compositor(*PANEL).compose(scene, RenderContext(*PANEL, time=time, metrics=metrics))


def _render(scene: Scene, time: float, metrics: dict) -> bytes:
    return _frame(scene, time, metrics).tobytes()


def _lit(scene: Scene, time: float, metrics: dict) -> int:
    return sum(1 for p in _frame(scene, time, metrics).get_flattened_data() if p != (0, 0, 0))


def _magenta(scene: Scene, time: float, metrics: dict) -> int:
    """Pixels in the highlight colour family (high red and blue, low green)."""
    pixels = _frame(scene, time, metrics).get_flattened_data()
    return sum(1 for r, g, b in pixels if r > 200 and b > 200 and g < 60)


def _scene(layer: dict) -> Scene:
    return Scene.from_dict({"background": "#000000", "layers": [layer]})


def _ring(**overrides) -> dict:
    layer = {
        "type": "donut",
        "metric": "test.pct",
        "rect": [0, 0, 200, 200],
        "thickness": 20,
        "track_color": "#00000000",
        "color": "#00E5FF",
    }
    layer.update(overrides)
    return layer


def _spark(**overrides) -> dict:
    layer = {"type": "spark", "rect": [0, 0, 200, 200], "color": "#FFFFFF", "arms": 5}
    layer.update(overrides)
    return layer


# ------------------------------------------------------------ pure helpers


class TestMotionHelpers:
    def test_approach_snaps_without_a_time_constant(self):
        assert approach(0.0, 10.0, 0.1, 0.0) == 10.0

    def test_approach_holds_still_without_elapsed_time(self):
        assert approach(0.0, 10.0, 0.0, 1.0) == 0.0

    def test_approach_covers_about_95_percent_in_three_time_constants(self):
        assert 0.94 < approach(0.0, 1.0, 3.0, 1.0) < 0.96

    def test_ease_out_cubic_clamps_and_starts_fast(self):
        assert ease_out_cubic(-1.0) == 0.0
        assert ease_out_cubic(2.0) == 1.0
        assert ease_out_cubic(0.5) > 0.5

    def test_pulse_wave_starts_at_its_peak_and_stays_in_range(self):
        assert pulse_wave(0.0, 1.0) == pytest.approx(1.0)
        assert pulse_wave(0.5, 1.0) == pytest.approx(0.0, abs=1e-9)
        for step in range(50):
            assert 0.0 <= pulse_wave(step * 0.07, 2.0) <= 1.0

    @pytest.mark.parametrize("value", [None, "abc", "nan", "inf", float("nan"), float("-inf"), [1]])
    def test_missing_or_non_finite_readings_are_none(self, value):
        assert as_finite_float(value) is None

    def test_numeric_readings_come_through(self):
        assert as_finite_float(7) == 7.0
        assert as_finite_float("42") == 42.0

    def test_mix_blends_channel_by_channel_and_clamps(self):
        a, b = (0, 0, 0, 255), (255, 100, 50, 255)
        assert mix(a, b, 0.0) == a
        assert mix(a, b, 1.0) == b
        assert mix(a, b, 2.0) == b


class TestValueGlide:
    def test_first_reading_snaps(self):
        assert ValueGlide().step(40.0, 0.0, glide=2.0) == 40.0

    def test_a_new_reading_eases_in(self):
        glide = ValueGlide()
        glide.step(0.0, 0.0, 2.0)
        value = glide.step(100.0, 0.5, 2.0)
        assert 0.0 < value < 100.0

    def test_one_frame_never_covers_more_than_the_max_step(self):
        glide = ValueGlide()
        glide.step(0.0, 0.0, 2.0)
        value = glide.step(100.0, 10.0, 2.0)  # a long stall between frames
        assert value == pytest.approx(100.0 * (1.0 - math.exp(-MAX_STEP / (2.0 / 3.0))))

    def test_time_going_backwards_snaps(self):
        glide = ValueGlide()
        glide.step(0.0, 5.0, 2.0)
        glide.step(100.0, 6.0, 2.0)
        assert glide.step(30.0, 0.1, 2.0) == 30.0

    def test_a_rise_highlights_from_where_it_was(self):
        glide = ValueGlide()
        glide.step(40.0, 0.0, 0.0)
        glide.step(80.0, 1.0, 0.0)
        assert glide.gain_from == 40.0
        assert glide.gain_progress(1.0, 2.0) == 0.0
        assert glide.gain_progress(2.0, 2.0) == 0.5

    def test_the_highlight_expires_after_its_flash(self):
        glide = ValueGlide()
        glide.step(40.0, 0.0, 0.0)
        glide.step(80.0, 1.0, 0.0)
        assert glide.gain_progress(3.5, 2.0) is None
        assert glide.gain_from is None

    def test_a_fall_cancels_the_highlight(self):
        glide = ValueGlide()
        glide.step(80.0, 0.0, 0.0)
        glide.step(40.0, 1.0, 0.0)
        assert glide.gain_progress(1.1, 2.0) is None

    def test_no_highlight_without_a_flash_time(self):
        glide = ValueGlide()
        glide.step(40.0, 0.0, 0.0)
        glide.step(80.0, 1.0, 0.0)
        assert glide.gain_progress(1.1, 0.0) is None

    def test_a_missing_reading_resets_and_the_next_one_snaps(self):
        glide = ValueGlide()
        glide.step(40.0, 0.0, 2.0)
        assert glide.step(None, 1.0, 2.0) is None
        assert glide.step(70.0, 1.1, 2.0) == 70.0, "eased up from a fake zero"


# ------------------------------------------------------------------- rings


class TestRingAnimation:
    def test_the_sweep_starts_empty(self):
        scene = _scene(_ring(sweep_in=1.0))
        assert _lit(scene, 0.0, {"test.pct": 100}) == 0

    def test_the_sweep_fills_progressively(self):
        scene = _scene(_ring(sweep_in=1.0))
        counts = [_lit(scene, t, {"test.pct": 100}) for t in (0.2, 0.5, 0.9)]
        assert counts == sorted(counts) and counts[0] < counts[-1]

    def test_the_sweep_is_finished_by_its_end(self):
        animated = _scene(_ring(sweep_in=1.0))
        plain = _scene(_ring())
        metrics = {"test.pct": 60}
        assert _render(animated, 1.5, metrics) == _render(plain, 1.5, metrics)

    def test_a_glide_moves_between_readings(self):
        scene = _scene(_ring(glide=2.0))
        start = _lit(scene, 0.0, {"test.pct": 0})
        middle = _lit(scene, 0.5, {"test.pct": 100})
        full = _lit(_scene(_ring()), 0.5, {"test.pct": 100})
        assert start == 0 < middle < full

    def test_a_clock_restart_snaps_the_glide(self):
        scene = _scene(_ring(glide=2.0))
        _render(scene, 0.0, {"test.pct": 0})
        _render(scene, 10.0, {"test.pct": 100})  # glide underway
        assert _render(scene, 0.1, {"test.pct": 40}) == _render(
            _scene(_ring()), 0.1, {"test.pct": 40}
        )

    def test_a_rise_lights_up_the_slice_it_added(self):
        scene = _scene(_ring(gain_flash=2.0, gain_color="#FF00FF"))
        _render(scene, 0.0, {"test.pct": 40})
        assert _magenta(scene, 0.5, {"test.pct": 80}) > 0

    def test_no_rise_means_no_highlight(self):
        scene = _scene(_ring(gain_flash=2.0, gain_color="#FF00FF"))
        _render(scene, 0.0, {"test.pct": 80})
        assert _magenta(scene, 0.5, {"test.pct": 80}) == 0

    def test_the_highlight_cools_back_to_the_fill(self):
        scene = _scene(_ring(gain_flash=2.0, gain_color="#FF00FF"))
        _render(scene, 0.0, {"test.pct": 40})
        assert _magenta(scene, 0.5, {"test.pct": 80}) > 0
        assert _magenta(scene, 2.4, {"test.pct": 80}) == 0

    def test_a_fall_draws_no_highlight(self):
        scene = _scene(_ring(gain_flash=2.0, gain_color="#FF00FF"))
        _render(scene, 0.0, {"test.pct": 80})
        assert _render(scene, 0.5, {"test.pct": 40}) == _render(
            _scene(_ring()), 0.5, {"test.pct": 40}
        )

    def test_the_pulse_only_runs_past_the_warn_line(self):
        def pulsing(value: float) -> bool:
            scene = _scene(_ring(pulse=1.0))
            return _render(scene, 0.0, {"test.pct": value}) != _render(
                _scene(_ring(pulse=1.0)), 0.25, {"test.pct": value}
            )

        assert pulsing(90), "a critical reading should pulse"
        assert not pulsing(50), "a calm reading should not pulse"

    def test_missing_or_non_finite_readings_draw_only_the_track(self):
        scene = _scene(
            _ring(sweep_in=1.0, glide=1.0, gain_flash=2.0, pulse=1.0, gain_color="#FFFFFF")
        )
        for value in (float("nan"), "nan", None):
            metrics = {} if value is None else {"test.pct": value}
            assert _lit(scene, 1.5, metrics) == 0

    def test_default_ring_is_unchanged_over_time(self):
        metrics = {"test.pct": 60}
        assert _render(_scene(_ring()), 0.0, metrics) == _render(_scene(_ring()), 5.0, metrics)

    def test_animation_fields_round_trip(self):
        scene = _scene(
            _ring(
                sweep_in=0.9,
                glide=1.2,
                gain_flash=2.5,
                gain_color="#F0EEE6",
                pulse=0.5,
            )
        )
        restored = Scene.from_dict(json.loads(json.dumps(scene.to_dict())))
        assert restored.layers[0].to_dict() == scene.layers[0].to_dict()


# ------------------------------------------------------------------- sparks


class TestSparkAnimation:
    def test_breathing_shrinks_the_arms_at_the_trough(self):
        scene = _scene(_spark(breathe=0.3))
        full = _lit(scene, 0.0, {})  # the wave peaks at t=0
        shrunk = _lit(scene, 2.0, {})  # and bottoms out at t=2 for 0.25 Hz
        assert shrunk < full

    def test_no_breathing_means_no_change_over_time(self):
        scene = _scene(_spark())
        assert _render(scene, 0.0, {}) == _render(scene, 2.0, {})

    def test_breathing_makes_the_mark_dynamic(self):
        assert _scene(_spark(breathe=0.1)).layers[0].is_dynamic is True
        assert _scene(_spark()).layers[0].is_dynamic is False

    def test_an_activity_linked_mark_is_dynamic(self):
        assert _scene(_spark(active_metric="test.active")).layers[0].is_dynamic is True

    def test_an_idle_activity_reading_keeps_the_mark_still(self):
        scene = _scene(_spark(active_metric="test.active", active_spin=180))
        frames = {_render(scene, t, {"test.active": 0.0}) for t in (0.0, 0.5, 1.0)}
        assert len(frames) == 1, "an idle mark moved"

    def test_activity_turns_the_mark(self):
        layer = _scene(_spark(active_metric="test.active", spin=0, active_spin=180)).layers[0]
        phases = []
        for step in range(11):
            ctx = RenderContext(*PANEL, time=step * 0.1, metrics={"test.active": 1.0})
            _, phase = layer._advance(ctx)
            phases.append(phase)
        # Fully active from the first frame: 180 deg/s over one second.
        assert phases[-1] == pytest.approx(180.0, abs=0.5)
        assert phases == sorted(phases)

    def test_switching_activity_on_does_not_jump_the_angle(self):
        layer = _scene(_spark(active_metric="test.active", spin=0, active_spin=180)).layers[0]
        layer._advance(RenderContext(*PANEL, time=0.0, metrics={"test.active": 0.0}))
        before = layer._advance(RenderContext(*PANEL, time=1.0, metrics={"test.active": 0.0}))[1]
        after = layer._advance(RenderContext(*PANEL, time=1.1, metrics={"test.active": 1.0}))[1]
        step = after - before
        assert 0.0 < step < 180.0 * 0.1 * 0.5, f"angle jumped {step} degrees"

    def test_a_missing_reading_idles_the_mark(self):
        scene = _scene(_spark(active_metric="test.active", active_spin=180))
        assert _render(scene, 0.0, {}) == _render(scene, 1.0, {})

    def test_active_glow_lifts_the_colour_toward_white(self):
        # Separate scenes: the glow eases on the layer's own state, so one scene
        # cannot be shown active and then idle at the same instant.
        def fresh():
            return _scene(_spark(color="#204060", active_metric="test.active", active_glow=1.0))

        def brightest(frame):
            return max(sum(p[:3]) for p in frame.get_flattened_data())

        active = brightest(_frame(fresh(), 0.0, {"test.active": 1.0}))
        idle = brightest(_frame(fresh(), 0.0, {"test.active": 0.0}))
        assert active > 600 and idle < 300

    def test_spark_motion_fields_round_trip(self):
        scene = _scene(
            _spark(
                breathe=0.2,
                active_metric="test.active",
                active_spin=120,
                active_glow=0.4,
            )
        )
        restored = Scene.from_dict(json.loads(json.dumps(scene.to_dict())))
        assert restored.layers[0].to_dict() == scene.layers[0].to_dict()


# ---------------------------------------------------------- the shipped scene


class TestBuiltinClaudeScene:
    def _scene(self) -> Scene:
        return Scene.from_dict(DEFAULT_SCENES["claude-usage"])

    def test_every_ring_sweeps_glides_flashes_and_pulses(self):
        rings = [layer for layer in self._scene().layers if layer.type_name == "donut"]
        assert len(rings) == 4
        for ring in rings:
            assert 0.0 < ring.sweep_in <= 1.0, "gallery thumbnails would show a half-filled ring"
            assert ring.glide > 0 and ring.gain_flash > 0 and ring.pulse > 0

    def test_both_sparks_follow_claude_code_activity(self):
        sparks = [layer for layer in self._scene().layers if layer.type_name == "spark"]
        assert len(sparks) == 2
        for spark in sparks:
            assert spark.active_metric == "claude.active"
            assert spark.breathe > 0

    def test_the_scene_renders_without_a_layer_failing(self, caplog):
        metrics = {
            "claude.limits.session.pct": 62.0,
            "claude.limits.week.pct": 91.0,
            "claude.today.tokens": 1234567,
            "claude.active": 1.0,
        }
        with caplog.at_level("ERROR", logger="fitzlcd.render.compositor"):
            _frame(self._scene(), 1.1, metrics)
        assert not [r for r in caplog.records if "will be skipped" in r.getMessage()]


# ----------------------------------------------------- claude.active metric


def _transcript(path: Path, stamp: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "type": "assistant",
        "timestamp": stamp,
        "sessionId": "sess-1",
        "message": {
            "model": "claude-sonnet-5",
            "usage": {"input_tokens": 10, "output_tokens": 5},
        },
    }
    path.write_text(json.dumps(record), encoding="utf-8")


def _stamp(seconds_ago: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(time.time() - seconds_ago))


def _now_iso() -> str:
    return _stamp(0)


class TestClaudeActivityMetric:
    def test_omitted_without_any_transcript(self, tmp_path: Path):
        snap = ClaudeUsageProvider(root=tmp_path / ".claude").read()
        assert "claude.active" not in snap

    def test_working_while_the_newest_transcript_is_fresh(self, tmp_path: Path):
        root = tmp_path / ".claude"
        _transcript(root / "projects" / "p" / "a.jsonl", _now_iso())
        assert ClaudeUsageProvider(root=root).read()["claude.active"] == 1.0

    def test_idle_once_the_transcript_goes_quiet(self, tmp_path: Path):
        root = tmp_path / ".claude"
        path = root / "projects" / "p" / "a.jsonl"
        _transcript(path, _now_iso())
        old = time.time() - ACTIVE_WINDOW_SECONDS - 60
        os.utime(path, (old, old))
        assert ClaudeUsageProvider(root=root).read()["claude.active"] == 0.0

    def test_survives_the_newest_transcript_being_deleted(self, tmp_path: Path):
        root = tmp_path / ".claude"
        older = root / "projects" / "p" / "old.jsonl"
        newer = root / "projects" / "p" / "new.jsonl"
        # The older transcript's events are two minutes back, so "newest" is
        # unambiguous and the fresh file is the one the metric follows.
        _transcript(older, _stamp(120))
        old = time.time() - ACTIVE_WINDOW_SECONDS - 60
        os.utime(older, (old, old))
        _transcript(newer, _now_iso())

        provider = ClaudeUsageProvider(root=root, refresh_seconds=0)
        assert provider.read()["claude.active"] == 1.0
        newer.unlink()
        assert provider.read()["claude.active"] == 0.0
