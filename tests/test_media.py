"""Media source tests, using files generated at test time."""

from __future__ import annotations

import threading
import time

import pytest
from PIL import Image, ImageDraw

from fitzlcd.render.layers.media import MediaLayer
from fitzlcd.sources.media import (
    GifSource,
    MediaError,
    StillSource,
    VideoSource,
    open_media,
)


@pytest.fixture
def still(tmp_path):
    path = tmp_path / "still.png"
    Image.new("RGB", (320, 200), "navy").save(path)
    return path


@pytest.fixture
def animated(tmp_path):
    path = tmp_path / "spin.gif"
    frames = []
    for i in range(6):
        image = Image.new("RGB", (160, 90), (10, 10, 30))
        ImageDraw.Draw(image).rectangle([i * 20, 20, i * 20 + 18, 70], fill="yellow")
        frames.append(image)
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=100, loop=0)
    return path


@pytest.fixture
def video(tmp_path):
    av = pytest.importorskip("av")
    path = tmp_path / "clip.mp4"
    container = av.open(str(path), mode="w")
    stream = container.add_stream("mpeg4", rate=24)
    stream.width, stream.height = 160, 96
    stream.pix_fmt = "yuv420p"
    for i in range(48):
        image = Image.new("RGB", (160, 96), (i * 5 % 256, 40, 90))
        frame = av.VideoFrame.from_image(image)
        container.mux(stream.encode(frame))
    container.mux(stream.encode())
    container.close()
    return path


class TestOpenMedia:
    def test_picks_the_still_source(self, still):
        assert isinstance(open_media(still), StillSource)

    def test_picks_the_gif_source(self, animated):
        assert isinstance(open_media(animated), GifSource)

    def test_picks_the_video_source(self, video):
        source = open_media(video)
        try:
            assert isinstance(source, VideoSource)
        finally:
            source.close()

    def test_missing_file_is_reported(self, tmp_path):
        with pytest.raises(MediaError, match="no such file"):
            open_media(tmp_path / "absent.png")

    def test_unsupported_content_is_reported(self, tmp_path):
        path = tmp_path / "notes.txt"
        path.write_text("this is not an image", encoding="utf-8")
        with pytest.raises(MediaError):
            open_media(path)

    def test_a_mislabelled_image_still_opens(self, tmp_path):
        """Plenty of images arrive with the wrong extension."""
        path = tmp_path / "photo.dat"
        Image.new("RGB", (64, 64), "green").save(path, format="PNG")
        assert isinstance(open_media(path), StillSource)


class TestStill:
    def test_is_not_animated_and_is_time_invariant(self, still):
        source = open_media(still)
        assert not source.is_animated
        assert source.duration == 0.0
        assert source.frame_at(0) is source.frame_at(99)
        assert source.size == (320, 200)


class TestGif:
    def test_reports_animation_and_duration(self, animated):
        source = open_media(animated)
        assert source.is_animated
        assert source.duration == pytest.approx(0.6, abs=0.05)
        assert source.size == (160, 90)

    def test_advances_and_loops(self, animated):
        source = open_media(animated)
        first = source.frame_at(0.0)
        middle = source.frame_at(0.35)
        assert list(first.get_flattened_data()) != list(middle.get_flattened_data())
        # One full period later, the same frame comes back around.
        assert list(source.frame_at(0.6).get_flattened_data()) == list(first.get_flattened_data())

    def test_single_frame_gif_is_not_animated(self, tmp_path):
        path = tmp_path / "one.gif"
        Image.new("RGB", (32, 32), "red").save(path)
        assert not open_media(path).is_animated


class TestVideo:
    def test_decodes_frames_in_the_background(self, video):
        source = open_media(video)
        try:
            assert source.size == (160, 96)
            deadline = time.monotonic() + 5
            while time.monotonic() < deadline:
                frame = source.frame_at(0.0)
                if any(p[3] for p in frame.get_flattened_data()):
                    break
                time.sleep(0.05)
            else:
                pytest.fail("no frame was decoded")
        finally:
            source.close()

    def test_close_joins_the_decoder_before_freeing_the_container(self, video):
        """Regression: closing mid-decode freed buffers in use and segfaulted.

        The container may only be closed once the decode thread has stopped, so
        that thread must be gone by the time close() returns.
        """
        source = open_media(video)
        time.sleep(0.3)  # let the decode thread get well inside the container
        assert source._thread is not None and source._thread.is_alive()

        source.close()

        assert not source._thread.is_alive()
        assert f"video-{video.name}" not in {t.name for t in threading.enumerate()}

    def test_close_is_idempotent(self, video):
        source = open_media(video)
        source.close()
        source.close()


class TestMediaLayer:
    def test_an_unreadable_source_disables_the_layer_once(self, tmp_path, caplog):
        layer = MediaLayer(source=str(tmp_path / "missing.png"))
        canvas = Image.new("RGBA", (100, 100))
        from fitzlcd.render.context import RenderContext

        ctx = RenderContext(100, 100)
        layer.draw(canvas, ctx)
        layer.draw(canvas, ctx)
        assert layer._failed  # remembered, so it is not retried every frame

    def test_changing_the_source_reopens(self, still, animated):
        layer = MediaLayer(source=str(still))
        assert not layer.is_dynamic  # a still
        layer.source = str(animated)
        assert layer.is_dynamic  # now a GIF
        layer.close()

    def test_empty_source_draws_nothing(self):
        layer = MediaLayer(source="")
        from fitzlcd.render.context import RenderContext

        canvas = Image.new("RGBA", (50, 50))
        layer.draw(canvas, RenderContext(50, 50))
        assert not any(p[3] for p in canvas.get_flattened_data())

    def test_runtime_state_is_not_serialised(self, still):
        layer = MediaLayer(source=str(still))
        layer._ensure_media()
        assert set(layer.to_dict()) == {"type", *(f.name for f in MediaLayer.FIELDS)}
