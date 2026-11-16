"""The pixel strip's wire format: encode -> screenshot -> decode."""

from __future__ import annotations

import math

import pytest
from PIL import Image

from fitzlcd.sources.wow import protocol as p


def _player(**overrides) -> p.PlayerState:
    values = {
        "flags": p.P_HAS_POSITION | p.P_HAS_FACING | p.P_COMBAT,
        "map_id": 1429,
        "x": p.POSITION_SCALE // 4,
        "y": p.POSITION_SCALE // 2,
        "facing": 64,
        "level": 23,
        "class_id": 8,
        "health": 87,
        "power": p.UNKNOWN,
    }
    values.update(overrides)
    return p.PlayerState(**values)


def _member(**overrides) -> p.MemberState:
    values = {
        "flags": p.M_PRESENT | p.M_ONLINE | p.M_HAS_POSITION,
        "class_id": 1,
        "level": 22,
        "health": 40,
        "map_id": 1429,
        "x": 30000,
        "y": 40000,
    }
    values.update(overrides)
    return p.MemberState(**values)


def _screenshot(data: bytes, pitch: int = 3, pad: tuple[int, int] = (0, 0)) -> Image.Image:
    strip = p.render_strip(p.frame_levels(data), pitch)
    canvas = Image.new("RGB", (strip.width + 120, strip.height + 20), (40, 60, 30))
    canvas.paste(strip, pad)
    return canvas


class TestRoundTrip:
    def test_fast_lane_survives_the_screen(self):
        data = p.pack_frame(7, _player(), [_member(), _member(class_id=5, health=p.UNKNOWN)])
        frame = p.decode_image(_screenshot(data))
        assert frame.seq == 7
        assert frame.player == _player()
        assert frame.party[0] == _member()
        assert frame.party[1].health == p.UNKNOWN
        assert not frame.party[2].present and not frame.party[3].present
        assert frame.chunk is None

    def test_absolute_values_and_power_type_round_trip(self):
        player = _player(power_type=0, hp=1234, hp_max=2000, pw=p.UNKNOWN32, pw_max=4_000_000)
        frame = p.decode_image(_screenshot(p.pack_frame(2, player)))
        assert frame.player == player
        assert frame.player.pw == p.UNKNOWN32  # unknown stays unknown, never 0

    def test_a_v1_addon_is_told_to_update(self):
        data = bytearray(p.pack_frame(1, _player()))
        data[0] = 1
        data[p.BODY_BYTES :] = p.fletcher16(data[: p.BODY_BYTES]).to_bytes(2, "big")
        with pytest.raises(p.StripError) as err:
            p.unpack_frame(bytes(data))
        assert err.value.reason == "version"

    def test_friendly_accessors(self):
        frame = p.decode_image(_screenshot(p.pack_frame(1, _player())))
        x, y = frame.player.position
        assert x == pytest.approx(0.25, abs=1e-4) and y == pytest.approx(0.5, abs=1e-4)
        assert frame.player.facing_radians == pytest.approx(math.pi / 2)

    def test_missing_position_and_facing_are_none_not_zero(self):
        player = _player(flags=p.P_INSTANCE, x=0, y=0)
        frame = p.decode_image(_screenshot(p.pack_frame(1, player)))
        assert frame.player.position is None
        assert frame.player.facing_radians is None

    @pytest.mark.parametrize("pitch", [2, 3, 4, 6])
    def test_any_cell_pitch_is_found(self, pitch):
        data = p.pack_frame(3, _player())
        assert p.decode_image(_screenshot(data, pitch)).player == _player()

    def test_a_small_offset_from_the_corner_is_tolerated(self):
        data = p.pack_frame(3, _player())
        assert p.decode_image(_screenshot(data, 3, pad=(5, 2))).player == _player()

    def test_slow_lane_chunk_round_trips(self):
        record = p.encode_record({"zone": "Elwynn Forest", "sub": "Goldshire"})
        (chunk,) = p.chunk_record(p.RECORD_INFO, record)
        frame = p.decode_image(_screenshot(p.pack_frame(9, _player(), chunk=chunk)))
        assert frame.chunk == chunk


class TestCorruption:
    def test_gamma_shifted_screen_still_decodes(self):
        data = p.pack_frame(4, _player(), [_member()])
        image = _screenshot(data).point(lambda v: int(255 * (v / 255) ** 0.8))
        assert p.decode_image(image).player == _player()

    def test_washed_out_screen_is_a_calibration_error(self):
        data = p.pack_frame(4, _player())
        image = _screenshot(data).point(lambda v: 128 + v // 32)
        with pytest.raises(p.StripError) as err:
            p.decode_image(image)
        assert err.value.reason in ("calibration", "not_found")

    def test_flipped_bits_fail_the_checksum(self):
        data = bytearray(p.pack_frame(4, _player()))
        data[10] ^= 0x10
        with pytest.raises(p.StripError) as err:
            p.unpack_frame(bytes(data))
        assert err.value.reason == "checksum"

    def test_other_version_is_reported(self):
        body = bytearray(p.pack_frame(4, _player())[: p.BODY_BYTES])
        body[0] = 9
        data = bytes(body) + p.fletcher16(body).to_bytes(2, "big")
        with pytest.raises(p.StripError) as err:
            p.unpack_frame(data)
        assert err.value.reason == "version"

    def test_no_strip_on_screen(self):
        with pytest.raises(p.StripError) as err:
            p.decode_image(Image.new("RGB", (300, 30), (20, 20, 20)))
        assert err.value.reason == "not_found"

    def test_cut_off_strip(self):
        image = _screenshot(p.pack_frame(1, _player())).crop((0, 0, 100, 6))
        with pytest.raises(p.StripError) as err:
            p.decode_image(image)
        assert err.value.reason == "not_found"

    def test_impossible_slow_header_is_dropped(self):
        bad = p.SlowChunk(p.RECORD_INFO, 1, 5, 2, b"x")  # index >= count
        frame = p.unpack_frame(p.pack_frame(1, _player(), chunk=bad))
        assert frame.chunk is None


class TestRecordAssembler:
    def test_reassembles_out_of_order_and_emits_once(self):
        record = ("k=" + "x" * (p.SLOW_CAPACITY * 2 + 50)).encode()
        chunks = p.chunk_record(p.RECORD_MAP, record)
        assert len(chunks) == 3
        asm = p.RecordAssembler()
        assert asm.feed(chunks[2]) is None
        assert asm.feed(chunks[0]) is None
        assert asm.feed(chunks[1]) == (p.RECORD_MAP, record)
        assert all(asm.feed(c) is None for c in chunks)  # unchanged: not again

    def test_a_changed_record_restarts(self):
        asm = p.RecordAssembler()
        old = p.chunk_record(1, ("a=" + "1" * (p.SLOW_CAPACITY + 50)).encode())
        new = p.chunk_record(1, ("a=" + "2" * (p.SLOW_CAPACITY + 50)).encode())
        asm.feed(old[0])
        assert asm.feed(new[1]) is None  # half a new one does not complete the old
        assert asm.feed(new[0]) == (1, ("a=" + "2" * (p.SLOW_CAPACITY + 50)).encode())

    def test_chunks_that_do_not_add_up_are_discarded(self):
        asm = p.RecordAssembler()
        a = p.chunk_record(1, b"a=" + b"1" * 200)
        forged = p.SlowChunk(1, a[0].digest, 1, 2, b"tampered")
        asm.feed(a[0])
        assert asm.feed(forged) is None

    def test_none_and_nonsense_are_ignored(self):
        asm = p.RecordAssembler()
        assert asm.feed(None) is None
        assert asm.feed(p.SlowChunk(1, 0, 0, 0, b"")) is None
        assert asm.feed(p.SlowChunk(1, 0, 0, p.MAX_CHUNKS + 1, b"")) is None


class TestRecords:
    def test_parse_record(self):
        assert p.parse_record(b"zone=Elwynn=Forest\nbad line\nxp=10") == {
            "zone": "Elwynn=Forest",
            "xp": "10",
        }

    def test_bad_utf8_does_not_raise(self):
        assert p.parse_record(b"name=\xff\xfe")["name"]

    def test_encode_strips_newlines(self):
        assert p.parse_record(p.encode_record({"a": "x\ny"})) == {"a": "x y"}


class TestGauges:
    """Health/power bars drawn under the strip for values the game keeps secret."""

    GAUGED = p.P_HAS_POSITION | p.P_HP_GAUGE | p.P_POWER_GAUGE

    def _shot(self, gauges, flags=GAUGED, pitch=3, pad=(0, 0)) -> Image.Image:
        data = p.pack_frame(1, _player(flags=flags, health=p.UNKNOWN))
        strip = p.render_strip(p.frame_levels(data), pitch, gauges=gauges)
        canvas = Image.new("RGB", (strip.width + 120, strip.height + 20), (40, 60, 30))
        canvas.paste(strip, pad)
        return canvas

    @pytest.mark.parametrize("pitch", [3, 4, 6])
    @pytest.mark.parametrize("fill", [0.0, 0.25, 0.731, 1.0])
    def test_fill_is_measured(self, fill, pitch):
        frame = p.decode_image(self._shot([fill, 1 - fill], pitch=pitch, pad=(5, 3)))
        assert frame.gauges[0] == pytest.approx(fill, abs=0.01)
        assert frame.gauges[1] == pytest.approx(1 - fill, abs=0.01)

    def test_without_the_flag_a_gauge_is_ignored(self):
        frame = p.decode_image(self._shot([0.5, 0.5], flags=p.P_HP_GAUGE))
        assert frame.gauges[0] == pytest.approx(0.5, abs=0.01)
        assert frame.gauges[1] is None

    def test_an_old_addon_draws_no_gauges(self):
        """Game scenery under the strip is never read as a bar."""
        frame = p.decode_image(self._shot([None, None]))
        assert frame.gauges == (None, None)

    def test_a_cut_off_capture_reads_no_gauge(self):
        image = self._shot([0.5, 0.5])
        frame = p.decode_image(image.crop((0, 0, image.width, p.ROWS * 3)))
        assert frame.gauges == (None, None)

    def test_a_bar_that_does_not_fill_from_the_left_is_scenery(self):
        image = self._shot([1.0, 1.0])
        y = p.ROWS * 3 + 1
        image.paste((p.GAUGE_TRACK,) * 3, (0, y - 1, 40, y + 2))  # dark start, bright end
        assert p.decode_image(image).gauges[0] is None

    def test_coloured_pixels_are_not_a_gauge(self):
        image = self._shot([0.5, 0.5])
        y = (p.ROWS + 1) * 3 + 1
        image.paste((200, 40, 40), (60, y - 1, 64, y + 2))  # something red over the bar
        assert p.decode_image(image).gauges[1] is None
