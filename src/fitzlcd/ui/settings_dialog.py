"""The Settings dialog.

Everything a user touches once in a while - how the display is mounted,
startup behaviour, integrations, updates, and the technical details of the
connected panel - lives here rather than on the main window, which is kept for
the one thing people do every day: choosing what's on the display.

This module only builds the widgets and lays them out; it stores each one as
an attribute on the owning :class:`~fitzlcd.ui.main_window.MainWindow`
(``window.rotation_combo``, ``window.device_chip``, ...) and wires it to that
window's handler methods. It is built once, eagerly, with the window: the
update controller in ``ui/app.py`` needs ``window.update_btn`` at startup.
"""

from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QVBoxLayout,
)

from fitzlcd import __version__, updater
from fitzlcd.ui import icons

#: Mounting orientations offered in the UI, in degrees counter-clockwise.
ROTATIONS = (0, 90, 180, 270)
ROTATION_LABELS = {
    0: "Landscape",
    90: "Portrait (turned left)",
    180: "Landscape, upside down",
    270: "Portrait (turned right)",
}


def _group(title: str) -> tuple[QGroupBox, QFormLayout]:
    box = QGroupBox(title)
    form = QFormLayout(box)
    form.setHorizontalSpacing(16)
    form.setVerticalSpacing(10)
    form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    return box, form


def _muted(text: str = "") -> QLabel:
    label = QLabel(text)
    label.setObjectName("muted")
    label.setWordWrap(True)
    label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return label


class SettingsDialog(QDialog):
    def __init__(self, window) -> None:
        super().__init__(window)
        self.setWindowTitle("Settings")
        self.setMinimumWidth(480)
        self.setWindowFlag(Qt.WindowType.WindowContextHelpButtonHint, False)
        self._build(window)

    def _build(self, window) -> None:
        column = QVBoxLayout(self)
        column.setContentsMargins(18, 8, 18, 16)
        column.setSpacing(4)

        # ---------------------------------------------------------- display
        box, form = _group("Display")
        window.rotation_combo = QComboBox()
        for degrees in ROTATIONS:
            window.rotation_combo.addItem(ROTATION_LABELS[degrees], degrees)
        window.rotation_combo.setCurrentIndex(
            ROTATIONS.index(window.config.rotation) if window.config.rotation in ROTATIONS else 0
        )
        window.rotation_combo.setToolTip(
            "How the display is physically mounted in your case. Scenes rearrange "
            "themselves to match."
        )
        window.rotation_combo.currentIndexChanged.connect(window._on_rotation)
        form.addRow("Mounted", window.rotation_combo)

        window.brightness = QSlider(Qt.Orientation.Horizontal)
        window.brightness.setRange(0, 100)
        window.brightness.setValue(window.config.brightness)
        window.brightness.setEnabled(False)
        window.brightness.setToolTip(
            "The brightness command is documented but not yet verified on this "
            "panel; run 'Probe: brightness' to confirm it before enabling."
        )
        window.brightness.valueChanged.connect(window._on_brightness)
        # Hidden until the command is verified: a control that does nothing is
        # worse than no control.
        window.brightness.setVisible(False)

        window.hour12_box = QCheckBox("Use 12-hour time")
        window.hour12_box.setChecked(not window.config.clock_24_hour)
        window.hour12_box.setToolTip("Show clocks as 1:30 PM rather than 13:30.")
        window.hour12_box.toggled.connect(window._on_hour12)
        form.addRow("Clock", window.hour12_box)
        column.addWidget(box)

        # ---------------------------------------------------------- startup
        box, form = _group("Startup")
        window.autostart_box = QCheckBox("Start FitzLCD when I sign in to Windows")
        window.autostart_box.setChecked(window.config.autostart)
        window.autostart_box.toggled.connect(window._on_autostart)
        form.addRow(window.autostart_box)

        window.tray_box = QCheckBox("Keep running in the tray when the window is closed")
        window.tray_box.setChecked(window.config.minimise_to_tray)
        window.tray_box.setToolTip(
            "Your display keeps updating while FitzLCD is in the tray. Quit from "
            "the tray icon's menu."
        )
        window.tray_box.toggled.connect(window._on_tray_pref)
        form.addRow(window.tray_box)
        column.addWidget(box)

        # ------------------------------------------------------ integrations
        box, form = _group("Integrations")
        cs2_row = QHBoxLayout()
        window.cs2_status = _muted()
        window.cs2_btn = QPushButton("Set up")
        window.cs2_btn.clicked.connect(window._on_cs2_setup)
        cs2_row.addWidget(window.cs2_status, 1)
        cs2_row.addWidget(window.cs2_btn)
        form.addRow("Counter-Strike 2", cs2_row)
        window._refresh_cs2_button()
        column.addWidget(box)

        # ------------------------------------------------------------ about
        box, form = _group("About your display")
        window.device_chip = _muted("Not connected")
        form.addRow(window.device_chip)
        window.diagnostics_box = QCheckBox("Show performance stats under the window")
        window.diagnostics_box.setChecked(window.config.show_diagnostics)
        window.diagnostics_box.toggled.connect(window._on_diagnostics)
        form.addRow(window.diagnostics_box)
        column.addWidget(box)

        box, form = _group("About FitzLCD")
        update_row = QHBoxLayout()
        update_row.addWidget(_muted(f"Version {__version__}"), 1)
        # Wired up by UpdateController in ui/app.py; meaningless outside the
        # packaged build, where updates come from git/pip instead.
        window.update_btn = QPushButton("Check for updates")
        window.update_btn.setIcon(icons.icon("refresh"))
        window.update_btn.setVisible(updater.is_frozen())
        update_row.addWidget(window.update_btn)
        form.addRow(update_row)
        column.addWidget(box)

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        buttons.accepted.connect(self.accept)
        column.addSpacing(8)
        column.addWidget(buttons)
