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
    window_geometry: list[int] = field(default_factory=list)

    @classmethod
    def load(cls, path: Path | None = None) -> AppConfig:
        target = path or config_path()
        if not target.exists():
            return cls()
        try:
            data = json.loads(target.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("could not read %s (%s); using defaults", target, exc)
            return cls()
        known = {f.name for f in fields(cls)}
        return cls(**{k: v for k, v in data.items() if k in known})

    def save(self, path: Path | None = None) -> Path:
        target = path or config_path()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(asdict(self), indent=2), encoding="utf-8")
        return target


class SceneLibrary:
    """The user's scenes on disk, seeded with defaults on first run."""

    def __init__(self, directory: Path | None = None) -> None:
        self.directory = directory or scenes_dir()

    def ensure_defaults(self) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        if any(self.directory.glob("*.json")):
            return
        for name, data in DEFAULT_SCENES.items():
            (self.directory / f"{name}.json").write_text(
                json.dumps(data, indent=2), encoding="utf-8"
            )
        log.info("seeded %d default scenes in %s", len(DEFAULT_SCENES), self.directory)

    def list(self) -> list[Scene]:
        scenes = []
        for path in sorted(self.directory.glob("*.json")):
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


def _slug(name: str) -> str:
    keep = [c if c.isalnum() or c in "-_" else "-" for c in name.strip().lower()]
    return "".join(keep).strip("-") or "scene"
