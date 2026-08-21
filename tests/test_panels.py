"""Panel abstraction, registry and virtual-driver tests. No hardware required."""

from __future__ import annotations

import pytest

from fitzlcd.panels import registry
from fitzlcd.panels.base import Panel, PanelCaps, PanelError, PanelHandle
from fitzlcd.panels.ds916.driver import DS916Panel
from fitzlcd.panels.virtual import VirtualPanel
from fitzlcd.render.encode import Transform


class FakePort:
    """Stands in for a pyserial ``ListPortInfo``."""

    def __init__(self, device, vid, pid, description="fake"):
        self.device = device
        self.vid = vid
        self.pid = pid
        self.description = description
        self.serial_number = "FAKE123"


class TestCaps:
    def test_geometry_helpers(self):
        caps = PanelCaps(1920, 462, Transform.ROT_270)
        assert caps.size == (1920, 462)
        assert caps.aspect == pytest.approx(4.156, abs=0.01)


class TestDetection:
    def test_matches_only_known_vid_pid(self, monkeypatch):
        ports = [
            FakePort("COM5", 0x33C3, 0x7788),
            FakePort("COM3", 0x1A86, 0x7523),  # some other serial adapter
            FakePort("COM1", None, None),  # a motherboard port
        ]
        monkeypatch.setattr("fitzlcd.panels.ds916.driver.list_ports.comports", lambda: ports)

        handles = DS916Panel.detect()
        assert [h.address for h in handles] == ["COM5"]
        assert handles[0].driver is DS916Panel

    def test_no_panels_is_empty_not_an_error(self, monkeypatch):
        monkeypatch.setattr("fitzlcd.panels.ds916.driver.list_ports.comports", lambda: [])
        assert DS916Panel.detect() == []

    def test_autodetect_survives_a_broken_driver(self, monkeypatch):
        class BrokenPanel(Panel):
            @classmethod
            def detect(cls):
                raise RuntimeError("driver exploded")

            def open(self): ...
            def close(self): ...
            def push_frame(self, payload): ...

        monkeypatch.setattr(registry, "_DRIVERS", [BrokenPanel])
        assert registry.autodetect(include_virtual=True) == VirtualPanel.detect()

    def test_virtual_panel_is_hidden_unless_requested(self, monkeypatch):
        monkeypatch.setattr("fitzlcd.panels.ds916.driver.list_ports.comports", lambda: [])
        assert registry.autodetect() == []
        assert len(registry.autodetect(include_virtual=True)) == 1


class TestResolve:
    def test_virtual_selector(self):
        handle = registry.resolve("virtual")
        assert handle is not None
        assert handle.driver is VirtualPanel

    def test_address_selector(self, monkeypatch):
        ports = [FakePort("COM5", 0x33C3, 0x7788)]
        monkeypatch.setattr("fitzlcd.panels.ds916.driver.list_ports.comports", lambda: ports)
        handle = registry.resolve("com5")
        assert handle is not None and handle.address == "COM5"

    def test_unknown_selector_returns_none(self, monkeypatch):
        monkeypatch.setattr("fitzlcd.panels.ds916.driver.list_ports.comports", lambda: [])
        assert registry.resolve("COM99") is None

    def test_auto_prefers_real_hardware(self, monkeypatch):
        ports = [FakePort("COM5", 0x33C3, 0x7788)]
        monkeypatch.setattr("fitzlcd.panels.ds916.driver.list_ports.comports", lambda: ports)
        handle = registry.resolve("auto")
        assert handle is not None and handle.driver is DS916Panel


class TestVirtualPanel:
    def test_lifecycle_and_frame_accounting(self, tmp_path):
        panel = VirtualPanel(dump_path=tmp_path / "frame.jpg")
        with panel:
            assert panel.is_open
            assert panel.caps.size == (1920, 462)
            assert panel.caps.transform is Transform.ROT_270
            panel.push_frame(b"\xff\xd8jpeg\xff\xd9")
            panel.push_frame(b"\xff\xd8more\xff\xd9")

        assert panel.frame_count == 2
        assert panel.byte_count == 16
        assert (tmp_path / "frame.jpg").read_bytes() == b"\xff\xd8more\xff\xd9"
        assert not panel.is_open

    def test_caps_before_open_is_an_error(self):
        with pytest.raises(PanelError, match="not open"):
            _ = VirtualPanel().caps

    def test_close_is_idempotent(self):
        panel = VirtualPanel(dump_path=None)
        panel.open()
        panel.close()
        panel.close()

    def test_handle_opens_a_working_panel(self):
        handle = VirtualPanel.detect()[0]
        assert isinstance(handle, PanelHandle)
        panel = handle.open()
        try:
            assert panel.is_open
        finally:
            panel.close()


class TestDS916Caps:
    def test_caps_derived_from_device_info(self):
        info = {
            "width": 1920,
            "height": 462,
            "model": "D215-FL7707N-9.16inch-hor",
            "version": "2.2",
            "angle": "\x05\x0b�e\x15",
        }
        caps = DS916Panel._caps_from_info(info)
        assert caps.size == (1920, 462)
        assert caps.transform is Transform.ROT_270
        assert caps.model == "D215-FL7707N-9.16inch-hor"
        assert caps.firmware == "2.2"

    def test_brightness_stays_disabled_until_verified_on_hardware(self):
        caps = DS916Panel._caps_from_info({"angle": 0})
        assert caps.supports_brightness is False

    def test_frame_rate_is_capped_because_flooding_wedges_the_panel(self):
        caps = DS916Panel._caps_from_info({"angle": 0})
        assert caps.max_fps <= 60
