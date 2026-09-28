"""The main window.

Two levels, deliberately:

home        what's on my display? - the live preview and a gallery of scenes
customize   the scene editor, one click away (``ui/customize.py``)

Everything else - mounting, startup, integrations, updates, the technical
details of the connected panel - is in Settings (``ui/settings_dialog.py``).
The header speaks plain language ("Your display is connected"); port names and
firmware versions are one click away, never in the way.
"""

from __future__ import annotations

import contextlib
import logging

from PySide6.QtCore import QRect, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QIcon, QImage, QKeySequence, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QInputDialog,
    QLabel,
    QListView,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from fitzlcd.config import AppConfig, SceneLibrary, app_dir
from fitzlcd.engine import EngineStats, RenderEngine
from fitzlcd.render.scene import Scene
from fitzlcd.sources.cs2gsi import cs2_gsi_cfg_installed, find_cs2_cfg_dir, install_cs2_gsi_cfg
from fitzlcd.sources.stats import StatsRegistry
from fitzlcd.ui import icons, theme
from fitzlcd.ui.customize import CustomizeView, card, section_label
from fitzlcd.ui.preview import MAX_PREVIEW_HEIGHT, PreviewWidget
from fitzlcd.ui.settings_dialog import ROTATIONS, SettingsDialog
from fitzlcd.ui.theme import COLORS, STATUS_COLORS
from fitzlcd.ui.thumbnails import ThumbnailRenderer, content_key

log = logging.getLogger(__name__)

__all__ = ["ROTATIONS", "MainWindow", "friendly_status"]

#: Auto-advance choices, in seconds; 0 is off.
CYCLE_CHOICES = (
    (0, "Off"),
    (10, "Every 10 seconds"),
    (30, "Every 30 seconds"),
    (60, "Every minute"),
    (300, "Every 5 minutes"),
    (600, "Every 10 minutes"),
)

#: The DS916's native landscape frame, used until a panel reports its own.
DEFAULT_FRAME = (1920, 462)

#: Height the preview is capped at while editing, leaving room for the editor.
CUSTOMIZE_PREVIEW_HEIGHT = 200

#: Longest edge of a gallery thumbnail, in pixels.
THUMB_EDGE = 232

#: Error text fragments that mean another program has the port open.
_BUSY_HINTS = ("busy", "denied", "in use", "access is denied", "permission")


def friendly_status(stats: EngineStats, paused: bool = False) -> str:
    """One plain sentence describing the connection, for the header."""
    if stats.connected:
        return (
            "Paused – your display is holding the last frame"
            if paused
            else ("Your display is connected")
        )
    if stats.last_error:
        error = stats.last_error.lower()
        if any(hint in error for hint in _BUSY_HINTS):
            return "Can't reach your display – another app may be using it"
        return "Can't reach your display"
    return "Looking for your display…"


def technical_details(stats: EngineStats) -> str:
    """The long form, one fact per line, for Settings and tooltips."""
    if stats.connected:
        rotation = f", turned {stats.rotation}°" if stats.rotation else ""
        return (
            f"{stats.panel_label} on {stats.address}\n"
            f"Model {stats.model or 'unknown'} · firmware {stats.firmware or '?'}\n"
            f"{stats.width}×{stats.height}{rotation}"
        )
    if stats.last_error:
        return f"Not connected: {stats.last_error}"
    return "Searching for a panel…"


class MainWindow(QMainWindow):
    stats_arrived = Signal(object)
    #: Scene names changed (added, renamed, deleted) - the tray menu listens.
    scenes_changed = Signal(list)

    def __init__(
        self,
        engine: RenderEngine,
        library: SceneLibrary,
        config: AppConfig,
        stats: StatsRegistry | None = None,
    ) -> None:
        super().__init__()
        self.engine = engine
        self.library = library
        self.config = config
        #: Optional: needed for the 12/24-hour toggle, and for live values in
        #: the metric pickers and thumbnails.
        self.stats = stats
        self.scenes: list[Scene] = []
        self.current_scene: Scene | None = None
        self._last_stats = EngineStats()

        self.setWindowTitle("FitzLCD")
        self.setWindowIcon(icons.app_icon())
        self.setMinimumSize(900, 620)
        self.resize(1180, 800)
        self._restore_geometry()

        self.thumbnails = ThumbnailRenderer(self._metrics_snapshot, self)
        self.thumbnails.clock_24_hour = config.clock_24_hour
        self.thumbnails.ready.connect(lambda *_: self._thumb_timer.start())
        self._thumb_timer = QTimer(self)
        self._thumb_timer.setSingleShot(True)
        self._thumb_timer.setInterval(400)
        self._thumb_timer.timeout.connect(self._refresh_thumbnails)

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
        layout.setContentsMargins(16, 12, 16, 12)
        layout.setSpacing(12)

        layout.addWidget(self._build_header())

        self.preview = PreviewWidget()
        self.preview.set_placeholder("Connecting to your display…")
        self.preview_card, preview_layout = card(margins=10)
        preview_layout.addWidget(self.preview)

        self.stack = QStackedWidget()
        self.home = self._build_home()
        self.customize = CustomizeView(self.engine, self.library, self._metrics_snapshot)
        self.customize.done.connect(self.show_home)
        self.customize.scene_edited.connect(self._thumb_timer.start)
        self.customize.rename_requested.connect(self._rename_current)
        self.stack.addWidget(self.home)
        self.stack.addWidget(self.customize)
        layout.addWidget(self.stack, 1)
        self.setCentralWidget(root)

        self.metrics_label = QLabel("")
        self.metrics_label.setObjectName("hint")
        self.statusBar().addPermanentWidget(self.metrics_label)
        self.statusBar().setSizeGripEnabled(False)
        self.statusBar().setVisible(self.config.show_diagnostics)

        # Built eagerly: the update controller needs window.update_btn at
        # startup, and the tests reach the settings widgets directly.
        self.settings_dialog = SettingsDialog(self)

        for keys, slot in (
            ("Ctrl+,", self.open_settings),
            ("Ctrl+E", self.show_customize),
        ):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(slot)
        pause = QShortcut(QKeySequence("Space"), self.home)
        pause.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        pause.activated.connect(self.pause_button.toggle)

        self.show_home()

    def _build_header(self) -> QWidget:
        header = QWidget()
        row = QHBoxLayout(header)
        row.setContentsMargins(4, 0, 0, 0)
        row.setSpacing(10)

        logo = QLabel()
        logo.setPixmap(icons.app_icon().pixmap(28, 28))
        title = QLabel("FitzLCD")
        title.setObjectName("title")

        self.status_dot = QLabel()
        self.status_dot.setStyleSheet(theme.dot_style("searching"))
        self.status_title = QLabel(friendly_status(EngineStats()))
        self.status_title.setObjectName("muted")

        self.pause_button = QPushButton("Pause")
        self.pause_button.setCheckable(True)
        self.pause_button.setIcon(icons.icon("pause"))
        self.pause_button.setToolTip("Freeze the display on the current frame (Space)")
        self.pause_button.toggled.connect(self._on_pause)

        self.settings_button = QPushButton("Settings")
        self.settings_button.setIcon(icons.icon("settings"))
        self.settings_button.setToolTip("Settings (Ctrl+,)")
        self.settings_button.clicked.connect(self.open_settings)

        row.addWidget(logo)
        row.addWidget(title)
        row.addSpacing(18)
        row.addWidget(self.status_dot)
        row.addWidget(self.status_title, 1)
        row.addWidget(self.pause_button)
        row.addWidget(self.settings_button)
        return header

    def _build_home(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(12)

        self.home_preview_slot = QVBoxLayout()
        layout.addLayout(self.home_preview_slot)

        scenes, scenes_layout = card(margins=14)
        top = QHBoxLayout()
        top.addWidget(section_label("Scenes"))
        top.addStretch(1)
        cycle_label = QLabel("Change scene automatically")
        cycle_label.setObjectName("muted")
        self.cycle_combo = QComboBox()
        for seconds, label in CYCLE_CHOICES:
            self.cycle_combo.addItem(label, seconds)
        self._select_cycle(self.config.cycle_seconds, emit=False)
        self.cycle_combo.currentIndexChanged.connect(self._on_cycle_changed)
        top.addWidget(cycle_label)
        top.addWidget(self.cycle_combo)
        scenes_layout.addLayout(top)

        self.scene_list = QListWidget()
        self.scene_list.setObjectName("gallery")
        self.scene_list.setViewMode(QListView.ViewMode.IconMode)
        self.scene_list.setMovement(QListView.Movement.Static)
        self.scene_list.setResizeMode(QListView.ResizeMode.Adjust)
        self.scene_list.setWrapping(True)
        self.scene_list.setUniformItemSizes(True)
        self.scene_list.setWordWrap(True)
        self.scene_list.setSpacing(4)
        self.scene_list.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.scene_list.customContextMenuRequested.connect(self._scene_menu)
        self.scene_list.currentRowChanged.connect(self._on_scene_selected)
        self.scene_list.itemDoubleClicked.connect(lambda _item: self.show_customize())
        scenes_layout.addWidget(self.scene_list, 1)

        buttons = QHBoxLayout()
        new = QPushButton("New scene")
        new.setIcon(icons.icon("plus"))
        new.clicked.connect(self._new_scene)
        hint = QLabel("Right-click a scene to rename, duplicate or delete it")
        hint.setObjectName("hint")
        self.customize_button = QPushButton("Customize")
        self.customize_button.setObjectName("primary")
        self.customize_button.setIcon(icons.icon("pencil", "#ffffff"))
        self.customize_button.setToolTip("Change what this scene shows (Ctrl+E)")
        self.customize_button.clicked.connect(self.show_customize)
        buttons.addWidget(new)
        buttons.addSpacing(8)
        buttons.addWidget(hint, 1)
        buttons.addWidget(self.customize_button)
        scenes_layout.addLayout(buttons)

        layout.addWidget(scenes, 1)
        return page

    # ------------------------------------------------------------------ views

    def show_home(self) -> None:
        self.home_preview_slot.addWidget(self.preview_card)
        self.preview.set_max_height(MAX_PREVIEW_HEIGHT)
        self.stack.setCurrentWidget(self.home)
        self.scene_list.setFocus()

    def show_customize(self) -> None:
        if self.current_scene is None:
            return
        self.customize.set_frame_size(self._frame_size())
        self.customize.set_scene(self.current_scene)
        self.customize.preview_slot.addWidget(self.preview_card)
        self.preview.set_max_height(CUSTOMIZE_PREVIEW_HEIGHT)
        self.stack.setCurrentWidget(self.customize)

    def open_settings(self) -> None:
        self._refresh_cs2_button()
        self.settings_dialog.show()
        self.settings_dialog.raise_()
        self.settings_dialog.activateWindow()

    # ------------------------------------------------------------------ scenes

    def reload_scenes(self, select: str | None = None) -> None:
        self.library.ensure_defaults()
        self.scenes = self.library.list()
        wanted_name = select or self.config.active_scene
        self.scene_list.blockSignals(True)
        self.scene_list.clear()
        self._apply_gallery_geometry()
        for scene in self.scenes:
            item = QListWidgetItem(scene.name)
            item.setToolTip(f"{scene.name}\nDouble-click to customize")
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop)
            self.scene_list.addItem(item)
        self.scene_list.blockSignals(False)
        self._refresh_thumbnails()
        self.scenes_changed.emit([s.name for s in self.scenes])

        if not self.scenes:
            self.current_scene = None
            self.engine.set_scene(None)
            self.customize.set_scene(None)
            return
        wanted = next((i for i, s in enumerate(self.scenes) if s.name == wanted_name), 0)
        self.scene_list.setCurrentRow(wanted)
        # setCurrentRow is a no-op signal-wise when the row didn't change.
        self._on_scene_selected(wanted)
        self._apply_cycle(self.config.cycle_seconds)

    def _on_scene_selected(self, row: int) -> None:
        if not (0 <= row < len(self.scenes)):
            return
        scene = self.scenes[row]
        if scene is self.current_scene:
            return
        self.current_scene = scene
        self.config.active_scene = scene.name
        self.config.save()
        self.engine.set_scene(scene)
        if self.stack.currentWidget() is self.customize:
            self.customize.set_scene(scene)

    def next_scene(self) -> None:
        self._step_scene(1)

    def previous_scene(self) -> None:
        self._step_scene(-1)

    def _step_scene(self, delta: int) -> None:
        if not self.scenes:
            return
        row = (self.scene_list.currentRow() + delta) % len(self.scenes)
        self.scene_list.setCurrentRow(row)

    def _select_cycle(self, seconds: int, emit: bool = True) -> None:
        """Pick an auto-advance interval, adding it to the list if it's unusual."""
        index = self.cycle_combo.findData(seconds)
        if index < 0:
            self.cycle_combo.addItem(f"Every {seconds} seconds", seconds)
            index = self.cycle_combo.count() - 1
        if not emit:
            self.cycle_combo.blockSignals(True)
        self.cycle_combo.setCurrentIndex(index)
        self.cycle_combo.blockSignals(False)

    def _on_cycle_changed(self, index: int) -> None:
        seconds = int(self.cycle_combo.itemData(index) or 0)
        self.config.cycle_seconds = seconds
        self.config.save()
        self._apply_cycle(seconds)

    def _apply_cycle(self, seconds: int) -> None:
        """Start or stop the auto-advance timer."""
        if seconds > 0 and len(self.scenes) > 1:
            self._cycle_timer.start(seconds * 1000)
        else:
            self._cycle_timer.stop()

    def _scene_menu(self, pos) -> None:
        item = self.scene_list.itemAt(pos)
        if item is None:
            return
        self.scene_list.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction(icons.icon("pencil"), "Customize…", self.show_customize)
        menu.addAction("Rename…", self._rename_dialog)
        menu.addAction(icons.icon("copy"), "Duplicate", self._duplicate_scene)
        menu.addSeparator()
        menu.addAction(icons.icon("trash", COLORS["danger"]), "Delete…", self._delete_scene)
        menu.exec(self.scene_list.viewport().mapToGlobal(pos))

    def _new_scene(self) -> None:
        name, ok = QInputDialog.getText(self, "New scene", "Name your new scene:", text="My scene")
        if not ok or not name.strip():
            return
        scene = Scene(name=self.library.unique_name(name))
        self.library.save(scene)
        self.reload_scenes(select=scene.name)
        self.show_customize()

    def _duplicate_scene(self) -> None:
        if self.current_scene is None:
            return
        copy = Scene.from_dict(self.current_scene.to_dict())
        copy.name = self.library.unique_name(f"{self.current_scene.name} copy")
        copy.path = None
        self.library.save(copy)
        self.reload_scenes(select=copy.name)

    def _rename_dialog(self) -> None:
        if self.current_scene is None:
            return
        name, ok = QInputDialog.getText(
            self, "Rename scene", "New name:", text=self.current_scene.name
        )
        if ok and name.strip() and name.strip() != self.current_scene.name:
            self._rename_current(name.strip())

    def _rename_current(self, name: str) -> None:
        if self.current_scene is None:
            return
        try:
            used = self.library.rename(self.current_scene, name)
        except OSError as exc:
            QMessageBox.warning(self, "Rename scene", f"Couldn't rename the scene:\n{exc}")
            self.customize.title_edit.setText(self.current_scene.name)
            return
        self.config.active_scene = used
        self.config.save()
        scene = self.current_scene
        # Keep the same Scene object (the editor and engine hold it).
        row = self.scene_list.currentRow()
        if 0 <= row < len(self.scenes):
            self.scene_list.item(row).setText(used)
        self.customize.title_edit.setText(used)
        self.engine.set_scene(scene)
        self.scenes_changed.emit([s.name for s in self.scenes])
        self._refresh_thumbnails()

    def _delete_scene(self) -> None:
        if self.current_scene is None:
            return
        box = QMessageBox(self)
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Delete scene")
        box.setText(f"Delete “{self.current_scene.name}”?")
        box.setInformativeText("This can't be undone.")
        delete = box.addButton("Delete", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton(QMessageBox.StandardButton.Cancel)
        box.exec()
        if box.clickedButton() is not delete:
            return
        row = self.scene_list.currentRow()
        self.library.delete(self.current_scene)
        self.current_scene = None
        remaining = [s.name for i, s in enumerate(self.scenes) if i != row]
        self.reload_scenes(select=remaining[min(row, len(remaining) - 1)] if remaining else None)

    # -------------------------------------------------------------- thumbnails

    def _frame_size(self) -> tuple[int, int]:
        stats = self._last_stats
        if stats.connected and stats.width and stats.height:
            return stats.width, stats.height
        width, height = DEFAULT_FRAME
        return (width, height) if self.config.rotation % 180 == 0 else (height, width)

    def _apply_gallery_geometry(self) -> None:
        width, height = self._frame_size()
        scale = THUMB_EDGE / max(width, height)
        if width >= height:
            icon = QSize(THUMB_EDGE, max(24, int(height * scale)))
        else:
            # Tall frames: shorter thumbnails, so several fit on a row.
            icon = QSize(max(24, int(width * scale * 0.7)), int(THUMB_EDGE * 0.7))
        self.scene_list.setIconSize(icon)
        self.scene_list.setGridSize(QSize(max(icon.width(), 120) + 28, icon.height() + 52))

    def _placeholder_icon(self) -> QIcon:
        size = self.scene_list.iconSize()
        px = QPixmap(size)
        px.fill(QColor(COLORS["preview_bg"]))
        return QIcon(px)

    def _refresh_thumbnails(self) -> None:
        size = self._frame_size()
        edge = int(THUMB_EDGE * self.devicePixelRatioF())
        for row, scene in enumerate(self.scenes):
            item = self.scene_list.item(row)
            if item is None:
                continue
            image = self.thumbnails.request(scene, size, edge)
            item.setIcon(self._thumb_icon(image) if image is not None else self._placeholder_icon())

    def _thumb_icon(self, image: QImage) -> QIcon:
        target = self.scene_list.iconSize()
        pixmap = QPixmap.fromImage(image).scaled(
            target * self.devicePixelRatioF(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        pixmap.setDevicePixelRatio(self.devicePixelRatioF())
        return QIcon(pixmap)

    def thumbnail_ready(self, scene: Scene) -> bool:
        """Whether this scene's thumbnail is cached (screenshots and tests)."""
        return content_key(scene, self._frame_size()) in self.thumbnails.cache

    # ------------------------------------------------------------------ status

    def _render_stats(self, stats: EngineStats) -> None:
        previous_size = self._frame_size()
        self._last_stats = stats
        status = stats.status
        self.status_dot.setStyleSheet(theme.dot_style(status))
        self.status_title.setText(friendly_status(stats, paused=self.engine.is_paused))
        self.status_title.setStyleSheet(
            "" if status == "connected" else theme.text_color(STATUS_COLORS[status])
        )
        details = technical_details(stats)
        self.status_title.setToolTip(details + "\n\nMore in Settings → About your display")
        self.device_chip.setText(details)

        self.metrics_label.setText(
            f"{stats.fps:.1f} fps · {stats.bytes_per_second / 1e6:.2f} MB/s · "
            f"{stats.frames_sent} sent · {stats.frames_skipped} skipped"
        )
        if not stats.connected:
            if stats.last_error:
                self.preview.set_placeholder(
                    "FitzLCD can't reach your display right now. It will keep trying.\n"
                    "Check the USB cable, and close any other app that controls the screen."
                )
            else:
                self.preview.set_placeholder(
                    "Looking for your display…\nConnect its USB cable – FitzLCD finds it "
                    "automatically."
                )
        if self._frame_size() != previous_size:
            self._on_frame_size_changed()

    def _on_frame_size_changed(self) -> None:
        self._apply_gallery_geometry()
        self._refresh_thumbnails()
        self.customize.set_frame_size(self._frame_size())

    def _metrics_snapshot(self) -> dict:
        return dict(self.stats.snapshot()) if self.stats is not None else {}

    # ------------------------------------------------------------------ actions

    def _on_pause(self, paused: bool) -> None:
        self.engine.set_paused(paused)
        self.pause_button.setText("Resume" if paused else "Pause")
        self.pause_button.setIcon(icons.icon("play" if paused else "pause"))
        self.status_title.setText(friendly_status(self._last_stats, paused=paused))

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
        self.preview.set_placeholder("Turning the picture…")
        self._on_frame_size_changed()

    def _on_brightness(self, value: int) -> None:
        self.config.brightness = value
        self.config.save()

    def _on_autostart(self, enabled: bool) -> None:
        from fitzlcd.platform.autostart import set_autostart

        try:
            set_autostart(enabled)
        except OSError as exc:
            QMessageBox.warning(self, "Start with Windows", f"Couldn't change this setting:\n{exc}")
            self.autostart_box.blockSignals(True)
            self.autostart_box.setChecked(not enabled)
            self.autostart_box.blockSignals(False)
            return
        self.config.autostart = enabled
        self.config.save()

    def _on_tray_pref(self, enabled: bool) -> None:
        self.config.minimise_to_tray = enabled
        self.config.save()

    def _on_diagnostics(self, enabled: bool) -> None:
        self.config.show_diagnostics = enabled
        self.config.save()
        self.statusBar().setVisible(enabled)

    def _on_hour12(self, enabled: bool) -> None:
        """Checkbox reads '12-hour', config stores 24-hour; invert here."""
        clock_24_hour = not enabled
        self.config.clock_24_hour = clock_24_hour
        self.config.save()
        # Two renderers to keep in step: clock layers read the engine's context,
        # while {time.now} is baked into the metric by the clock provider.
        self.engine.set_clock_24_hour(clock_24_hour)
        if self.stats is not None:
            self.stats.set_clock_24_hour(clock_24_hour)
        self.thumbnails.clock_24_hour = clock_24_hour
        self.thumbnails.cache.clear()
        self._refresh_thumbnails()

    def _refresh_cs2_button(self) -> None:
        """Reflect whether CS2's game-state integration is installed."""
        cs2_dir = find_cs2_cfg_dir()
        if cs2_dir and cs2_gsi_cfg_installed():
            cfg_path = cs2_dir / "gamestate_integration_fitzlcd.cfg"
            self.cs2_status.setText("Ready")
            self.cs2_status.setStyleSheet(theme.text_color(STATUS_COLORS["connected"]))
            self.cs2_btn.setText("Details")
            self.cs2_btn.setIcon(icons.icon("check", STATUS_COLORS["connected"]))
            self.cs2_btn.setToolTip(
                f"Match data appears while you play.\n\nConfig file:\n{cfg_path}"
            )
        elif cs2_dir:
            self.cs2_status.setText("Not set up yet")
            self.cs2_status.setStyleSheet(theme.text_color(STATUS_COLORS["searching"]))
            self.cs2_btn.setText("Set up")
            self.cs2_btn.setIcon(QIcon())
            self.cs2_btn.setToolTip("Install the small config file CS2 needs to share match data.")
        else:
            self.cs2_status.setText("Not found on this PC")
            self.cs2_status.setStyleSheet("")
            self.cs2_btn.setText("Set up manually")
            self.cs2_btn.setIcon(QIcon())
            self.cs2_btn.setToolTip("Show where to copy the config file by hand.")

    def _on_cs2_setup(self) -> None:
        """Install the GSI config into CS2's cfg folder, or show setup instructions."""
        cs2_dir = find_cs2_cfg_dir()

        if cs2_dir and cs2_gsi_cfg_installed():
            cfg_path = cs2_dir / "gamestate_integration_fitzlcd.cfg"
            QMessageBox.information(
                self,
                "Counter-Strike 2",
                "FitzLCD is set up to receive match data from Counter-Strike 2.\n\n"
                f"Config file:\n{cfg_path}\n\n"
                "If the display still says 'Waiting for match', restart CS2 so it "
                "picks up the file.",
            )
            return

        if cs2_dir:
            try:
                installed_path = install_cs2_gsi_cfg(self.config)
            except OSError as exc:
                QMessageBox.warning(
                    self, "Counter-Strike 2", f"Couldn't install the config file:\n{exc}"
                )
                return
            self._refresh_cs2_button()
            QMessageBox.information(
                self,
                "Counter-Strike 2",
                "All set. Restart Counter-Strike 2, then join a match.\n\n"
                f"Config file installed to:\n{installed_path}",
            )
            return

        # CS2 not found — open the folder containing the staging copy
        import subprocess  # noqa: PLC0415

        staging = app_dir() / "gamestate_integration_fitzlcd.cfg"
        QMessageBox.information(
            self,
            "Counter-Strike 2 – manual setup",
            "FitzLCD couldn't find Counter-Strike 2 automatically.\n\n"
            "Copy this file into CS2's cfg folder:\n\n"
            f"  {staging}\n\n"
            "The cfg folder is usually:\n"
            "  <Steam>\\steamapps\\common\\Counter-Strike Global Offensive"
            "\\game\\csgo\\cfg\\\n\n"
            "The folder containing the file will open now.",
        )
        with contextlib.suppress(Exception):  # noqa: BLE001 - opening Explorer is best-effort
            subprocess.Popen(["explorer", "/select,", str(staging)])  # noqa: S603, S607

    # ---------------------------------------------------------------- geometry

    def _restore_geometry(self) -> None:
        geometry = self.config.window_geometry
        if len(geometry) != 4:
            return
        rect = QRect(*geometry)
        screen = self.screen()
        if screen is not None and screen.availableVirtualGeometry().intersects(rect):
            self.setGeometry(rect)

    def save_geometry(self) -> None:
        if self.isMinimized() or self.isMaximized():
            return
        rect = self.geometry()
        self.config.window_geometry = [rect.x(), rect.y(), rect.width(), rect.height()]
        self.config.save()

    def closeEvent(self, event) -> None:  # noqa: N802 - Qt naming
        """Closing hides to the tray; quitting is an explicit tray action."""
        if self.isVisible():
            self.save_geometry()
        if self.config.minimise_to_tray and self.isVisible():
            event.ignore()
            self.hide()
            return
        event.accept()
