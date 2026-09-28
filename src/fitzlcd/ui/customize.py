"""The Customize view: the scene editor.

Layers on the left (top of the list draws on top), the inspector on the right,
the live preview above both. Every change saves immediately and can be undone.

Layer rows are identified by ``id(layer)`` stored on the item, never by their
label: two layers can share a label (a duplicate starts identical), and a
label-keyed lookup silently collapses them.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from typing import Any

from PySide6.QtCore import QEvent, QModelIndex, QRect, QSize, Qt, Signal
from PySide6.QtGui import QColor, QIcon, QKeySequence, QPainter, QShortcut
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMenu,
    QPushButton,
    QSplitter,
    QStyle,
    QStyledItemDelegate,
    QStyleOptionViewItem,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from fitzlcd.config import SceneLibrary
from fitzlcd.engine import RenderEngine
from fitzlcd.render.scene import Layer, Scene, layer_types
from fitzlcd.ui import icons
from fitzlcd.ui.history import SceneHistory
from fitzlcd.ui.properties import PropertiesPane
from fitzlcd.ui.theme import COLORS

LAYER_ID = Qt.ItemDataRole.UserRole
TYPE_NAME = Qt.ItemDataRole.UserRole + 1
VISIBLE = Qt.ItemDataRole.UserRole + 2

#: Order of the Add menu: everyday layers first, game-specific ones last.
ADD_ORDER = (
    "text",
    "clock",
    "gauge",
    "donut",
    "sparkline",
    "media",
    "solid",
    "spark",
    "cs2_hit_timeline",
    "cs2_hit_flash",
)


def card(*, margins: int = 12) -> tuple[QFrame, QVBoxLayout]:
    frame = QFrame()
    frame.setObjectName("card")
    layout = QVBoxLayout(frame)
    layout.setContentsMargins(margins, margins, margins, margins)
    layout.setSpacing(8)
    return frame, layout


def section_label(text: str) -> QLabel:
    label = QLabel(text.upper())
    label.setObjectName("section")
    return label


def tool_button(icon: str, tip: str, slot: Callable[[], Any]) -> QToolButton:
    button = QToolButton()
    button.setIcon(icons.icon(icon))
    button.setIconSize(QSize(16, 16))
    button.setToolTip(tip)
    button.setAutoRaise(True)
    button.clicked.connect(lambda _c=False: slot())
    return button


class LayerDelegate(QStyledItemDelegate):
    """Row: type icon, name, muted type label, and a visibility eye."""

    visibility_clicked = Signal(int)
    ROW_HEIGHT = 34
    EYE = 28

    def sizeHint(self, option, index) -> QSize:  # noqa: N802 - Qt naming
        return QSize(option.rect.width(), self.ROW_HEIGHT)

    def _eye_rect(self, rect: QRect) -> QRect:
        return QRect(rect.right() - self.EYE - 4, rect.top(), self.EYE, rect.height())

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        rect = option.rect.adjusted(2, 1, -2, -1)
        selected = bool(option.state & QStyle.StateFlag.State_Selected)
        hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)
        if selected or hovered:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(COLORS["accent_soft" if selected else "surface_hover"]))
            painter.drawRoundedRect(rect, 6, 6)

        visible = bool(index.data(VISIBLE))
        text_color = QColor(COLORS["text" if visible else "muted_dim"])
        icon: QIcon = index.data(Qt.ItemDataRole.DecorationRole)
        if icon is not None:
            icon.paint(painter, QRect(rect.left() + 8, rect.center().y() - 8, 16, 16))

        eye = self._eye_rect(rect)
        eye_icon = icons.icon("eye" if visible else "eye-off", COLORS["muted"])
        eye_icon.paint(painter, QRect(eye.center().x() - 8, eye.center().y() - 8, 16, 16))

        type_text = str(index.data(TYPE_NAME) or "")
        metrics = option.fontMetrics
        type_width = min(metrics.horizontalAdvance(type_text) + 8, rect.width() // 3)
        type_rect = QRect(eye.left() - type_width - 4, rect.top(), type_width, rect.height())
        painter.setPen(QColor(COLORS["muted_dim"]))
        painter.drawText(
            type_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, type_text
        )

        name_rect = QRect(
            rect.left() + 32, rect.top(), type_rect.left() - rect.left() - 36, rect.height()
        )
        name = metrics.elidedText(
            str(index.data(Qt.ItemDataRole.DisplayRole) or ""),
            Qt.TextElideMode.ElideRight,
            name_rect.width(),
        )
        painter.setPen(text_color)
        painter.drawText(
            name_rect, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, name
        )
        painter.restore()

    def editorEvent(self, event, model, option, index) -> bool:  # noqa: N802 - Qt naming
        if event.type() == QEvent.Type.MouseButtonRelease and self._eye_rect(
            option.rect.adjusted(2, 1, -2, -1)
        ).contains(event.position().toPoint()):
            self.visibility_clicked.emit(index.row())
            return True
        return super().editorEvent(event, model, option, index)


class CustomizeView(QWidget):
    """Edits one scene's layers."""

    #: The user is finished; go back to the scene gallery.
    done = Signal()
    #: The scene was saved (thumbnails and the tray may want refreshing).
    scene_edited = Signal()
    #: The user typed a new scene name.
    rename_requested = Signal(str)

    def __init__(
        self,
        engine: RenderEngine,
        library: SceneLibrary,
        metrics_source: Callable[[], Mapping[str, Any]] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.engine = engine
        self.library = library
        self.scene: Scene | None = None
        self.history = SceneHistory()
        #: The composed frame size, so layers for the other orientation can
        #: be tucked away. Set by the main window.
        self.frame_size: tuple[int, int] = (1920, 462)
        self._shown: list[Layer] = []
        self._build(metrics_source)

    # ------------------------------------------------------------------ build

    def _build(self, metrics_source) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(6)
        back = QPushButton("All scenes")
        back.setIcon(icons.icon("chevron-left"))
        back.setToolTip("Back to your scenes (Esc)")
        back.clicked.connect(self.done.emit)
        self.title_edit = QLineEdit()
        self.title_edit.setObjectName("inlineTitle")
        self.title_edit.setToolTip("Click to rename this scene")
        self.title_edit.editingFinished.connect(self._on_title_edited)
        self.undo_button = tool_button("undo", "Undo (Ctrl+Z)", self.undo)
        self.redo_button = tool_button("redo", "Redo (Ctrl+Shift+Z)", self.redo)
        done = QPushButton("Done")
        done.setObjectName("primary")
        done.clicked.connect(self.done.emit)
        top.addWidget(back)
        top.addSpacing(6)
        top.addWidget(self.title_edit, 1)
        top.addWidget(self.undo_button)
        top.addWidget(self.redo_button)
        top.addSpacing(6)
        top.addWidget(done)
        root.addLayout(top)

        #: The main window drops the shared preview card in here.
        self.preview_slot = QVBoxLayout()
        self.preview_slot.setContentsMargins(0, 0, 0, 0)
        root.addLayout(self.preview_slot)

        splitter = QSplitter(Qt.Orientation.Horizontal)
        splitter.setHandleWidth(10)
        splitter.setChildrenCollapsible(False)
        splitter.addWidget(self._build_layers())

        inspector, inspector_layout = card()
        inspector_layout.addWidget(section_label("Layer"))
        self.properties = PropertiesPane(metrics_source=metrics_source)
        self.properties.about_to_change.connect(self._on_about_to_change)
        self.properties.layer_changed.connect(self._on_layer_edited)
        inspector_layout.addWidget(self.properties, 1)
        splitter.addWidget(inspector)
        splitter.setSizes([360, 520])
        splitter.setStretchFactor(1, 1)
        root.addWidget(splitter, 1)

        for keys, slot in (
            (QKeySequence.StandardKey.Undo, self.undo),
            (QKeySequence.StandardKey.Redo, self.redo),
            ("Ctrl+Shift+Z", self.redo),
            ("Ctrl+D", self._duplicate_layer),
            ("Escape", self.done.emit),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
            shortcut.activated.connect(slot)
        self._update_history_buttons()

    def _build_layers(self) -> QWidget:
        frame, layout = card()
        header = QHBoxLayout()
        header.addWidget(section_label("Layers"), 1)
        self.add_button = QToolButton()
        self.add_button.setText("Add")
        self.add_button.setIcon(icons.icon("plus", COLORS["accent"]))
        self.add_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.add_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.add_button.setStyleSheet(f"QToolButton {{ color: {COLORS['accent']}; }}")
        self.add_button.setMenu(self._build_add_menu())
        header.addWidget(self.add_button)
        layout.addLayout(header)

        self.layer_list = QListWidget()
        self.layer_list.setMouseTracking(True)
        self.layer_list.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.delegate = LayerDelegate(self.layer_list)
        self.delegate.visibility_clicked.connect(self._toggle_visibility)
        self.layer_list.setItemDelegate(self.delegate)
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.layer_list.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.layer_list.currentRowChanged.connect(self._on_layer_selected)
        self.layer_list.model().rowsMoved.connect(self._on_layers_reordered)
        delete = QShortcut(QKeySequence.StandardKey.Delete, self.layer_list)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self._delete_layer)
        layout.addWidget(self.layer_list, 1)

        self.orientation_box = QCheckBox("Show layers for the other orientation")
        self.orientation_box.setToolTip(
            "Some scenes carry a separate arrangement for when the display is "
            "mounted the other way round. They are hidden here unless you ask."
        )
        self.orientation_box.toggled.connect(lambda _on: self.refresh_layers())
        layout.addWidget(self.orientation_box)

        actions = QHBoxLayout()
        actions.setSpacing(2)
        actions.addWidget(tool_button("arrow-up", "Move up (draws above)", lambda: self._move(-1)))
        actions.addWidget(
            tool_button("arrow-down", "Move down (draws below)", lambda: self._move(1))
        )
        actions.addStretch(1)
        actions.addWidget(tool_button("copy", "Duplicate (Ctrl+D)", self._duplicate_layer))
        actions.addWidget(tool_button("trash", "Delete (Del)", self._delete_layer))
        layout.addLayout(actions)
        return frame

    def _build_add_menu(self) -> QMenu:
        menu = QMenu(self)
        registered = layer_types()
        ordered = [name for name in ADD_ORDER if name in registered]
        ordered += sorted(name for name in registered if name not in ADD_ORDER)
        for i, name in enumerate(ordered):
            cls = registered[name]
            if name.startswith("cs2") and i and not ordered[i - 1].startswith("cs2"):
                menu.addSeparator()
            action = menu.addAction(icons.icon(cls.icon), cls.friendly_name())
            action.setToolTip(cls.summary)
            action.setStatusTip(cls.summary)
            action.triggered.connect(lambda _c=False, n=name: self._add_layer(n))
        menu.setToolTipsVisible(True)
        return menu

    # ------------------------------------------------------------------ scene

    def set_scene(self, scene: Scene | None) -> None:
        if scene is not self.scene:
            self.history.reset()
        self.scene = scene
        self.title_edit.setText(scene.name if scene else "")
        self.refresh_layers()
        self._update_history_buttons()

    def set_frame_size(self, size: tuple[int, int]) -> None:
        if size != self.frame_size:
            self.frame_size = size
            self.refresh_layers()

    def _visible_in_list(self, layer: Layer) -> bool:
        return self.orientation_box.isChecked() or layer.applies_to(self.frame_size)

    def refresh_layers(self, select: Layer | None = None) -> None:
        """Rebuild the list. Top of the list is the topmost (last drawn) layer."""
        keep = select if select is not None else self._selected_layer()
        self.layer_list.blockSignals(True)
        self.layer_list.clear()
        self._shown = []
        has_variants = False
        if self.scene is not None:
            for layer in reversed(self.scene.layers):
                has_variants |= layer.orientation != "any"
                if not self._visible_in_list(layer):
                    continue
                self._shown.append(layer)
                item = QListWidgetItem()
                self._fill_item(item, layer)
                self.layer_list.addItem(item)
        self.orientation_box.setVisible(has_variants)
        self.layer_list.blockSignals(False)

        row = self._shown.index(keep) if keep in self._shown else -1
        self.layer_list.setCurrentRow(row)
        self.properties.show_layer(self._shown[row] if row >= 0 else None)

    @staticmethod
    def _fill_item(item: QListWidgetItem, layer: Layer) -> None:
        item.setText(layer.describe())
        item.setIcon(icons.icon(layer.icon, COLORS["muted"]))
        item.setData(LAYER_ID, id(layer))
        item.setData(TYPE_NAME, layer.friendly_name())
        item.setData(VISIBLE, layer.visible)
        item.setToolTip(layer.summary or layer.friendly_name())

    def _layer_for_id(self, layer_id: int) -> Layer | None:
        if self.scene is None:
            return None
        return next((layer for layer in self.scene.layers if id(layer) == layer_id), None)

    def _selected_layer(self) -> Layer | None:
        item = self.layer_list.currentItem()
        return self._layer_for_id(item.data(LAYER_ID)) if item is not None else None

    def _on_layer_selected(self, _row: int) -> None:
        self.properties.show_layer(self._selected_layer())

    # ------------------------------------------------------------------ edits

    def _snapshot(self, key: str | None = None) -> None:
        if self.scene is not None:
            self.history.record(self.scene.to_dict(), key)
            self._update_history_buttons()

    def _on_about_to_change(self, field_name: str) -> None:
        layer = self._selected_layer()
        self._snapshot(f"{id(layer)}:{field_name}")

    def _on_layer_edited(self, _field_name: str) -> None:
        # Update the row in place; rebuilding the inspector here would destroy
        # the very widget the user is typing into.
        item = self.layer_list.currentItem()
        layer = self._selected_layer()
        if item is not None and layer is not None:
            self._fill_item(item, layer)
        self._save()

    def _add_layer(self, type_name: str) -> None:
        if self.scene is None:
            return
        self._snapshot()
        layer = Layer.from_dict({"type": type_name})
        self.scene.layers.append(layer)
        self._save()
        self.refresh_layers(select=layer)

    def _duplicate_layer(self) -> None:
        layer = self._selected_layer()
        if layer is None or self.scene is None:
            return
        self._snapshot()
        copy = Layer.from_dict(layer.to_dict())
        self.scene.layers.insert(self.scene.layers.index(layer) + 1, copy)
        self._save()
        self.refresh_layers(select=copy)

    def _delete_layer(self) -> None:
        layer = self._selected_layer()
        if layer is None or self.scene is None:
            return
        self._snapshot()
        row = self.layer_list.currentRow()
        self.scene.layers.remove(layer)
        self._save()
        self.refresh_layers()
        if self._shown:
            select = self._shown[min(row, len(self._shown) - 1)]
            self.refresh_layers(select=select)

    def _toggle_visibility(self, row: int) -> None:
        if not (0 <= row < len(self._shown)):
            return
        layer = self._shown[row]
        self._snapshot()
        layer.visible = not layer.visible
        item = self.layer_list.item(row)
        if item is not None:
            self._fill_item(item, layer)
        if layer is self._selected_layer():
            self.properties.show_layer(layer)
        self._save()

    def _move(self, delta: int) -> None:
        """Move the selected layer one row up (-1) or down (+1) in the list."""
        layer = self._selected_layer()
        row = self.layer_list.currentRow()
        target = row + delta
        if layer is None or self.scene is None or not (0 <= target < len(self._shown)):
            return
        order = list(self._shown)
        order[row], order[target] = order[target], order[row]
        self._snapshot()
        self._apply_order(order)
        self._save()
        self.refresh_layers(select=layer)

    def _on_layers_reordered(self, *_args) -> None:
        if self.scene is None:
            return
        ids = [self.layer_list.item(i).data(LAYER_ID) for i in range(self.layer_list.count())]
        order = [self._layer_for_id(i) for i in ids]
        if None in order or len(order) != len(self._shown):
            self.refresh_layers()
            return
        self._snapshot()
        self._apply_order(order)
        self._save()
        self._shown = order

    def _apply_order(self, top_first: list[Layer]) -> None:
        """Write a new order for the listed layers back into the scene.

        Hidden (other-orientation) layers keep their slots; only the listed
        ones are permuted among the positions they already occupy.
        """
        assert self.scene is not None
        listed = {id(layer) for layer in top_first}
        slots = [i for i, layer in enumerate(self.scene.layers) if id(layer) in listed]
        for slot, layer in zip(slots, reversed(top_first), strict=True):
            self.scene.layers[slot] = layer

    def _save(self) -> None:
        if self.scene is None:
            return
        self.library.save(self.scene)
        self.engine.set_scene(self.scene)
        self.scene_edited.emit()

    # ------------------------------------------------------------------ undo

    def undo(self) -> None:
        if self.scene is not None:
            self._restore(self.history.undo(self.scene.to_dict()))

    def redo(self) -> None:
        if self.scene is not None:
            self._restore(self.history.redo(self.scene.to_dict()))

    def _restore(self, data: dict[str, Any] | None) -> None:
        if data is None or self.scene is None:
            return
        restored = Scene.from_dict(data)
        # Keep the identity and name of the scene object the rest of the app
        # holds; only its content goes back in time.
        self.scene.layers = restored.layers
        self.scene.background = restored.background
        self.scene.fps = restored.fps
        self._save()
        self.refresh_layers()
        self._update_history_buttons()

    def _update_history_buttons(self) -> None:
        self.undo_button.setEnabled(self.history.can_undo)
        self.redo_button.setEnabled(self.history.can_redo)

    # ------------------------------------------------------------------ name

    def _on_title_edited(self) -> None:
        text = self.title_edit.text().strip()
        if self.scene is None:
            return
        if not text:
            self.title_edit.setText(self.scene.name)
        elif text != self.scene.name:
            self.rename_requested.emit(text)
