"""The main window: preview-first layout.

header      app name + device chip + pause/settings
preview     full-width live frame, engine metrics beneath
editor      scenes | layers | properties
footer      brightness, autostart, minimise-to-tray
"""

from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
    QComboBox,
    QFrame,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSlider,
    QSpinBox,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from fitzlcd.config import AppConfig, SceneLibrary, app_dir
from fitzlcd.engine import EngineStats, RenderEngine
from fitzlcd.render.scene import Layer, Scene, layer_types
from fitzlcd.sources.cs2gsi import cs2_gsi_cfg_installed, find_cs2_cfg_dir, install_cs2_gsi_cfg
from fitzlcd.ui.preview import PreviewWidget
from fitzlcd.ui.properties import PropertiesPane

log = logging.getLogger(__name__)

#: Mounting orientations offered in the UI, in degrees counter-clockwise.
ROTATIONS = (0, 90, 180, 270)

STATUS_COLORS = {
    "connected": "#39d353",
    "searching": "#d9a441",
    "error": "#f05f5f",
}


class MainWindow(QMainWindow):
    stats_arrived = Signal(object)

    def __init__(
        self,
        engine: RenderEngine,
        library: SceneLibrary,
        config: AppConfig,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.library = library
        self.config = config
        self.scenes: list[Scene] = []
        self.current_scene: Scene | None = None

        self.setWindowTitle("FitzLCD")
        self.resize(1180, 760)
        self._build()

        # The engine publishes stats from its own thread; hop them to the GUI.
        self.stats_arrived.connect(self._render_stats, Qt.ConnectionType.QueuedConnection)
        engine.on_stats = self.stats_arrived.emit
        engine.on_frame = self.preview.submit

        self._cycle_timer = QTimer(self)
        self._cycle_timer.timeout.connect(self.next_scene)

        self.reload_scenes()
        self._apply_cycle(self.config.cycle_seconds)

    # ------------------------------------------------------------------ build

    def _build(self) -> None:
        root = QWidget()
        layout = QVBoxLayout(root)
        layout.setContentsMargins(12, 10, 12, 10)
        layout.setSpacing(10)

        layout.addLayout(self._build_header())
        layout.addWidget(self._build_preview())
        layout.addWidget(self._build_editor(), 1)
        layout.addWidget(self._build_footer())

        self.setCentralWidget(root)

    def _build_header(self) -> QHBoxLayout:
        row = QHBoxLayout()

        title = QLabel("<b>FitzLCD</b>")
        title.setStyleSheet("font-size: 16px;")

        self.device_chip = QLabel("searching for a panel...")
        self.device_chip.setStyleSheet("color: #d9a441;")

        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.setFixedWidth(90)
        self.pause_button.toggled.connect(self._on_pause)

        row.addWidget(title)
        row.addSpacing(16)
        row.addWidget(self.device_chip, 1)
        row.addWidget(self.pause_button)
        return row

    def _build_preview(self) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        self.preview = PreviewWidget()
        self.metrics_label = QLabel("--")
        self.metrics_label.setAlignment(Qt.AlignmentFlag.AlignRight)
        self.metrics_label.setStyleSheet("color: #8892b0; font-size: 11px;")

        layout.addWidget(self.preview)
        layout.addWidget(self.metrics_label)
        return box

    def _build_editor(self) -> QWidget:
        splitter = QSplitter(Qt.Orientation.Horizontal)

        splitter.addWidget(self._build_scene_pane())
        splitter.addWidget(self._build_layer_pane())

        self.properties = PropertiesPane()
        self.properties.layer_changed.connect(self._on_layer_edited)
        splitter.addWidget(self._titled("PROPERTIES", self.properties))

        splitter.setSizes([220, 380, 420])
        return splitter

    def _build_scene_pane(self) -> QWidget:
        self.scene_list = QListWidget()
        self.scene_list.currentRowChanged.connect(self._on_scene_selected)

        flip = QHBoxLayout()
        for label, slot, tip in (
            ("Prev", self.previous_scene, "Previous scene"),
            ("Next", self.next_scene, "Next scene"),
        ):
            button = QPushButton(label)
            button.setFixedWidth(52)
            button.setToolTip(tip)
            button.clicked.connect(slot)
            flip.addWidget(button)
        self.cycle_spin = QSpinBox()
        self.cycle_spin.setRange(0, 3600)
        self.cycle_spin.setSuffix(" s")
        self.cycle_spin.setSpecialValueText("off")
        self.cycle_spin.setValue(self.config.cycle_seconds)
        self.cycle_spin.setToolTip("Flip to the next scene automatically; 0 disables it")
        self.cycle_spin.valueChanged.connect(self._on_cycle_changed)
        flip.addWidget(QLabel("Cycle"))
        flip.addWidget(self.cycle_spin, 1)

        buttons = QHBoxLayout()
        for label, slot in (("New", self._new_scene), ("Delete", self._delete_scene)):
            button = QPushButton(label)
            button.clicked.connect(slot)
            buttons.addWidget(button)

        return self._titled("SCENES", self.scene_list, flip, buttons)

    def _build_layer_pane(self) -> QWidget:
        self.layer_list = QListWidget()
        self.layer_list.currentRowChanged.connect(self._on_layer_selected)
        self.layer_list.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.layer_list.model().rowsMoved.connect(self._on_layers_reordered)

        self.layer_type_combo = QComboBox()
        self.layer_type_combo.addItems(sorted(layer_types()))

        buttons = QHBoxLayout()
        buttons.addWidget(self.layer_type_combo, 1)
        for label, slot in (
            ("Add", self._add_layer),
            ("Dup", self._duplicate_layer),
            ("Del", self._delete_layer),
        ):
            button = QPushButton(label)
            button.setFixedWidth(52)
            button.clicked.connect(slot)
            buttons.addWidget(button)

        hint = QLabel("Drag to reorder. The bottom row draws first.")
        hint.setStyleSheet("color: #6b7488; font-size: 10px;")

        return self._titled("LAYERS", self.layer_list, buttons, hint)

    def _build_footer(self) -> QWidget:
        frame = QFrame()
        frame.setFrameShape(QFrame.Shape.StyledPanel)
        row = QHBoxLayout(frame)
        row.setContentsMargins(10, 6, 10, 6)

        self.brightness = QSlider(Qt.Orientation.Horizontal)
        self.brightness.setRange(0, 100)
        self.brightness.setValue(self.config.brightness)
        self.brightness.setFixedWidth(180)
        self.brightness.setEnabled(False)
        self.brightness.setToolTip(
            "The brightness command is documented but not yet verified on this "
            "panel; run 'Probe: brightness' to confirm it before enabling."
        )
        self.brightness.valueChanged.connect(self._on_brightness)

        self.autostart_box = QCheckBox("Start with Windows")
        self.autostart_box.setChecked(self.config.autostart)
        self.autostart_box.toggled.connect(self._on_autostart)

        self.tray_box = QCheckBox("Minimise to tray")
        self.tray_box.setChecked(self.config.minimise_to_tray)
        self.tray_box.toggled.connect(self._on_tray_pref)

        self.rotation_combo = QComboBox()
        for degrees in ROTATIONS:
            self.rotation_combo.addItem(f"{degrees}°", degrees)
        self.rotation_combo.setCurrentIndex(
            ROTATIONS.index(self.config.rotation) if self.config.rotation in ROTATIONS else 0
        )
        self.rotation_combo.setToolTip(
            "How the panel is physically mounted. Scenes are composed in what "
            "you see, so this changes the frame shape as well as the output."
        )
        self.rotation_combo.currentIndexChanged.connect(self._on_rotation)

        self.cs2_btn = QPushButton("CS2 GSI…")
        self.cs2_btn.setFixedWidth(150)
        self.cs2_btn.clicked.connect(self._on_cs2_setup)
        self._refresh_cs2_button()

        row.addWidget(QLabel("Orientation"))
        row.addWidget(self.rotation_combo)
        row.addSpacing(18)
        row.addWidget(QLabel("Brightness"))
        row.addWidget(self.brightness)
        row.addStretch(1)
        row.addWidget(self.cs2_btn)
        row.addSpacing(8)
        row.addWidget(self.autostart_box)
        row.addWidget(self.tray_box)
        return frame

    @staticmethod
    def _titled(title: str, widget: QWidget, *extras) -> QWidget:
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(title)
        label.setStyleSheet("color: #8892b0; font-size: 10px; letter-spacing: 1px;")
        layout.addWidget(label)
        layout.addWidget(widget, 1)
        for extra in extras:
            if isinstance(extra, QWidget):
                layout.addWidget(extra)
            else:
                layout.addLayout(extra)
        return box

    # ------------------------------------------------------------------ scenes

    def reload_scenes(self) -> None:
        self.library.ensure_defaults()
        self.scenes = self.library.list()
        self.scene_list.clear()
        for scene in self.scenes:
            self.scene_list.addItem(QListWidgetItem(scene.name))

        if not self.scenes:
            return
        wanted = next(
            (i for i, s in enumerate(self.scenes) if s.name == self.config.active_scene), 0
        )
        self.scene_list.setCurrentRow(wanted)
        self._apply_cycle(self.config.cycle_seconds)

    def _on_scene_selected(self, row: int) -> None:
        if not (0 <= row < len(self.scenes)):
            return
        self.current_scene = self.scenes[row]
        self.config.active_scene = self.current_scene.name
        self.config.save()
        self.engine.set_scene(self.current_scene)
        self._refresh_layers()

    def next_scene(self) -> None:
        self._step_scene(1)

    def previous_scene(self) -> None:
        self._step_scene(-1)

    def _step_scene(self, delta: int) -> None:
        if not self.scenes:
            return
        row = (self.scene_list.currentRow() + delta) % len(self.scenes)
        self.scene_list.setCurrentRow(row)

    def _on_cycle_changed(self, seconds: int) -> None:
        self.config.cycle_seconds = seconds
        self.config.save()
        self._apply_cycle(seconds)

    def _apply_cycle(self, seconds: int) -> None:
        """Start or stop the auto-advance timer."""
        if seconds > 0 and len(self.scenes) > 1:
            self._cycle_timer.start(seconds * 1000)
        else:
            self._cycle_timer.stop()

    def _new_scene(self) -> None:
        name, ok = QInputDialog.getText(self, "New scene", "Name:")
        if not ok or not name.strip():
            return
        scene = Scene(name=name.strip())
        self.library.save(scene)
        self.reload_scenes()
        for i, existing in enumerate(self.scenes):
            if existing.name == scene.name:
                self.scene_list.setCurrentRow(i)
                break

    def _delete_scene(self) -> None:
        if self.current_scene is None:
            return
        confirm = QMessageBox.question(self, "Delete scene", f"Delete {self.current_scene.name!r}?")
        if confirm is not QMessageBox.StandardButton.Yes:
            return
        self.library.delete(self.current_scene)
        self.current_scene = None
        self.reload_scenes()

    # ------------------------------------------------------------------ layers

    def _refresh_layers(self) -> None:
        self.layer_list.clear()
        if self.current_scene is None:
            self.properties.show_layer(None)
            return
        # Top of the list is the topmost layer, so the list is drawn in reverse.
        for layer in reversed(self.current_scene.layers):
            item = QListWidgetItem(f"{layer.type_name:<9} {layer.describe()}")
            if not layer.visible:
                item.setForeground(Qt.GlobalColor.gray)
            self.layer_list.addItem(item)
        self.properties.show_layer(None)

    def _selected_layer(self) -> Layer | None:
        row = self.layer_list.currentRow()
        if self.current_scene is None or row < 0:
            return None
        index = len(self.current_scene.layers) - 1 - row
        if 0 <= index < len(self.current_scene.layers):
            return self.current_scene.layers[index]
        return None

    def _on_layer_selected(self, _row: int) -> None:
        self.properties.show_layer(self._selected_layer())

    def _on_layer_edited(self) -> None:
        self._save_current_scene()
        row = self.layer_list.currentRow()
        self._refresh_layers()
        self.layer_list.setCurrentRow(row)

    def _on_layers_reordered(self, *_args) -> None:
        if self.current_scene is None:
            return
        # The widget order is top-first; the scene stores bottom-first.
        order = [self.layer_list.item(i).text() for i in range(self.layer_list.count())]
        by_label = {
            f"{layer.type_name:<9} {layer.describe()}": layer for layer in self.current_scene.layers
        }
        reordered = [by_label[label] for label in order if label in by_label]
        if len(reordered) == len(self.current_scene.layers):
            self.current_scene.layers = list(reversed(reordered))
            self._save_current_scene()

    def _add_layer(self) -> None:
        if self.current_scene is None:
            return
        layer = Layer.from_dict({"type": self.layer_type_combo.currentText()})
        self.current_scene.layers.append(layer)
        self._save_current_scene()
        self._refresh_layers()
        self.layer_list.setCurrentRow(0)

    def _duplicate_layer(self) -> None:
        layer = self._selected_layer()
        if layer is None or self.current_scene is None:
            return
        self.current_scene.layers.append(Layer.from_dict(layer.to_dict()))
        self._save_current_scene()
        self._refresh_layers()

    def _delete_layer(self) -> None:
        layer = self._selected_layer()
        if layer is None or self.current_scene is None:
            return
        self.current_scene.layers.remove(layer)
        self._save_current_scene()
        self._refresh_layers()

    def _save_current_scene(self) -> None:
        if self.current_scene is None:
            return
        self.library.save(self.current_scene)
        self.engine.set_scene(self.current_scene)

    # ------------------------------------------------------------------ status

    def _render_stats(self, stats: EngineStats) -> None:
        color = STATUS_COLORS.get(stats.status, "#8892b0")
        if stats.connected:
            rotation = f" · {stats.rotation}°" if stats.rotation else ""
            text = (
                f"● {stats.panel_label} · {stats.address} · "
                f"{stats.model or 'unknown model'} fw {stats.firmware or '?'} · "
                f"{stats.width}×{stats.height}{rotation}"
            )
        elif stats.last_error:
            text = f"● {stats.last_error}"
        else:
            text = "● searching for a panel..."

        self.device_chip.setText(text)
        self.device_chip.setStyleSheet(f"color: {color};")

        self.metrics_label.setText(
            f"{stats.fps:.1f} fps · {stats.bytes_per_second / 1e6:.2f} MB/s · "
            f"{stats.frames_sent} sent · {stats.frames_skipped} skipped"
        )
        if not stats.connected:
            self.preview.set_placeholder(stats.last_error or "waiting for a panel")

    # ------------------------------------------------------------------ actions

    def _on_pause(self, paused: bool) -> None:
        self.engine.set_paused(paused)
        self.pause_button.setText("Resume" if paused else "Pause")

    def _on_rotation(self, index: int) -> None:
        degrees = self.rotation_combo.itemData(index)
        if degrees is None:
            return
        self.engine.set_rotation(int(degrees))
        self.config.rotation = int(degrees)
        self.config.save()
        # The frame shape changed, so the stale preview would be the wrong
        # aspect until the next frame lands.
        self.preview.clear()
        self.preview.set_placeholder(f"rotating to {degrees}°...")

    def _on_brightness(self, value: int) -> None:
        self.config.brightness = value
        self.config.save()

    def _on_autostart(self, enabled: bool) -> None:
        from fitzlcd.platform.autostart import set_autostart

        try:
            set_autostart(enabled)
        except OSError as exc:
            QMessageBox.warning(self, "Autostart", f"Could not update autostart:\n{exc}")
            self.autostart_box.setChecked(not enabled)
            return
        self.config.autostart = enabled
        self.config.save()

    def _on_tray_pref(self, enabled: bool) -> None:
        self.config.minimise_to_tray = enabled
        self.config.save()

    def _refresh_cs2_button(self) -> None:
        """Update the CS2 GSI button label and tooltip to reflect installation state."""
        cs2_dir = find_cs2_cfg_dir()
        if cs2_dir and cs2_gsi_cfg_installed():
            cfg_path = cs2_dir / "gamestate_integration_fitzlcd.cfg"
            self.cs2_btn.setText("CS2 GSI ✓ Ready")
            self.cs2_btn.setToolTip(f"Config installed at:\n{cfg_path}\n\nClick for details.")
            self.cs2_btn.setStyleSheet("color: #39d353;")
        elif cs2_dir:
            self.cs2_btn.setText("Install CS2 GSI")
            self.cs2_btn.setToolTip(
                "The GSI config file is missing from CS2's cfg folder.\n"
                "Click to install it automatically."
            )
            self.cs2_btn.setStyleSheet("color: #d9a441;")
        else:
            self.cs2_btn.setText("CS2 GSI: Setup")
            self.cs2_btn.setToolTip(
                "CS2 installation not found automatically.\n"
                "Click for manual setup instructions."
            )
            self.cs2_btn.setStyleSheet("color: #8892b0;")

    def _on_cs2_setup(self) -> None:
        """Install the GSI config into CS2's cfg folder, or show setup instructions."""
        cs2_dir = find_cs2_cfg_dir()

        if cs2_dir and cs2_gsi_cfg_installed():
            cfg_path = cs2_dir / "gamestate_integration_fitzlcd.cfg"
            QMessageBox.information(
                self,
                "CS2 GSI Ready",
                f"The GSI config file is already installed:\n\n{cfg_path}\n\n"
                "CS2 will send match data to FitzLCD while a game is active.\n"
                "If the LCD still shows 'Waiting for match', restart CS2 so it "
                "re-reads its cfg folder.",
            )
            return

        if cs2_dir:
            try:
                installed_path = install_cs2_gsi_cfg(self.config)
            except OSError as exc:
                QMessageBox.warning(self, "CS2 GSI Error", f"Could not write config file:\n{exc}")
                return
            self._refresh_cs2_button()
            QMessageBox.information(
                self,
                "CS2 GSI Installed",
                f"Config file installed to:\n\n{installed_path}\n\n"
                "Restart CS2 for it to take effect, then re-join a match.",
            )
            return

        # CS2 not found — open the folder containing the staging copy
        import subprocess  # noqa: PLC0415

        staging = app_dir() / "gamestate_integration_fitzlcd.cfg"
        QMessageBox.information(
            self,
            "CS2 GSI Manual Setup",
            "Could not locate CS2 automatically.\n\n"
            "Copy this file into CS2's cfg folder:\n\n"
            f"  Source:  {staging}\n\n"
            "  Destination:\n"
            "  <Steam>\\steamapps\\common\\Counter-Strike Global Offensive"
            "\\game\\csgo\\cfg\\\n\n"
            "The folder containing the source file will open now.",
        )
        with contextlib.suppress(Exception):  # noqa: BLE001 - opening Explorer is best-effort
            subprocess.Popen(["explorer", "/select,", str(staging)])  # noqa: S603, S607

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Closing hides to the tray; quitting is an explicit tray action."""
        if self.config.minimise_to_tray and self.isVisible():
            event.ignore()
            self.hide()
            return
        event.accept()
