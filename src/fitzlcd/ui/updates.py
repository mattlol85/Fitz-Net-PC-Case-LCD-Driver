"""GUI glue for self-update.

Owns the schedule (once shortly after launch, then daily), the footer button's
state, and the download dialog. All of the actual work lives in
``fitzlcd.updater``; this file only moves it on and off the GUI thread.
"""

from __future__ import annotations

import logging
import threading
import time

from PySide6.QtCore import QObject, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import QMessageBox, QProgressDialog, QPushButton, QSystemTrayIcon

from fitzlcd import updater
from fitzlcd.config import AppConfig
from fitzlcd.ui.theme import COLORS, STATUS_COLORS
from fitzlcd.updater import Release

log = logging.getLogger(__name__)

#: Long enough that the check never competes with the panel handshake at start-up.
STARTUP_DELAY_MS = 8_000
DAILY_MS = 24 * 60 * 60 * 1000
DAILY_SECONDS = 24 * 60 * 60


class UpdateController(QObject):
    """Checks for updates in the background and drives the one-click install."""

    #: Emitted from the worker thread; queued onto the GUI thread.
    checked = Signal(object)
    progressed = Signal(int, int)
    downloaded = Signal(object, object)
    failed = Signal(str)

    def __init__(
        self,
        button: QPushButton,
        config: AppConfig,
        parent: QObject | None = None,
        tray: QSystemTrayIcon | None = None,
    ) -> None:
        super().__init__(parent)
        self.button = button
        self.config = config
        self.tray = tray
        self.available: Release | None = None
        self._busy = False

        self.checked.connect(self._on_checked, Qt.ConnectionType.QueuedConnection)
        self.progressed.connect(self._on_progress, Qt.ConnectionType.QueuedConnection)
        self.downloaded.connect(self._on_downloaded, Qt.ConnectionType.QueuedConnection)
        self.failed.connect(self._on_failed, Qt.ConnectionType.QueuedConnection)

        self.button.clicked.connect(self.activate)
        self._progress: QProgressDialog | None = None
        self._reset_button()

    # ---------------------------------------------------------------- schedule

    def start(self) -> None:
        """Arm the automatic checks. Manual checks work regardless."""
        if not self.config.update_check_enabled or not updater.is_frozen():
            return
        # A previous update may have left its download behind.
        threading.Thread(target=updater.cleanup, daemon=True).start()

        if time.time() - self.config.update_last_check >= DAILY_SECONDS:
            QTimer.singleShot(STARTUP_DELAY_MS, self.check_async)

        self._daily = QTimer(self)
        self._daily.setInterval(DAILY_MS)
        self._daily.timeout.connect(self.check_async)
        self._daily.start()

    def check_async(self, announce: bool = True) -> None:
        if self._busy:
            return
        self._busy = True

        def work() -> None:
            try:
                release = updater.check()
            except Exception as exc:  # noqa: BLE001 - else _busy sticks and checks stop forever
                log.warning("update check crashed: %s", exc)
                release = None
            self.checked.emit((release, announce))

        threading.Thread(target=work, daemon=True).start()

    # ----------------------------------------------------------------- results

    def _on_checked(self, payload) -> None:
        release, announce = payload
        self._busy = False
        self.config.update_last_check = time.time()
        self.config.save()

        if release is None:
            self.available = None
            self._reset_button()
            if not announce:
                QMessageBox.information(
                    self.parent(),
                    "No update",
                    "You have the latest version of FitzLCD.",
                )
            return

        self.available = release
        self.button.setText(f"Update to v{release.version}")
        self.button.setToolTip(f"Version {release.version} is available. Click to install it.")
        self.button.setStyleSheet(f"color: {STATUS_COLORS['connected']};")

        if announce and release.version != self.config.update_skipped_version and self.tray:
            self.tray.showMessage(
                "FitzLCD update available",
                f"Version {release.version} is ready to install.",
                QSystemTrayIcon.MessageIcon.Information,
                8000,
            )

    def _reset_button(self) -> None:
        self.button.setText("Check for updates")
        self.button.setToolTip("Look for a newer FitzLCD release on GitHub.")
        self.button.setStyleSheet(f"color: {COLORS['text']};")

    # ------------------------------------------------------------------ action

    def activate(self) -> None:
        """Button click: check if we don't know of an update, else offer to install."""
        if self.available is None:
            self.check_async(announce=False)
            return
        self._offer(self.available)

    def _offer(self, release: Release) -> None:
        notes = release.notes or "No release notes were provided."
        if len(notes) > 1500:
            notes = notes[:1500] + "\n…"

        box = QMessageBox(self.parent())
        box.setWindowTitle("FitzLCD update")
        box.setText(f"<b>Version {release.version} is available.</b>")
        box.setInformativeText(
            "FitzLCD will download it, install it and restart itself.\n\n" + notes
        )
        install = box.addButton("Update now", QMessageBox.ButtonRole.AcceptRole)
        skip = box.addButton("Skip this version", QMessageBox.ButtonRole.DestructiveRole)
        box.addButton("Later", QMessageBox.ButtonRole.RejectRole)
        box.exec()

        clicked = box.clickedButton()
        if clicked is skip:
            self.config.update_skipped_version = release.version
            self.config.save()
            return
        if clicked is not install:
            return

        if not updater.install_writable():
            self._manual_fallback(
                release,
                "FitzLCD is installed somewhere it cannot update itself "
                f"({updater.install_dir()}).\n\n"
                "Download the new version and unzip it over that folder, or move "
                "FitzLCD somewhere writable.",
            )
            return
        self._download(release)

    def _download(self, release: Release) -> None:
        self._progress = QProgressDialog(
            f"Downloading FitzLCD {release.version}…", None, 0, 100, self.parent()
        )
        self._progress.setWindowTitle("Updating FitzLCD")
        self._progress.setWindowModality(Qt.WindowModality.ApplicationModal)
        self._progress.setAutoClose(False)
        self._progress.setMinimumDuration(0)
        self._progress.show()

        def work() -> None:
            try:
                report = self.progressed.emit
                archive = updater.download(release, progress=lambda d, t: report(d, t))
                staged = updater.stage(archive, release.version)
            except Exception as exc:  # noqa: BLE001 - reported in a dialog, never fatal
                log.warning("update download failed: %s", exc)
                self.failed.emit(str(exc))
                return
            self.downloaded.emit(release, staged)

        threading.Thread(target=work, daemon=True).start()

    def _on_progress(self, done: int, total: int) -> None:
        if self._progress is None:
            return
        self._progress.setValue(int(done * 100 / total) if total else 0)

    def _on_downloaded(self, release: Release, staged) -> None:
        self._close_progress()
        confirm = QMessageBox.question(
            self.parent(),
            "Restart to finish",
            f"FitzLCD {release.version} is ready.\n\n"
            "The app will close, swap in the new version and start again. "
            "This takes a few seconds.",
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        # Compare by value: PySide6 returns a plain int here, so `is` against the
        # enum member is always False and silently skipped the restart.
        if confirm != QMessageBox.StandardButton.Ok:
            log.info("update to %s deferred by the user", release.version)
            return
        log.info("launching update helper for %s", release.version)
        try:
            updater.apply_and_restart(staged)
        except Exception as exc:  # noqa: BLE001 - fall back to the manual route
            log.warning("could not launch the update helper: %s", exc)
            self._manual_fallback(release, f"Could not start the installer:\n{exc}")
            return

        from PySide6.QtWidgets import QApplication  # noqa: PLC0415 - avoids an import cycle

        QApplication.quit()

    def _on_failed(self, message: str) -> None:
        self._close_progress()
        release = self.available
        if release is not None:
            self._manual_fallback(release, message)
        else:
            QMessageBox.warning(self.parent(), "Update failed", message)

    def _close_progress(self) -> None:
        if self._progress is not None:
            self._progress.close()
            self._progress = None

    def _manual_fallback(self, release: Release, message: str) -> None:
        box = QMessageBox(self.parent())
        box.setIcon(QMessageBox.Icon.Warning)
        box.setWindowTitle("Update failed")
        box.setText(message)
        box.setInformativeText("You can download the release and install it by hand instead.")
        open_page = box.addButton("Open release page", QMessageBox.ButtonRole.AcceptRole)
        box.addButton("Close", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_page:
            QDesktopServices.openUrl(QUrl(release.page))
