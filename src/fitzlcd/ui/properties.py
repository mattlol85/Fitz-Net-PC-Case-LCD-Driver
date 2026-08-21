"""The properties pane.

Forms are generated from each layer's ``FIELDS`` declaration, so adding a layer
type never means writing new UI code - which is the whole point of layers
describing their own schema.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QColorDialog,
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
    QWidget,
)

from fitzlcd.render.scene import Field, Layer
from fitzlcd.sources.media import SUPPORTED_SUFFIXES


class ColorButton(QPushButton):
    """A swatch that opens a colour picker; keeps the #RRGGBBAA text form."""

    changed = Signal(str)

    def __init__(self, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = value or "#000000"
        self.setFixedHeight(26)
        self.clicked.connect(self._pick)
        self._refresh()

    def value(self) -> str:
        return self._value

    def _refresh(self) -> None:
        color = QColor(self._value)
        readable = "#000000" if color.lightness() > 128 else "#FFFFFF"
        self.setText(self._value)
        self.setStyleSheet(
            f"background-color: {self._value}; color: {readable};"
            "border: 1px solid #333; border-radius: 3px;"
        )

    def _pick(self) -> None:
        initial = QColor(self._value)
        chosen = QColorDialog.getColor(
            initial, self, "Choose colour", QColorDialog.ColorDialogOption.ShowAlphaChannel
        )
        if not chosen.isValid():
            return
        self._value = f"#{chosen.red():02X}{chosen.green():02X}{chosen.blue():02X}" + (
            f"{chosen.alpha():02X}" if chosen.alpha() < 255 else ""
        )
        self._refresh()
        self.changed.emit(self._value)


class PathEdit(QWidget):
    """A text field with a Browse button for media paths."""

    changed = Signal(str)

    def __init__(self, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._edit = QLineEdit(value)
        self._edit.editingFinished.connect(lambda: self.changed.emit(self._edit.text()))
        browse = QPushButton("...")
        browse.setFixedWidth(32)
        browse.clicked.connect(self._browse)
        layout.addWidget(self._edit, 1)
        layout.addWidget(browse)

    def _browse(self) -> None:
        patterns = " ".join(f"*{s}" for s in sorted(SUPPORTED_SUFFIXES))
        path, _ = QFileDialog.getOpenFileName(
            self, "Choose media", self._edit.text(), f"Media ({patterns});;All files (*)"
        )
        if path:
            self._edit.setText(path)
            self.changed.emit(path)


class PropertiesPane(QScrollArea):
    """Edits the selected layer, one row per declared field."""

    layer_changed = Signal()

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWidgetResizable(True)
        self._layer: Layer | None = None
        self._body = QWidget()
        self._form = QFormLayout(self._body)
        self._form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        self.setWidget(self._body)
        self.show_layer(None)

    def show_layer(self, layer: Layer | None) -> None:
        self._layer = layer
        while self._form.rowCount():
            self._form.removeRow(0)

        if layer is None:
            self._form.addRow(QLabel("Select a layer to edit its properties."))
            return

        self._form.addRow("Type", QLabel(f"<b>{layer.type_name}</b>"))
        for spec in layer.FIELDS:
            widget = self._widget_for(spec, getattr(layer, spec.name))
            if widget is not None:
                self._form.addRow(spec.title, widget)

    def _apply(self, name: str, value: Any) -> None:
        if self._layer is None:
            return
        setattr(self._layer, name, value)
        self.layer_changed.emit()

    def _widget_for(self, spec: Field, value: Any) -> QWidget | None:
        setter: Callable[[Any], None] = lambda v, n=spec.name: self._apply(n, v)  # noqa: E731

        if spec.kind == "bool":
            box = QCheckBox()
            box.setChecked(bool(value))
            box.toggled.connect(setter)
            return box

        if spec.kind == "number":
            fractional = isinstance(spec.default, float) or isinstance(value, float)
            if fractional:
                spin = QDoubleSpinBox()
                spin.setDecimals(2)
                spin.setSingleStep(0.05)
            else:
                spin = QSpinBox()
            spin.setRange(
                spec.minimum if spec.minimum is not None else -1e6,
                spec.maximum if spec.maximum is not None else 1e6,
            )
            spin.setValue(value or 0)
            spin.valueChanged.connect(setter)
            return spin

        if spec.kind == "choice":
            combo = QComboBox()
            combo.addItems(list(spec.choices))
            if value in spec.choices:
                combo.setCurrentText(str(value))
            combo.currentTextChanged.connect(setter)
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
            edit = QLineEdit(", ".join(str(v) for v in (value or ())))
            expected = 2 if spec.kind == "point" else 4
            edit.setPlaceholderText(
                "x, y  (px, -px, 50%, center)"
                if expected == 2
                else "x, y, w, h  (px, -px, 50%, center)"
            )
            edit.setToolTip(spec.help)
            edit.editingFinished.connect(
                lambda e=edit, n=spec.name, k=expected: self._apply_lengths(e, n, k)
            )
            return edit

        if spec.kind == "multiline":
            edit = QTextEdit(str(value or ""))
            edit.setFixedHeight(70)
            edit.setToolTip(spec.help)
            edit.textChanged.connect(lambda e=edit, n=spec.name: self._apply(n, e.toPlainText()))
            return edit

        edit = QLineEdit(str(value or ""))
        edit.setToolTip(spec.help)
        edit.editingFinished.connect(lambda e=edit, n=spec.name: self._apply(n, e.text()))
        return edit

    def _apply_lengths(self, edit: QLineEdit, name: str, expected: int) -> None:
        """Parse a coordinate list, keeping relative values as written.

        Plain numbers become ints so scenes stay tidy; anything else ("50%",
        "center") is stored verbatim for the geometry resolver to interpret.
        """
        text = edit.text().strip()
        if not text:
            self._apply(name, [])
            return

        parts = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
        if len(parts) != expected:
            return  # incomplete input: leave the layer alone rather than guessing

        values: list[int | str] = []
        for part in parts:
            try:
                values.append(int(float(part)))
            except ValueError:
                values.append(part)
        self._apply(name, values)
