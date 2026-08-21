"""Wire protocol for the Jonsbo DS916 / "Jungle Leopard" USB panel.

Pure functions only -- no I/O -- so the framing can be unit-tested without hardware.
See ``docs/Jonsbo_DS916_Protocol_Spec.md`` for the derivation of everything here.
"""

from __future__ import annotations

import json
from enum import IntEnum

MAGIC = b"\x55\xaa"
HEADER_LEN = 5  # magic(2) + length(2) + key(1)
CHECKSUM_LEN = 2

#: USB VID/PID pairs known to speak this protocol. Adding a sibling model is a
#: one-line change here.
KNOWN_IDS: tuple[tuple[int, int], ...] = ((0x33C3, 0x7788),)

#: The vendor app opens the port at this rate. CDC-ACM ignores it, but drivers
#: still expect a sane value.
BAUD_RATE = 2_000_000

#: Frame payloads are written in chunks of this size with no per-chunk framing.
IMAGE_CHUNK_SIZE = 20 * 1024


class Command(IntEnum):
    """Command keys. Only RESTART/INFO/START are confirmed against hardware."""

    RESTART = 0x01
    SET_BRIGHTNESS = 0x03  # unverified -- sourced from vendor app code
    GET_INFO = 0x06  # confirmed
    OTA_HEADER = 0x0C  # deliberately never sent: bricking risk
    START = 0x11  # confirmed -- required before any frame data
    SET_MOTION_BEFORE_OFF = 0x14  # unverified
    SET_MOTION_TIMEOUT = 0x15  # unverified, firmware >= 2.8
    SET_REGION = 0x20  # unverified
    CLOSE = 0x21  # unverified, firmware >= 3.1
    SET_SERIAL = 0x23  # unverified
    SET_MOTOR = 0x25  # unverified, not applicable to the DS916
    SET_REALTIME_TIMEOUT = 0x26  # unverified, firmware >= 4.1


#: Command keys we have actually seen work on real hardware.
VERIFIED_COMMANDS = frozenset({Command.GET_INFO, Command.START})

#: Sent right after connecting, before querying device info: two JPEG EOI markers
#: then four zero bytes. Mirrors what the vendor app does.
CLEAR_SEQUENCE: tuple[bytes, ...] = (b"\xff\xd9\xff\xd9", b"\x00\x00\x00\x00")


class ProtocolError(Exception):
    """Raised when a response cannot be parsed as a valid packet."""


def checksum(data: bytes) -> int:
    """16-bit additive checksum of every byte preceding the checksum field."""
    return sum(data) & 0xFFFF


def build_packet(key: int, payload: bytes = b"") -> bytes:
    """Frame a command: magic, total length, key, payload, additive checksum."""
    payload = bytes(payload)
    length = len(payload) + HEADER_LEN + CHECKSUM_LEN
    if length > 0xFFFF:
        raise ValueError(f"payload too large: {len(payload)} bytes")
    body = MAGIC + bytes([length & 0xFF, (length >> 8) & 0xFF, int(key)]) + payload
    total = checksum(body)
    return body + bytes([total & 0xFF, (total >> 8) & 0xFF])


def parse_packet(data: bytes, *, verify_checksum: bool = True) -> tuple[int, bytes]:
    """Split a response into ``(key, payload)``.

    Raises :class:`ProtocolError` if the buffer is too short, lacks the magic
    header, is shorter than its declared length, or fails the checksum.
    """
    if len(data) < HEADER_LEN + CHECKSUM_LEN:
        raise ProtocolError(f"packet too short: {len(data)} bytes")
    if not data.startswith(MAGIC):
        raise ProtocolError(f"bad magic: {data[:2].hex()}")
    declared = data[2] | (data[3] << 8)
    if len(data) < declared:
        raise ProtocolError(f"truncated packet: declared {declared}, got {len(data)}")
    packet = data[:declared]
    if verify_checksum:
        expected = packet[-2] | (packet[-1] << 8)
        actual = checksum(packet[:-CHECKSUM_LEN])
        if expected != actual:
            raise ProtocolError(f"checksum mismatch: expected {expected:#06x}, got {actual:#06x}")
    return packet[4], packet[HEADER_LEN:-CHECKSUM_LEN]


def extract_json(buffer: bytes) -> dict | None:
    """Best-effort parse of a ``GET_INFO`` reply out of a growing read buffer.

    The reply spans multiple USB reads, so callers accumulate bytes and retry;
    ``None`` means "not complete yet". The firmware embeds raw non-UTF-8 bytes in
    the ``angle`` field (see :func:`transform_from_info`), so the payload is
    decoded leniently rather than strictly.
    """
    start = buffer.find(b"{")
    end = buffer.rfind(b"}")
    if start < 0 or end <= start:
        return None
    text = buffer[start : end + 1].decode("utf-8", errors="replace")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    data = obj.get("data", obj)
    return data if isinstance(data, dict) else obj


def raw_angle(buffer: bytes) -> bytes:
    """Return the raw bytes of the ``angle`` field, before lossy decoding.

    The DS916's angle is a magic sentinel containing bytes that are not valid
    UTF-8, so the decoded string cannot be trusted to round-trip.
    """
    key = b'"angle":"'
    start = buffer.find(key)
    if start < 0:
        return b""
    start += len(key)
    end = buffer.find(b'"', start)
    return buffer[start:end] if end > start else b""


def transform_from_info(info: dict) -> str:
    """Derive the scan-out rotation for a panel from its ``getDeviceInfo`` reply.

    A numeric ``angle`` is taken at face value. Anything else is the DS916-family
    sentinel meaning "mounted landscape, frame buffer is native portrait", which
    needs a clockwise rotation before encoding. Returns a
    :class:`fitzlcd.render.encode.Transform` value.
    """
    angle = info.get("angle")
    if isinstance(angle, (int, float)):
        return {0: "none", 90: "rot90", 180: "rot180", 270: "rot270"}.get(int(angle) % 360, "none")
    if isinstance(angle, str) and angle.strip().lstrip("-").isdigit():
        return {0: "none", 90: "rot90", 180: "rot180", 270: "rot270"}.get(
            int(angle.strip()) % 360, "none"
        )
    return "rot270"


def chunk_image(jpeg: bytes, chunk_size: int = IMAGE_CHUNK_SIZE):
    """Yield the raw JPEG in the chunk size the device expects."""
    for i in range(0, len(jpeg), chunk_size):
        yield jpeg[i : i + chunk_size]
