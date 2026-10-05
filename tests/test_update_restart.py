"""The restart prompt must launch the helper when the user clicks OK."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication, QMessageBox, QPushButton  # noqa: E402

from fitzlcd import updater  # noqa: E402
from fitzlcd.config import AppConfig  # noqa: E402
from fitzlcd.ui import updates  # noqa: E402
from fitzlcd.ui.updates import UpdateController  # noqa: E402


@pytest.fixture
def controller():
    app = QApplication.instance() or QApplication([])
    ctl = UpdateController(QPushButton(), AppConfig())
    yield ctl
    del app


def _release():
    return updater.Release(version="9.9.9", notes="", url="http://x/y.zip", size=0, page="")


@pytest.mark.parametrize("answer", [1024, QMessageBox.StandardButton.Ok])
def test_ok_launches_helper(controller, monkeypatch, tmp_path, answer):
    # PySide6 hands back a plain int, not the enum member, so both must work.
    launched = []
    monkeypatch.setattr(updates.QMessageBox, "question", lambda *a, **k: answer)
    monkeypatch.setattr(updater, "apply_and_restart", lambda staged: launched.append(staged))
    monkeypatch.setattr(QApplication, "quit", staticmethod(lambda: None))
    controller._on_downloaded(_release(), tmp_path)
    assert launched == [tmp_path]


def test_cancel_does_not_launch_helper(controller, monkeypatch, tmp_path):
    launched = []
    monkeypatch.setattr(
        updates.QMessageBox, "question", lambda *a, **k: int(QMessageBox.StandardButton.Cancel)
    )
    monkeypatch.setattr(updater, "apply_and_restart", lambda staged: launched.append(staged))
    controller._on_downloaded(_release(), tmp_path)
    assert launched == []
