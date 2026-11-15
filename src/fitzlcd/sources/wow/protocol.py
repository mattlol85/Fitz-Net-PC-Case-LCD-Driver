"""The FitzLCD addon's pixel strip: layout, encoder and decoder.

The addon draws ``ROWS x COLUMNS`` square cells at the game's top-left corner.
Every cell's red, green and blue each carry one 4-bit *level*, drawn as
``level * 17`` (0, 17, ... 255). Sixteen widely spaced levels survive gamma
tweaks and colour filters that would corrupt full 8-bit values, and the
calibration cells let the decoder learn whatever curve the screen applied:

    cell 0        sentinel A   levels (15, 0, 15)   magenta - find the strip
    cell 1        sentinel B   levels (0, 15, 0)    green   - measure the cell pitch
    cells 2-7     calibration  18 channel slots, slot s at level s % 16
    cells 8-127   data         360 levels = 180 bytes, high nibble first

The 180 data bytes are one frame (all integers big-endian)::

    u8   version            VERSION; anything else means "update the addon"
    u8   seq                increments every frame
    30   player             see PLAYER_STRUCT
    40   party slots 1-4    see MEMBER_STRUCT
    6    slow-lane header   key, digest, index, count, length
    100  slow-lane data     one chunk of a larger record, zero padded
    u16  checksum           Fletcher-16 of the 178 bytes before it

The *fast lane* (player and party) is sent whole every frame. Anything larger
or rarely changing (zone names, party names, the zone map's tile list) rides the
*slow lane*: each record is split into chunks that the addon cycles through, one
per frame, and :class:`RecordAssembler` puts them back together.

``Protocol.lua`` in the addon is the other half of this file; the tests run it
under a real Lua 5.1 and decode its output here, so keep the two in step.
"""

from __future__ import annotations

import math
import struct
from bisect import bisect_left
from collections.abc import Sequence
from dataclasses import dataclass, field

from PIL import Image

from fitzlcd.sources.wow import WowError

VERSION = 2
COLUMNS = 64
ROWS = 2
CELLS = COLUMNS * ROWS
LEVELS = 16
LEVEL_STEP = 17  # level * 17 -> 0..255

SENTINEL_A = (15, 0, 15)
SENTINEL_B = (0, 15, 0)
CALIBRATION_CELLS = range(2, 8)
DATA_CELL0 = 8

FRAME_BYTES = (CELLS - DATA_CELL0) * 3 // 2  # 180
BODY_BYTES = FRAME_BYTES - 2
PARTY_SLOTS = 4

#: flags map x y facing level class health power, then power_type hp hp_max pw pw_max
PLAYER_STRUCT = struct.Struct(">HHHHBBBBBBIIII")
MEMBER_STRUCT = struct.Struct(">BBBBHHH")  # flags class level health map x y
SLOW_STRUCT = struct.Struct(">BHBBB")  # key digest index count length
FAST_BYTES = PLAYER_STRUCT.size + PARTY_SLOTS * MEMBER_STRUCT.size  # 70
SLOW_CAPACITY = BODY_BYTES - 2 - FAST_BYTES - SLOW_STRUCT.size  # 100

#: A health or power byte the addon could not read (secret in combat, or absent).
UNKNOWN = 255
#: The same for a 32-bit absolute value (hit points, mana).
UNKNOWN32 = 0xFFFFFFFF
#: Positions are 0..1 across the map, sent as 0..POSITION_SCALE.
POSITION_SCALE = 65535

# Player flags.
P_HAS_POSITION = 1 << 0
P_COMBAT = 1 << 1
P_DEAD = 1 << 2
P_GHOST = 1 << 3
P_INSTANCE = 1 << 4
P_RESTING = 1 << 5
P_AFK = 1 << 6
P_HAS_FACING = 1 << 7
P_IN_GROUP = 1 << 8
P_IN_RAID = 1 << 9

# Party member flags.
M_PRESENT = 1 << 0
M_ONLINE = 1 << 1
M_DEAD = 1 << 2
M_GHOST = 1 << 3
M_COMBAT = 1 << 4
M_HAS_POSITION = 1 << 5
#: The position came from the member's own addon (a party broadcast), not the map API.
M_SHARED = 1 << 6

# Slow-lane record keys.
RECORD_INFO = 1
RECORD_MAP = 2

#: A record split into more chunks than this is not one the addon would send.
MAX_CHUNKS = 32


class StripError(WowError):
    """The strip could not be read. ``reason`` says why, for status text."""

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason


# ---------------------------------------------------------------- frame model


@dataclass(frozen=True)
class PlayerState:
    flags: int = 0
    map_id: int = 0
    x: int = 0
    y: int = 0
    facing: int = 0
    level: int = 0
    class_id: int = 0
    health: int = UNKNOWN
    power: int = UNKNOWN
    power_type: int = UNKNOWN
    hp: int = UNKNOWN32
    hp_max: int = UNKNOWN32
    pw: int = UNKNOWN32
    pw_max: int = UNKNOWN32

    @property
    def position(self) -> tuple[float, float] | None:
        if not self.flags & P_HAS_POSITION or not self.map_id:
            return None
        return self.x / POSITION_SCALE, self.y / POSITION_SCALE

    @property
    def facing_radians(self) -> float | None:
        """Counter-clockwise from north, as ``GetPlayerFacing`` reports it."""
        if not self.flags & P_HAS_FACING:
            return None
        return self.facing / 256 * math.tau


@dataclass(frozen=True)
class MemberState:
    flags: int = 0
    class_id: int = 0
    level: int = 0
    health: int = UNKNOWN
    map_id: int = 0
    x: int = 0
    y: int = 0

    @property
    def present(self) -> bool:
        return bool(self.flags & M_PRESENT)

    @property
    def position(self) -> tuple[float, float] | None:
        if not self.flags & M_HAS_POSITION or not self.map_id:
            return None
        return self.x / POSITION_SCALE, self.y / POSITION_SCALE


@dataclass(frozen=True)
class SlowChunk:
    key: int
    digest: int
    index: int
    count: int
    data: bytes


@dataclass(frozen=True)
class Frame:
    seq: int
    player: PlayerState
    party: tuple[MemberState, ...]
    chunk: SlowChunk | None = None


# ------------------------------------------------------------------ encoding


def fletcher16(data: bytes | Sequence[int]) -> int:
    s1 = s2 = 0
    for b in data:
        s1 = (s1 + b) % 255
        s2 = (s2 + s1) % 255
    return (s2 << 8) | s1


def chunk_record(key: int, data: bytes) -> list[SlowChunk]:
    """Split one slow-lane record into the chunks the addon would send."""
    digest = fletcher16(data)
    parts = [data[i : i + SLOW_CAPACITY] for i in range(0, len(data), SLOW_CAPACITY)] or [b""]
    return [SlowChunk(key, digest, i, len(parts), part) for i, part in enumerate(parts)]


def pack_frame(
    seq: int,
    player: PlayerState,
    party: Sequence[MemberState] = (),
    chunk: SlowChunk | None = None,
) -> bytes:
    """The 180 data bytes of one frame. The reference the Lua encoder mirrors."""
    slots = list(party)[:PARTY_SLOTS]
    slots += [MemberState()] * (PARTY_SLOTS - len(slots))
    body = bytearray([VERSION, seq & 0xFF])
    body += PLAYER_STRUCT.pack(
        player.flags,
        player.map_id,
        player.x,
        player.y,
        player.facing,
        player.level,
        player.class_id,
        player.health,
        player.power,
        player.power_type,
        player.hp,
        player.hp_max,
        player.pw,
        player.pw_max,
    )
    for m in slots:
        body += MEMBER_STRUCT.pack(m.flags, m.class_id, m.level, m.health, m.map_id, m.x, m.y)
    if chunk is None:
        body += SLOW_STRUCT.pack(0, 0, 0, 0, 0)
    else:
        data = chunk.data[:SLOW_CAPACITY]
        body += SLOW_STRUCT.pack(chunk.key, chunk.digest, chunk.index, chunk.count, len(data))
        body += data
    body += bytes(BODY_BYTES - len(body))
    return bytes(body) + fletcher16(body).to_bytes(2, "big")


def frame_levels(data: bytes) -> list[tuple[int, int, int]]:
    """Every cell's (r, g, b) levels, sentinels and calibration included."""
    if len(data) != FRAME_BYTES:
        raise ValueError(f"a frame is {FRAME_BYTES} bytes, not {len(data)}")
    slots = [i % LEVELS for i in range(len(CALIBRATION_CELLS) * 3)]
    calibration = [tuple(slots[i : i + 3]) for i in range(0, len(slots), 3)]
    nibbles = [n for b in data for n in (b >> 4, b & 0x0F)]
    cells = [tuple(nibbles[i : i + 3]) for i in range(0, len(nibbles), 3)]
    return [SENTINEL_A, SENTINEL_B, *calibration, *cells]  # type: ignore[list-item]


def render_strip(levels: Sequence[tuple[int, int, int]], pitch: int = 3) -> Image.Image:
    """Draw cells the way the addon does: each a ``pitch``-pixel square."""
    image = Image.new("RGB", (COLUMNS * pitch, ROWS * pitch))
    for i, (r, g, b) in enumerate(levels):
        col, row = i % COLUMNS, i // COLUMNS
        box = (col * pitch, row * pitch, (col + 1) * pitch, (row + 1) * pitch)
        image.paste((r * LEVEL_STEP, g * LEVEL_STEP, b * LEVEL_STEP), box)
    return image


# ------------------------------------------------------------------ decoding


@dataclass(frozen=True)
class StripGeometry:
    x: int
    y: int
    pitch_x: float
    pitch_y: float

    def centre(self, cell: int) -> tuple[int, int]:
        col, row = cell % COLUMNS, cell // COLUMNS
        return (
            int(self.x + (col + 0.5) * self.pitch_x),
            int(self.y + (row + 0.5) * self.pitch_y),
        )


def _is_a(px: tuple[int, ...]) -> bool:
    return px[0] >= 128 and px[1] < 96 and px[2] >= 128


def _is_b(px: tuple[int, ...]) -> bool:
    return px[0] < 96 and px[1] >= 128 and px[2] < 96


#: How far from the capture's corner the strip may start (window borders).
_SEARCH = 16


def locate(image: Image.Image) -> StripGeometry:
    """Find the strip's top-left corner and cell pitch from its two sentinels."""
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    px = rgb.load()
    width, height = rgb.size
    for y in range(min(height, _SEARCH)):
        for x0 in range(min(width, _SEARCH)):
            if not _is_a(px[x0, y]):
                continue
            x = x0
            while x < width and _is_a(px[x, y]):
                x += 1
            a_end = x
            while x < width and _is_b(px[x, y]):
                x += 1
            a_len, b_len = a_end - x0, x - a_end
            if not b_len or not 0.5 <= a_len / b_len <= 2.0:
                continue
            pitch_x = (x - x0) / 2
            probe = int(x0 + pitch_x / 2)
            yy = y
            while yy < height and _is_a(px[probe, yy]):
                yy += 1
            pitch_y = float(yy - y)
            if not 0.5 <= pitch_y / pitch_x <= 2.0:
                continue
            geometry = StripGeometry(x0, y, pitch_x, pitch_y)
            right = geometry.x + COLUMNS * pitch_x
            bottom = geometry.y + ROWS * pitch_y
            if right > width + 0.5 or bottom > height + 0.5:
                raise StripError("not_found", "strip is cut off by the capture region")
            return geometry
    raise StripError("not_found")


def _level_table(samples: Sequence[int]) -> list[int]:
    """A 256-entry lookup from an observed channel value to its level."""
    by_level: dict[int, list[int]] = {}
    for slot, value in enumerate(samples):
        by_level.setdefault(slot % LEVELS, []).append(value)
    means = [sum(by_level[lv]) / len(by_level[lv]) for lv in range(LEVELS)]
    for lo, hi in zip(means, means[1:], strict=False):
        if hi - lo < 3:
            raise StripError("calibration", f"levels too close ({lo:.0f}, {hi:.0f})")
    cuts = [(a + b) / 2 for a, b in zip(means, means[1:], strict=False)]
    return [bisect_left(cuts, v) for v in range(256)]


def read_cells(image: Image.Image) -> list[tuple[int, int, int]]:
    """Every cell's levels, read off a screenshot and corrected by calibration."""
    rgb = image if image.mode == "RGB" else image.convert("RGB")
    geometry = locate(rgb)
    px = rgb.load()
    raw = [px[geometry.centre(i)] for i in range(CELLS)]
    samples = [channel for cell in CALIBRATION_CELLS for channel in raw[cell]]
    table = _level_table(samples)
    cells = [(table[r], table[g], table[b]) for r, g, b in raw]
    if cells[0] != SENTINEL_A or cells[1] != SENTINEL_B:
        raise StripError("not_found", "sentinels do not decode")
    return cells


def unpack_frame(data: bytes) -> Frame:
    if len(data) != FRAME_BYTES:
        raise StripError("checksum", f"{len(data)} bytes")
    if int.from_bytes(data[BODY_BYTES:], "big") != fletcher16(data[:BODY_BYTES]):
        raise StripError("checksum")
    if data[0] != VERSION:
        raise StripError("version", f"addon speaks v{data[0]}, app v{VERSION}")
    offset = 2
    player = PlayerState(*PLAYER_STRUCT.unpack_from(data, offset))
    offset += PLAYER_STRUCT.size
    party = []
    for _ in range(PARTY_SLOTS):
        party.append(MemberState(*MEMBER_STRUCT.unpack_from(data, offset)))
        offset += MEMBER_STRUCT.size
    key, digest, index, count, length = SLOW_STRUCT.unpack_from(data, offset)
    offset += SLOW_STRUCT.size
    chunk = None
    if key and 0 < count <= MAX_CHUNKS and index < count and length <= SLOW_CAPACITY:
        chunk = SlowChunk(key, digest, index, count, bytes(data[offset : offset + length]))
    return Frame(data[1], player, tuple(party), chunk)


def decode_image(image: Image.Image) -> Frame:
    """Screenshot of the strip -> :class:`Frame`. Raises :class:`StripError`."""
    cells = read_cells(image)
    nibbles = [n for cell in cells[DATA_CELL0:] for n in cell]
    data = bytes((nibbles[i] << 4) | nibbles[i + 1] for i in range(0, len(nibbles), 2))
    return unpack_frame(data)


# ----------------------------------------------------------------- slow lane


@dataclass
class _Pending:
    digest: int
    count: int
    parts: dict[int, bytes] = field(default_factory=dict)
    done: bool = False


class RecordAssembler:
    """Reassembles slow-lane records from chunks, in any order, across gaps.

    A record is emitted once per distinct content: the addon re-sends every
    record forever, and the caller only cares when one changes.
    """

    def __init__(self) -> None:
        self._pending: dict[int, _Pending] = {}

    def feed(self, chunk: SlowChunk | None) -> tuple[int, bytes] | None:
        if chunk is None or not 0 < chunk.count <= MAX_CHUNKS or chunk.index >= chunk.count:
            return None
        entry = self._pending.get(chunk.key)
        if entry is None or entry.digest != chunk.digest or entry.count != chunk.count:
            entry = self._pending[chunk.key] = _Pending(chunk.digest, chunk.count)
        if entry.done:
            return None
        entry.parts[chunk.index] = chunk.data
        if len(entry.parts) < entry.count:
            return None
        data = b"".join(entry.parts[i] for i in range(entry.count))
        if fletcher16(data) != entry.digest:  # chunks from two versions got mixed
            del self._pending[chunk.key]
            return None
        entry.done = True
        entry.parts.clear()
        return chunk.key, data


def parse_record(data: bytes) -> dict[str, str]:
    """A slow-lane record body: ``key=value`` lines, UTF-8."""
    values: dict[str, str] = {}
    for line in data.decode("utf-8", errors="replace").splitlines():
        key, sep, value = line.partition("=")
        if sep and key:
            values[key.strip()] = value
    return values


def encode_record(values: dict[str, object]) -> bytes:
    """The inverse of :func:`parse_record`, as the addon builds records."""
    lines = []
    for key, value in values.items():
        text = str(value).replace("\r", " ").replace("\n", " ")
        lines.append(f"{key}={text}")
    return "\n".join(lines).encode("utf-8")
