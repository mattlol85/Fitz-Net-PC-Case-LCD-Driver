"""GUI tests, run against Qt's offscreen platform so they need no desktop."""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

pytest.importorskip("PySide6")

from PIL import Image  # noqa: E402
from PySide6.QtWidgets import QApplication, QLineEdit  # noqa: E402

import fitzlcd.render.layers  # noqa: E402, F401 - registers the built-in layer types
from fitzlcd.config import AppConfig, SceneLibrary  # noqa: E402
from fitzlcd.engine import EngineStats, RenderEngine  # noqa: E402
from fitzlcd.render.geometry import ANCHORS  # noqa: E402
from fitzlcd.render.scene import Scene  # noqa: E402
from fitzlcd.sources import catalog  # noqa: E402
from fitzlcd.ui.controls import (  # noqa: E402
    AnchorPicker,
    LengthEdit,
    LengthsEdit,
    MetricPicker,
    join_length,
    split_length,
)
from fitzlcd.ui.history import SceneHistory  # noqa: E402
from fitzlcd.ui.main_window import MainWindow, friendly_status  # noqa: E402
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
    win.thumbnails.wait()
    win.close()


@pytest.fixture
def editor(window):
    """The window, switched to the Customize view of its current scene."""
    window.show_customize()
    return window.customize


def text_layer(**fields):
    return Scene.from_dict({"layers": [{"type": "text", **fields}]}).layers[0]


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

    def test_the_editor_can_shrink_the_cap(self, qt_app):
        preview = PreviewWidget()
        preview.set_max_height(120)
        assert preview.heightForWidth(1920) == 120


class TestTheme:
    def test_applies_without_error(self, qt_app):
        from PySide6.QtGui import QPalette

        from fitzlcd.ui.theme import COLORS, apply_theme

        apply_theme(qt_app)
        window_color = qt_app.palette().color(QPalette.ColorRole.Window).name()
        assert window_color == COLORS["bg"]

    def test_no_unscoped_frame_rule(self):
        """A bare QFrame rule boxes every QLabel, since QLabel is a QFrame."""
        from fitzlcd.ui.theme import build_stylesheet

        assert "\nQFrame {" not in build_stylesheet()


class TestPropertiesPane:
    def test_builds_an_editor_for_every_declared_field(self, qt_app):
        pane = PropertiesPane()
        layer = text_layer()
        pane.show_layer(layer)
        # 'visible' is the eye in the layer list, not a form row.
        expected = {f.name for f in layer.FIELDS} - {"visible"}
        assert set(pane.editors) == expected

    def test_groups_fields_and_collapses_advanced(self, qt_app):
        pane = PropertiesPane()
        pane.show_layer(text_layer())
        assert list(pane.sections) == ["content", "layout", "appearance", "advanced"]
        assert pane.sections["advanced"].header.isChecked() is False
        assert pane.sections["content"].header.isChecked() is True

    def test_editing_a_field_updates_the_layer(self, qt_app):
        pane = PropertiesPane()
        layer = text_layer()
        pane.show_layer(layer)
        pane._apply("size", 96)
        assert layer.size == 96

    def test_announces_edits_before_and_after(self, qt_app):
        pane = PropertiesPane()
        layer = text_layer()
        pane.show_layer(layer)
        seen = []
        pane.about_to_change.connect(lambda n: seen.append(("before", n, layer.size)))
        pane.layer_changed.connect(lambda n: seen.append(("after", n, layer.size)))
        pane._apply("size", 30)
        assert seen == [("before", "size", 48), ("after", "size", 30)]

    def test_point_fields_reject_the_wrong_arity(self, qt_app):
        pane = PropertiesPane()
        layer = text_layer(pos=[10, 20])
        pane.show_layer(layer)
        pane._apply_lengths(QLineEdit("1, 2, 3"), "pos", 2)
        assert layer.pos == [10, 20]  # unchanged rather than half-applied

    def test_empty_layer_selection_is_handled(self, qt_app):
        pane = PropertiesPane()
        pane.show_layer(None)
        assert pane.editors == {}

    def test_metric_fields_get_a_picker(self, qt_app):
        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "gauge"}]}).layers[0]
        pane.show_layer(layer)
        assert isinstance(pane.editors["metric"], MetricPicker)

    def test_every_builtin_scene_can_be_shown_in_the_properties_pane(self, qt_app):
        """Regression: a 'center' coordinate used to crash the editor."""
        from fitzlcd.scenes_builtin import DEFAULT_SCENES

        pane = PropertiesPane()
        for name, data in DEFAULT_SCENES.items():
            for layer in Scene.from_dict(data).layers:
                pane.show_layer(layer)  # must not raise
                assert pane._layer is layer, name


class TestLengthEditing:
    """Coordinates must round-trip relative values, not just pixels."""

    @pytest.mark.parametrize(
        ("value", "expected"),
        [
            (40, 40),
            (-40, -40),
            ("50%", "50%"),
            ("-10%", "-10%"),
            ("12.5%", "12.5%"),
            ("center", "center"),
            ("centre", "center"),
            ("middle", "center"),
        ],
    )
    def test_round_trips_every_form_of_the_grammar(self, qt_app, value, expected):
        assert join_length(*split_length(value)) == expected
        assert LengthEdit(value).value() == expected

    def test_a_point_editor_reports_both_axes(self, qt_app):
        edit = LengthsEdit(["50%", "center"], "point")
        assert edit.value() == ["50%", "center"]

    def test_switching_units_emits_the_new_value(self, qt_app):
        edit = LengthEdit(40)
        seen = []
        edit.changed.connect(seen.append)
        edit.unit.setCurrentIndex(edit.unit.findData("%"))
        assert seen == ["40%"]

    def test_an_empty_rect_means_the_whole_display(self, qt_app):
        edit = LengthsEdit([], "rect", allow_empty=True)
        assert edit.fill.isChecked()
        assert edit.value() == []
        edit.fill.setChecked(False)
        assert len(edit.value()) == 4

    def test_typed_lists_accept_percentages_and_keywords(self, qt_app):
        pane = PropertiesPane()
        layer = text_layer(pos=[10, 20])
        pane.show_layer(layer)
        pane._apply_lengths(QLineEdit("50%, center"), "pos", 2)
        assert layer.pos == ["50%", "center"]

    def test_plain_numbers_stay_numbers(self, qt_app):
        pane = PropertiesPane()
        layer = Scene.from_dict({"layers": [{"type": "gauge"}]}).layers[0]
        pane.show_layer(layer)
        pane._apply_lengths(QLineEdit("10, 20, 30, 40"), "rect", 4)
        assert layer.rect == [10, 20, 30, 40]

    def test_negative_offsets_survive(self, qt_app):
        pane = PropertiesPane()
        layer = text_layer()
        pane.show_layer(layer)
        pane._apply_lengths(QLineEdit("-40, -10"), "pos", 2)
        assert layer.pos == [-40, -10]


class TestAnchorPicker:
    @pytest.mark.parametrize("anchor", ANCHORS)
    def test_round_trips_all_nine_anchors(self, qt_app, anchor):
        assert AnchorPicker(anchor).value() == anchor

    def test_clicking_a_dot_reports_its_anchor(self, qt_app):
        picker = AnchorPicker("top-left")
        seen = []
        picker.changed.connect(seen.append)
        picker.buttons["bottom-right"].click()
        assert seen == ["bottom-right"]


class TestMetricCatalog:
    def test_known_metrics_have_friendly_names(self):
        assert catalog.describe("cpu.load").label == "Processor usage"
        assert catalog.describe("cpu.load").token == "{cpu.load:.0f}%"

    def test_unknown_metrics_fall_back_to_the_raw_key(self):
        info = catalog.describe("custom.thing")
        assert info.label == "custom.thing"
        assert info.group == "Other"

    def test_live_keys_are_offered_alongside_known_ones(self):
        keys = {m.key for m in catalog.catalog(["custom.thing"])}
        assert {"cpu.load", "custom.thing"} <= keys

    def test_picker_keeps_an_unlisted_value(self, qt_app):
        picker = MetricPicker("custom.thing")
        assert picker.value() == "custom.thing"
        assert picker.currentData() == "custom.thing"


class TestHistory:
    def test_undo_and_redo_walk_the_snapshots(self):
        history = SceneHistory()
        history.record({"n": 1})
        assert history.undo({"n": 2}) == {"n": 1}
        assert history.redo({"n": 1}) == {"n": 2}

    def test_repeated_edits_to_one_field_coalesce(self):
        history = SceneHistory()
        history.record({"n": 1}, key="size")
        history.record({"n": 2}, key="size")
        assert history.undo({"n": 3}) == {"n": 1}
        assert not history.can_undo

    def test_a_new_edit_clears_redo(self):
        history = SceneHistory()
        history.record({"n": 1})
        history.undo({"n": 2})
        history.record({"n": 1})
        assert not history.can_redo


class TestStatus:
    def test_connected(self):
        assert friendly_status(EngineStats(connected=True)) == "Your display is connected"

    def test_searching(self):
        assert "Looking" in friendly_status(EngineStats())

    def test_a_busy_port_suggests_another_app(self):
        text = friendly_status(EngineStats(last_error="could not open port: Access is denied"))
        assert "another app" in text

    def test_paused(self):
        assert "Paused" in friendly_status(EngineStats(connected=True), paused=True)


class TestMainWindow:
    def test_seeds_and_lists_scenes(self, window):
        assert window.scene_list.count() >= 3
        assert window.current_scene is not None

    def test_settings_widgets_exist_before_the_dialog_is_opened(self, window):
        """The update controller needs update_btn at startup."""
        assert window.settings_dialog.isVisible() is False
        for name in ("rotation_combo", "hour12_box", "autostart_box", "tray_box", "update_btn"):
            assert getattr(window, name).window() is window.settings_dialog

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
        # ...but the header stays in plain language.
        assert window.status_title.text() == "Your display is connected"
        assert "COM5" not in window.status_title.text()

    def test_an_error_is_surfaced_in_the_details(self, window):
        window._render_stats(EngineStats(connected=False, last_error="port is busy"))
        assert "port is busy" in window.device_chip.text()
        assert "another app" in window.status_title.text()

    def test_brightness_stays_disabled_until_the_command_is_verified(self, window):
        assert not window.brightness.isEnabled()
        assert "not yet verified" in window.brightness.toolTip()

    def test_pause_button_toggles_the_engine(self, window):
        window.pause_button.setChecked(True)
        assert window.engine.is_paused
        assert window.pause_button.text() == "Resume"
        window.pause_button.setChecked(False)
        assert not window.engine.is_paused

    def test_customize_and_back(self, window):
        window.show_customize()
        assert window.stack.currentWidget() is window.customize
        assert window.customize.scene is window.current_scene
        window.customize.done.emit()
        assert window.stack.currentWidget() is window.home

    def test_renaming_moves_the_file_and_remembers_it(self, window):
        old_path = window.current_scene.path
        window._rename_current("Renamed Scene")
        assert window.current_scene.name == "Renamed Scene"
        assert window.config.active_scene == "Renamed Scene"
        assert not old_path.exists()
        assert window.library.get("Renamed Scene") is not None

    def test_renaming_never_overwrites_another_scene(self, window):
        other = window.scenes[1].name if window.scenes[0] is window.current_scene else None
        other = other or window.scenes[0].name
        window._rename_current(other)
        assert window.current_scene.name != other
        assert window.library.get(other) is not None

    def test_duplicating_a_scene_gets_a_fresh_name(self, window):
        before = window.scene_list.count()
        window._duplicate_scene()
        assert window.scene_list.count() == before + 1
        assert window.current_scene.name.endswith("copy")


class TestCustomize:
    def test_layer_list_is_top_layer_first(self, editor):
        top = [layer for layer in editor.scene.layers if layer.applies_to(editor.frame_size)][-1]
        assert editor.layer_list.item(0).text() == top.describe()

    def test_selecting_a_row_shows_that_layer(self, editor):
        editor.layer_list.setCurrentRow(0)
        assert editor.properties._layer is editor._shown[0]

    def test_adding_a_layer_persists_it(self, editor):
        before = len(editor.scene.layers)
        editor._add_layer("gauge")
        assert len(editor.scene.layers) == before + 1
        assert editor._selected_layer() is editor.scene.layers[-1]
        reloaded = editor.library.get(editor.scene.name)
        assert len(reloaded.layers) == before + 1

    def test_deleting_a_layer_persists(self, editor):
        editor._add_layer("solid")
        before = len(editor.scene.layers)
        editor._delete_layer()
        assert len(editor.scene.layers) == before - 1
        assert len(editor.library.get(editor.scene.name).layers) == before - 1

    def test_duplicating_a_layer_makes_an_independent_copy(self, editor):
        editor.layer_list.setCurrentRow(0)
        original = editor._selected_layer()
        editor._duplicate_layer()
        copy = editor._selected_layer()
        assert copy is not original
        assert copy.to_dict() == original.to_dict()

    def test_undo_restores_a_deleted_layer_and_redo_removes_it_again(self, editor):
        before = [layer.to_dict() for layer in editor.scene.layers]
        editor.layer_list.setCurrentRow(0)
        editor._delete_layer()
        editor.undo()
        assert [layer.to_dict() for layer in editor.scene.layers] == before
        editor.redo()
        assert len(editor.scene.layers) == len(before) - 1

    def test_undo_reverts_a_property_edit(self, editor):
        editor.layer_list.setCurrentRow(0)
        layer = editor._selected_layer()
        original = layer.opacity
        editor.properties._apply("opacity", 0.25)
        editor.undo()
        restored = editor._shown[0]
        assert restored.opacity == original

    def test_reordering_after_a_duplicate_keeps_every_layer(self, editor):
        """Regression: identical labels used to make drag-reordering a no-op."""
        editor.layer_list.setCurrentRow(0)
        editor._duplicate_layer()
        count = len(editor.scene.layers)
        editor.layer_list.setCurrentRow(0)
        top = editor._selected_layer()
        editor._move(1)
        assert len(editor.scene.layers) == count
        assert len({id(layer) for layer in editor.scene.layers}) == count
        assert editor._shown[1] is top

    def test_visibility_toggle(self, editor):
        layer = editor._shown[0]
        was = layer.visible
        editor._toggle_visibility(0)
        assert layer.visible is not was
        editor.undo()
        assert editor._shown[0].visible is was

    def test_other_orientation_layers_are_tucked_away(self, window):
        scene = Scene.from_dict(
            {
                "name": "Split",
                "layers": [
                    {"type": "text", "name": "wide", "orientation": "landscape"},
                    {"type": "text", "name": "tall", "orientation": "portrait"},
                    {"type": "text", "name": "both"},
                ],
            }
        )
        window.library.save(scene)
        window.reload_scenes(select="Split")
        window.show_customize()
        editor = window.customize
        editor.set_frame_size((462, 1920))
        names = [editor.layer_list.item(i).text() for i in range(editor.layer_list.count())]
        assert names == ["both", "tall"]
        assert editor.orientation_box.isVisibleTo(editor)
        editor.orientation_box.setChecked(True)
        assert editor.layer_list.count() == 3


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
            win.thumbnails.wait()
            win.close()

    def test_details_report_the_orientation(self, window):
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
        assert window.cycle_combo.currentData() == 0
        assert not window._cycle_timer.isActive()

    def test_choosing_an_interval_starts_the_timer_and_persists(self, window):
        window._select_cycle(30)
        assert window._cycle_timer.isActive()
        assert window._cycle_timer.interval() == 30_000
        assert window.config.cycle_seconds == 30

    def test_an_unusual_saved_interval_is_kept(self, window):
        window._select_cycle(20)
        assert window.cycle_combo.currentData() == 20
        assert window._cycle_timer.interval() == 20_000

    def test_off_stops_cycling(self, window):
        window._select_cycle(10)
        window._select_cycle(0)
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
            win.thumbnails.wait()
            win.close()


class TestSceneLibraryNames:
    def test_unique_name_avoids_existing_files(self, tmp_path):
        library = SceneLibrary(tmp_path)
        library.save(Scene(name="Clock"))
        assert library.unique_name("Clock") == "Clock 2"
        assert library.unique_name("clock") == "clock 2"  # same slug, same file
        assert library.unique_name("Fresh") == "Fresh"


class TestUpdateButton:
    def test_hidden_outside_the_packaged_build(self, window):
        """Source installs update through git/pip, so the button is pointless there."""
        assert window.update_btn.isVisibleTo(window.settings_dialog) is False

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
