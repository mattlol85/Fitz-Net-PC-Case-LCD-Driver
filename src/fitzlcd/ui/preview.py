"""The live preview strip.

Shows exactly the frame the panel is being sent (before the scan-out rotation),
letterboxed into whatever width the window has. The engine hands frames in from
its own thread, so they are marshalled onto the Qt thread with a signal - never
touch widgets from the render loop.
"""

from __future__ import annotations

from PIL import Image
from PySide6.QtCore import QRect, QRectF, QSize, Qt, Signal
from PySide6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtWidgets import QSizePolicy, QWidget

from fitzlcd.ui.theme import COLORS

#: Upper bound on the preview strip, in pixels.
MAX_PREVIEW_HEIGHT = 340
#: Corner rounding of the drawn frame, like the bezel of a real screen.
CORNER_RADIUS = 6


def pil_to_qimage(image: Image.Image) -> QImage:
    """Convert a Pillow RGB image to a QImage that owns its buffer."""
    if image.mode != "RGB":
        image = image.convert("RGB")
    data = image.tobytes("raw", "RGB")
    qimage = QImage(data, image.width, image.height, image.width * 3, QImage.Format.Format_RGB888)
    return qimage.copy()  # detach from the temporary bytes object


class PreviewWidget(QWidget):
    """Aspect-correct view of the current frame."""

    frame_ready = Signal(object)

    def __init__(self, aspect: float = 1920 / 462, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self._pixmap: QPixmap | None = None
        self._aspect = aspect
        self._placeholder = "Waiting for the first frame…"
        self._max_height = MAX_PREVIEW_HEIGHT
        self.setMinimumHeight(90)
        # A portrait panel is 4:1 the other way, and its natural height would
        # squeeze the editor off the bottom of the window. The frame is
        # letterboxed into whatever room it gets, so capping height is safe.
        self.setMaximumHeight(self._max_height)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        # Frames arrive on the engine thread; the signal hops them to the GUI thread.
        self.frame_ready.connect(self._on_frame, Qt.ConnectionType.QueuedConnection)

    def set_aspect(self, aspect: float) -> None:
        if aspect > 0 and abs(aspect - self._aspect) > 1e-6:
            self._aspect = aspect
            self.updateGeometry()
            self.update()

    def set_max_height(self, height: int) -> None:
        """Cap the strip's height (the editor view keeps it shorter)."""
        self._max_height = height
        self.setMaximumHeight(height)
        self.updateGeometry()

    def set_placeholder(self, text: str) -> None:
        self._placeholder = text
        if self._pixmap is None:
            self.update()

    def submit(self, image: Image.Image) -> None:
        """Thread-safe entry point for the render engine."""
        self.frame_ready.emit(image)

    def clear(self) -> None:
        self._pixmap = None
        self.update()

    def _on_frame(self, image: Image.Image) -> None:
        self._pixmap = QPixmap.fromImage(pil_to_qimage(image))
        self.set_aspect(image.width / max(1, image.height))
        self.update()

    def heightForWidth(self, width: int) -> int:  # noqa: N802 - Qt naming
        return min(self._max_height, int(width / self._aspect))

    def hasHeightForWidth(self) -> bool:  # noqa: N802 - Qt naming
        return True

    def sizeHint(self):  # noqa: N802 - Qt naming
        return QSize(960, min(self._max_height, int(960 / self._aspect)))

    def paintEvent(self, event) -> None:  # noqa: N802 - Qt naming
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        box = self._target_rect()
        path = QPainterPath()
        path.addRoundedRect(QRectF(box), CORNER_RADIUS, CORNER_RADIUS)
        painter.fillPath(path, QColor(COLORS["preview_bg"]))

        if self._pixmap is None:
            painter.setPen(QPen(QColor(COLORS["muted"])))
            painter.drawText(
                box.adjusted(16, 0, -16, 0),
                Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                self._placeholder,
            )
        else:
            painter.save()
            painter.setClipPath(path)
            painter.drawPixmap(box, self._pixmap)
            painter.restore()
        painter.setPen(QPen(QColor(COLORS["border"])))
        painter.drawPath(path)

    def _target_rect(self) -> QRect:
        width = self.width()
        height = self.height()
        fitted_h = int(width / self._aspect)
        if fitted_h <= height:
            return QRect(0, (height - fitted_h) // 2, width, fitted_h)
        fitted_w = int(height * self._aspect)
        return QRect((width - fitted_w) // 2, 0, fitted_w, height)
