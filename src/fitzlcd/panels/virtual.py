"""A fake panel, so the whole app can be run and debugged without hardware.

Two reasons this exists rather than being a test fixture:

* Only one process may hold the real serial port, so you cannot debug the app
  while anything else is talking to the panel.
* A wedged or unplugged panel should never block development.

It accepts frames like a real driver, tracks the same statistics, and optionally
writes the most recent frame to disk so you can eyeball what was sent.
"""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

from fitzlcd.panels.base import Panel, PanelCaps, PanelHandle
from fitzlcd.render.encode import Transform

log = logging.getLogger(__name__)

DEFAULT_SIZE = (1920, 462)
DEFAULT_DUMP = Path("build/preview/frame.jpg")


class VirtualPanel(Panel):
    """In-memory panel mirroring the DS916's geometry."""

    driver_name = "Virtual panel"

    def __init__(
        self,
        address: str = "virtual",
        size: tuple[int, int] = DEFAULT_SIZE,
        transform: Transform = Transform.ROT_270,
        dump_path: Path | None = DEFAULT_DUMP,
        max_fps: int = 30,
    ) -> None:
        super().__init__(address)
        self._size = size
        self._transform = transform
        self._max_fps = max_fps
        self._dump_path = Path(dump_path) if dump_path else None
        self._lock = threading.Lock()
        self.last_frame: bytes | None = None
        self.frame_count = 0
        self.byte_count = 0
        self.opened_at = 0.0

    @classmethod
    def detect(cls) -> list[PanelHandle]:
        """Always available -- it is a software device."""
        return [
            PanelHandle(
                driver=cls,
                address="virtual",
                label=cls.driver_name,
                details={"note": "software panel for development"},
            )
        ]

    def open(self) -> None:
        width, height = self._size
        self._caps = PanelCaps(
            width=width,
            height=height,
            transform=self._transform,
            max_fps=self._max_fps,
            supports_brightness=True,
            model="virtual",
            firmware="0.0",
        )
        self.opened_at = time.monotonic()
        if self._dump_path is not None:
            self._dump_path.parent.mkdir(parents=True, exist_ok=True)
        log.info("virtual panel open: %dx%d %s", width, height, self._transform.value)

    def close(self) -> None:
        self._caps = None

    def push_frame(self, payload: bytes) -> None:
        with self._lock:
            self.last_frame = payload
            self.frame_count += 1
            self.byte_count += len(payload)
        if self._dump_path is not None:
            # Write via a temp file so a reader never sees a half-written frame.
            tmp = self._dump_path.with_suffix(".tmp")
            tmp.write_bytes(payload)
            tmp.replace(self._dump_path)

    def set_brightness(self, level: int) -> None:
        log.debug("virtual panel brightness -> %d", level)

    @property
    def average_fps(self) -> float:
        elapsed = time.monotonic() - self.opened_at
        return self.frame_count / elapsed if elapsed > 0 else 0.0
