"""Protocol framing tests, anchored to captures from the real device."""

from __future__ import annotations

import pytest

from fitzlcd.panels.ds916 import protocol as proto

# Captured from hardware: the reply to a no-payload START (0x11).
START_REPLY = bytes.fromhex("55AA080011001801")

# Captured from hardware: a GET_INFO reply. Note the raw 0x93 byte inside the
# "angle" value -- it is not valid UTF-8, which is exactly why the parser is lenient.
INFO_REPLY = (
    b'U\xaap\x01\x06{"cmd":"info","data":{"uid":"8370D07832275608000B32020000B004",'
    b'"width":1920,"height":462,"diplay_on":true,"brightness":100,"i_blocks":204560,'
    b'"i_block_size":512,"i_block_free":126744,"i_path":"/data","e_blocks":0,'
    b'"e_block_size":0,"e_block_free":0,"e_path":"/sdcard",'
    b'"model":"D215-FL7707N-9.16inch-hor","version":"2.2","region":"",'
    b'"angle":"\\u0005\\u000b\x93e\\u0015"}}&l'
)


class TestBuildPacket:
    def test_no_payload_framing(self):
        packet = proto.build_packet(proto.Command.START)
        assert packet[:2] == proto.MAGIC
        assert packet[2] | (packet[3] << 8) == len(packet) == 7
        assert packet[4] == 0x11

    def test_checksum_is_additive_over_preceding_bytes(self):
        packet = proto.build_packet(proto.Command.START)
        expected = sum(packet[:-2]) & 0xFFFF
        assert packet[-2] | (packet[-1] << 8) == expected

    def test_payload_is_included_in_length_and_checksum(self):
        packet = proto.build_packet(proto.Command.SET_BRIGHTNESS, bytes([60]))
        assert packet[2] | (packet[3] << 8) == len(packet) == 8
        assert packet[5] == 60
        assert packet[-2] | (packet[-1] << 8) == sum(packet[:-2]) & 0xFFFF

    def test_oversized_payload_rejected(self):
        with pytest.raises(ValueError):
            proto.build_packet(proto.Command.SET_REGION, b"x" * 0x10000)


class TestParsePacket:
    def test_parses_captured_start_reply(self):
        key, payload = proto.parse_packet(START_REPLY)
        assert key == proto.Command.START
        assert payload == b"\x00"

    def test_checksum_matches_spec_worked_example(self):
        assert proto.checksum(START_REPLY[:-2]) == 0x0118

    def test_rejects_bad_magic(self):
        with pytest.raises(proto.ProtocolError, match="magic"):
            proto.parse_packet(b"\x00\x00\x08\x00\x11\x00\x18\x01")

    def test_rejects_short_buffer(self):
        with pytest.raises(proto.ProtocolError, match="too short"):
            proto.parse_packet(b"\x55\xaa\x08")

    def test_rejects_truncated_packet(self):
        with pytest.raises(proto.ProtocolError, match="truncated"):
            proto.parse_packet(START_REPLY[:-1])

    def test_rejects_bad_checksum(self):
        corrupt = bytearray(START_REPLY)
        corrupt[-1] ^= 0xFF
        with pytest.raises(proto.ProtocolError, match="checksum"):
            proto.parse_packet(bytes(corrupt))


class TestInfoParsing:
    def test_extracts_device_data(self):
        info = proto.extract_json(INFO_REPLY)
        assert info is not None
        assert info["model"] == "D215-FL7707N-9.16inch-hor"
        assert info["version"] == "2.2"
        assert (info["width"], info["height"]) == (1920, 462)

    def test_incomplete_buffer_returns_none(self):
        assert proto.extract_json(INFO_REPLY[:60]) is None
        assert proto.extract_json(b"") is None

    def test_raw_angle_preserves_non_utf8_bytes(self):
        raw = proto.raw_angle(INFO_REPLY)
        assert b"\x93" in raw

    def test_sentinel_angle_means_clockwise_rotation(self):
        info = proto.extract_json(INFO_REPLY)
        assert proto.transform_from_info(info) == "rot270"

    @pytest.mark.parametrize(
        ("angle", "expected"),
        [(0, "none"), (90, "rot90"), (180, "rot180"), (270, "rot270"), ("90", "rot90")],
    )
    def test_numeric_angle_is_taken_at_face_value(self, angle, expected):
        assert proto.transform_from_info({"angle": angle}) == expected


class TestChunking:
    def test_chunks_cover_payload_exactly(self):
        payload = bytes(range(256)) * 400  # 102,400 bytes
        chunks = list(proto.chunk_image(payload))
        assert b"".join(chunks) == payload
        assert all(len(c) <= proto.IMAGE_CHUNK_SIZE for c in chunks)
        assert len(chunks) == 5

    def test_short_payload_is_one_chunk(self):
        assert list(proto.chunk_image(b"\xff\xd9")) == [b"\xff\xd9"]


def test_ota_command_is_never_in_the_verified_set():
    """OTA can brick the panel; nothing should treat it as safe to send."""
    assert proto.Command.OTA_HEADER not in proto.VERIFIED_COMMANDS
