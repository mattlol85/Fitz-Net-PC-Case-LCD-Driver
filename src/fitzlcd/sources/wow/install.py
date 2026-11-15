"""Find World of Warcraft: Forever on disk and install the FitzLCD addon into it.

A Battle.net install keeps every flavour of the game side by side, sharing one
``Data`` folder::

    World of Warcraft/
        .build.info         one row per product (Forever's is ``wowf``)
        Data/               the shared CASC storage
        _retail_/  _classic_era_/  _forever_/ ...
            .flavor.info    names the product this folder runs
            Wow.exe         Forever's executable is called Wow.exe too
            Interface/AddOns/

The root is found through the uninstall registry entry Battle.net writes, then
the usual install locations, or ``AppConfig.wow_install_dir`` (either the root
or the ``_forever_`` folder itself).
"""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from fitzlcd.sources.wow import WowError

if TYPE_CHECKING:
    from fitzlcd.config import AppConfig

log = logging.getLogger(__name__)

FOREVER_FOLDER = "_forever_"
FOREVER_FLAVORS = ("wowf",)
ADDON_NAME = "FitzLCD"

_REGISTRY = (
    (
        r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\World of Warcraft",
        "InstallLocation",
    ),
    (r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\World of Warcraft", "InstallLocation"),
    (r"SOFTWARE\WOW6432Node\Blizzard Entertainment\World of Warcraft", "InstallPath"),
    (r"SOFTWARE\Blizzard Entertainment\World of Warcraft", "InstallPath"),
)


def bundled_addon() -> Path:
    """The addon shipped with the app (inside ``_internal`` in the frozen build)."""
    return Path(__file__).resolve().parent / "addon" / ADDON_NAME


def _is_root(path: Path) -> bool:
    try:
        return (path / ".build.info").is_file()
    except OSError:
        return False


def flavor(product_dir: Path) -> str:
    """The product code in a flavour folder's ``.flavor.info``, or ``""``."""
    try:
        lines = (product_dir / ".flavor.info").read_text(encoding="utf-8").splitlines()
    except (OSError, ValueError):
        return ""
    values = [line.strip() for line in lines[1:] if line.strip()]
    return values[0] if values else ""


def _registry_paths() -> list[Path]:
    if sys.platform != "win32":
        return []
    import winreg

    found = []
    for hive in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for subkey, value_name in _REGISTRY:
            try:
                with winreg.OpenKey(hive, subkey) as key:
                    value, _ = winreg.QueryValueEx(key, value_name)
            except OSError:
                continue
            if isinstance(value, str) and value.strip():
                found.append(Path(value.strip()))
    return found


def _candidates(override: str) -> list[Path]:
    paths = []
    if override.strip():
        paths.append(Path(override.strip()))
    paths += _registry_paths()
    for env in ("PROGRAMFILES(X86)", "PROGRAMFILES"):
        base = os.environ.get(env)
        if base:
            paths.append(Path(base) / "World of Warcraft")
    return paths


def find_wow_root(override: str = "") -> Path | None:
    """The install root holding ``.build.info``, if one can be found."""
    for path in _candidates(override):
        # The registry and the user may name the root, a flavour folder, or the exe's folder.
        for candidate in (path, path.parent, path.parent.parent):
            if _is_root(candidate):
                return candidate
    return None


def find_forever_dir(root: Path) -> Path | None:
    """Forever's flavour folder under ``root``."""
    direct = root / FOREVER_FOLDER
    if direct.is_dir():
        return direct
    try:
        folders = sorted(p for p in root.iterdir() if p.is_dir() and p.name.startswith("_"))
    except OSError:
        return None
    for folder in folders:
        if flavor(folder) in FOREVER_FLAVORS:
            return folder
    return None


def locate(config: AppConfig) -> tuple[Path, Path] | None:
    """(install root, Forever folder), or ``None`` if Forever is not installed."""
    root = find_wow_root(config.wow_install_dir)
    if root is None:
        return None
    forever = find_forever_dir(root)
    return (root, forever) if forever is not None else None


def product_code(forever_dir: Path) -> str:
    return flavor(forever_dir) or FOREVER_FLAVORS[0]


def addon_target(forever_dir: Path) -> Path:
    return forever_dir / "Interface" / "AddOns" / ADDON_NAME


def _toc_version(folder: Path) -> str:
    try:
        text = (folder / f"{ADDON_NAME}.toc").read_text(encoding="utf-8")
    except (OSError, ValueError):
        return ""
    for line in text.splitlines():
        if line.lower().startswith("## version:"):
            return line.split(":", 1)[1].strip()
    return ""


def addon_state(config: AppConfig) -> str:
    """``missing_game``, ``not_installed``, ``outdated`` or ``installed``."""
    found = locate(config)
    if found is None:
        return "missing_game"
    target = addon_target(found[1])
    installed = _toc_version(target)
    if not installed:
        return "not_installed"
    return "installed" if installed == _toc_version(bundled_addon()) else "outdated"


def install_addon(config: AppConfig) -> Path:
    """Copy the bundled addon into Forever's AddOns folder, replacing an older copy.

    Raises :exc:`FileNotFoundError` when Forever cannot be found, and lets an
    ``OSError`` from the copy propagate so the UI can say what went wrong.
    """
    found = locate(config)
    if found is None:
        raise FileNotFoundError("Could not find World of Warcraft: Forever on this PC.")
    source = bundled_addon()
    if not (source / f"{ADDON_NAME}.toc").is_file():
        raise FileNotFoundError(f"The bundled addon is missing from {source}.")
    target = addon_target(found[1])
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = target.with_name(f".{ADDON_NAME}.installing")
    if staging.exists():
        shutil.rmtree(staging)
    shutil.copytree(source, staging)
    if target.exists():
        shutil.rmtree(target)
    staging.replace(target)
    log.info("installed the WoW addon to %s", target)
    return target


def open_storage(config: AppConfig, cache_path: Path | None = None):
    """The local CASC storage for Forever's build. Raises :class:`WowError`."""
    from fitzlcd.sources.wow.casc import LocalStorage

    found = locate(config)
    if found is None:
        raise WowError("World of Warcraft: Forever is not installed")
    root, forever = found
    return LocalStorage(root, product_code(forever), cache_path=cache_path)
