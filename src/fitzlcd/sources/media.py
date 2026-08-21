"""Media decoding: stills, animated GIFs, and video.

Every source answers the same question - "what does this look like at time t?" -
so the render loop never needs to know which kind it has. Sources keep their own
clock, so a 24 fps video played on a 30 fps panel still runs at the right speed
instead of being tied to the frame rate.
"""

from __future__ import annotations

import logging
import threading
from abc import ABC, abstractmethod
from pathlib import Path

from PIL import Image, ImageSequence

log = logging.getLogger(__name__)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".bmp", ".webp", ".tif", ".tiff"}
ANIMATED_SUFFIXES = {".gif", ".apng"}
VIDEO_SUFFIXES = {".mp4", ".mkv", ".mov", ".avi", ".webm", ".m4v", ".wmv", ".flv"}

SUPPORTED_SUFFIXES = IMAGE_SUFFIXES | ANIMATED_SUFFIXES | VIDEO_SUFFIXES


class MediaError(Exception):
    """Raised when a media file cannot be opened or decoded."""


class MediaSource(ABC):
    """A time-addressable source of frames."""

    def __init__(self, path: Path) -> None:
        self.path = Path(path)

    @property
    @abstractmethod
    def size(self) -> tuple[int, int]: ...

    @property
    def is_animated(self) -> bool:
        return False

    @property
    def duration(self) -> float:
        """Length in seconds; 0 for stills."""
        return 0.0

    @abstractmethod
    def frame_at(self, t: float) -> Image.Image:
        """Return the RGBA frame for playback time ``t`` (seconds, loops)."""

    def close(self) -> None:  # noqa: B027 - stills have nothing to release
        """Release decoder resources. Safe to call more than once."""

    def __enter__(self) -> MediaSource:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


class StillSource(MediaSource):
    """A single image, decoded once."""

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        try:
            with Image.open(self.path) as img:
                self._image = img.convert("RGBA")
        except OSError as exc:
            raise MediaError(f"cannot open image {self.path.name}: {exc}") from exc

    @property
    def size(self) -> tuple[int, int]:
        return self._image.size

    def frame_at(self, t: float) -> Image.Image:
        return self._image


class GifSource(MediaSource):
    """An animated GIF/APNG, fully decoded into memory.

    Panels are small and GIFs are short, so decoding up front buys smooth,
    seek-free playback for a few MB of RAM.
    """

    #: Browsers clamp absurdly fast GIFs to this; matching them keeps timing sane.
    MIN_FRAME_MS = 20
    DEFAULT_FRAME_MS = 100

    def __init__(self, path: Path) -> None:
        super().__init__(path)
        self._frames: list[Image.Image] = []
        self._starts: list[float] = []
        try:
            with Image.open(self.path) as img:
                elapsed = 0.0
                for frame in ImageSequence.Iterator(img):
                    self._frames.append(frame.convert("RGBA"))
                    self._starts.append(elapsed)
                    ms = frame.info.get("duration") or self.DEFAULT_FRAME_MS
                    elapsed += max(self.MIN_FRAME_MS, int(ms)) / 1000.0
                self._duration = elapsed
        except OSError as exc:
            raise MediaError(f"cannot open animation {self.path.name}: {exc}") from exc
        if not self._frames:
            raise MediaError(f"{self.path.name} contains no frames")

    @property
    def size(self) -> tuple[int, int]:
        return self._frames[0].size

    @property
    def is_animated(self) -> bool:
        return len(self._frames) > 1

    @property
    def duration(self) -> float:
        return self._duration

    def frame_at(self, t: float) -> Image.Image:
        if self._duration <= 0 or len(self._frames) == 1:
            return self._frames[0]
        position = t % self._duration
        # Frame counts are small; a linear scan beats the complexity of bisect
        # bookkeeping and is never the bottleneck.
        index = 0
        for i, start in enumerate(self._starts):
            if start > position:
                break
            index = i
        return self._frames[index]


class VideoSource(MediaSource):
    """Video decoded lazily on a background thread.

    Decoding runs ahead of the render loop and hands over the most recent frame;
    the render loop never blocks on a slow decode, it just re-shows the last one.
    """

    def __init__(self, path: Path, loop: bool = True) -> None:
        super().__init__(path)
        try:
            import av
        except ImportError as exc:  # pragma: no cover - dependency is declared
            raise MediaError("video playback needs PyAV (pip install av)") from exc

        self._av = av
        self._loop = loop
        self._lock = threading.Lock()
        self._current: Image.Image | None = None
        self._current_pts = -1.0
        self._stop = threading.Event()
        self._seek_to: float | None = 0.0

        try:
            container = av.open(str(self.path))
            stream = container.streams.video[0]
        except Exception as exc:  # noqa: BLE001 - PyAV raises a wide range
            raise MediaError(f"cannot open video {self.path.name}: {exc}") from exc

        self._size = (stream.codec_context.width, stream.codec_context.height)
        self._duration = float(stream.duration * stream.time_base) if stream.duration else 0.0
        stream.thread_type = "AUTO"
        self._container = container
        self._stream = stream

        self._thread = threading.Thread(
            target=self._decode_loop, name=f"video-{self.path.name}", daemon=True
        )
        self._thread.start()

    @property
    def size(self) -> tuple[int, int]:
        return self._size

    @property
    def is_animated(self) -> bool:
        return True

    @property
    def duration(self) -> float:
        return self._duration

    def frame_at(self, t: float) -> Image.Image:
        with self._lock:
            if self._current is not None:
                return self._current
        # Nothing decoded yet: a transparent frame keeps the first render honest
        # rather than flashing whatever was on screen before.
        return Image.new("RGBA", self._size, (0, 0, 0, 0))

    def _decode_loop(self) -> None:
        while not self._stop.is_set():
            try:
                for frame in self._container.decode(self._stream):
                    if self._stop.is_set():
                        return
                    image = frame.to_image().convert("RGBA")
                    with self._lock:
                        self._current = image
                        self._current_pts = float(frame.pts * self._stream.time_base or 0)
                    # Pace decoding to the video's own frame rate so playback
                    # speed is independent of how fast the panel is running.
                    rate = float(self._stream.average_rate or 30)
                    self._stop.wait(1.0 / max(1.0, rate))
                if not self._loop:
                    return
                self._container.seek(0)
            except Exception as exc:  # noqa: BLE001 - a bad file must not kill the app
                log.warning("video decode stopped for %s: %s", self.path.name, exc)
                return

    def close(self) -> None:
        self._stop.set()
        try:
            self._container.close()
        except Exception as exc:  # noqa: BLE001 - closing must never raise
            log.debug("error closing %s: %s", self.path.name, exc)


def open_media(path: Path | str) -> MediaSource:
    """Open ``path`` with the right source for its type."""
    path = Path(path)
    if not path.exists():
        raise MediaError(f"no such file: {path}")
    suffix = path.suffix.lower()
    if suffix in VIDEO_SUFFIXES:
        return VideoSource(path)
    if suffix in ANIMATED_SUFFIXES:
        return GifSource(path)
    if suffix in IMAGE_SUFFIXES:
        return StillSource(path)
    # Unknown extension: let Pillow decide before giving up, since plenty of
    # images arrive with the wrong suffix.
    try:
        return StillSource(path)
    except MediaError as exc:
        raise MediaError(f"unsupported media type: {path.name}") from exc
