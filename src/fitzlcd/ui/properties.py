"""The inspector: edits the selected layer.

Forms are generated from each layer's ``FIELDS`` declaration, so adding a layer
type never means writing new UI code - which is the whole point of layers
describing their own schema. Fields are grouped by :attr:`Field.group` into
Content / Position & size / Appearance, with rarely needed ones in a collapsed
Advanced section. Field kinds pick a friendly editor from ``ui/controls.py``;
unknown kinds fall back to a plain text box.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFileDialog,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QScrollArea,
    QSpinBox,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from fitzlcd.render.scene import Field, Layer
from fitzlcd.sources.media import SUPPORTED_SUFFIXES
from fitzlcd.ui import icons
from fitzlcd.ui.controls import (
    AnchorPicker,
    ColorButton,
    FontPicker,
    InsertValueButton,
    LengthsEdit,
    MetricPicker,
    PercentSlider,
    Section,
    parse_lengths,
)
from fitzlcd.ui.theme import COLORS

__all__ = ["ColorButton", "PathEdit", "PropertiesPane"]

#: Section order and titles.
SECTIONS = (
    ("content", "Content"),
    ("layout", "Position & size"),
    ("appearance", "Appearance"),
    ("advanced", "Advanced"),
)

#: Shown in the header or the layer list instead of as form rows.
HEADER_FIELDS = frozenset({"name", "visible"})

#: Friendlier choice labels; the stored value is unchanged.
CHOICE_LABELS = {
    "any": "Both orientations",
    "landscape": "Landscape only",
    "portrait": "Portrait only",
}


class PathEdit(QWidget):
    """A file field with a Choose button for media paths."""

    changed = Signal(str)

    def __init__(self, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(value)
        self._edit.setPlaceholderText("No file chosen")
        self._edit.editingFinished.connect(lambda: self.changed.emit(self._edit.text()))
        browse = QPushButton("Choose…")
        browse.clicked.connect(self._browse)
        layout.addWidget(self._edit, 1)
        layout.addWidget(browse)

    def _browse(self) -> None:
        patterns = " ".join(f"*{s}" for s in sorted(SUPPORTED_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(
            self,
            "Choose an image or video",
            self._edit.text(),
            f"Media ({patterns});;All files (*)",
        )
        if path:
            self._edit.setText(path)
            self.changed.emit(path)


class PropertiesPane(QScrollArea):
    """Edits the selected layer, one row per declared field."""

    #: Emitted with the field name just before a value is written, so the
    #: owner can snapshot the scene for undo.
    about_to_change = Signal(str)
    #: Emitted with the field name after the layer was updated.
    layer_changed = Signal(str)

    def __init__(
        self,
        parent: QWidget | None = None,
        metrics_source: Callable[[], Mapping[str, Any]] | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.metrics_source = metrics_source or dict
        self._layer: Layer | None = None
        #: Field name -> editor widget, for the current layer.
        self.editors: dict[str, QWidget] = {}
        self.sections: dict[str, Section] = {}
        self._body: QWidget | None = None
        self.show_layer(None)

    # ------------------------------------------------------------------ build

    def show_layer(self, layer: Layer | None) -> None:
        self._layer = layer
        self.editors = {}
        self.sections = {}
        body = QWidget()
        column = QVBoxLayout(body)
        column.setContentsMargins(4, 4, 8, 12)
        column.setSpacing(14)

        if layer is None:
            empty = QLabel("Select a layer to change how it looks.")
            empty.setObjectName("muted")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setWordWrap(True)
            column.addStretch(1)
            column.addWidget(empty)
            column.addStretch(2)
        else:
            column.addLayout(self._build_header(layer))
            metrics = self._metrics()
            by_group: dict[str, list[Field]] = {}
            for spec in layer.FIELDS:
                if spec.name not in HEADER_FIELDS:
                    by_group.setdefault(spec.group, []).append(spec)
            for key, title in SECTIONS:
                specs = by_group.get(key)
                if not specs:
                    continue
                section = Section(title, expanded=key != "advanced")
                form = QFormLayout(section.body)
                form.setContentsMargins(0, 0, 0, 0)
                form.setHorizontalSpacing(12)
                form.setVerticalSpacing(8)
                form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
                for spec in specs:
                    widget = self._widget_for(spec, getattr(layer, spec.name), metrics)
                    if widget is None:
                        continue
                    self.editors[spec.name] = widget
                    label = QLabel(spec.title)
                    label.setObjectName("muted")
                    if spec.help:
                        label.setToolTip(spec.help)
                        widget.setToolTip(widget.toolTip() or spec.help)
                    if spec.kind in ("point", "rect", "multiline"):
                        label.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
                    form.addRow(label, widget)
                self.sections[key] = section
                column.addWidget(section)
            column.addStretch(1)

        # setWidget() deletes the previous body itself.
        self._body = body
        self.setWidget(body)

    def _build_header(self, layer: Layer) -> QVBoxLayout:
        header = QVBoxLayout()
        header.setSpacing(6)
        row = QHBoxLayout()
        badge = QLabel()
        badge.setPixmap(icons.pixmap(layer.icon, COLORS["accent"], 18, self.devicePixelRatioF()))
        title = QLabel(layer.friendly_name())
        title.setObjectName("heading")
        row.addWidget(badge)
        row.addWidget(title, 1)
        header.addLayout(row)

        name = QLineEdit(layer.name)
        name.setPlaceholderText(f"Name (optional) – {layer.describe()}")
        name.editingFinished.connect(lambda e=name: self._apply_if_changed("name", e.text()))
        self.editors["name"] = name
        header.addWidget(name)
        return header

    def _metrics(self) -> Mapping[str, Any]:
        try:
            return self.metrics_source() or {}
        except Exception:  # noqa: BLE001 - a stats hiccup must not break editing
            return {}

    # ------------------------------------------------------------------ apply

    def _apply(self, name: str, value: Any) -> None:
        if self._layer is None:
            return
        self.about_to_change.emit(name)
        setattr(self._layer, name, value)
        self.layer_changed.emit(name)

    def _apply_if_changed(self, name: str, value: Any) -> None:
        if self._layer is not None and getattr(self._layer, name) != value:
            self._apply(name, value)

    def _apply_lengths(self, edit: QLineEdit, name: str, expected: int) -> None:
        """Parse a typed coordinate list, keeping relative values as written."""
        values = parse_lengths(edit.text(), expected)
        if values is None:
            return  # incomplete input: leave the layer alone rather than guessing
        self._apply(name, values)

    # ---------------------------------------------------------------- widgets

    def _widget_for(self, spec: Field, value: Any, metrics: Mapping[str, Any]) -> QWidget | None:
        setter: Callable[[Any], None] = lambda v, n=spec.name: self._apply(n, v)  # noqa: E731

        if spec.kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            box.toggled.connect(setter)
            return box

        if spec.kind == "number":
            unit_range = (
                spec.minimum is not None
                and spec.maximum is not None
                and spec.minimum >= 0
                and spec.maximum <= 1
            )
            if unit_range:
                slider = PercentSlider(float(value or 0), spec.minimum, spec.maximum)
                slider.changed.connect(setter)
                return slider
            fractional = isinstance(spec.default, float) or isinstance(value, float)
            if fractional:
                spin = QDoubleSpinBox()
                spin.setDecimals(2)
                spin.setSingleStep(0.1 if (spec.maximum or 100) <= 10 else 1)
            else:
                spin = QSpinBox()
            spin.setRange(
                spec.minimum if spec.minimum is not None else -1e6,
                spec.maximum if spec.maximum is not None else 1e6,
            )
            spin.setKeyboardTracking(False)
            spin.setValue(value or 0)
            spin.valueChanged.connect(setter)
            return spin

        if spec.kind == "choice":
            if spec.name == "anchor":
                picker = AnchorPicker(str(value))
                picker.changed.connect(setter)
                return picker
            combo = QComboBox()
            for choice in spec.choices:
                combo.addItem(
                    CHOICE_LABELS.get(choice, choice.replace("-", " ").capitalize()), choice
                )
            index = combo.findData(value)
            if index >= 0:
                combo.setCurrentIndex(index)
            combo.activated.connect(lambda i, c=combo: setter(c.itemData(i)))
            return combo

        if spec.kind == "color":
            button = ColorButton(str(value or "#000000"))
            button.changed.connect(setter)
            return button

        if spec.kind == "path":
            edit = PathEdit(str(value or ""))
            edit.changed.connect(setter)
            return edit

        if spec.kind in ("point", "rect"):
            # Coordinates are not necessarily numbers: "50%", "-40" and "center"
            # are all valid, which is what makes a scene survive rotation.
            allow_empty = spec.kind == "rect" and (not value or spec.default == [])
            lengths = LengthsEdit(value, spec.kind, allow_empty=allow_empty)
            lengths.changed.connect(setter)
            return lengths

        if spec.kind == "metric":
            picker = MetricPicker(str(value or ""), metrics)
            picker.changed.connect(setter)
            return picker

        if spec.kind == "font":
            font = FontPicker(str(value or ""))
            font.changed.connect(setter)
            return font

        if spec.kind == "multiline":
            edit = QTextEdit()
            edit.setAcceptRichText(False)
            edit.setPlainText(str(value or ""))
            edit.setFixedHeight(72)
            edit.textChanged.connect(lambda e=edit, n=spec.name: self._apply(n, e.toPlainText()))
            return self._with_insert(edit, metrics)

        edit = QLineEdit(str(value or ""))
        edit.editingFinished.connect(
            lambda e=edit, n=spec.name: self._apply_if_changed(n, e.text())
        )
        if spec.kind == "text" and spec.name in ("text", "sub_text"):
            return self._with_insert(edit, metrics)
        return edit

    @staticmethod
    def _with_insert(edit: QLineEdit | QTextEdit, metrics: Mapping[str, Any]) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(2)
        layout.addWidget(edit)
        row = QHBoxLayout()
        row.addStretch(1)
        row.addWidget(InsertValueButton(edit, metrics))
        layout.addLayout(row)
        box.setFocusProxy(edit)
        return box
