"""Panel abstraction shared by every display driver.

A panel is anything that can accept a fully composed frame. Drivers differ in
transport (serial, WinUSB, ...) and geometry, but everything above this module
only ever sees :class:`Panel`.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace

from fitzlcd.render.encode import Transform


class PanelError(Exception):
    """Base class for driver failures."""


class PanelBusyError(PanelError):
    """The device exists but something else holds it (e.g. the vendor app)."""


class PanelUnavailableError(PanelError):
    """The device went away, or stopped responding."""


@dataclass(frozen=True)
class PanelCaps:
    """Everything the render stack needs to know about a panel.

    ``width``/``height`` are the *logical* orientation scenes are composed in.
    ``transform`` is applied at encode time to match the panel's scan-out.
    """

    width: int
    height: int
    transform: Transform = Transform.NONE
    max_fps: int = 30
    supports_brightness: bool = False
    model: str = ""
    firmware: str = ""

    @property
    def size(self) -> tuple[int, int]:
        return self.width, self.height

    @property
    def aspect(self) -> float:
        return self.width / self.height

    @property
    def is_portrait(self) -> bool:
        return self.height > self.width

    def rotated(self, degrees: int) -> PanelCaps:
        """Caps for a panel physically mounted ``degrees`` counter-clockwise.

        Scenes are composed in whatever the *viewer* sees, so a panel turned on
        its side is composed portrait. Rotating the composed frame back into the
        panel's own logical space and then applying the panel's scan-out
        transform are both rotations about the same centre, so they collapse into
        a single one: the angles simply add.
        """
        degrees %= 360
        if degrees % 90:
            raise ValueError(f"rotation must be a multiple of 90, got {degrees}")
        swap = degrees % 180 == 90
        return replace(
            self,
            width=self.height if swap else self.width,
            height=self.width if swap else self.height,
            transform=self.transform.combine(degrees),
        )


@dataclass(frozen=True)
class PanelHandle:
    """A detected-but-not-yet-opened panel.

    Produced by :meth:`Panel.detect` and cheap to create: detection must not
    open the device or have side effects, so the UI can rescan freely.
    """

    driver: type[Panel]
    address: str  # serial port, USB path, ... -- driver-specific
    label: str
    details: dict = field(default_factory=dict)

    def open(self) -> Panel:
        panel = self.driver(self.address)
        panel.open()
        return panel

    def __str__(self) -> str:
        return f"{self.label} ({self.address})"


class Panel(ABC):
    """A display device that accepts composed frames."""

    #: Human-readable driver name, shown in the UI.
    driver_name: str = "panel"

    def __init__(self, address: str) -> None:
        self.address = address
        self._caps: PanelCaps | None = None

    @classmethod
    @abstractmethod
    def detect(cls) -> list[PanelHandle]:
        """Enumerate candidate devices without opening them."""

    @abstractmethod
    def open(self) -> None:
        """Connect and populate :attr:`caps`."""

    @abstractmethod
    def close(self) -> None:
        """Release the device. Must be safe to call twice."""

    @abstractmethod
    def push_frame(self, payload: bytes) -> None:
        """Send one encoded frame. Raises :class:`PanelError` on failure."""

    def set_brightness(self, level: int) -> None:  # noqa: B027 - optional hook
        """Set backlight 0-100. Deliberately concrete and empty: most panels
        have no brightness control, and those drivers should not be forced to
        implement a stub."""

    @property
    def caps(self) -> PanelCaps:
        if self._caps is None:
            raise PanelError("panel is not open")
        return self._caps

    @property
    def is_open(self) -> bool:
        return self._caps is not None

    def __enter__(self) -> Panel:
        if not self.is_open:
            self.open()
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
