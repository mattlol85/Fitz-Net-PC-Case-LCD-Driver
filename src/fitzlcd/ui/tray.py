"""System tray icon.

Closing the window hides it; the render loop keeps running so the panel stays
live. Quitting is a deliberate action from this menu.
"""

from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from fitzlcd.ui import icons


def make_icon() -> QIcon:
    """The app's panel-shaped mark, drawn in code so there is no asset to ship."""
    return icons.app_icon()


class TrayIcon(QSystemTrayIcon):
    def __init__(
        self,
        on_show: Callable[[], None],
        on_pause: Callable[[bool], None],
        on_quit: Callable[[], None],
        on_next: Callable[[], None] | None = None,
        on_previous: Callable[[], None] | None = None,
        parent=None,
    ) -> None:
        super().__init__(make_icon(), parent)
        self.setToolTip("FitzLCD")
        self._on_show = on_show
        self._on_scene: Callable[[str], None] | None = None

        menu = QMenu()
        show = QAction("Open FitzLCD", menu)
        show.triggered.connect(lambda: on_show())
        menu.addAction(show)

        menu.addSeparator()
        self.scene_menu = menu.addMenu("Show scene")

        if on_previous is not None:
            previous = QAction("Previous scene", menu)
            previous.triggered.connect(lambda: on_previous())
            menu.addAction(previous)
        if on_next is not None:
            following = QAction("Next scene", menu)
            following.triggered.connect(lambda: on_next())
            menu.addAction(following)

        menu.addSeparator()
        self.pause_action = QAction("Pause display", menu, checkable=True)
        self.pause_action.toggled.connect(on_pause)
        menu.addAction(self.pause_action)

        menu.addSeparator()
        quit_action = QAction("Quit FitzLCD", menu)
        quit_action.triggered.connect(lambda: on_quit())
        menu.addAction(quit_action)

        self.setContextMenu(menu)
        self.activated.connect(self._on_activated)

    def set_scenes(self, names: list[str], on_scene: Callable[[str], None]) -> None:
        self._on_scene = on_scene
        self.scene_menu.clear()
        for name in names:
            action = QAction(name, self.scene_menu)
            action.triggered.connect(lambda _checked=False, n=name: on_scene(n))
            self.scene_menu.addAction(action)

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason in (
            QSystemTrayIcon.ActivationReason.Trigger,
            QSystemTrayIcon.ActivationReason.DoubleClick,
        ):
            self._on_show()
