"""The zone map's real in-game art, stitched into one cached PNG per map.

The addon cannot hand over pixels, but it can name them: ``C_Map.GetMapArtLayers``
and ``GetMapArtLayerTextures`` give the layer's size, its tile size and the
fileID of every tile, row-major, which the addon sends as the map manifest. The
tiles themselves are BLP files inside the local game install, read through a
:class:`TileSource` (see :mod:`fitzlcd.sources.wow.casc`) and decoded by Pillow.

Stitching runs on its own worker thread, once per map, and the result is cached
under ``%APPDATA%/FitzLCD/wow/maps/<mapID>.png``. Nothing is redistributed: the
art only ever comes from the user's own install. A map that cannot be built is
simply left without art, and the map layer draws its grid instead.
"""

from __future__ import annotations

import io
import logging
import math
import queue
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from PIL import Image

from fitzlcd.sources.wow import WowError

log = logging.getLogger(__name__)

#: Bigger than any real zone (Kalimdor is a few dozen tiles); a manifest asking
#: for more is not one the game produced.
MAX_TILES = 400
MAX_SIDE = 8192
#: Texture coordinates are kept in ten-thousandths.
ICON_SCALE = 10000


class TileSource(Protocol):
    def read_file(self, file_id: int) -> bytes: ...


def _int(value: object, default: int = 0) -> int:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return default


@dataclass(frozen=True)
class MapManifest:
    map_id: int
    width: int
    height: int
    tile_width: int
    tile_height: int
    tiles: tuple[int, ...]

    @property
    def columns(self) -> int:
        return math.ceil(self.width / self.tile_width)

    @property
    def rows(self) -> int:
        return math.ceil(self.height / self.tile_height)

    @property
    def key(self) -> str:
        return str(self.map_id)

    def render(self, source: TileSource) -> Image.Image:
        return stitch(self, source)

    @classmethod
    def from_record(cls, record: dict[str, str]) -> MapManifest | None:
        """The addon's map record -> a manifest, or ``None`` if it has no usable art."""
        tiles = tuple(_int(t) for t in record.get("t", "").split(",") if t.strip())
        manifest = cls(
            _int(record.get("id")),
            _int(record.get("w")),
            _int(record.get("h")),
            _int(record.get("tw")),
            _int(record.get("th")),
            tiles,
        )
        if (
            manifest.map_id <= 0
            or not 0 < manifest.width <= MAX_SIDE
            or not 0 < manifest.height <= MAX_SIDE
            or manifest.tile_width <= 0
            or manifest.tile_height <= 0
            or not 0 < len(tiles) <= MAX_TILES
            or len(tiles) < manifest.columns * manifest.rows
        ):
            return None
        return manifest


def _decode(source: TileSource, file_id: int) -> Image.Image:
    try:
        with Image.open(io.BytesIO(source.read_file(file_id))) as raw:
            raw.load()
            return raw.convert("RGBA")
    except WowError:
        raise
    except Exception as exc:  # noqa: BLE001 - Pillow raises odd types on bad BLPs
        raise WowError(f"texture {file_id} could not be decoded: {exc}") from exc


@dataclass(frozen=True)
class ClassIcon:
    """The class portrait icon: one texture file and the part of it to cut out.

    Texture coordinates are sent as 0..1 fractions; they are kept here in
    ten-thousandths so the cache key is built from integers only.
    """

    file_id: int
    left: int
    right: int
    top: int
    bottom: int

    @classmethod
    def from_record(cls, record: dict[str, str]) -> ClassIcon | None:
        """The ``ci``/``cc`` keys of the addon's info record, or ``None``."""
        file_id = _int(record.get("ci"))
        parts = record.get("cc", "").split(",")
        if file_id <= 0 or len(parts) != 4:
            return None
        try:
            coords = [round(float(v) * ICON_SCALE) for v in parts]
        except (TypeError, ValueError, OverflowError):
            return None
        left, right, top, bottom = coords
        if not (0 <= left < right <= ICON_SCALE and 0 <= top < bottom <= ICON_SCALE):
            return None
        return cls(file_id, left, right, top, bottom)

    @property
    def key(self) -> str:
        return f"icon_{self.file_id}_{self.left}_{self.right}_{self.top}_{self.bottom}"

    def render(self, source: TileSource) -> Image.Image:
        texture = _decode(source, self.file_id)
        w, h = texture.size
        box = (
            round(self.left / ICON_SCALE * w),
            round(self.top / ICON_SCALE * h),
            round(self.right / ICON_SCALE * w),
            round(self.bottom / ICON_SCALE * h),
        )
        if box[2] - box[0] < 4 or box[3] - box[1] < 4:
            raise WowError(f"icon {self.key} is too small to use")
        return texture.crop(box)


def stitch(manifest: MapManifest, source: TileSource) -> Image.Image:
    """Every tile pasted into place and cropped to the layer. Raises on bad art."""
    cols, rows = manifest.columns, manifest.rows
    tw, th = manifest.tile_width, manifest.tile_height
    canvas = Image.new("RGB", (cols * tw, rows * th))
    for i, file_id in enumerate(manifest.tiles[: cols * rows]):
        if file_id <= 0:  # a tile the map simply does not have
            continue
        tile = _decode(source, file_id)
        if tile.size != (tw, th):
            tile = tile.resize((tw, th), Image.Resampling.LANCZOS)
        canvas.paste(tile, ((i % cols) * tw, (i // cols) * th), tile)
    return canvas.crop((0, 0, manifest.width, manifest.height))


class ArtJob(Protocol):
    """Something the cache can build: a stable file-safe key and how to draw it."""

    @property
    def key(self) -> str: ...

    def render(self, source: TileSource) -> Image.Image: ...


class MapArtCache:
    """Builds missing art (maps, class icons) on a worker thread, once each.

    Each job is cached as ``<directory>/<job.key>.png``; ``path_for`` accepts
    the key or, for maps, the bare map ID.
    """

    def __init__(self, directory: Path, source_factory: Callable[[], TileSource]) -> None:
        self._directory = directory
        self._source_factory = source_factory
        self._source: TileSource | None = None
        self._source_failed = False
        self._attempted: set[str] = set()
        self._ready: set[str] = set()
        self._lock = threading.Lock()
        self._queue: queue.Queue[ArtJob | None] = queue.Queue()
        self._thread: threading.Thread | None = None

    def path(self, key: int | str) -> Path:
        return self._directory / f"{key}.png"

    def path_for(self, key: int | str) -> Path | None:
        """The cached art for ``key``, if it has been built."""
        key = str(key)
        with self._lock:
            if key in self._ready:
                return self.path(key)
        target = self.path(key)
        try:
            exists = target.is_file()
        except OSError:
            exists = False
        if exists:
            with self._lock:
                self._ready.add(key)
            return target
        return None

    def request(self, job: ArtJob) -> None:
        """Queue ``job`` to be built unless it exists or already failed."""
        if self.path_for(job.key) is not None:
            return
        with self._lock:
            if job.key in self._attempted or self._source_failed:
                return
            self._attempted.add(job.key)
            if self._thread is None:
                self._thread = threading.Thread(
                    target=self._run, name="fitzlcd-wow-mapart", daemon=True
                )
                self._thread.start()
        self._queue.put(job)

    def _run(self) -> None:
        while True:
            job = self._queue.get()
            try:
                if job is None:
                    return
                self._build(job)
            except Exception as exc:  # noqa: BLE001 - the worker must outlive any one job
                log.warning("no art for %s: %s", job.key if job else "?", exc)
            finally:
                self._queue.task_done()

    def _build(self, job: ArtJob) -> None:
        if self._source is None:
            try:
                self._source = self._source_factory()
            except Exception:
                with self._lock:
                    self._source_failed = True  # no install found: stop trying for this run
                raise
        image = job.render(self._source)
        target = self.path(job.key)
        target.parent.mkdir(parents=True, exist_ok=True)
        partial = target.with_suffix(".part")
        image.save(partial, format="PNG")
        partial.replace(target)
        with self._lock:
            self._ready.add(job.key)
        log.info("built art %s (%dx%d)", job.key, *image.size)

    def wait_idle(self, timeout: float = 5.0) -> None:
        """Tests: block until every queued map has been processed."""
        done = threading.Event()
        threading.Thread(target=lambda: (self._queue.join(), done.set()), daemon=True).start()
        done.wait(timeout)

    def close(self) -> None:
        if self._thread is not None:
            self._queue.put(None)
            self._thread.join(timeout=2.0)
