"""WowProvider: synthetic screenshots in, ``wow.*`` metrics out."""

from __future__ import annotations

import io
import json
import math

import pytest
from PIL import Image

from fitzlcd.sources.wow import WowError
from fitzlcd.sources.wow import protocol as p
from fitzlcd.sources.wow.capture import Grab
from fitzlcd.sources.wow.mapart import ClassIcon, MapArtCache, MapManifest, stitch
from fitzlcd.sources.wow.provider import (
    WowProvider,
    format_duration,
    format_money,
    looks_drawn,
    parse_rect,
)

PLAYER = p.PlayerState(
    flags=p.P_HAS_POSITION | p.P_HAS_FACING | p.P_IN_GROUP,
    map_id=1429,
    x=p.POSITION_SCALE // 2,
    y=p.POSITION_SCALE // 2,
    facing=0,
    level=20,
    class_id=8,
    health=90,
    power=p.UNKNOWN,
)
ALLY = p.MemberState(
    flags=p.M_PRESENT | p.M_ONLINE | p.M_HAS_POSITION,
    class_id=1,
    level=19,
    health=p.UNKNOWN,
    map_id=1429,
    x=int(p.POSITION_SCALE * 0.6),
    y=p.POSITION_SCALE // 2,
)


def shot(chunk=None, player=PLAYER, party=(ALLY,), seq=1) -> Image.Image:
    data = p.pack_frame(seq, player, party, chunk)
    return p.render_strip(p.frame_levels(data), 3)


class FakeGrabber:
    def __init__(self) -> None:
        self.queue: list[Grab | Exception] = []
        self.closed = False

    def push(self, *grabs) -> None:
        self.queue.extend(grabs)

    def grab(self) -> Grab:
        item = self.queue.pop(0) if self.queue else Grab("not_running")
        if isinstance(item, Exception):
            raise item
        return item

    def close(self) -> None:
        self.closed = True


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000.0

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def rig(tmp_path):
    grabber, clock = FakeGrabber(), Clock()
    provider = WowProvider(grabber, state_path=tmp_path / "state.json", start=False, clock=clock)
    yield provider, grabber, clock
    provider.close()


def feed_record(provider, grabber, key, values, player=PLAYER):
    for chunk in p.chunk_record(key, p.encode_record(values)):
        grabber.push(Grab("ok", shot(chunk, player=player)))
        provider.step()


INFO = {
    "name": "Fitz",
    "zone": "Elwynn Forest",
    "sub": "Goldshire",
    "xp": 250,
    "xpmax": 1000,
    "rest": 500,
    "money": 1234567,
    "moneygain": -150,
    "xpgain": 3000,
    "sess": 1800,
    "deaths": 1,
    "dur": 64,
    "bags": 7,
    "tname": "Hogger",
    "tlvl": 11,
    "tcls": "elite",
    "trx": 2,
    "p1": "Ally",
}
MAP = {"id": 1429, "name": "Elwynn Forest", "w": 1000, "h": 500, "yw": 3000, "yh": 2000}


class TestConnected:
    def test_before_anything_the_game_is_not_running(self, rig):
        provider, _, _ = rig
        values = provider.read()
        assert values["wow.connected"] is False
        assert values["wow.status_text"] == "WOW NOT RUNNING"
        assert values["wow.party"] == []

    def test_a_good_frame_publishes_the_player(self, rig):
        provider, grabber, _ = rig
        grabber.push(Grab("ok", shot()))
        provider.step()
        values = provider.read()
        assert values["wow.connected"] is True and values["wow.status_text"] == ""
        assert values["wow.player.x"] == pytest.approx(0.5, abs=1e-4)
        assert values["wow.coords_text"] == "50.0, 50.0"
        assert values["wow.player.class"] == "Mage"
        assert values["wow.player.health_pct"] == 90.0
        assert "wow.player.power_pct" not in values  # unknown: omitted, not 0
        assert values["wow.player.in_group"] is True

    def test_records_fill_in_the_session(self, rig):
        provider, grabber, _ = rig
        feed_record(provider, grabber, p.RECORD_INFO, INFO)
        feed_record(provider, grabber, p.RECORD_MAP, MAP)
        v = provider.read()
        assert v["wow.location_text"] == "Goldshire, Elwynn Forest"
        assert v["wow.xp.pct"] == 25.0 and v["wow.xp.rested_pct"] == 50.0
        assert v["wow.gold_text"] == "123g 45s 67c"
        assert v["wow.session.gold_text"] == "-1s 50c"
        assert v["wow.session.played_text"] == "30m"
        assert v["wow.session.xp_per_hour"] == 6000
        assert v["wow.durability.pct"] == 64 and v["wow.bags.free"] == 7
        assert v["wow.target.text"] == "Hogger · 11 Elite"
        assert v["wow.target.reaction"] == "hostile"
        assert v["wow.map.aspect"] == 2.0
        (ally,) = v["wow.party"]
        assert ally["name"] == "Ally" and ally["class"] == "Warrior"
        assert "health_pct" not in ally
        assert ally["distance_yd"] == pytest.approx(300, abs=1)  # 0.1 of 3000 yards

    def test_distance_needs_the_world_size(self, rig):
        provider, grabber, _ = rig
        grabber.push(Grab("ok", shot()))
        provider.step()
        (ally,) = provider.read()["wow.party"]
        assert "distance_yd" not in ally


class TestGameGoesAway:
    def test_keeps_the_last_map_with_a_last_seen_line(self, rig):
        provider, grabber, clock = rig
        grabber.push(Grab("ok", shot()))
        provider.step()
        clock.now += 125
        grabber.push(Grab("not_running"))
        provider.step()
        v = provider.read()
        assert v["wow.connected"] is False
        assert v["wow.status_text"] == "WOW NOT RUNNING"
        assert v["wow.last_seen_text"] == "last seen 2m ago"
        assert v["wow.player.x"] == pytest.approx(0.5, abs=1e-4)
        assert len(v["wow.party"]) == 1

    def test_state_survives_a_restart(self, rig, tmp_path):
        provider, grabber, clock = rig
        grabber.push(Grab("ok", shot()))
        provider.step()
        clock.now += 10
        grabber.push(Grab("not_running"))
        provider.step()
        again = WowProvider(
            FakeGrabber(), state_path=tmp_path / "state.json", start=False, clock=clock
        )
        v = again.read()
        assert v["wow.player.map_id"] == 1429 and v["wow.connected"] is False
        assert v["wow.last_seen_text"] == "last seen 10s ago"

    @pytest.mark.parametrize(
        "content", ["{nope", "[]", json.dumps({"player": {"flags": "x"}}), json.dumps({"party": 5})]
    )
    def test_corrupt_state_is_ignored(self, tmp_path, content):
        path = tmp_path / "state.json"
        path.write_text(content, encoding="utf-8")
        provider = WowProvider(FakeGrabber(), state_path=path, start=False)
        assert provider.read()["wow.connected"] is False

    def test_hidden_strip_while_unfocused_says_so(self, rig):
        provider, grabber, _ = rig
        blank = Image.new("RGB", (300, 30))
        for _ in range(12):
            grabber.push(Grab("ok", blank, foreground=False))
            provider.step()
        assert provider.read()["wow.status_text"] == "WOW IS BEHIND ANOTHER WINDOW"

    def test_one_bad_frame_is_not_news(self, rig):
        provider, grabber, clock = rig
        grabber.push(Grab("ok", shot()))
        provider.step()
        clock.now += 5
        grabber.push(Grab("ok", Image.new("RGB", (300, 30))))
        provider.step()
        assert provider.read()["wow.status_text"] == "WOW NOT RUNNING"  # unchanged reason

    def test_old_addon_is_reported_at_once(self, rig):
        provider, grabber, clock = rig
        body = bytearray(p.pack_frame(1, PLAYER)[: p.BODY_BYTES])
        body[0] = 99
        data = bytes(body) + p.fletcher16(body).to_bytes(2, "big")
        clock.now += 5
        grabber.push(Grab("ok", p.render_strip(p.frame_levels(data))))
        provider.step()
        assert provider.read()["wow.status_text"] == "UPDATE THE WOW ADDON IN SETTINGS"

    def test_grabber_oserror_does_not_escape(self, rig):
        provider, grabber, _ = rig
        grabber.push(OSError("GetDC failed"))
        provider.step()
        assert provider.read()["wow.status_text"] == "CAN'T SEE THE GAME"


def test_thread_survives_a_crashing_grabber(tmp_path):
    class Exploding(FakeGrabber):
        calls = 0

        def grab(self):
            Exploding.calls += 1
            raise RuntimeError("boom")

    provider = WowProvider(Exploding(), hz=30, state_path=None)
    try:
        import time

        deadline = time.monotonic() + 2
        while Exploding.calls < 3 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert Exploding.calls >= 3
    finally:
        provider.close()


def test_formatting():
    assert format_money(5) == "5c" and format_money(105) == "1s 5c"
    assert format_money(12_345_678) == "1,234g 56s 78c"
    assert format_duration(59) == "59s" and format_duration(3700) == "1h 01m"
    assert format_duration(3 * 86400) == "3d 0h"


# ------------------------------------------------------------------ map art


def png(color, size=(4, 4)) -> bytes:
    out = io.BytesIO()
    Image.new("RGBA", size, color).save(out, format="PNG")
    return out.getvalue()


class Tiles:
    def __init__(self, files: dict[int, bytes]) -> None:
        self.files = files

    def read_file(self, file_id: int) -> bytes:
        if file_id not in self.files:
            raise WowError(f"no file {file_id}")
        return self.files[file_id]


MANIFEST = {"id": "7", "w": "7", "h": "5", "tw": "4", "th": "4", "t": "1,2,3,4"}


class TestMapArt:
    def test_manifest_validation(self):
        assert MapManifest.from_record(MANIFEST).columns == 2
        assert MapManifest.from_record({**MANIFEST, "t": "1,2"}) is None  # too few tiles
        assert MapManifest.from_record({**MANIFEST, "w": "x"}) is None
        assert MapManifest.from_record({}) is None

    def test_stitches_row_major_and_crops(self):
        tiles = Tiles({1: png("red"), 2: png("lime"), 3: png("blue"), 4: png("white", (8, 8))})
        image = stitch(MapManifest.from_record(MANIFEST), tiles)
        assert image.size == (7, 5)
        assert image.getpixel((0, 0)) == (255, 0, 0)
        assert image.getpixel((5, 0)) == (0, 255, 0)
        assert image.getpixel((0, 4)) == (0, 0, 255)
        assert image.getpixel((5, 4)) == (255, 255, 255)  # resized from 8x8

    def test_corrupt_tile_raises_wowerror(self):
        tiles = Tiles({1: b"not an image", 2: png("red"), 3: png("red"), 4: png("red")})
        with pytest.raises(WowError):
            stitch(MapManifest.from_record(MANIFEST), tiles)

    def test_cache_builds_once_and_survives_failures(self, tmp_path):
        good = Tiles({1: png("red"), 2: png("red"), 3: png("red"), 4: png("red")})
        cache = MapArtCache(tmp_path, lambda: good)
        try:
            cache.request(MapManifest.from_record(MANIFEST))
            cache.request(MapManifest.from_record({**MANIFEST, "id": "8", "t": "1,2,3,99"}))
            cache.wait_idle()
            assert cache.path_for(7) == tmp_path / "7.png"
            assert cache.path_for(8) is None  # missing tile: no art, no crash
        finally:
            cache.close()

    def test_no_install_stops_trying(self, tmp_path):
        calls = []

        def factory():
            calls.append(1)
            raise WowError("no install")

        cache = MapArtCache(tmp_path, factory)
        try:
            cache.request(MapManifest.from_record(MANIFEST))
            cache.wait_idle()
            cache.request(MapManifest.from_record({**MANIFEST, "id": "9"}))
            cache.wait_idle()
            assert len(calls) == 1
        finally:
            cache.close()

    def test_provider_publishes_built_art(self, tmp_path):
        good = Tiles({1: png("red"), 2: png("red"), 3: png("red"), 4: png("red")})
        cache = MapArtCache(tmp_path / "maps", lambda: good)
        grabber = FakeGrabber()
        provider = WowProvider(grabber, map_art=cache, start=False)
        try:
            player = p.PlayerState(flags=p.P_HAS_POSITION, map_id=7, x=1, y=1)
            feed_record(provider, grabber, p.RECORD_MAP, MANIFEST, player=player)
            cache.wait_idle()
            grabber.push(Grab("ok", shot(player=player)))
            provider.step()
            assert provider.read()["wow.map.image"] == str(tmp_path / "maps" / "7.png")
        finally:
            provider.close()


@pytest.mark.parametrize(
    ("name", "exe", "rank"),
    [
        ("Wow.exe", r"F:\Blizzard Games\World of Warcraft\_forever_\Wow.exe", 0),
        ("Wow.exe", r"F:\Blizzard Games\World of Warcraft\_retail_\Wow.exe", 1),
        ("WowClassic.exe", "", 3),
        ("Wow.exe", "", 1),
    ],
)
def test_forever_process_is_preferred(name, exe, rank):
    from fitzlcd.sources.wow.capture import _rank

    assert _rank(name, exe) == rank


# ------------------------------------------------------------ unit frame data


def face(size: int = 40) -> Image.Image:
    """Something that looks like a drawn portrait: not flat, not black."""
    image = Image.new("RGB", (size, size), (90, 60, 40))
    image.paste((220, 180, 140), (size // 4, size // 4, size * 3 // 4, size * 3 // 4))
    return image


class PortraitGrabber(FakeGrabber):
    def __init__(self) -> None:
        super().__init__()
        self.regions: list[tuple[int, int, int, int]] = []
        self.portrait: Image.Image | Exception | None = face()

    def grab_region(self, x, y, w, h):
        self.regions.append((x, y, w, h))
        if isinstance(self.portrait, Exception):
            raise self.portrait
        return self.portrait


@pytest.fixture
def portrait_rig(tmp_path):
    grabber, clock = PortraitGrabber(), Clock()
    provider = WowProvider(grabber, state_path=tmp_path / "state.json", start=False, clock=clock)
    yield provider, grabber, clock
    provider.close()


def with_fields(state, **changes):
    return type(state)(**{**state.__dict__, **changes})


class TestUnitFrameData:
    def test_absolute_values_and_power_colour(self, rig):
        provider, grabber, _ = rig
        player = with_fields(PLAYER, power_type=0, hp=812, hp_max=900)
        grabber.push(Grab("ok", shot(player=player)))
        provider.step()
        m = provider.read()
        assert (m["wow.player.hp"], m["wow.player.hp_max"]) == (812, 900)
        assert m["wow.player.power_type"] == "Mana" and m["wow.player.power_color"] == "#0070DD"
        assert "wow.player.power" not in m and "wow.player.power_max" not in m  # unknown

    def test_unknown_power_type_is_omitted(self, rig):
        provider, grabber, _ = rig
        grabber.push(Grab("ok", shot()))
        provider.step()
        assert "wow.player.power_type" not in provider.read()

    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            ("38,38,120,120", (38, 38, 120, 120)),
            ("1,2,3", None),
            ("a,b,c,d", None),
            ("-5,0,100,100", None),
            ("0,0,4000,100", None),  # bigger than any portrait
            ("0,0,2,2", None),
            (None, None),
            (["0", "0", "64", "64"], None),
        ],
    )
    def test_parse_rect(self, text, expected):
        assert parse_rect(text) == expected

    def test_flat_or_black_squares_do_not_look_drawn(self):
        assert looks_drawn(face())
        assert not looks_drawn(Image.new("RGB", (40, 40)))
        assert not looks_drawn(Image.new("RGB", (40, 40), (120, 120, 120)))

    def test_portrait_is_grabbed_about_once_a_second(self, portrait_rig):
        provider, grabber, clock = portrait_rig
        feed_record(provider, grabber, p.RECORD_INFO, {**INFO, "pr": "38,38,40,40"})
        clock.now += 1.5
        grabber.push(Grab("ok", shot()))
        provider.step()
        m = provider.read()
        assert m["wow.player.portrait"].size == (40, 40)
        assert m["wow.player.portrait_source"] == "live"
        calls = len(grabber.regions)
        grabber.push(Grab("ok", shot()), Grab("ok", shot()))
        provider.step()
        provider.step()
        assert len(grabber.regions) == calls  # same second: no new grab
        clock.now += 1.5
        grabber.push(Grab("ok", shot()))
        provider.step()
        assert len(grabber.regions) == calls + 1 and grabber.regions[-1] == (38, 38, 40, 40)

    @pytest.mark.parametrize("result", [None, OSError("covered"), Image.new("RGB", (40, 40))])
    def test_a_failed_or_blank_grab_publishes_no_portrait(self, portrait_rig, result):
        provider, grabber, clock = portrait_rig
        grabber.portrait = result
        feed_record(provider, grabber, p.RECORD_INFO, {**INFO, "pr": "38,38,40,40"})
        clock.now += 1.5
        grabber.push(Grab("ok", shot()))
        provider.step()
        assert grabber.regions  # it did try
        assert "wow.player.portrait" not in provider.read()

    def test_no_rect_means_no_grab(self, portrait_rig):
        provider, grabber, clock = portrait_rig
        feed_record(provider, grabber, p.RECORD_INFO, INFO)
        clock.now += 1.5
        grabber.push(Grab("ok", shot()))
        provider.step()
        assert not grabber.regions

    def test_a_grabber_without_regions_is_fine(self, rig):
        provider, grabber, clock = rig  # FakeGrabber has no grab_region
        feed_record(provider, grabber, p.RECORD_INFO, {**INFO, "pr": "38,38,40,40"})
        clock.now += 1.5
        grabber.push(Grab("ok", shot()))
        provider.step()
        assert "wow.player.portrait" not in provider.read()

    def test_portrait_survives_a_restart_but_not_an_alt(self, portrait_rig, tmp_path):
        provider, grabber, clock = portrait_rig
        feed_record(provider, grabber, p.RECORD_INFO, {**INFO, "pr": "38,38,40,40"})
        clock.now += 1.5
        grabber.push(Grab("ok", shot()))
        provider.step()
        provider.close()
        assert (tmp_path / "portrait.png").is_file()
        again = WowProvider(
            FakeGrabber(), state_path=tmp_path / "state.json", start=False, clock=clock
        )
        try:
            assert again.read()["wow.player.portrait"].size == (40, 40)
            again._info = {**again._info, "name": "Somebody Else"}
            again._publish()
            assert "wow.player.portrait" not in again.read()
        finally:
            again.close()

    def test_corrupt_saved_portrait_is_ignored(self, tmp_path):
        (tmp_path / "state.json").write_text(
            json.dumps({"portrait_owner": "Fitz", "info": {"name": "Fitz"}}), encoding="utf-8"
        )
        (tmp_path / "portrait.png").write_bytes(b"not a png")
        provider = WowProvider(FakeGrabber(), state_path=tmp_path / "state.json", start=False)
        try:
            assert "wow.player.portrait" not in provider.read()
        finally:
            provider.close()


class TestClassIcon:
    RECORD = {"ci": "4567", "cc": "0.25,0.5,0,0.5"}

    def test_from_record(self):
        icon = ClassIcon.from_record(self.RECORD)
        assert icon == ClassIcon(4567, 2500, 5000, 0, 5000)
        assert icon.key == "icon_4567_2500_5000_0_5000"

    @pytest.mark.parametrize(
        "record",
        [
            {},
            {"ci": "0", "cc": "0,1,0,1"},
            {"ci": "12", "cc": "0,1,0"},
            {"ci": "12", "cc": "0.5,0.25,0,1"},  # right before left
            {"ci": "12", "cc": "0,2,0,1"},  # past the texture
            {"ci": "12", "cc": "a,b,c,d"},
            {"ci": "12", "cc": "nan,1,0,1"},
        ],
    )
    def test_bad_records_are_rejected(self, record):
        assert ClassIcon.from_record(record) is None

    def test_render_cuts_out_the_icon(self):
        texture = Image.new("RGBA", (64, 64), "red")
        texture.paste((0, 0, 255, 255), (16, 0, 32, 32))
        out = io.BytesIO()
        texture.save(out, format="PNG")
        icon = ClassIcon.from_record(self.RECORD).render(Tiles({4567: out.getvalue()}))
        assert icon.size == (16, 32) and icon.getpixel((8, 8)) == (0, 0, 255, 255)

    def test_corrupt_texture_raises_wowerror(self):
        with pytest.raises(WowError):
            ClassIcon.from_record(self.RECORD).render(Tiles({4567: b"junk"}))

    def test_provider_falls_back_to_the_class_icon(self, tmp_path):
        out = io.BytesIO()
        Image.new("RGBA", (64, 64), "purple").save(out, format="PNG")
        cache = MapArtCache(tmp_path / "maps", lambda: Tiles({4567: out.getvalue()}))
        grabber = FakeGrabber()
        provider = WowProvider(grabber, map_art=cache, start=False)
        try:
            feed_record(provider, grabber, p.RECORD_INFO, {**INFO, **self.RECORD})
            cache.wait_idle()
            grabber.push(Grab("ok", shot()))
            provider.step()
            m = provider.read()
            assert m["wow.player.class_icon"].endswith("icon_4567_2500_5000_0_5000.png")
            assert m["wow.player.portrait_source"] == "class"
        finally:
            provider.close()


def feed_party_info(provider, grabber, values, party):
    for chunk in p.chunk_record(p.RECORD_INFO, p.encode_record({**INFO, **values})):
        grabber.push(Grab("ok", shot(chunk, party=party)))
        provider.step()


class TestPartyWhereabouts:
    def test_member_in_another_zone(self, rig):
        provider, grabber, _ = rig
        away = with_fields(ALLY, map_id=1436)
        feed_party_info(provider, grabber, {"pz1": "Westfall", "pd1": "1240,1.5"}, (away,))
        (member,) = provider.read()["wow.party"]
        assert member["zone"] == "Westfall"
        assert member["distance_yd"] == 1240 and member["bearing"] == 1.5

    @pytest.mark.parametrize("pd", ["", "x,y", "-5,1", "1,2,3", "inf,1"])
    def test_bad_far_away_values_are_left_out(self, rig, pd):
        provider, grabber, _ = rig
        away = with_fields(ALLY, map_id=1436)
        feed_party_info(provider, grabber, {"pd1": pd}, (away,))
        (member,) = provider.read()["wow.party"]
        assert "distance_yd" not in member and "bearing" not in member

    def test_same_map_bearing_points_at_the_member(self, rig):
        provider, grabber, _ = rig
        feed_record(provider, grabber, p.RECORD_MAP, MAP)
        grabber.push(Grab("ok", shot()))
        provider.step()
        (member,) = provider.read()["wow.party"]
        # ALLY is due east: a quarter turn clockwise, so -pi/2 counter-clockwise.
        assert member["bearing"] == pytest.approx(-math.pi / 2, abs=1e-3)

    def test_shared_flag(self, rig):
        provider, grabber, _ = rig
        shared = with_fields(ALLY, flags=ALLY.flags | p.M_SHARED)
        grabber.push(Grab("ok", shot(party=(shared,))))
        provider.step()
        assert provider.read()["wow.party"][0]["shared"] is True
