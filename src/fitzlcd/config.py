"""Application configuration and the on-disk scene library.

Everything user-owned lives outside the repo, under ``%APPDATA%/FitzLCD``:

    config.json     app settings
    scenes/*.json   the scene library

so scenes survive reinstalls and can be backed up or shared on their own.
"""

from __future__ import annotations

import json
import logging
import os
import secrets
from dataclasses import asdict, dataclass, field, fields
from pathlib import Path

from fitzlcd.render.scene import Scene, SceneError
from fitzlcd.scenes_builtin import DEFAULT_SCENES

log = logging.getLogger(__name__)

APP_NAME = "FitzLCD"


def app_dir() -> Path:
    """Per-user data directory, honouring ``FITZLCD_HOME`` for tests and portability."""
    override = os.environ.get("FITZLCD_HOME")
    if override:
        return Path(override)
    base = os.environ.get("APPDATA") or os.environ.get("XDG_CONFIG_HOME")
    if base:
        return Path(base) / APP_NAME
    return Path.home() / f".{APP_NAME.lower()}"


def scenes_dir() -> Path:
    return app_dir() / "scenes"


def config_path() -> Path:
    return app_dir() / "config.json"


@dataclass
class AppConfig:
    panel: str = "auto"
    #: Physical mounting orientation, degrees counter-clockwise (0/90/180/270).
    rotation: int = 0
    quality: int = 90
    max_fps: int | None = None
    active_scene: str = ""
    brightness: int = 100
    autostart: bool = False
    #: Flip through the scene library automatically; 0 disables it.
    cycle_seconds: int = 0
    minimise_to_tray: bool = True
    start_minimised: bool = False
    #: Render clocks as 24-hour (the default) or 12-hour with an am/pm suffix.
    #: Drives the ``time.now`` metric and every clock layer that has not been
    #: given an explicit strftime format of its own.
    clock_24_hour: bool = True
    #: Show frame rate and throughput under the window. Off by default: it is
    #: diagnostic detail, not something an everyday user needs to watch.
    show_diagnostics: bool = False
    window_geometry: list[int] = field(default_factory=list)
    cs2_gsi_port: int = 13001
    cs2_gsi_token: str = ""
    #: Poll the Claude usage endpoint for subscription limit percentages.
    claude_limits_enabled: bool = True
    #: Seconds between polls. Floor-clamped to 180 s by the provider: the endpoint
    #: throttles hard, and this bucket is shared with your own Claude Code.
    claude_limits_poll_seconds: int = 300
    #: Look for a newer release on launch and once a day. Only ever does
    #: anything in the packaged build; source installs update through git/pip.
    update_check_enabled: bool = True
    #: Unix time of the last completed check, so a restart doesn't re-check.
    update_last_check: float = 0.0
    #: A release the user chose to skip; it stops prompting until a newer one.
    update_skipped_version: str = ""

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        target = path or config_path()
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:  # ValueError covers JSON and bad UTF-8
            log.warning("could not read %s (%s); using defaults", target, exc)
            return cls()
        if not isinstance(data, dict):
            log.warning("%s is not a JSON object; using defaults", target)
            return cls()
        defaults = cls()
        config = cls()
        for f in fields(cls):
            if f.name not in data:
                continue
            value, fallback = data[f.name], getattr(defaults, f.name)
            # Hand-edited files get wrong types; keep the default rather than
            # let a string where a number belongs crash startup later.
            # bool is an int subclass, so it must match exactly, not loosely.
            if (
                type(value) is type(fallback)
                or (isinstance(fallback, float) and type(value) is int)
                or (f.name == "max_fps" and (value is None or type(value) is int))
            ):
                setattr(config, f.name, value)
            else:
                log.warning("config %r has a bad value %r; using the default", f.name, value)
        return config

    def save(self, path: Path | None = None) -> Path:
        """Best-effort write: a read-only profile must not crash the app."""
        target = path or config_path()
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        except OSError as exc:
            log.warning("could not save settings to %s: %s", target, exc)
        return target


def ensure_cs2_gsi_token(config: AppConfig) -> AppConfig:
    """Generate a persisted GSI shared secret on first use.

    A predictable default token (or none at all) would let anything else on
    the machine post fake match state to the listener; generating one lazily
    and saving it means it's stable across restarts without ever having
    shipped a guessable default.
    """
    if not config.cs2_gsi_token:
        config.cs2_gsi_token = secrets.token_hex(8)
        config.save()
    return config


class SceneLibrary:
    """The user's scenes on disk, seeded with defaults on first run."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or scenes_dir()

    def ensure_defaults(self) -> None:
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            if any(self.directory.glob("*.json")):
                return
            for name, data in DEFAULT_SCENES.items():
                (self.directory / f"{name}.json").write_text(
                    json.dumps(data, indent=2), encoding="utf-8"
                )
        except OSError as exc:
            log.warning("could not seed default scenes in %s: %s", self.directory, exc)
            return
        log.info("seeded %d default scenes in %s", len(DEFAULT_SCENES), self.directory)

    def list(self) -> list[Scene]:
        scenes = []
        try:
            paths = sorted(self.directory.glob("*.json"))
        except OSError as exc:
            log.warning("could not list scenes in %s: %s", self.directory, exc)
            return scenes
        for path in paths:
            try:
                scenes.append(Scene.load(path))
            except SceneError as exc:
                log.warning("skipping %s: %s", path.name, exc)
        return scenes

    def get(self, name: str) -> Scene | None:
        for scene in self.list():
            if scene.name == name:
                return scene
        return None

    def save(self, scene: Scene) -> Path:
        self.directory.mkdir(parents=True, exist_ok=True)
        target = scene.path or self.directory / f"{_slug(scene.name)}.json"
        return scene.save(target)

    def delete(self, scene: Scene) -> None:
        if scene.path and scene.path.exists():
            scene.path.unlink()

    def unique_name(self, base: str, ignore: Scene | None = None) -> str:
        """``base``, or ``base 2``, ``base 3``... - whichever won't overwrite a file.

        Scene files are named from a slug of the scene name, so two names that
        slug the same would share one file and the second save would silently
        replace the first.
        """
        base = base.strip() or "Untitled"
        taken = {
            _slug(s.name)
            for s in self.list()
            if ignore is None or s.path is None or s.path != ignore.path
        }
        candidate, n = base, 2
        while _slug(candidate) in taken:
            candidate = f"{base} {n}"
            n += 1
        return candidate

    def rename(self, scene: Scene, new_name: str) -> str:
        """Rename a scene and move its file to match. Returns the name used."""
        name = self.unique_name(new_name, ignore=scene)
        old_path = scene.path
        scene.name = name
        scene.path = None
        new_path = self.save(scene)
        if old_path is not None and old_path.exists() and old_path.resolve() != new_path.resolve():
            old_path.unlink()
        return name


def _slug(name: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in name.strip().lower()]
    return "".join(keep).strip("-") or "scene"
