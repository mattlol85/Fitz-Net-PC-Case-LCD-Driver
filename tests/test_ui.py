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
from fitzlcd.ui.preview import MAX_PREVIEW_HEIGHT, PreviewWidget, pil_to_qimage  # noqa: E402
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
        assert preview.heightForWidth(800) == int(800 / (1920 / 462))

    def test_adopts_the_aspect_of_the_frame_it_receives(self, qt_app):
        preview = PreviewWidget()
        preview._on_frame(Image.new("RGB", (320, 320)))
        assert preview.heightForWidth(320) == 320

    def test_height_is_capped_so_a_portrait_panel_leaves_room_for_the_editor(self, qt_app):
        preview = PreviewWidget()
        preview._on_frame(Image.new("RGB", (462, 1920)))
        # Uncapped, a portrait frame would ask for four screens of height.
        assert preview.heightForWidth(462) == MAX_PREVIEW_HEIGHT
        assert preview.maximumHeight() == MAX_PREVIEW_HEIGHT


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
        pane._apply_lengths(edit, "pos", 2)
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


class TestOrientationControl:
    def test_offers_every_quarter_turn(self, window):
        assert [
            window.rotation_combo.itemData(i) for i in range(window.rotation_combo.count())
        ] == [0, 90, 180, 270]

    def test_selecting_an_orientation_rotates_the_engine_and_persists(self, window):
        window.rotation_combo.setCurrentIndex(1)  # 90 degrees
        assert window.engine.config.rotation == 90
        assert window.config.rotation == 90
        assert AppConfig.load(window.config.save()).rotation == 90

    def test_starts_on_the_configured_orientation(self, qt_app, tmp_path, monkeypatch):
        monkeypatch.setenv("FITZLCD_HOME", str(tmp_path))
        config = AppConfig()
        config.rotation = 180
        config.minimise_to_tray = False
        win = MainWindow(RenderEngine(), SceneLibrary(tmp_path / "scenes"), config)
        try:
            assert win.rotation_combo.currentData() == 180
        finally:
            win.close()

    def test_status_line_reports_the_orientation(self, window):
        window._render_stats(
            EngineStats(
                connected=True,
                panel_label="DS916",
                address="COM5",
                width=462,
                height=1920,
                rotation=90,
            )
        )
        assert "462×1920" in window.device_chip.text()
        assert "90°" in window.device_chip.text()


class TestSceneFlipping:
    def test_next_and_previous_wrap_around(self, window):
        count = window.scene_list.count()
        assert count > 1
        window.scene_list.setCurrentRow(0)
        window.previous_scene()
        assert window.scene_list.currentRow() == count - 1
        window.next_scene()
        assert window.scene_list.currentRow() == 0
        window.next_scene()
        assert window.scene_list.currentRow() == 1

    def test_flipping_activates_the_scene_on_the_engine(self, window):
        window.scene_list.setCurrentRow(0)
        first = window.current_scene.name
        window.next_scene()
        assert window.current_scene.name != first
        assert window.engine.stats.scene_name == window.current_scene.name

    def test_cycling_is_off_by_default(self, window):
        assert window.cycle_spin.value() == 0
        assert not window._cycle_timer.isActive()

    def test_setting_an_interval_starts_the_timer_and_persists(self, window):
        window.cycle_spin.setValue(20)
        assert window._cycle_timer.isActive()
        assert window._cycle_timer.interval() == 20_000
        assert window.config.cycle_seconds == 20

    def test_zero_stops_cycling(self, window):
        window.cycle_spin.setValue(15)
        window.cycle_spin.setValue(0)
        assert not window._cycle_timer.isActive()

    def test_a_single_scene_library_does_not_cycle(self, qt_app, tmp_path, monkeypatch):
        monkeypatch.setenv("FITZLCD_HOME", str(tmp_path))
        library = SceneLibrary(tmp_path / "scenes")
        library.save(Scene(name="Only One"))
        config = AppConfig()
        config.cycle_seconds = 5
        config.minimise_to_tray = False
        win = MainWindow(RenderEngine(), library, config)
        try:
            assert not win._cycle_timer.isActive()
        finally:
            win.close()


class TestLengthEditing:
    """The properties pane must round-trip relative coordinates, not just pixels."""

    def test_shows_relative_values_as_written(self, qt_app):
        from PySide6.QtWidgets import QLineEdit

        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "text", "pos": ["50%", "center"]}]}).layers[0]
        pane.show_layer(layer)
        rows = [
            pane._form.itemAt(i, pane._form.ItemRole.FieldRole).widget()
            for i in range(pane._form.rowCount())
        ]
        edits = [w.text() for w in rows if isinstance(w, QLineEdit)]
        assert "50%, center" in edits

    def test_accepts_percentages_and_keywords(self, qt_app):
        from PySide6.QtWidgets import QLineEdit

        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "text", "pos": [10, 20]}]}).layers[0]
        pane.show_layer(layer)
        pane._apply_lengths(QLineEdit("50%, center"), "pos", 2)
        assert layer.pos == ["50%", "center"]

    def test_plain_numbers_stay_numbers(self, qt_app):
        from PySide6.QtWidgets import QLineEdit

        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "gauge"}]}).layers[0]
        pane.show_layer(layer)
        pane._apply_lengths(QLineEdit("10, 20, 30, 40"), "rect", 4)
        assert layer.rect == [10, 20, 30, 40]

    def test_negative_offsets_survive(self, qt_app):
        from PySide6.QtWidgets import QLineEdit

        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "text"}]}).layers[0]
        pane.show_layer(layer)
        pane._apply_lengths(QLineEdit("-40, -10"), "pos", 2)
        assert layer.pos == [-40, -10]

    def test_every_builtin_scene_can_be_shown_in_the_properties_pane(self, qt_app):
        """Regression: a 'center' coordinate used to crash the editor."""
        from fitzlcd.scenes_builtin import DEFAULT_SCENES

        pane = PropertiesPane()
        for name, data in DEFAULT_SCENES.items():
            for layer in Scene.from_dict(data).layers:
                pane.show_layer(layer)  # must not raise
                assert pane._layer is layer, name


class TestUpdateButton:
    def test_hidden_outside_the_packaged_build(self, window):
        """Source installs update through git/pip, so the button is pointless there."""
        assert window.update_btn.isVisibleTo(window) is False

    def test_offers_the_version_once_a_check_finds_one(self, window, qt_app):
        from fitzlcd.ui.updates import UpdateController
        from fitzlcd.updater import Release

        controller = UpdateController(window.update_btn, window.config, parent=window)
        assert controller.button.text() == "Check for updates"

        release = Release(
            version="9.9.9",
            notes="notes",
            url="https://example.invalid/FitzLCD-9.9.9-windows.zip",
            size=1,
            page="https://example.invalid",
        )
        controller._on_checked((release, True))

        assert controller.available is release
        assert controller.button.text() == "Update to v9.9.9"
        # The check is stamped so a restart doesn't immediately re-check.
        assert window.config.update_last_check > 0
