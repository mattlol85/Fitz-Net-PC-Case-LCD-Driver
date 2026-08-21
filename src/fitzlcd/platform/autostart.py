"""Windows autostart via the per-user Run key.

The Run key is used rather than a scheduled task or a service: it needs no
elevation, is trivial for the user to inspect or remove, and matches what other
tray apps do.
"""

from __future__ import annotations

import logging
import sys
from pathlib import Path

log = logging.getLogger(__name__)

RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
VALUE_NAME = "FitzLCD"


def _winreg():
    try:
        import winreg
    except ImportError as exc:  # pragma: no cover - Windows only
        raise OSError("autostart is only supported on Windows") from exc
    return winreg


def launch_command() -> str:
    """The command Windows should run at login.

    Prefers a frozen executable; otherwise re-launches this interpreter with the
    module, quoted because the project path contains spaces.
    """
    if getattr(sys, "frozen", False):
        return f'"{Path(sys.executable)}"'
    # pythonw avoids a console window flashing up at login.
    interpreter = Path(sys.executable)
    windowless = interpreter.with_name("pythonw.exe")
    if windowless.exists():
        interpreter = windowless
    return f'"{interpreter}" -m fitzlcd'


def is_enabled() -> bool:
    winreg = _winreg()
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
            winreg.QueryValueEx(key, VALUE_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError as exc:
        log.debug("could not read autostart key: %s", exc)
        return False


def set_autostart(enabled: bool) -> None:
    winreg = _winreg()
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(key, VALUE_NAME, 0, winreg.REG_SZ, launch_command())
            log.info("autostart enabled: %s", launch_command())
        else:
            try:
                winreg.DeleteValue(key, VALUE_NAME)
                log.info("autostart disabled")
            except FileNotFoundError:
                pass
