"""Scene thumbnails for the gallery.

Rendered with the real compositor, so a thumbnail is exactly what the panel
would show, but on a worker thread and at most one at a time: composing a full
frame costs tens of milliseconds, and the render engine must keep its pace.
Results are cached by the scene's content, so an unchanged scene is never
re-rendered.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import logging
from collections.abc import Callable, Mapping
from typing import Any

from PIL import Image
from PySide6.QtCore import QObject, QRunnable, QThreadPool, Signal
from PySide6.QtGui import QImage

from fitzlcd.render.compositor import Compositor
from fitzlcd.render.context import RenderContext
from fitzlcd.render.scene import Scene, SceneError

log = logging.getLogger(__name__)

#: Frames composed before the one kept; sparklines need history to draw.
WARM_UP_FRAMES = 12


def content_key(scene: Scene, size: tuple[int, int]) -> str:
    data = json.dumps(scene.to_dict(), sort_keys=True, default=str)
    return hashlib.sha1(f"{size}:{data}".encode(), usedforsecurity=False).hexdigest()


def render_thumbnail(
    data: dict[str, Any],
    size: tuple[int, int],
    longest_edge: int,
    metrics: Mapping[str, Any],
    clock_24_hour: bool = True,
) -> Image.Image:
    """Compose one scene at full panel size and shrink it."""
    scene = Scene.from_dict(data)
    compositor = Compositor(*size)
    frame = None
    for i in range(WARM_UP_FRAMES):
        ctx = RenderContext(
            size[0],
            size[1],
            time=i / 10,
            frame_index=i,
            metrics=dict(metrics),
            clock_24_hour=clock_24_hour,
        )
        frame = compositor.compose(scene, ctx)
    frame = frame.convert("RGB")
    frame.thumbnail((longest_edge, longest_edge), Image.Resampling.LANCZOS)
    return frame


def pil_to_qimage(image: Image.Image) -> QImage:
    data = image.tobytes("raw", "RGB")
    qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()


class _Job(QRunnable):
    def __init__(self, owner: ThumbnailRenderer, name: str, key: str, data, size, edge, metrics):
        super().__init__()
        self.owner = owner
        self.args = (name, key, data, size, edge, metrics)

    def run(self) -> None:
        name, key, data, size, edge, metrics = self.args
        try:
            image = render_thumbnail(data, size, edge, metrics, self.owner.clock_24_hour)
            qimage = pil_to_qimage(image)
        except (SceneError, OSError, ValueError) as exc:
            log.warning("thumbnail for %r failed: %s", name, exc)
            return
        except Exception:  # noqa: BLE001 - a broken layer must not kill the gallery
            log.exception("thumbnail for %r failed", name)
            return
        # RuntimeError: the window closed while this was rendering.
        with contextlib.suppress(RuntimeError):
            self.owner.ready.emit(name, key, qimage)


class ThumbnailRenderer(QObject):
    """Queues thumbnail renders and emits ``ready(name, key, QImage)``."""

    ready = Signal(str, str, QImage)

    def __init__(self, metrics_source: Callable[[], Mapping[str, Any]], parent=None) -> None:
        super().__init__(parent)
        self.metrics_source = metrics_source
        self.clock_24_hour = True
        self.cache: dict[str, QImage] = {}
        self._pending: set[str] = set()
        self._pool = QThreadPool(self)
        self._pool.setMaxThreadCount(1)
        self.ready.connect(self._store)

    def request(self, scene: Scene, size: tuple[int, int], longest_edge: int) -> QImage | None:
        """The cached thumbnail if there is one; otherwise queue a render."""
        key = content_key(scene, size)
        if key in self.cache:
            return self.cache[key]
        if key not in self._pending:
            self._pending.add(key)
            try:
                metrics = dict(self.metrics_source() or {})
            except Exception:  # noqa: BLE001
                metrics = {}
            self._pool.start(
                _Job(self, scene.name, key, scene.to_dict(), size, longest_edge, metrics)
            )
        return None

    def _store(self, _name: str, key: str, image: QImage) -> None:
        self._pending.discard(key)
        self.cache[key] = image

    def wait(self, msecs: int = 30_000) -> bool:
        """Block until queued renders finish (for screenshots and tests)."""
        return self._pool.waitForDone(msecs)
