"""GUI tests, run against Qt's offscreen platform so they need no desktop."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

import fitzlcd.render.layers  # noqa: E402, F401 - registers the built-in layer types
from fitzlcd.config import AppConfig, SceneLibrary  # noqa: E402
from fitzlcd.engine import EngineStats, RenderEngine  # noqa: E402
from fitzlcd.render.scene import Scene  # noqa: E402
from fitzlcd.ui.main_window import MainWindow  # noqa: E402
from fitzlcd.ui.preview import PreviewWidget, pil_to_qimage  # noqa: E402
from fitzlcd.ui.properties import PropertiesPane  # noqa: E402


@pytest.fixture(scope="session")
def qt_app():
    return QApplication.instance() or QApplication([])


@pytest.fixture
def window(qt_app, tmp_path, monkeypatch):
    monkeypatch.setenv("FITZLCD_HOME", str(tmp_path))
    library = SceneLibrary(tmp_path / "scenes")
    config = AppConfig()
    config.minimise_to_tray = False
    win = MainWindow(RenderEngine(), library, config)
    yield win
    win.close()


class TestPreview:
    def test_converts_pillow_frames(self, qt_app):
        image = Image.new("RGB", (1920, 462), "red")
        qimage = pil_to_qimage(image)
        assert (qimage.width(), qimage.height()) == (1920, 462)
        assert qimage.pixelColor(5, 5).red() == 255

    def test_keeps_the_panel_aspect_ratio(self, qt_app):
        preview = PreviewWidget()
        assert preview.heightForWidth(1920) == 462

    def test_adopts_the_aspect_of_the_frame_it_receives(self, qt_app):
        preview = PreviewWidget()
        preview._on_frame(Image.new("RGB", (320, 320)))
        assert preview.heightForWidth(320) == 320


class TestPropertiesPane:
    def test_builds_a_row_for_every_declared_field(self, qt_app):
        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "text"}]}).layers[0]
        pane.show_layer(layer)
        # One row per field, plus the type row.
        assert pane._form.rowCount() == len(layer.FIELDS) + 1

    def test_editing_a_field_updates_the_layer(self, qt_app):
        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "text"}]}).layers[0]
        pane.show_layer(layer)
        pane._apply("size", 96)
        assert layer.size == 96

    def test_point_fields_reject_the_wrong_arity(self, qt_app):
        from PySide6.QtWidgets import QLineEdit

        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "text", "pos": [10, 20]}]}).layers[0]
        pane.show_layer(layer)
        edit = QLineEdit("1, 2, 3")
        pane._apply_numbers(edit, "pos", 2)
        assert layer.pos == [10, 20]  # unchanged rather than half-applied

    def test_empty_layer_selection_is_handled(self, qt_app):
        pane = PropertiesPane()
        pane.show_layer(None)
        assert pane._form.rowCount() == 1


class TestMainWindow:
    def test_seeds_and_lists_scenes(self, window):
        assert window.scene_list.count() >= 3
        assert window.current_scene is not None

    def test_layer_list_is_top_layer_first(self, window):
        scene = window.current_scene
        top = scene.layers[-1]
        assert top.describe() in window.layer_list.item(0).text()

    def test_selecting_a_row_shows_that_layer(self, window):
        window.layer_list.setCurrentRow(0)
        assert window.properties._layer is window.current_scene.layers[-1]

    def test_adding_a_layer_persists_it(self, window):
        before = len(window.current_scene.layers)
        window.layer_type_combo.setCurrentText("gauge")
        window._add_layer()
        assert len(window.current_scene.layers) == before + 1
        reloaded = window.library.get(window.current_scene.name)
        assert len(reloaded.layers) == before + 1

    def test_deleting_a_layer_persists(self, window):
        window.layer_type_combo.setCurrentText("solid")
        window._add_layer()
        window.layer_list.setCurrentRow(0)
        before = len(window.current_scene.layers)
        window._delete_layer()
        assert len(window.current_scene.layers) == before - 1

    def test_duplicating_a_layer_makes_an_independent_copy(self, window):
        window.layer_list.setCurrentRow(0)
        original = window._selected_layer()
        window._duplicate_layer()
        copy = window.current_scene.layers[-1]
        assert copy is not original
        assert copy.to_dict() == original.to_dict()

    def test_connected_stats_show_the_device_identity(self, window):
        window._render_stats(
            EngineStats(
                connected=True,
                panel_label="Jonsbo DS916",
                address="COM5",
                model="D215-FL7707N-9.16inch-hor",
                firmware="2.2",
                width=1920,
                height=462,
            )
        )
        text = window.device_chip.text()
        assert "COM5" in text and "D215-FL7707N-9.16inch-hor" in text

    def test_an_error_is_surfaced_in_the_chip(self, window):
        window._render_stats(EngineStats(connected=False, last_error="port is busy"))
        assert "port is busy" in window.device_chip.text()

    def test_brightness_stays_disabled_until_the_command_is_verified(self, window):
        assert not window.brightness.isEnabled()
        assert "not yet verified" in window.brightness.toolTip()

    def test_pause_button_toggles_the_engine(self, window):
        window.pause_button.setChecked(True)
        assert window.engine.is_paused
        assert window.pause_button.text() == "Resume"
        window.pause_button.setChecked(False)
        assert not window.engine.is_paused
