"""Failure paths: malformed files and payloads must degrade, never crash."""

from __future__ import annotations

import json

import pytest

import fitzlcd.render.layers  # noqa: F401 - registers layer types
from fitzlcd.config import AppConfig, SceneLibrary
from fitzlcd.render.scene import Layer, Scene, SceneError
from fitzlcd.sources.claude_limits import read_access_token
from fitzlcd.sources.claude_usage import _ingest_file
from fitzlcd.sources.media import MediaError, open_media


class TestAppConfigLoad:
    def test_non_object_json_gives_defaults(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text("[1, 2]", encoding="utf-8")
        assert AppConfig.load(path) == AppConfig()

    def test_bad_utf8_gives_defaults(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_bytes(b"\xff\xfe\x00bad")
        assert AppConfig.load(path) == AppConfig()

    def test_wrong_types_fall_back_per_field(self, tmp_path):
        path = tmp_path / "config.json"
        path.write_text(
            json.dumps({"quality": "high", "rotation": 90, "max_fps": 30, "autostart": 1}),
            encoding="utf-8",
        )
        config = AppConfig.load(path)
        assert config.quality == AppConfig().quality
        assert config.autostart is False
        assert config.rotation == 90
        assert config.max_fps == 30

    def test_save_to_unwritable_location_does_not_raise(self, tmp_path):
        blocker = tmp_path / "file"
        blocker.write_text("x", encoding="utf-8")
        AppConfig().save(blocker / "config.json")  # parent is a file


class TestSceneParsing:
    @pytest.mark.parametrize(
        "doc",
        [
            {"fps": "fast"},
            {"version": "1"},
            {"layers": ["not-a-dict"]},
        ],
    )
    def test_malformed_documents_raise_scene_error(self, doc):
        with pytest.raises(SceneError):
            Scene.from_dict(doc)

    def test_non_dict_layer_raises_scene_error(self):
        with pytest.raises(SceneError):
            Layer.from_dict([])  # type: ignore[arg-type]

    def test_undecodable_file_raises_scene_error(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_bytes(b"\xff\xfe\x00")
        with pytest.raises(SceneError):
            Scene.load(path)

    def test_library_skips_unreadable_scene(self, tmp_path):
        (tmp_path / "bad.json").write_bytes(b"\xff\xfe\x00")
        assert SceneLibrary(tmp_path).list() == []


class TestMedia:
    def test_corrupt_image_is_a_media_error(self, tmp_path):
        path = tmp_path / "broken.png"
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"garbage")
        with pytest.raises(MediaError):
            open_media(path)


class TestClaudeSources:
    def test_credentials_with_non_object_root(self, tmp_path):
        (tmp_path / ".credentials.json").write_text("[]", encoding="utf-8")
        assert read_access_token(tmp_path) == (None, None)

    def test_corrupt_transcript_records_are_skipped(self, tmp_path):
        good = {
            "timestamp": "2026-01-01T00:00:00Z",
            "message": {"model": "x", "usage": {"input_tokens": 5}},
        }
        bad_tokens = {
            "timestamp": "2026-01-01T00:00:01Z",
            "message": {"usage": {"input_tokens": "lots"}},
        }
        lines = ["[]", "null", json.dumps(bad_tokens), json.dumps(good)]
        path = tmp_path / "t.jsonl"
        path.write_text("\n".join(lines), encoding="utf-8")
        days, _ = _ingest_file(path)
        assert sum(d.messages for d in days.values()) == 1
