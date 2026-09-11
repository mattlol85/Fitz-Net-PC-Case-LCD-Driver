"""Serial driver for the Jonsbo DS916 / "Jungle Leopard" family.

Hard-won I/O rules encoded here (see the Findings section of the protocol spec):

* Never call ``Serial.flush()`` on the frame path. On Windows it waits on
  ``FlushFileBuffers`` with no timeout, so if the panel stops draining its bulk
  endpoint the caller blocks forever.
* Never free-run frames. The host can push frames several times faster than the
  panel renders them; flooding wedges the firmware until it is power-cycled.
  ``PanelCaps.max_fps`` is a real limit, not a hint.
* Opening the port can itself block when the firmware is wedged, so the open is
  performed on a watchdog thread and abandoned on timeout.
"""

from __future__ import annotations

import logging
import threading
import time

import serial
from serial.tools import list_ports

from fitzlcd.panels.base import (
    Panel,
    PanelBusyError,
    PanelCaps,
    PanelError,
    PanelHandle,
    PanelUnavailableError,
)
from fitzlcd.panels.ds916 import protocol as proto
from fitzlcd.render.encode import Transform

log = logging.getLogger(__name__)

#: Frames per second we allow by default. The transport sustains far more, but
#: the panel does not -- see the module docstring.
DEFAULT_MAX_FPS = 30

OPEN_TIMEOUT = 5.0
WRITE_TIMEOUT = 3.0
READ_TIMEOUT = 1.0


def _open_serial_guarded(port: str, timeout: float = OPEN_TIMEOUT) -> serial.Serial:
    """Open ``port``, giving up if the driver call itself blocks.

    A wedged panel makes ``serial.Serial()`` hang indefinitely, which would take
    the whole app down with it. The open runs on a daemon thread so a stuck call
    can be abandoned instead of joined.
    """
    result: dict[str, object] = {}

    def _open() -> None:
        try:
            result["port"] = serial.Serial(
                port,
                proto.BAUD_RATE,
                timeout=READ_TIMEOUT,
                write_timeout=WRITE_TIMEOUT,
            )
        except BaseException as exc:  # noqa: BLE001 - re-raised on the calling thread
            result["error"] = exc

    worker = threading.Thread(target=_open, name=f"ds916-open-{port}", daemon=True)
    worker.start()
    worker.join(timeout)

    if worker.is_alive():
        raise PanelUnavailableError(
            f"{port} did not open within {timeout:.0f}s - the panel is likely wedged; "
            "power-cycle it (unplug the USB header or reboot)"
        )
    if "error" in result:
        exc = result["error"]
        if isinstance(exc, serial.SerialException) and "access is denied" in str(exc).lower():
            raise PanelBusyError(f"{port} is held by another process (vendor app?)") from exc
        raise PanelUnavailableError(f"could not open {port}: {exc}") from exc
    return result["port"]  # type: ignore[return-value]


class DS916Panel(Panel):
    driver_name = "Jonsbo DS916"

    def __init__(self, address: str) -> None:
        super().__init__(address)
        self._serial: serial.Serial | None = None
        self._lock = threading.Lock()
        self._awake = False

    # ------------------------------------------------------------------ detect

    @classmethod
    def detect(cls) -> list[PanelHandle]:
        handles = []
        for port in list_ports.comports():
            if (port.vid, port.pid) in proto.KNOWN_IDS:
                handles.append(
                    PanelHandle(
                        driver=cls,
                        address=port.device,
                        label=cls.driver_name,
                        details={
                            "vid": port.vid,
                            "pid": port.pid,
                            "description": port.description,
                            "serial_number": port.serial_number,
                        },
                    )
                )
        return handles

    # -------------------------------------------------------------- lifecycle

    def open(self) -> None:
        if self._serial is not None:
            return
        ser = _open_serial_guarded(self.address)
        self._serial = ser
        try:
            time.sleep(0.2)
            ser.reset_input_buffer()
            self._clear()
            info = self._read_info()
            self._caps = self._caps_from_info(info)
            log.info("opened %s: %s fw %s", self.address, self._caps.model, self._caps.firmware)
        except PanelError:
            self.close()
            raise
        except Exception as exc:
            # A device mid-enumeration (e.g. a front-panel USB header, which
            # settles slower than a rear motherboard port) can throw a raw
            # pyserial error here. That must still come out as a PanelError -
            # anything else escapes the engine's retry loop and kills it for
            # good instead of triggering a reconnect.
            self.close()
            raise PanelUnavailableError(f"failed to open {self.address}: {exc}") from exc

    def close(self) -> None:
        self._caps = None
        self._awake = False
        ser, self._serial = self._serial, None
        if ser is not None:
            try:
                ser.close()
            except Exception as exc:  # noqa: BLE001 - closing must never raise
                log.debug("error closing %s: %s", self.address, exc)

    # ------------------------------------------------------------------ frames

    def push_frame(self, payload: bytes) -> None:
        ser = self._require_port()
        with self._lock:
            if not self._awake:
                self._wake_locked()
            try:
                for chunk in proto.chunk_image(payload):
                    ser.write(chunk)
            except serial.SerialTimeoutException as exc:
                # The panel stopped draining. Treat as backpressure, not a crash:
                # the engine drops frames and the watchdog reconnects if it persists.
                raise PanelUnavailableError(f"write timed out on {self.address}") from exc
            except serial.SerialException as exc:
                raise PanelUnavailableError(f"write failed on {self.address}: {exc}") from exc

    def set_brightness(self, level: int) -> None:
        if not self.caps.supports_brightness:
            return
        level = max(0, min(100, int(level)))
        ser = self._require_port()
        with self._lock:
            ser.write(proto.build_packet(proto.Command.SET_BRIGHTNESS, bytes([level])))

    # --------------------------------------------------------------- internals

    def _require_port(self) -> serial.Serial:
        if self._serial is None:
            raise PanelUnavailableError(f"{self.address} is not open")
        return self._serial

    def _clear(self) -> None:
        ser = self._require_port()
        for seq in proto.CLEAR_SEQUENCE:
            ser.write(seq)

    def _wake_locked(self) -> None:
        """Send START. Without it the panel ignores frame data."""
        ser = self._require_port()
        try:
            ser.write(proto.build_packet(proto.Command.START))
            time.sleep(0.15)
            ser.read(64)  # drain the ack
        except serial.SerialException as exc:
            raise PanelUnavailableError(f"wake failed on {self.address}: {exc}") from exc
        self._awake = True

    def _read_info(self, timeout: float = 2.0) -> dict:
        ser = self._require_port()
        ser.write(proto.build_packet(proto.Command.GET_INFO))
        buf = bytearray()
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            buf += ser.read(4096)
            info = proto.extract_json(bytes(buf))
            if info is not None:
                info["_raw_angle"] = proto.raw_angle(bytes(buf)).hex()
                return info
            time.sleep(0.05)
        raise PanelUnavailableError(f"{self.address} did not answer GET_INFO")

    @staticmethod
    def _caps_from_info(info: dict) -> PanelCaps:
        width = int(info.get("width") or 1920)
        height = int(info.get("height") or 462)
        return PanelCaps(
            width=width,
            height=height,
            transform=Transform(proto.transform_from_info(info)),
            max_fps=DEFAULT_MAX_FPS,
            # SET_BRIGHTNESS is documented but unverified; enabled only once the
            # probe confirms the device actually honours it.
            supports_brightness=False,
            model=str(info.get("model", "")),
            firmware=str(info.get("version", "")),
        )
