"""Self-update for the packaged Windows build.

Checks the repo's GitHub Releases, downloads the standalone zip, stages it,
and hands the swap to a detached helper script.

The helper is the crux. The build is one-dir: ``FitzLCD.exe`` and every DLL
under ``_internal`` live in the directory that has to be replaced, and Windows
holds them open for as long as the app runs. So the interpreter being replaced
can never be the one doing the replacing — we stage the new build under
``%APPDATA%/FitzLCD/updates``, spawn a batch file that waits for our PID to
disappear, and quit.

Nothing here imports Qt, so the whole flow is testable headless; the GUI glue
lives in ``fitzlcd.ui.updates``.
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from fitzlcd import __version__

log = logging.getLogger(__name__)

REPO = "mattlol85/Fitz-Net-PC-Case-LCD-Driver"
LATEST_URL = f"https://api.github.com/repos/{REPO}/releases/latest"
RELEASES_PAGE = f"https://github.com/{REPO}/releases/latest"

#: The exe the helper relaunches, and the marker that says a staged directory
#: really is a FitzLCD build. Must match ``COLLECT(name=...)`` in
#: ``packaging/fitzlcd.spec``.
EXE_NAME = "FitzLCD.exe"
INTERNAL_DIR = "_internal"

_TIMEOUT = 20
_DOWNLOAD_TIMEOUT = 120
#: The release workflow names its asset ``FitzLCD-<version>-windows.zip``.
_ASSET_RE = re.compile(r"^FitzLCD-.*-windows\.zip$", re.IGNORECASE)

#: Injectable transport, as in ``sources/claude_limits.py`` — tests never reach
#: the network.
Fetch = Callable[[str, dict], "tuple[int, str]"]


class UpdateError(Exception):
    """A step of the update failed in a way worth telling the user about."""


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    url: str
    size: int
    page: str

    @property
    def asset_name(self) -> str:
        return self.url.rsplit("/", 1)[-1] or f"FitzLCD-{self.version}-windows.zip"


# --------------------------------------------------------------------- guards


def is_frozen() -> bool:
    """True only in the PyInstaller build; source installs update via git/pip."""
    return bool(getattr(sys, "frozen", False))


def install_dir() -> Path:
    """The directory the helper will replace."""
    return Path(sys.executable).resolve().parent


def install_writable(target: Path | None = None) -> bool:
    """Can we swap the install in place, or is it somewhere needing elevation?

    Probes with a real file rather than checking ACLs: under Program Files
    without elevation the write is what actually fails.
    """
    directory = target or install_dir()
    probe = directory / ".fitzlcd-write-probe"
    try:
        probe.write_bytes(b"")
        probe.unlink()
        return True
    except OSError:
        return False


def updates_dir() -> Path:
    # Deferred import: config imports the scene library, which would cycle.
    from fitzlcd.config import app_dir

    return app_dir() / "updates"


# -------------------------------------------------------------------- version


def parse_version(text: str | None) -> tuple[int, ...]:
    """``v1.2.0`` -> ``(1, 2, 0)``. Anything unparsable sorts oldest."""
    if not text:
        return (0,)
    numbers = re.findall(r"\d+", str(text))
    if not numbers:
        return (0,)
    return tuple(int(n) for n in numbers[:3])


def is_newer(candidate: str, current: str = __version__) -> bool:
    return parse_version(candidate) > parse_version(current)


# ---------------------------------------------------------------------- check


def _http_get(url: str, headers: dict) -> tuple[int, str]:
    request = urllib.request.Request(url, method="GET", headers=headers)  # noqa: S310
    try:
        with urllib.request.urlopen(request, timeout=_TIMEOUT) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # A rate-limit body still says something useful; the caller decides.
        return exc.code, exc.read().decode("utf-8", "replace")


def _pick_asset(assets: list[dict]) -> dict | None:
    for asset in assets:
        if _ASSET_RE.match(str(asset.get("name", ""))):
            return asset
    zips = [a for a in assets if str(a.get("name", "")).lower().endswith(".zip")]
    return zips[0] if len(zips) == 1 else None


def check(current: str = __version__, fetch: Fetch | None = None) -> Release | None:
    """Return the newer release, or ``None`` if we're current or can't tell.

    Never raises: a failed check must not disturb a running app.
    """
    get = fetch or _http_get
    headers = {
        "Accept": "application/vnd.github+json",
        "User-Agent": f"FitzLCD/{current}",
    }
    try:
        status, body = get(LATEST_URL, headers)
        if status != 200:
            log.info("update check: HTTP %s", status)
            return None
        data = json.loads(body)
        version = str(data.get("tag_name", "")).lstrip("vV")
        if not version or not is_newer(version, current):
            return None
        asset = _pick_asset(data.get("assets") or [])
        if not asset or not asset.get("browser_download_url"):
            log.info("update check: release %s has no Windows zip", version)
            return None
        return Release(
            version=version,
            notes=str(data.get("body") or "").strip(),
            url=str(asset["browser_download_url"]),
            size=int(asset.get("size") or 0),
            page=str(data.get("html_url") or RELEASES_PAGE),
        )
    except Exception as exc:  # noqa: BLE001 - a failed check must never propagate
        log.info("update check failed: %s", exc)
        return None


# ------------------------------------------------------------------- download

Progress = Callable[[int, int], None]


def download(
    release: Release, dest_dir: Path | None = None, progress: Progress | None = None
) -> Path:
    """Stream the release zip to disk, returning its path."""
    directory = dest_dir or updates_dir()
    directory.mkdir(parents=True, exist_ok=True)
    target = directory / release.asset_name
    partial = target.with_suffix(target.suffix + ".part")

    request = urllib.request.Request(  # noqa: S310
        release.url,
        method="GET",
        headers={"User-Agent": f"FitzLCD/{__version__}", "Accept": "application/octet-stream"},
    )
    done = 0
    try:
        with urllib.request.urlopen(request, timeout=_DOWNLOAD_TIMEOUT) as response:  # noqa: S310
            total = int(response.headers.get("Content-Length") or release.size or 0)
            with partial.open("wb") as handle:
                while chunk := response.read(64 * 1024):
                    handle.write(chunk)
                    done += len(chunk)
                    if progress is not None:
                        progress(done, total)
    except OSError as exc:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Download failed: {exc}") from exc

    if release.size and done != release.size:
        partial.unlink(missing_ok=True)
        raise UpdateError(f"Download was {done} bytes, expected {release.size}.")

    target.unlink(missing_ok=True)
    partial.replace(target)
    return target


# ---------------------------------------------------------------------- stage


def _safe_members(archive: zipfile.ZipFile, root: Path) -> list[str]:
    """Reject any member that would escape the staging directory (zip slip)."""
    names = []
    for name in archive.namelist():
        resolved = (root / name).resolve()
        if not resolved.is_relative_to(root):
            raise UpdateError(f"Update archive contains an unsafe path: {name!r}")
        names.append(name)
    return names


def stage(zip_path: Path, version: str, dest_dir: Path | None = None) -> Path:
    """Extract the zip and confirm it looks like a FitzLCD one-dir build.

    The workflow zips ``dist/FitzLCD/*``, so the archive root *is* the app
    directory — no wrapping folder to strip.
    """
    directory = dest_dir or updates_dir()
    staged = (directory / f"staged-{version}").resolve()
    _rmtree(staged)
    staged.mkdir(parents=True, exist_ok=True)

    try:
        with zipfile.ZipFile(zip_path) as archive:
            archive.extractall(staged, members=_safe_members(archive, staged))
    except zipfile.BadZipFile as exc:
        _rmtree(staged)
        raise UpdateError("The downloaded update is not a valid zip file.") from exc

    if not (staged / EXE_NAME).is_file() or not (staged / INTERNAL_DIR).is_dir():
        _rmtree(staged)
        raise UpdateError(
            f"The update archive does not look like a FitzLCD build "
            f"({EXE_NAME} and {INTERNAL_DIR}/ are missing)."
        )
    return staged


def _rmtree(path: Path) -> None:
    import shutil

    shutil.rmtree(path, ignore_errors=True)


# ---------------------------------------------------------------------- apply

# Mirrors the staged build over the install. /MIR rather than /E so orphaned
# modules from the old build can't shadow the new ones — safe because the
# install directory is exclusively app-owned (user data lives in %APPDATA%)
# and stage() has already refused anything that isn't a FitzLCD build.
# Every tool is called by absolute path: a user's PATH may well shadow `find`
# or `ping` (a Git-for-Windows install does exactly that), and a silently wrong
# `find` would make the wait loop swap the files out from under a live app.
_HELPER = """@echo off
setlocal
set "PID={pid}"
set "STAGED={staged}"
set "INSTALL={install}"
set "SYS=%SystemRoot%\\System32"
set "TASKS=%SYS%\\tasklist.exe"
set "MATCH=%SYS%\\findstr.exe"
set "LOG={log}"

echo [%DATE% %TIME%] waiting for pid %PID% > "%LOG%"

rem Wait for FitzLCD to exit before touching its files (~60s ceiling).
rem ping is the sleep: timeout.exe needs a console, and this runs detached.
for /l %%i in (1,1,60) do (
    "%TASKS%" /fi "PID eq %PID%" /nh 2>nul | "%MATCH%" /r "\\<%PID%\\>" >nul || goto :swap
    "%SYS%\\ping.exe" -n 2 127.0.0.1 >nul
)
echo [%TIME%] timed out waiting for pid %PID%; not touching the install >> "%LOG%"
goto :done

:swap
echo [%TIME%] swapping "%STAGED%" -^> "%INSTALL%" >> "%LOG%"
"%SYS%\\robocopy.exe" "%STAGED%" "%INSTALL%" /MIR /R:3 /W:1 /NFL /NDL /NJH /NJS >> "%LOG%"
if errorlevel 8 (
    echo [%TIME%] robocopy failed with %ERRORLEVEL%; install may be incomplete >> "%LOG%"
    goto :done
)
rem /d so the new process does not hold a handle on the staging dir we delete.
echo [%TIME%] relaunching >> "%LOG%"
start "" /d "%INSTALL%" "%INSTALL%\\{exe}"

:cleanup
"%SYS%\\cmd.exe" /c rmdir /s /q "%STAGED%" 2>nul
echo [%TIME%] done >> "%LOG%"
(goto) 2>nul & del "%~f0"

:done
echo [%TIME%] finished without relaunching >> "%LOG%"
"""


def write_helper(staged: Path, target: Path | None = None, pid: int | None = None) -> Path:
    """Write the swap-and-relaunch batch file. Split out so tests can read it."""
    destination = (target or install_dir()).resolve()
    script = staged.parent / f"apply-{staged.name}.bat"
    script.write_text(
        _HELPER.format(
            pid=pid if pid is not None else os.getpid(),
            staged=staged,
            install=destination,
            exe=EXE_NAME,
            # The helper outlives us, so its log is the only record of why an
            # update did not land.
            log=staged.parent / "apply.log",
        ),
        # cmd reads the file in the OEM code page; ASCII keeps paths honest.
        encoding="ascii",
        errors="replace",
    )
    return script


def apply_and_restart(staged: Path, target: Path | None = None) -> Path:
    """Launch the detached helper. The caller must then quit promptly.

    ``ui/app.py`` ends in ``os._exit``, which skips atexit handlers — hence a
    detached child process rather than anything hooked to shutdown.
    """
    script = write_helper(staged, target)
    # CREATE_NO_WINDOW rather than DETACHED_PROCESS: the two are mutually
    # exclusive, and DETACHED_PROCESS leaves the helper with no valid standard
    # handles, which wedges the piped tasklist in its wait loop. A Windows child
    # outlives its parent either way, so this is detached in the sense that
    # matters. Handles are pointed at NUL for the same reason.
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
        subprocess, "CREATE_NEW_PROCESS_GROUP", 0
    )
    subprocess.Popen(  # noqa: S603 - fixed cmd.exe invocation on a file we wrote
        ["cmd.exe", "/c", str(script)],
        close_fds=True,
        creationflags=flags,
        cwd=str(script.parent),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    return script


def cleanup(dest_dir: Path | None = None) -> None:
    """Drop downloads and staging left behind by a previous update."""
    directory = dest_dir or updates_dir()
    try:
        children = list(directory.iterdir()) if directory.exists() else []
    except OSError as exc:
        log.debug("could not list %s: %s", directory, exc)
        return
    for child in children:
        try:
            _rmtree(child) if child.is_dir() else child.unlink()
        except OSError as exc:  # noqa: PERF203 - best effort, never fatal
            log.debug("could not clean %s: %s", child, exc)
