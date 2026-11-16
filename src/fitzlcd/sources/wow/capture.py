"""Find the WoW window and copy the strip's corner of it off the screen.

Plain GDI through ctypes, so there is no new dependency: ``BitBlt`` from the
screen DC copies what the compositor is showing, which works for windowed and
"Windowed (Fullscreen)" modes. Exclusive fullscreen may hand back black pixels,
which the decoder reports as "strip not found". The strip has to be visible on
screen: another window covering the game's top-left corner hides it.

Only a few hundred pixels are copied per grab, so ten grabs a second cost next
to nothing.
"""

from __future__ import annotations

import ctypes
import logging
import re
import sys
import time
from dataclasses import dataclass
from pathlib import PureWindowsPath
from typing import Protocol

from PIL import Image

from fitzlcd.sources.wow.protocol import COLUMNS, GAUGES, ROWS

log = logging.getLogger(__name__)

#: Game executables, most preferred after one running from Forever's ``_forever_``
#: folder (Forever's exe is plain ``Wow.exe``, the same name as retail's).
_PREFERRED = ("wow.exe", "wowb.exe", "wowclassic.exe")
_EXE = re.compile(r"^wow[a-z0-9_]*\.exe$", re.IGNORECASE)

#: Big enough for an 8-pixel cell pitch plus a small border.
CAPTURE_WIDTH = COLUMNS * 8 + 16
CAPTURE_HEIGHT = (ROWS + GAUGES) * 8 + 16

_RESCAN_SECONDS = 2.0


@dataclass(frozen=True)
class Grab:
    #: ok | not_running | minimized | unsupported | error
    status: str
    image: Image.Image | None = None
    #: Whether the game is the foreground window (helps explain a hidden strip).
    foreground: bool = True


#: The biggest portrait square worth copying; anything larger is a bad rect.
MAX_REGION = 512


class Grabber(Protocol):
    def grab(self) -> Grab: ...

    def grab_region(self, x: int, y: int, width: int, height: int) -> Image.Image | None:
        """A rectangle of the game's client area, or ``None`` if it cannot be seen."""
        ...

    def close(self) -> None: ...


def _preference(name: str) -> int:
    lowered = name.lower()
    return _PREFERRED.index(lowered) if lowered in _PREFERRED else len(_PREFERRED)


class ScreenGrabber:
    """Locates the game by process name and grabs its client area's corner."""

    def __init__(self) -> None:
        self._pid: int | None = None
        self._hwnd: int | None = None
        self._next_scan = 0.0
        self._dpi_set = False
        self._win = _Win32() if sys.platform == "win32" else None

    def grab(self) -> Grab:
        if self._win is None:
            return Grab("unsupported")
        win = self._win
        if not self._dpi_set:
            # Physical pixels for this thread only; never touches the Qt UI's awareness.
            win.per_monitor_dpi_for_thread()
            self._dpi_set = True
        if self._hwnd is None or not win.is_window(self._hwnd):
            self._hwnd = self._find_window()
            if self._hwnd is None:
                return Grab("not_running")
        hwnd = self._hwnd
        if win.is_iconic(hwnd):
            return Grab("minimized")
        origin, size = win.client_origin_and_size(hwnd)
        width = min(CAPTURE_WIDTH, size[0])
        height = min(CAPTURE_HEIGHT, size[1])
        if width <= 0 or height <= 0:
            return Grab("minimized")
        image = win.copy_screen(origin[0], origin[1], width, height)
        return Grab("ok", image, foreground=win.foreground() == hwnd)

    def grab_region(self, x: int, y: int, width: int, height: int) -> Image.Image | None:
        """Copy part of the client area (the player portrait). Only after a good :meth:`grab`."""
        win, hwnd = self._win, self._hwnd
        if win is None or hwnd is None or not win.is_window(hwnd) or win.is_iconic(hwnd):
            return None
        if not 0 < width <= MAX_REGION or not 0 < height <= MAX_REGION:
            return None
        origin, size = win.client_origin_and_size(hwnd)
        if x < 0 or y < 0 or x + width > size[0] or y + height > size[1]:
            return None
        return win.copy_screen(origin[0] + x, origin[1] + y, width, height)

    def _find_window(self) -> int | None:
        now = time.monotonic()
        if self._pid is None or not _pid_alive(self._pid):
            if now < self._next_scan:
                return None
            self._next_scan = now + _RESCAN_SECONDS
            self._pid = _find_game_pid()
            if self._pid is None:
                return None
        assert self._win is not None
        return self._win.main_window(self._pid)

    def close(self) -> None:
        self._hwnd = None


def _pid_alive(pid: int) -> bool:
    import psutil

    return psutil.pid_exists(pid)


def _rank(name: str, exe: str) -> int:
    """Lower is better: Forever's own folder, then any other WoW client.

    Within a folder the game exe beats helpers like ``Utils\\WowVoiceProxy.exe``,
    which match the name pattern but have no window to grab.
    """
    forever = "_forever_" in (part.lower() for part in PureWindowsPath(exe).parts)
    return (0 if forever else len(_PREFERRED) + 1) + _preference(name)


def _find_game_pid() -> int | None:
    import psutil

    best: tuple[int, int] | None = None
    for proc in psutil.process_iter(["name", "exe"]):
        name = proc.info.get("name") or ""
        if not _EXE.match(name):
            continue
        rank = (_rank(name, proc.info.get("exe") or ""), proc.pid)
        if best is None or rank < best:
            best = rank
    return None if best is None else best[1]


class _Win32:
    """The handful of user32/gdi32 calls the grabber needs, typed for 64-bit."""

    SRCCOPY = 0x00CC0020
    DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 = ctypes.c_void_p(-4)

    def __init__(self) -> None:
        from ctypes import wintypes as wt

        self.wt = wt
        user32 = ctypes.WinDLL("user32", use_last_error=True)
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)
        self.user32, self.gdi32 = user32, gdi32

        self.enum_proc = ctypes.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        user32.EnumWindows.argtypes = [self.enum_proc, wt.LPARAM]
        user32.GetWindowThreadProcessId.argtypes = [wt.HWND, ctypes.POINTER(wt.DWORD)]
        user32.GetWindowThreadProcessId.restype = wt.DWORD
        for fn in (user32.IsWindow, user32.IsWindowVisible, user32.IsIconic):
            fn.argtypes = [wt.HWND]
            fn.restype = wt.BOOL
        user32.GetWindow.argtypes = [wt.HWND, ctypes.c_uint]
        user32.GetWindow.restype = wt.HWND
        user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
        user32.ClientToScreen.argtypes = [wt.HWND, ctypes.POINTER(wt.POINT)]
        user32.GetForegroundWindow.restype = wt.HWND
        user32.GetDC.argtypes = [wt.HWND]
        user32.GetDC.restype = wt.HDC
        user32.ReleaseDC.argtypes = [wt.HWND, wt.HDC]
        try:
            user32.SetThreadDpiAwarenessContext.argtypes = [ctypes.c_void_p]
            user32.SetThreadDpiAwarenessContext.restype = ctypes.c_void_p
        except AttributeError:  # before Windows 10 1607
            pass

        gdi32.CreateCompatibleDC.argtypes = [wt.HDC]
        gdi32.CreateCompatibleDC.restype = wt.HDC
        gdi32.CreateCompatibleBitmap.argtypes = [wt.HDC, ctypes.c_int, ctypes.c_int]
        gdi32.CreateCompatibleBitmap.restype = wt.HBITMAP
        gdi32.SelectObject.argtypes = [wt.HDC, wt.HGDIOBJ]
        gdi32.SelectObject.restype = wt.HGDIOBJ
        gdi32.BitBlt.argtypes = [
            wt.HDC,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_int,
            wt.HDC,
            ctypes.c_int,
            ctypes.c_int,
            wt.DWORD,
        ]
        gdi32.GetDIBits.argtypes = [
            wt.HDC,
            wt.HBITMAP,
            ctypes.c_uint,
            ctypes.c_uint,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_uint,
        ]
        gdi32.DeleteObject.argtypes = [wt.HGDIOBJ]
        gdi32.DeleteDC.argtypes = [wt.HDC]

    def per_monitor_dpi_for_thread(self) -> None:
        fn = getattr(self.user32, "SetThreadDpiAwarenessContext", None)
        if fn is not None:
            fn(self.DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2)

    def is_window(self, hwnd: int) -> bool:
        return bool(self.user32.IsWindow(hwnd))

    def is_iconic(self, hwnd: int) -> bool:
        return bool(self.user32.IsIconic(hwnd))

    def foreground(self) -> int | None:
        return self.user32.GetForegroundWindow()

    def main_window(self, pid: int) -> int | None:
        """The largest visible, unowned top-level window of ``pid``."""
        wt = self.wt
        found: list[tuple[int, int]] = []

        def visit(hwnd, _lparam):
            owner = wt.DWORD()
            self.user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
            if (
                owner.value == pid
                and self.user32.IsWindowVisible(hwnd)
                and not self.user32.GetWindow(hwnd, 4)  # GW_OWNER: skip dialogs
            ):
                _, (w, h) = self.client_origin_and_size(hwnd)
                found.append((w * h, hwnd))
            return True

        self.user32.EnumWindows(self.enum_proc(visit), 0)
        return max(found)[1] if found else None

    def client_origin_and_size(self, hwnd: int) -> tuple[tuple[int, int], tuple[int, int]]:
        wt = self.wt
        rect = wt.RECT()
        self.user32.GetClientRect(hwnd, ctypes.byref(rect))
        point = wt.POINT(0, 0)
        self.user32.ClientToScreen(hwnd, ctypes.byref(point))
        return (point.x, point.y), (rect.right - rect.left, rect.bottom - rect.top)

    def copy_screen(self, x: int, y: int, width: int, height: int) -> Image.Image:
        user32, gdi32 = self.user32, self.gdi32
        screen = user32.GetDC(None)
        if not screen:
            raise OSError("GetDC failed")
        try:
            memory = gdi32.CreateCompatibleDC(screen)
            try:
                bitmap = gdi32.CreateCompatibleBitmap(screen, width, height)
                try:
                    previous = gdi32.SelectObject(memory, bitmap)
                    try:
                        if not gdi32.BitBlt(
                            memory, 0, 0, width, height, screen, x, y, self.SRCCOPY
                        ):
                            raise OSError("BitBlt failed")
                    finally:
                        gdi32.SelectObject(memory, previous)
                    header = (ctypes.c_uint32 * 11)()  # BITMAPINFO: header + one RGBQUAD
                    header[0] = 40  # BITMAPINFOHEADER.biSize
                    header[1] = width
                    header[2] = ctypes.c_uint32(-height).value  # negative: top-down rows
                    header[3] = 1 | (32 << 16)  # biPlanes=1, biBitCount=32
                    buffer = ctypes.create_string_buffer(width * height * 4)
                    rows = gdi32.GetDIBits(memory, bitmap, 0, height, buffer, header, 0)
                    if rows != height:
                        raise OSError("GetDIBits failed")
                finally:
                    gdi32.DeleteObject(bitmap)
            finally:
                gdi32.DeleteDC(memory)
        finally:
            user32.ReleaseDC(None, screen)
        return Image.frombuffer("RGB", (width, height), buffer.raw, "raw", "BGRX", 0, 1)
