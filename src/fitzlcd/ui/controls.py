"""Friendly editors for the inspector.

Each one hides a piece of scene syntax behind a control: coordinates written
as ``"-10%"`` or ``"center"`` become a number plus a unit, metric keys become a
named list, ``{cpu.load:.0f}%`` tokens are inserted from a menu. Every editor
still reads and writes exactly the JSON values the renderer understands, so a
hand-edited scene and a GUI-edited one are indistinguishable.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QFontDatabase, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import (
    QButtonGroup,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QDoubleSpinBox,
    QGridLayout,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QLineEdit,
    QMenu,
    QPushButton,
    QSlider,
    QTextEdit,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fitzlcd.render.geometry import ANCHORS, CENTER_KEYWORDS
from fitzlcd.sources import catalog
from fitzlcd.ui import icons
from fitzlcd.ui.theme import COLORS

MetricsSource = Callable[[], Mapping[str, Any]]

# ------------------------------------------------------------------ lengths


def split_length(value: Any) -> tuple[float, str]:
    """``40`` -> (40, "px"), ``"-10%"`` -> (-10, "%"), ``"center"`` -> (0, "center")."""
    if isinstance(value, bool):
        return float(value), "px"
    if isinstance(value, int | float):
        return float(value), "px"
    text = str(value).strip().lower()
    if text in CENTER_KEYWORDS:
        return 0.0, "center"
    if text.endswith("%"):
        try:
            return float(text[:-1]), "%"
        except ValueError:
            return 0.0, "%"
    try:
        return float(text), "px"
    except ValueError:
        return 0.0, "px"


def join_length(number: float, unit: str) -> int | str:
    if unit == "center":
        return "center"
    if unit == "%":
        return f"{number:g}%"
    return int(round(number))


def parse_lengths(text: str, expected: int) -> list[int | str] | None:
    """Parse ``"10, 50%, center"``; None if the count is wrong.

    Plain numbers become ints so scenes stay tidy; anything else ("50%",
    "center") is stored verbatim for the geometry resolver to interpret.
    """
    text = text.strip()
    if not text:
        return []
    parts = [part.strip() for part in text.replace(";", ",").split(",") if part.strip()]
    if len(parts) != expected:
        return None
    values: list[int | str] = []
    for part in parts:
        try:
            values.append(int(float(part)))
        except ValueError:
            values.append(part)
    return values


class LengthEdit(QWidget):
    """One coordinate: a number and a unit (pixels, percent, or centred)."""

    changed = Signal(object)

    UNITS = (("px", "px"), ("%", "%"), ("Centered", "center"))

    def __init__(self, value: Any, allow_center: bool = True, parent=None) -> None:
        super().__init__(parent)
        self._original = value
        number, unit = split_length(value)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.spin = QDoubleSpinBox()
        self.spin.setRange(-100_000, 100_000)
        self.spin.setKeyboardTracking(False)
        self.spin.setToolTip("Negative values measure from the far edge")
        self.unit = QComboBox()
        for label, data in self.UNITS:
            if data == "center" and not allow_center:
                continue
            self.unit.addItem(label, data)
        layout.addWidget(self.spin, 1)
        layout.addWidget(self.unit)

        self._set(number, unit)
        self.spin.valueChanged.connect(self._emit)
        self.unit.currentIndexChanged.connect(self._on_unit)

    def _set(self, number: float, unit: str) -> None:
        index = self.unit.findData(unit)
        self.unit.setCurrentIndex(max(0, index))
        self._configure(unit)
        self.spin.setValue(number)

    def _configure(self, unit: str) -> None:
        self.spin.setEnabled(unit != "center")
        self.spin.setDecimals(1 if unit == "%" else 0)
        self.spin.setSingleStep(1)

    def _on_unit(self) -> None:
        self._configure(self.unit.currentData())
        self._emit()

    def value(self) -> int | str:
        return join_length(self.spin.value(), self.unit.currentData())

    def _emit(self) -> None:
        self.changed.emit(self.value())


class LengthsEdit(QWidget):
    """A point (X, Y) or rect (X, Y, width, height) as labelled LengthEdits."""

    changed = Signal(list)

    POINT = ("Across", "Down")
    RECT = ("Across", "Down", "Width", "Height")

    def __init__(self, value: list | None, kind: str, allow_empty: bool = False, parent=None):
        super().__init__(parent)
        labels = self.POINT if kind == "point" else self.RECT
        values = list(value or [])
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.fill: QCheckBox | None = None
        if allow_empty:
            self.fill = QCheckBox("Fill the whole display")
            self.fill.setChecked(not values)
            self.fill.toggled.connect(self._on_fill)
            layout.addWidget(self.fill)

        defaults = [0, 0, "100%", "100%"]
        grid = QGridLayout()
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(6)
        grid.setVerticalSpacing(4)
        self.edits: list[LengthEdit] = []
        for i, label in enumerate(labels):
            current = values[i] if i < len(values) else defaults[i]
            edit = LengthEdit(current, allow_center=i < 2)
            edit.changed.connect(self._emit)
            caption = QLabel(label)
            caption.setObjectName("muted")
            grid.addWidget(caption, i, 0)
            grid.addWidget(edit, i, 1)
            self.edits.append(edit)
        self._grid_host = QWidget()
        self._grid_host.setLayout(grid)
        layout.addWidget(self._grid_host)
        self._grid_host.setVisible(not (self.fill and self.fill.isChecked()))

    def value(self) -> list:
        if self.fill is not None and self.fill.isChecked():
            return []
        return [edit.value() for edit in self.edits]

    def _on_fill(self, checked: bool) -> None:
        self._grid_host.setVisible(not checked)
        self._emit()

    def _emit(self, *_args) -> None:
        self.changed.emit(self.value())


# ------------------------------------------------------------------ anchor


class AnchorPicker(QWidget):
    """Nine dots: which corner, edge or centre a position is measured from."""

    changed = Signal(str)

    def __init__(self, value: str, parent=None) -> None:
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setSpacing(2)
        self.group = QButtonGroup(self)
        self.group.setExclusive(True)
        self.buttons: dict[str, QToolButton] = {}
        for i, anchor in enumerate(ANCHORS):
            button = QToolButton()
            button.setCheckable(True)
            button.setFixedSize(22, 22)
            button.setToolTip(anchor.replace("-", " ").replace("middle center", "centre"))
            button.setStyleSheet(
                "QToolButton { border: none; border-radius: 4px;"
                f" background: {COLORS['surface_alt']}; }}"
                f"QToolButton:hover {{ background: {COLORS['surface_hover']}; }}"
                f"QToolButton:checked {{ background: {COLORS['accent']}; }}"
            )
            self.group.addButton(button, i)
            self.buttons[anchor] = button
            grid.addWidget(button, i // 3, i % 3)
        grid.setColumnStretch(3, 1)
        self.set_value(value)
        self.group.idClicked.connect(lambda i: self.changed.emit(ANCHORS[i]))

    def set_value(self, value: str) -> None:
        button = self.buttons.get(str(value)) or self.buttons["top-left"]
        button.setChecked(True)

    def value(self) -> str:
        return ANCHORS[self.group.checkedId()]


# ------------------------------------------------------------------ metrics


class MetricPicker(QComboBox):
    """Choose a reading by name; stores the raw metric key."""

    changed = Signal(str)
    CUSTOM = "__custom__"

    def __init__(self, value: str, metrics: Mapping[str, Any] | None = None, parent=None):
        super().__init__(parent)
        self.setMaxVisibleItems(20)
        metrics = metrics or {}
        infos = catalog.catalog(metrics.keys())
        if value and value not in {m.key for m in infos}:
            infos.append(catalog.describe(value))
        group = None
        for info in infos:
            if info.group != group:
                group = info.group
                self._add_header(group)
            label = info.label
            if info.key in metrics:
                label = f"{label}  ·  {info.format(metrics[info.key])}"
            self.addItem(label, info.key)
            self.setItemData(self.count() - 1, info.key, Qt.ItemDataRole.ToolTipRole)
        self.insertSeparator(self.count())
        self.addItem("Other reading…", self.CUSTOM)

        index = self.findData(value)
        self.setCurrentIndex(index if index >= 0 else -1)
        self._last = value
        self.activated.connect(self._on_activated)

    def _add_header(self, title: str) -> None:
        self.addItem(title.upper())
        item = self.model().item(self.count() - 1)
        item.setEnabled(False)
        item.setForeground(QBrush(QColor(COLORS["muted"])))

    def value(self) -> str:
        return self._last

    def _on_activated(self, index: int) -> None:
        key = self.itemData(index)
        if key == self.CUSTOM:
            key, ok = QInputDialog.getText(
                self, "Other reading", "Metric name (for example cpu.load):", text=self._last
            )
            key = key.strip()
            if not ok or not key:
                self.setCurrentIndex(self.findData(self._last))
                return
            if self.findData(key) < 0:
                self.insertItem(self.count() - 2, key, key)
            self.setCurrentIndex(self.findData(key))
        if key and key != self._last:
            self._last = key
            self.changed.emit(key)


class InsertValueButton(QToolButton):
    """A menu of live readings; picking one inserts its token at the cursor."""

    def __init__(self, target: QLineEdit | QTextEdit, metrics: Mapping[str, Any] | None = None):
        super().__init__()
        self.target = target
        self.setText("Insert live value")
        self.setIcon(icons.icon("plus", COLORS["accent"]))
        self.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.setStyleSheet(f"QToolButton {{ color: {COLORS['accent']}; padding: 2px 4px; }}")
        metrics = metrics or {}
        menu = QMenu(self)
        submenus: dict[str, QMenu] = {}
        for info in catalog.catalog(metrics.keys()):
            if info.group not in submenus:
                submenus[info.group] = menu.addMenu(info.group)
            label = info.label
            if info.key in metrics:
                label = f"{label}   ({info.format(metrics[info.key])})"
            action = submenus[info.group].addAction(label)
            action.triggered.connect(lambda _c=False, t=info.token: self.insert(t))
        self.setMenu(menu)

    def insert(self, token: str) -> None:
        if isinstance(self.target, QLineEdit):
            self.target.insert(token)
            self.target.editingFinished.emit()
        else:
            self.target.insertPlainText(token)
        self.target.setFocus()


# ------------------------------------------------------------------ fonts


class FontPicker(QComboBox):
    """Installed font families, plus the renderer's portable aliases."""

    changed = Signal(str)
    ALIASES = (("sans", "Default sans-serif"), ("mono", "Default monospace"))

    def __init__(self, value: str, parent=None) -> None:
        super().__init__(parent)
        self.setEditable(True)
        self.setInsertPolicy(QComboBox.InsertPolicy.NoInsert)
        self.setMaxVisibleItems(20)
        for alias, label in self.ALIASES:
            self.addItem(label, alias)
        self.insertSeparator(self.count())
        for family in QFontDatabase.families():
            if not family.startswith("@"):
                self.addItem(family, family)
        index = self.findData(value)
        if index < 0 and value:
            self.addItem(value, value)
            index = self.count() - 1
        self.setCurrentIndex(index)
        self._last = value
        self.activated.connect(lambda _i: self._commit())
        self.lineEdit().editingFinished.connect(self._commit)

    def value(self) -> str:
        return self._last

    def _commit(self) -> None:
        text = self.currentText().strip()
        index = self.findText(text, Qt.MatchFlag.MatchFixedString)
        value = self.itemData(index) if index >= 0 else text
        if value and value != self._last:
            self._last = value
            self.changed.emit(value)


# ------------------------------------------------------------------ colour


def _swatch(value: str, size: int = 16) -> QPixmap:
    px = QPixmap(size, size)
    px.fill(Qt.GlobalColor.transparent)
    painter = QPainter(px)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    # A checkerboard under the colour, so transparency is visible.
    half = size // 2
    painter.fillRect(0, 0, size, size, QColor("#d0d0d0"))
    painter.fillRect(0, 0, half, half, QColor("#9a9a9a"))
    painter.fillRect(half, half, half, half, QColor("#9a9a9a"))
    painter.fillRect(0, 0, size, size, QColor(_qt_color(value)))
    painter.setPen(QColor(COLORS["border_strong"]))
    painter.drawRect(0, 0, size - 1, size - 1)
    painter.end()
    return px


def _qt_color(value: str) -> QColor:
    """Scenes write #RRGGBBAA; Qt reads #AARRGGBB."""
    text = (value or "#000000").strip()
    if len(text) == 9 and text.startswith("#"):
        return QColor(f"#{text[7:9]}{text[1:7]}")
    return QColor(text)


class ColorButton(QPushButton):
    """A swatch that opens a colour picker; keeps the #RRGGBBAA text form."""

    changed = Signal(str)

    def __init__(self, value: str, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._value = value or "#000000"
        self.setStyleSheet("text-align: left; padding-left: 8px;")
        self.clicked.connect(self._pick)
        self._refresh()

    def value(self) -> str:
        return self._value

    def _refresh(self) -> None:
        self.setIcon(QIcon(_swatch(self._value)))
        self.setText(self._value.upper())

    def _pick(self) -> None:
        chosen = QColorDialog.getColor(
            _qt_color(self._value),
            self,
            "Choose a colour",
            QColorDialog.ColorDialogOption.ShowAlphaChannel,
        )
        if not chosen.isValid():
            return
        self._value = f"#{chosen.red():02X}{chosen.green():02X}{chosen.blue():02X}" + (
            f"{chosen.alpha():02X}" if chosen.alpha() < 255 else ""
        )
        self._refresh()
        self.changed.emit(self._value)


# ------------------------------------------------------------------ numbers


class PercentSlider(QWidget):
    """A 0-1 fraction shown as a 0-100% slider."""

    changed = Signal(float)

    def __init__(self, value: float, minimum: float = 0.0, maximum: float = 1.0, parent=None):
        super().__init__(parent)
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(int(minimum * 100), int(maximum * 100))
        self.slider.setValue(int(round((value or 0) * 100)))
        self.label = QLabel()
        self.label.setObjectName("muted")
        self.label.setFixedWidth(40)
        self.label.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        layout.addWidget(self.slider, 1)
        layout.addWidget(self.label)
        self._show(self.slider.value())
        self.slider.valueChanged.connect(self._on_value)

    def _show(self, value: int) -> None:
        self.label.setText(f"{value}%")

    def _on_value(self, value: int) -> None:
        self._show(value)
        self.changed.emit(value / 100)

    def value(self) -> float:
        return self.slider.value() / 100


# ------------------------------------------------------------------ sections


class Section(QWidget):
    """A titled, collapsible group of inspector rows."""

    def __init__(self, title: str, expanded: bool = True, parent=None) -> None:
        super().__init__(parent)
        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(6)
        self.header = QToolButton()
        self.header.setText(title.upper().replace("&", "&&"))
        self.header.setCheckable(True)
        self.header.setChecked(expanded)
        self.header.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.header.setStyleSheet(
            f"QToolButton {{ color: {COLORS['muted']}; font-size: 8pt; font-weight: 600;"
            " padding: 2px 0; border: none; background: transparent; }"
        )
        self.header.toggled.connect(self._on_toggled)
        self.body = QWidget()
        outer.addWidget(self.header)
        outer.addWidget(self.body)
        self._on_toggled(expanded)

    def _on_toggled(self, expanded: bool) -> None:
        self.header.setIcon(
            icons.icon("chevron-down" if expanded else "chevron-right", COLORS["muted"])
        )
        self.body.setVisible(expanded)
