"""Scene and layer model.

A scene is an ordered stack of layers composited bottom-up into one frame. It is
plain JSON so scenes are hand-editable, diffable, and shareable.

Layers describe their own editable properties through :attr:`Layer.FIELDS`, which
is what the GUI's properties pane is generated from - adding a layer type never
requires new UI code.
"""

from __future__ import annotations

import json
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

if TYPE_CHECKING:
    from PIL import Image

    from fitzlcd.render.context import RenderContext

SCHEMA_VERSION = 1

#: Values for a layer's ``orientation`` filter.
ORIENTATIONS = ("any", "landscape", "portrait")


class SceneError(Exception):
    """Raised when a scene document is malformed."""


@dataclass(frozen=True)
class Field:
    """One editable layer property, used to build the GUI form."""

    name: str
    #: text | multiline | number | color | choice | path | point | rect | bool
    #: | metric | font. ``metric`` and ``font`` are stored as plain strings; the
    #: kind only picks a friendlier editor.
    kind: str
    label: str = ""
    default: Any = None
    choices: tuple[str, ...] = ()
    minimum: float | None = None
    maximum: float | None = None
    help: str = ""
    #: Which inspector section the field sits in: content | layout | appearance.
    #: Blank infers one from the kind.
    section: str = ""
    #: Rarely needed; tucked into the collapsed Advanced section.
    advanced: bool = False

    @property
    def title(self) -> str:
        return self.label or self.name.replace("_", " ").title()

    @property
    def group(self) -> str:
        """The inspector section this field belongs in."""
        if self.advanced:
            return "advanced"
        if self.section:
            return self.section
        if self.kind in ("point", "rect") or self.name == "anchor":
            return "layout"
        if self.kind == "color":
            return "appearance"
        return "content"


_LAYER_TYPES: dict[str, type[Layer]] = {}


def layer_type(name: str):
    """Register a layer class under its JSON ``type`` discriminator."""

    def decorate(cls: type[Layer]) -> type[Layer]:
        cls.type_name = name
        _LAYER_TYPES[name] = cls
        return cls

    return decorate


def layer_types() -> dict[str, type[Layer]]:
    return dict(_LAYER_TYPES)


@dataclass
class Layer(ABC):
    """Base class for everything that can draw into a frame."""

    #: JSON discriminator, set by the :func:`layer_type` decorator.
    type_name: ClassVar[str] = "layer"
    #: What the GUI calls this layer type; blank derives one from ``type_name``.
    display_name: ClassVar[str] = ""
    #: One line shown in the Add menu.
    summary: ClassVar[str] = ""
    #: Name of a GUI icon (see ``fitzlcd.ui.icons.PATHS``).
    icon: ClassVar[str] = "square"
    #: Editable properties, consumed by the GUI. Subclasses extend this.
    FIELDS: ClassVar[tuple[Field, ...]] = (
        Field("name", "text", "Name", ""),
        Field("visible", "bool", "Visible", True),
        Field(
            "orientation",
            "choice",
            "Show in",
            "any",
            choices=ORIENTATIONS,
            help="Restrict this layer to one panel orientation",
            advanced=True,
        ),
        Field("opacity", "number", "Opacity", 1.0, minimum=0.0, maximum=1.0, advanced=True),
    )

    name: str = ""
    visible: bool = True
    orientation: str = "any"
    opacity: float = 1.0

    def applies_to(self, size: tuple[int, int]) -> bool:
        """Whether this layer should be drawn at this frame geometry.

        A scene that needs genuinely different content when the panel is turned
        on its side can carry both versions and tag each one, rather than being
        duplicated wholesale.
        """
        if self.orientation == "any":
            return True
        portrait = size[1] > size[0]
        return self.orientation == ("portrait" if portrait else "landscape")

    @abstractmethod
    def draw(self, canvas: Image.Image, ctx: RenderContext) -> None:
        """Draw onto the RGBA ``canvas`` in place."""

    @property
    def is_dynamic(self) -> bool:
        """Whether this layer can change between frames.

        A scene of entirely static layers is composed once and never re-encoded,
        which is what makes a still wallpaper cost almost nothing.
        """
        return False

    def describe(self) -> str:
        """Short label for the layer list."""
        return self.name or self.type_name

    @classmethod
    def friendly_name(cls) -> str:
        return cls.display_name or cls.type_name.replace("_", " ").capitalize()

    # ---------------------------------------------------------------- JSON

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"type": self.type_name}
        for f in self.FIELDS:
            value = getattr(self, f.name)
            if isinstance(value, tuple):
                value = list(value)
            data[f.name] = value
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Layer:
        type_name = data.get("type")
        if type_name not in _LAYER_TYPES:
            known = ", ".join(sorted(_LAYER_TYPES)) or "(none registered)"
            raise SceneError(f"unknown layer type {type_name!r}; known types: {known}")
        target = _LAYER_TYPES[type_name]
        kwargs = {}
        for f in target.FIELDS:
            if f.name in data:
                kwargs[f.name] = data[f.name]
        try:
            return target(**kwargs)
        except TypeError as exc:
            raise SceneError(f"bad {type_name} layer: {exc}") from exc


@dataclass
class Scene:
    """An ordered layer stack plus its playback settings."""

    name: str = "Untitled"
    fps: int = 30
    background: str = "#000000"
    layers: list[Layer] = field(default_factory=list)
    path: Path | None = None

    @property
    def is_dynamic(self) -> bool:
        return any(layer.is_dynamic for layer in self.layers if layer.visible)

    @property
    def visible_layers(self) -> list[Layer]:
        return [layer for layer in self.layers if layer.visible]

    # ---------------------------------------------------------------- JSON

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": SCHEMA_VERSION,
            "name": self.name,
            "fps": self.fps,
            "background": self.background,
            "layers": [layer.to_dict() for layer in self.layers],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any], path: Path | None = None) -> Scene:
        if not isinstance(data, dict):
            raise SceneError("scene document must be an object")
        version = data.get("version", SCHEMA_VERSION)
        if version > SCHEMA_VERSION:
            raise SceneError(
                f"scene needs schema version {version}, this build understands {SCHEMA_VERSION}"
            )
        raw_layers = data.get("layers", [])
        if not isinstance(raw_layers, list):
            raise SceneError("'layers' must be a list")
        fps = int(data.get("fps", 30))
        if fps < 1:
            raise SceneError(f"fps must be >= 1, got {fps}")
        return cls(
            name=str(data.get("name", "Untitled")),
            fps=fps,
            background=str(data.get("background", "#000000")),
            layers=[Layer.from_dict(item) for item in raw_layers],
            path=path,
        )

    def save(self, path: Path | None = None) -> Path:
        target = Path(path or self.path or f"{self.name}.json")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        self.path = target
        return target

    @classmethod
    def load(cls, path: Path | str) -> Scene:
        path = Path(path)
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise SceneError(f"{path.name} is not valid JSON: {exc}") from exc
        return cls.from_dict(data, path=path)
