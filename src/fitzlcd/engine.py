"""The render loop: scene in, frames out, on its own thread.

Responsibilities, in the order they matter:

1. Never wedge the panel. Frames are paced to ``min(scene.fps, caps.max_fps)``
   and dropped rather than queued - flooding this hardware locks it up.
2. Never block the UI. Everything here runs off the Qt thread and communicates
   through immutable snapshots and callbacks.
3. Never die. A missing device, an unreadable file or a broken layer degrades
   to a reconnect or a skipped layer, not a crash.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from PIL import Image

from fitzlcd.panels import registry
from fitzlcd.panels.base import Panel, PanelBusyError, PanelError, PanelHandle
from fitzlcd.render.compositor import Compositor
from fitzlcd.render.context import RenderContext
from fitzlcd.render.encode import encode_jpeg
from fitzlcd.render.scene import Scene

log = logging.getLogger(__name__)

RECONNECT_BACKOFF = (1.0, 2.0, 5.0, 10.0, 30.0)


@dataclass(frozen=True)
class EngineStats:
    """Immutable snapshot of the loop's state, safe to read from any thread."""

    connected: bool = False
    panel_label: str = ""
    model: str = ""
    firmware: str = ""
    address: str = ""
    width: int = 0
    height: int = 0
    rotation: int = 0
    fps: float = 0.0
    target_fps: int = 0
    bytes_per_second: float = 0.0
    frames_sent: int = 0
    frames_dropped: int = 0
    frames_skipped: int = 0
    last_error: str = ""
    scene_name: str = ""

    @property
    def status(self) -> str:
        if self.connected:
            return "connected"
        return "error" if self.last_error else "searching"


@dataclass
class EngineConfig:
    panel: str | None = "auto"
    #: How the panel is physically mounted, in degrees counter-clockwise.
    #: Scenes are composed in what the viewer sees, so this changes the frame
    #: geometry as well as the encode transform.
    rotation: int = 0
    quality: int = 90
    max_fps: int | None = None  # overrides the panel's own cap when lower
    reconnect: bool = True
    preview_every: int = 1  # emit 1 in N frames to the preview callback
    #: Passed to every RenderContext; clock layers with no explicit format
    #: follow it.
    clock_24_hour: bool = True


@dataclass
class _Counters:
    frames: int = 0
    bytes_: int = 0
    window_start: float = field(default_factory=time.perf_counter)


class RenderEngine:
    """Drives one panel from one scene."""

    def __init__(
        self,
        config: EngineConfig | None = None,
        metrics_provider: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        self.config = config or EngineConfig()
        self._metrics_provider = metrics_provider

        self._scene: Scene | None = None
        self._panel: Panel | None = None
        self._compositor: Compositor | None = None

        self._thread: threading.Thread | None = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._lock = threading.Lock()
        self._paused = False

        self._stats = EngineStats()
        self._counters = _Counters()
        self._scene_started = time.perf_counter()
        self._frame_index = 0
        self._last_payload: bytes | None = None

        #: Called with each composed RGB frame; used by the GUI preview.
        self.on_frame: Callable[[Image.Image], None] | None = None
        #: Called whenever the stats snapshot changes materially.
        self.on_stats: Callable[[EngineStats], None] | None = None

    # ------------------------------------------------------------------ public

    @property
    def stats(self) -> EngineStats:
        return self._stats

    @property
    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def set_scene(self, scene: Scene | None) -> None:
        with self._lock:
            self._scene = scene
            self._scene_started = time.perf_counter()
            self._frame_index = 0
            self._last_payload = None
        self._update_stats(scene_name=scene.name if scene else "")
        self._wake.set()

    def set_rotation(self, degrees: int) -> None:
        """Change the mounting orientation and recompose at the new geometry."""
        degrees %= 360
        if degrees % 90:
            raise ValueError(f"rotation must be a multiple of 90, got {degrees}")
        if degrees == self.config.rotation:
            return
        with self._lock:
            self.config.rotation = degrees
            # The frame size changes, so the cached compositor and the
            # last-sent frame are both stale.
            self._compositor = None
            self._last_payload = None
        self._publish_caps()
        self._wake.set()

    def set_clock_24_hour(self, clock_24_hour: bool) -> None:
        """Switch clock layers between 24- and 12-hour without a restart."""
        if clock_24_hour == self.config.clock_24_hour:
            return
        with self._lock:
            self.config.clock_24_hour = clock_24_hour
            # A scene whose only moving part is the clock is otherwise identical
            # frame to frame, and the dirty-frame check would swallow the change.
            self._last_payload = None
        self._wake.set()

    def set_paused(self, paused: bool) -> None:
        self._paused = paused
        self._wake.set()

    @property
    def is_paused(self) -> bool:
        return self._paused

    def start(self) -> None:
        if self.is_running:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fitzlcd-engine", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 3.0) -> None:
        self._stop.set()
        self._wake.set()
        thread, self._thread = self._thread, None
        if thread is not None:
            thread.join(timeout)
        self._disconnect()

    # -------------------------------------------------------------- loop body

    def _run(self) -> None:
        attempt = 0
        while not self._stop.is_set():
            if self._panel is None:
                try:
                    connected = self._connect()
                except Exception as exc:  # noqa: BLE001 - a bad driver must not kill the loop
                    log.exception("connect attempt failed: %s", exc)
                    self._update_stats(connected=False, last_error=str(exc))
                    connected = False
                if not connected:
                    delay = RECONNECT_BACKOFF[min(attempt, len(RECONNECT_BACKOFF) - 1)]
                    attempt += 1
                    if not self.config.reconnect:
                        return
                    self._stop.wait(delay)
                    continue
                attempt = 0

            frame_start = time.perf_counter()
            try:
                interval = self._render_once()
            except PanelError as exc:
                log.warning("panel dropped out: %s", exc)
                self._disconnect(str(exc))
                continue
            except Exception as exc:  # noqa: BLE001 - the loop must survive anything
                log.exception("render loop error: %s", exc)
                self._update_stats(last_error=str(exc))
                interval = 0.5

            elapsed = time.perf_counter() - frame_start
            self._wake.wait(max(0.0, interval - elapsed))
            self._wake.clear()

        self._disconnect()

    def _render_once(self) -> float:
        scene = self._scene
        panel = self._panel
        if scene is None or panel is None or self._paused:
            return 0.25  # idle tick: stay responsive without burning CPU

        caps = panel.caps.rotated(self.config.rotation)
        target_fps = self._target_fps(scene, caps.max_fps)
        interval = 1.0 / max(1, target_fps)

        if self._compositor is None or self._compositor.size != caps.size:
            self._compositor = Compositor(caps.width, caps.height)

        ctx = RenderContext(
            width=caps.width,
            height=caps.height,
            time=time.perf_counter() - self._scene_started,
            frame_index=self._frame_index,
            metrics=dict(self._metrics_provider() if self._metrics_provider else {}),
            clock_24_hour=self.config.clock_24_hour,
        )
        image = self._compositor.compose(scene, ctx)
        self._frame_index += 1

        if self.on_frame and self._frame_index % max(1, self.config.preview_every) == 0:
            try:
                self.on_frame(image)
            except Exception as exc:  # noqa: BLE001 - a bad preview must not stop output
                log.debug("preview callback failed: %s", exc)

        payload = encode_jpeg(image, caps.transform, self.config.quality)

        # A still scene that produced an identical frame costs nothing further:
        # this is what makes a static wallpaper effectively free.
        if not scene.is_dynamic and payload == self._last_payload:
            self._bump(skipped=True)
            return interval

        panel.push_frame(payload)
        self._last_payload = payload
        self._bump(sent=True, size=len(payload), target_fps=target_fps)
        return interval

    def _target_fps(self, scene: Scene, panel_cap: int) -> int:
        limits = [scene.fps, panel_cap]
        if self.config.max_fps:
            limits.append(self.config.max_fps)
        return max(1, min(limits))

    # ------------------------------------------------------------ connection

    def _connect(self) -> bool:
        handle: PanelHandle | None = registry.resolve(self.config.panel)
        if handle is None:
            self._update_stats(connected=False, last_error="no panel detected")
            return False
        try:
            panel = handle.open()
        except PanelBusyError as exc:
            self._update_stats(connected=False, last_error=str(exc))
            return False
        except PanelError as exc:
            self._update_stats(connected=False, last_error=str(exc))
            return False
        except OSError as exc:  # a driver that leaks a raw serial/USB error
            self._update_stats(connected=False, last_error=str(exc))
            return False

        self._panel = panel
        self._last_payload = None
        self._compositor = None
        caps = panel.caps
        self._update_stats(
            connected=True,
            panel_label=handle.label,
            address=handle.address,
            model=caps.model,
            firmware=caps.firmware,
            last_error="",
        )
        self._publish_caps()
        log.info("engine connected to %s", handle)
        return True

    def _publish_caps(self) -> None:
        """Publish the geometry scenes are composed in, not the panel's native one."""
        panel = self._panel
        if panel is None or not panel.is_open:
            self._update_stats(rotation=self.config.rotation)
            return
        caps = panel.caps.rotated(self.config.rotation)
        self._update_stats(width=caps.width, height=caps.height, rotation=self.config.rotation)

    def _disconnect(self, error: str = "") -> None:
        panel, self._panel = self._panel, None
        if panel is not None:
            try:
                panel.close()
            except Exception as exc:  # noqa: BLE001 - a dead port must not break teardown
                log.warning("error closing panel: %s", exc)
        self._last_payload = None
        self._update_stats(connected=False, fps=0.0, bytes_per_second=0.0, last_error=error)

    # ------------------------------------------------------------------ stats

    def _bump(
        self,
        *,
        sent: bool = False,
        skipped: bool = False,
        size: int = 0,
        target_fps: int = 0,
    ) -> None:
        counters = self._counters
        if sent:
            counters.frames += 1
            counters.bytes_ += size

        updates: dict[str, Any] = {}
        if sent:
            updates["frames_sent"] = self._stats.frames_sent + 1
        if skipped:
            updates["frames_skipped"] = self._stats.frames_skipped + 1
        if target_fps:
            updates["target_fps"] = target_fps

        window = time.perf_counter() - counters.window_start
        if window >= 1.0:
            updates["fps"] = counters.frames / window
            updates["bytes_per_second"] = counters.bytes_ / window
            self._counters = _Counters()

        if updates:
            self._update_stats(**updates)

    def _update_stats(self, **changes: Any) -> None:
        self._stats = replace(self._stats, **changes)
        if self.on_stats:
            try:
                self.on_stats(self._stats)
            except Exception as exc:  # noqa: BLE001 - listeners must not break the loop
                log.debug("stats callback failed: %s", exc)
