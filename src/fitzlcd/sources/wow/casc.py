"""A minimal, read-only reader for the game's local CASC storage.

Just enough to turn a fileID into its bytes, for the zone map tiles in
:mod:`fitzlcd.sources.wow.mapart`. The chain, all from files under the install
directory and nothing from the network:

    .build.info              which build each product (Forever is ``wowf``) runs
    Data/config/ab/cd/<key>  that build's config: the root and encoding keys
    root (MFST)              fileID -> content key (ckey)
    encoding                 ckey -> encoded key (ekey)
    Data/data/*.idx          ekey -> (archive, offset, size)
    Data/data/data.NNN       the BLTE-encoded bytes

There is no maintained Python library for this; the formats follow the
community documentation (wowdev.wiki "CASC", "TACT" and "Root"). Only what map
tiles need is implemented: BLTE ``N`` (raw) and ``Z`` (zlib) chunks. An
encrypted chunk raises :class:`WowError`, which costs that map its art, not the
app anything.

Resolving fileIDs means decoding the whole root and encoding files (tens of
MB), so results are kept in a small JSON cache keyed by build; a zone seen
before never needs them again until the game patches.
"""

from __future__ import annotations

import bisect
import json
import logging
import struct
import threading
import zlib
from pathlib import Path

import numpy as np

from fitzlcd.sources.wow import WowError

log = logging.getLogger(__name__)

#: The product code Forever's rows carry in ``.build.info``.
FOREVER_PRODUCT = "wowf"

_LOCALE_ENUS = 0x2
_NO_NAME_HASH = 0x10000000
_DATA_HEADER = 0x1E


def parse_build_info(text: str) -> list[dict[str, str]]:
    """``.build.info``: a ``|``-separated table whose header cells are ``Name!TYPE:size``."""
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    names = [cell.split("!", 1)[0] for cell in lines[0].split("|")]
    rows = []
    for line in lines[1:]:
        cells = line.split("|")
        rows.append({name: cells[i] if i < len(cells) else "" for i, name in enumerate(names)})
    return rows


def parse_config(text: str) -> dict[str, list[str]]:
    """A build config: ``key = value value ...`` lines."""
    values: dict[str, list[str]] = {}
    for line in text.splitlines():
        if line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split()
    return values


def blte_decode(data: bytes) -> bytes:
    """Decode a BLTE blob: raw and zlib chunks only."""
    if len(data) < 8 or data[:4] != b"BLTE":
        raise WowError("not BLTE data")
    header_size = struct.unpack_from(">I", data, 4)[0]
    if header_size == 0:
        return _blte_chunk(data[8:])
    if header_size < 12 or header_size > len(data):
        raise WowError("bad BLTE header")
    count = int.from_bytes(data[9:12], "big")
    if 12 + count * 24 > header_size:
        raise WowError("bad BLTE chunk table")
    out = bytearray()
    offset = header_size
    for i in range(count):
        compressed, _decompressed = struct.unpack_from(">II", data, 12 + i * 24)
        chunk = data[offset : offset + compressed]
        if len(chunk) != compressed:
            raise WowError("truncated BLTE chunk")
        out += _blte_chunk(chunk)
        offset += compressed
    return bytes(out)


def _blte_chunk(chunk: bytes) -> bytes:
    if not chunk:
        return b""
    mode, body = chunk[:1], chunk[1:]
    if mode == b"N":
        return bytes(body)
    if mode == b"Z":
        try:
            return zlib.decompress(body)
        except zlib.error as exc:
            raise WowError(f"bad zlib chunk: {exc}") from exc
    if mode == b"E":
        raise WowError("encrypted content")
    raise WowError(f"unsupported BLTE chunk mode {mode!r}")


def _bucket(ekey: bytes) -> int:
    i = 0
    for b in ekey[:9]:
        i ^= b
    return (i & 0xF) ^ (i >> 4)


class _Index:
    """One bucket's ``.idx`` file: sorted 18-byte entries, searched in place."""

    ENTRY = 18

    def __init__(self, data: bytes) -> None:
        if len(data) < 0x28:
            raise WowError("index file too short")
        key_len, offset_bits = data[0x0E], data[0x0F]
        if (data[0x0C], data[0x0D], key_len) != (4, 5, 9):
            raise WowError("unexpected index layout")
        self._offset_bits = offset_bits
        size = struct.unpack_from("<I", data, 0x20)[0]
        self._entries = memoryview(data)[0x28 : 0x28 + size - size % self.ENTRY]
        self._count = len(self._entries) // self.ENTRY
        self._keys = _KeyView(self._entries, self._count)

    def find(self, ekey: bytes) -> tuple[int, int, int] | None:
        key = ekey[:9]
        i = bisect.bisect_left(self._keys, key)
        if i >= self._count or self._keys[i] != key:
            return None
        entry = self._entries[i * self.ENTRY : (i + 1) * self.ENTRY]
        packed = int.from_bytes(entry[9:14], "big")
        mask = (1 << self._offset_bits) - 1
        size = struct.unpack_from("<I", entry, 14)[0]
        return packed >> self._offset_bits, packed & mask, size


class _KeyView:
    """Lets :mod:`bisect` search an index's keys without copying them out."""

    def __init__(self, entries: memoryview, count: int) -> None:
        self._entries, self._count = entries, count

    def __len__(self) -> int:
        return self._count

    def __getitem__(self, i: int) -> bytes:
        start = i * _Index.ENTRY
        return bytes(self._entries[start : start + 9])


class LocalStorage:
    """A product's files in a local install, by fileID. Thread-safe for reads."""

    def __init__(self, root: Path, product: str = FOREVER_PRODUCT, cache_path: Path | None = None):
        self._root = Path(root)
        self._data = self._root / "Data" / "data"
        self._cache_path = cache_path
        self._lock = threading.Lock()
        try:
            rows = parse_build_info((self._root / ".build.info").read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WowError(f"no readable .build.info in {self._root}: {exc}") from exc
        row = next((r for r in rows if r.get("Product") == product), None)
        if row is None or not row.get("Build Key"):
            raise WowError(f"{product} is not installed in {self._root}")
        self.build = row["Build Key"]
        self.version = row.get("Version", "")
        config = self._read_config(self.build)
        try:
            self._root_ckey = bytes.fromhex(config["root"][0])
            self._encoding_ekey = bytes.fromhex(config["encoding"][1])
        except (KeyError, IndexError, ValueError) as exc:
            raise WowError("build config has no root/encoding keys") from exc
        self._indexes = self._load_indexes()
        self._known: dict[int, str] = self._load_cache()

    # ------------------------------------------------------------- reading

    def read_file(self, file_id: int) -> bytes:
        ekey = self.resolve([file_id]).get(file_id)
        if ekey is None:
            raise WowError(f"file {file_id} is not in this build")
        return self.read_ekey(ekey)

    def read_ekey(self, ekey: bytes) -> bytes:
        index = self._indexes.get(_bucket(ekey))
        location = index.find(ekey) if index else None
        if location is None:
            raise WowError(f"{ekey.hex()} is not in the local storage")
        archive, offset, size = location
        path = self._data / f"data.{archive:03d}"
        try:
            with path.open("rb") as fh:
                fh.seek(offset)
                blob = fh.read(size)
        except OSError as exc:
            raise WowError(f"could not read {path.name}: {exc}") from exc
        if len(blob) != size or size < _DATA_HEADER:
            raise WowError(f"truncated entry in {path.name}")
        return blte_decode(blob[_DATA_HEADER:])

    # ----------------------------------------------------------- resolving

    def resolve(self, file_ids: list[int]) -> dict[int, bytes]:
        """fileID -> ekey for every one that exists, decoding root/encoding if needed."""
        with self._lock:
            wanted = {int(f) for f in file_ids if int(f) > 0}
            missing = [f for f in wanted if f not in self._known]
            if missing:
                ckeys = self._root_lookup(missing)
                for file_id, ekey in self._encoding_lookup(ckeys).items():
                    self._known[file_id] = ekey.hex()
                self._save_cache()
            return {f: bytes.fromhex(self._known[f]) for f in wanted if f in self._known}

    def _root_lookup(self, file_ids: list[int]) -> dict[int, bytes]:
        ekey = self._encoding_lookup_one(self._root_ckey)
        return parse_root(self.read_ekey(ekey), file_ids)

    def _encoding_lookup_one(self, ckey: bytes) -> bytes:
        found = self._encoding_lookup({0: ckey})
        if 0 not in found:
            raise WowError(f"{ckey.hex()} is not in the encoding table")
        return found[0]

    def _encoding_lookup(self, ckeys: dict[int, bytes]) -> dict[int, bytes]:
        table = EncodingTable(self.read_ekey(self._encoding_ekey))
        return {k: e for k, c in ckeys.items() if (e := table.find(c)) is not None}

    # --------------------------------------------------------------- setup

    def _read_config(self, key: str) -> dict[str, list[str]]:
        path = self._root / "Data" / "config" / key[:2] / key[2:4] / key
        try:
            return parse_config(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise WowError(f"no build config {key}: {exc}") from exc

    def _load_indexes(self) -> dict[int, _Index]:
        newest: dict[int, Path] = {}
        try:
            for path in self._data.glob("*.idx"):
                stem = path.stem
                if len(stem) != 10:
                    continue
                bucket = int(stem[:2], 16)
                if bucket not in newest or stem > newest[bucket].stem:
                    newest[bucket] = path
        except (OSError, ValueError) as exc:
            raise WowError(f"could not list {self._data}: {exc}") from exc
        indexes = {}
        for bucket, path in newest.items():
            try:
                indexes[bucket] = _Index(path.read_bytes())
            except (OSError, WowError) as exc:
                log.warning("skipping CASC index %s: %s", path.name, exc)
        if not indexes:
            raise WowError(f"no CASC indexes in {self._data}")
        return indexes

    def _load_cache(self) -> dict[int, str]:
        if self._cache_path is None:
            return {}
        try:
            data = json.loads(self._cache_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            log.debug("ignoring CASC cache %s: %s", self._cache_path, exc)
            return {}
        if not isinstance(data, dict) or data.get("build") != self.build:
            return {}  # the game patched: every key may have moved
        files = data.get("files")
        if not isinstance(files, dict):
            return {}
        known = {}
        for key, value in files.items():
            try:
                if isinstance(value, str) and len(bytes.fromhex(value)) == 16:
                    known[int(key)] = value
            except ValueError:
                continue
        return known

    def _save_cache(self) -> None:
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"build": self.build, "files": {str(k): v for k, v in self._known.items()}}
            self._cache_path.write_text(json.dumps(payload), encoding="utf-8")
        except OSError as exc:
            log.warning("could not write CASC cache %s: %s", self._cache_path, exc)


class EncodingTable:
    """The decoded encoding file, searched by content key without unpacking it."""

    def __init__(self, data: bytes) -> None:
        if len(data) < 22 or data[:2] != b"EN":
            raise WowError("not an encoding file")
        ckey_size, ekey_size = data[3], data[4]
        if ckey_size != 16 or ekey_size != 16:
            raise WowError("unexpected encoding key sizes")
        page_kb = struct.unpack_from(">H", data, 5)[0]
        page_count = struct.unpack_from(">I", data, 9)[0]
        espec_size = struct.unpack_from(">I", data, 18)[0]
        self._data = data
        self._page_size = page_kb * 1024
        index_start = 22 + espec_size
        self._firsts = [
            bytes(data[index_start + i * 32 : index_start + i * 32 + 16]) for i in range(page_count)
        ]
        self._pages_start = index_start + page_count * 32
        if self._pages_start + page_count * self._page_size > len(data):
            raise WowError("truncated encoding file")

    def find(self, ckey: bytes) -> bytes | None:
        page = bisect.bisect_right(self._firsts, ckey) - 1
        if page < 0:
            return None
        data = self._data
        pos = self._pages_start + page * self._page_size
        end = pos + self._page_size
        while pos + 22 <= end:
            count = data[pos]
            if count == 0:
                break
            key = data[pos + 6 : pos + 22]
            if key == ckey:
                return bytes(data[pos + 22 : pos + 38])
            pos += 22 + count * 16
        return None


def parse_root(data: bytes, file_ids: list[int]) -> dict[int, bytes]:
    """fileID -> ckey from a root (MFST) file, for just the IDs asked for.

    English or locale-free entries win when a file has several.
    """
    if data[:4] != b"TSFM":
        raise WowError("root is not an MFST manifest")
    first = struct.unpack_from("<I", data, 4)[0]
    if first in (20, 24):  # 10.1.7+: headerSize, version, total, named
        version = struct.unpack_from("<I", data, 8)[0]
        pos = first
    else:  # 8.2 - 10.1.5: total, named
        version = 0
        pos = 12
    wanted = np.array(sorted(set(file_ids)), dtype=np.int64)
    found: dict[int, bytes] = {}
    preferred: set[int] = set()
    size = len(data)
    while pos + 12 <= size:
        if version >= 2:
            count, locale, flags_a, flags_b = struct.unpack_from("<IIII", data, pos)
            flags = flags_a | flags_b | (data[pos + 16] << 17)
            pos += 17
        else:
            count, flags, locale = struct.unpack_from("<III", data, pos)
            pos += 12
        if count == 0:
            continue
        deltas = np.frombuffer(data, dtype="<i4", count=count, offset=pos).astype(np.int64)
        pos += 4 * count
        ids = np.cumsum(deltas + 1) - 1
        ckey_start = pos
        pos += 16 * count
        if not flags & _NO_NAME_HASH:
            pos += 8 * count
        if pos > size:
            raise WowError("truncated root block")
        hits = np.nonzero(np.isin(ids, wanted, assume_unique=False))[0]
        english = bool(locale & _LOCALE_ENUS) or locale == 0
        for i in hits.tolist():
            file_id = int(ids[i])
            if file_id in preferred:
                continue
            ckey = bytes(data[ckey_start + 16 * i : ckey_start + 16 * (i + 1)])
            if english or file_id not in found:
                found[file_id] = ckey
            if english:
                preferred.add(file_id)
    return found
