"""The CASC reader against a tiny synthetic storage, and the addon installer.

The reader was also checked by hand against a real Forever install; these
fixtures pin the formats it relies on so a refactor cannot quietly break them.
"""

from __future__ import annotations

import hashlib
import json
import struct
import zlib
from pathlib import Path

import pytest

from fitzlcd.config import AppConfig
from fitzlcd.sources.wow import WowError, install
from fitzlcd.sources.wow.casc import (
    EncodingTable,
    LocalStorage,
    _bucket,
    blte_decode,
    parse_build_info,
    parse_root,
)

BUILD = "ab" * 16


def blte_single(payload: bytes, mode: bytes = b"N") -> bytes:
    body = zlib.compress(payload) if mode == b"Z" else payload
    return b"BLTE" + struct.pack(">I", 0) + mode + body


def blte_chunked(*chunks: tuple[bytes, bytes]) -> bytes:
    encoded = [mode + (zlib.compress(p) if mode == b"Z" else p) for mode, p in chunks]
    header_size = 12 + 24 * len(encoded)
    table = b"".join(
        struct.pack(">II", len(e), len(p)) + bytes(16)
        for e, (_, p) in zip(encoded, chunks, strict=True)
    )
    head = b"BLTE" + struct.pack(">I", header_size) + b"\x0f" + len(encoded).to_bytes(3, "big")
    return head + table + b"".join(encoded)


def encoding_file(pairs: dict[bytes, bytes]) -> bytes:
    page = b""
    for ckey in sorted(pairs):
        page += b"\x01" + (100).to_bytes(5, "big") + ckey + pairs[ckey]
    page = page.ljust(1024, b"\0")
    header = b"EN\x01\x10\x10" + struct.pack(">HHIIBI", 1, 1, 1, 0, 0, 0)
    return header + min(pairs) + bytes(16) + page


def root_file(files: dict[int, bytes], version: int = 1) -> bytes:
    ids = sorted(files)
    deltas, prev = [], -1
    for fid in ids:
        deltas.append(fid - prev - 1)
        prev = fid
    block = struct.pack("<III", len(ids), 0x10000000, 0x2)
    block += struct.pack(f"<{len(ids)}i", *deltas) + b"".join(files[f] for f in ids)
    return b"TSFM" + struct.pack("<IIIII", 24, version, len(ids), 0, 0) + block


def build_storage(root: Path, files: dict[int, bytes], product: str = "wowf") -> None:
    """A complete local storage: build info, config, one archive, its indexes."""
    data_dir = root / "Data" / "data"
    data_dir.mkdir(parents=True)
    archive = bytearray()
    entries: dict[bytes, tuple[int, int]] = {}

    def store(blob: bytes) -> bytes:
        ekey = hashlib.md5(blob).digest()
        record = bytes(16) + struct.pack("<I", 30 + len(blob)) + bytes(10) + blob
        entries[ekey] = (len(archive), len(record))
        archive.extend(record)
        return ekey

    ckeys = {}
    encoding_pairs = {}
    for fid, content in files.items():
        ckey = hashlib.md5(content).digest()
        ckeys[fid] = ckey
        encoding_pairs[ckey] = store(blte_single(content, b"Z"))
    root_bytes = root_file(ckeys)
    root_ckey = hashlib.md5(root_bytes).digest()
    encoding_pairs[root_ckey] = store(blte_single(root_bytes))
    encoding_bytes = encoding_file(encoding_pairs)
    encoding_ekey = store(blte_single(encoding_bytes, b"Z"))
    (data_dir / "data.000").write_bytes(bytes(archive))

    buckets: dict[int, list[bytes]] = {}
    for ekey, (offset, size) in entries.items():
        packed = (0 << 30) | offset
        entry = ekey[:9] + packed.to_bytes(5, "big") + struct.pack("<I", size)
        buckets.setdefault(_bucket(ekey), []).append(entry)
    for bucket, items in buckets.items():
        body = b"".join(sorted(items))
        header = struct.pack("<II", 0x10, 0) + struct.pack(
            "<HBBBBBBQ", 7, bucket, 0, 4, 5, 9, 30, 0
        )
        header = header.ljust(0x20, b"\0") + struct.pack("<II", len(body), 0)
        (data_dir / f"{bucket:02x}00000001.idx").write_bytes(header + body)

    config_dir = root / "Data" / "config" / BUILD[:2] / BUILD[2:4]
    config_dir.mkdir(parents=True)
    (config_dir / BUILD).write_text(
        f"# Build Configuration\nroot = {root_ckey.hex()}\n"
        f"encoding = {hashlib.md5(encoding_bytes).hexdigest()} {encoding_ekey.hex()}\n",
        encoding="utf-8",
    )
    (root / ".build.info").write_text(
        "Branch!STRING:0|Active!DEC:1|Build Key!HEX:16|Version!STRING:0|Product!STRING:0\n"
        f"us|1|{'cd' * 16}|12.1.5|wow\n"
        f"us|1|{BUILD}|1.60.1|{product}\n",
        encoding="utf-8",
    )


FILES = {134400: b"question mark", 5_000_001: b"tile one", 5_000_002: b"tile two" * 50}


class TestFormats:
    def test_build_info(self):
        rows = parse_build_info("A!STRING:0|B!HEX:16\nx|y\nz\n")
        assert rows == [{"A": "x", "B": "y"}, {"A": "z", "B": ""}]

    def test_blte_modes(self):
        assert blte_decode(blte_single(b"hello")) == b"hello"
        assert blte_decode(blte_single(b"hello", b"Z")) == b"hello"
        assert blte_decode(blte_chunked((b"N", b"ab"), (b"Z", b"cd" * 40))) == b"ab" + b"cd" * 40

    @pytest.mark.parametrize(
        "blob",
        [
            b"nope",
            b"BLTE" + struct.pack(">I", 0) + b"Eencrypted",
            b"BLTE" + struct.pack(">I", 0) + b"Q?",
        ],
    )
    def test_blte_rejects(self, blob):
        with pytest.raises(WowError):
            blte_decode(blob)

    def test_bad_zlib_is_a_wowerror(self):
        with pytest.raises(WowError):
            blte_decode(b"BLTE" + struct.pack(">I", 0) + b"Znot zlib")

    def test_encoding_lookup(self):
        a, b = bytes([1] * 16), bytes([9] * 16)
        table = EncodingTable(encoding_file({a: bytes([2] * 16), b: bytes([3] * 16)}))
        assert table.find(a) == bytes([2] * 16) and table.find(b) == bytes([3] * 16)
        assert table.find(bytes(16)) is None and table.find(bytes([5] * 16)) is None

    def test_root_both_header_styles(self):
        ckeys = {7: bytes([7] * 16), 300: bytes([3] * 16)}
        new = root_file(ckeys)
        assert parse_root(new, [300, 7, 8]) == ckeys
        old = b"TSFM" + struct.pack("<II", 2, 0) + new[24:]
        assert parse_root(old, [7]) == {7: ckeys[7]}

    def test_root_rejects_other_formats(self):
        with pytest.raises(WowError):
            parse_root(b"\0" * 40, [1])


class TestLocalStorage:
    def test_reads_files_by_id(self, tmp_path):
        build_storage(tmp_path, FILES)
        storage = LocalStorage(tmp_path, "wowf")
        assert storage.version == "1.60.1"
        for fid, content in FILES.items():
            assert storage.read_file(fid) == content

    def test_unknown_file_is_a_wowerror(self, tmp_path):
        build_storage(tmp_path, FILES)
        with pytest.raises(WowError):
            LocalStorage(tmp_path).read_file(42)

    def test_resolved_keys_are_cached_per_build(self, tmp_path):
        build_storage(tmp_path / "wow", FILES)
        cache = tmp_path / "cache.json"
        LocalStorage(tmp_path / "wow", cache_path=cache).read_file(5_000_001)
        saved = json.loads(cache.read_text(encoding="utf-8"))
        assert saved["build"] == BUILD and "5000001" in saved["files"]
        again = LocalStorage(tmp_path / "wow", cache_path=cache)
        assert 5_000_001 in again._known  # no root/encoding decode needed

    def test_cache_from_another_build_is_ignored(self, tmp_path):
        build_storage(tmp_path / "wow", FILES)
        cache = tmp_path / "cache.json"
        cache.write_text(json.dumps({"build": "old", "files": {"1": "00" * 16}}), encoding="utf-8")
        assert LocalStorage(tmp_path / "wow", cache_path=cache)._known == {}

    @pytest.mark.parametrize("content", ["{", "[]", '{"build": "x", "files": 3}'])
    def test_corrupt_cache_is_ignored(self, tmp_path, content):
        build_storage(tmp_path / "wow", FILES)
        cache = tmp_path / "cache.json"
        cache.write_text(content, encoding="utf-8")
        assert LocalStorage(tmp_path / "wow", cache_path=cache).read_file(134400)

    def test_missing_product(self, tmp_path):
        build_storage(tmp_path, FILES, product="wow_classic")
        with pytest.raises(WowError, match="not installed"):
            LocalStorage(tmp_path, "wowf")

    def test_no_install(self, tmp_path):
        with pytest.raises(WowError):
            LocalStorage(tmp_path / "nothing")


# ------------------------------------------------------------------ install


@pytest.fixture
def game(tmp_path, monkeypatch):
    """A WoW root with a Forever folder, and nothing else discoverable."""
    monkeypatch.setattr(install, "_registry_paths", lambda: [])
    monkeypatch.setenv("PROGRAMFILES(X86)", str(tmp_path / "pf86"))
    monkeypatch.setenv("PROGRAMFILES", str(tmp_path / "pf"))
    root = tmp_path / "World of Warcraft"
    forever = root / "_forever_"
    forever.mkdir(parents=True)
    (root / ".build.info").write_text("Product!STRING:0\nwowf\n", encoding="utf-8")
    (forever / ".flavor.info").write_text("Product Flavor!STRING:0\nwowf\n", encoding="utf-8")
    return root, forever


class TestInstall:
    def test_not_found_without_a_hint(self, game):
        config = AppConfig()
        assert install.locate(config) is None
        assert install.addon_state(config) == "missing_game"
        with pytest.raises(FileNotFoundError):
            install.install_addon(config)

    @pytest.mark.parametrize("hint", ["root", "forever"])
    def test_found_from_either_folder(self, game, hint):
        root, forever = game
        config = AppConfig(wow_install_dir=str(root if hint == "root" else forever))
        assert install.locate(config) == (root, forever)

    def test_renamed_flavor_folder_is_found_by_its_flavor_info(self, game):
        root, forever = game
        renamed = forever.rename(root / "_forever_beta_")
        config = AppConfig(wow_install_dir=str(root))
        assert install.locate(config) == (root, renamed)

    def test_install_update_cycle(self, game):
        root, forever = game
        config = AppConfig(wow_install_dir=str(root))
        assert install.addon_state(config) == "not_installed"
        target = install.install_addon(config)
        assert (target / "FitzLCD.toc").is_file() and (target / "Protocol.lua").is_file()
        assert install.addon_state(config) == "installed"
        toc = target / "FitzLCD.toc"
        toc.write_text(
            toc.read_text(encoding="utf-8").replace("## Version: ", "## Version: 0."),
            encoding="utf-8",
        )
        (target / "stale.lua").write_text("--", encoding="utf-8")
        assert install.addon_state(config) == "outdated"
        install.install_addon(config)
        assert install.addon_state(config) == "installed"
        assert not (target / "stale.lua").exists()

    def test_bundled_addon_files_exist(self):
        names = {p.name for p in install.bundled_addon().iterdir()}
        assert {"FitzLCD.toc", "Protocol.lua", "Strip.lua", "Collect.lua"} <= names
        toc = (install.bundled_addon() / "FitzLCD.toc").read_text(encoding="utf-8")
        listed = [line for line in toc.splitlines() if line.endswith(".lua")]
        assert all((install.bundled_addon() / name).is_file() for name in listed)
