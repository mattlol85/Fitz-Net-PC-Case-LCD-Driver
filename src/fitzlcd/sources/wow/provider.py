"""The ``wow.*`` metrics: read the addon's strip off the screen and publish it.

Like :class:`~fitzlcd.sources.cs2gsi.Cs2GsiProvider` this runs on its own
thread, here grabbing and decoding the strip about ten times a second, and
:meth:`WowProvider.read` only copies the latest snapshot.

The map is meant to stay up around the clock, so when the game closes the last
known map, positions and party are kept and published with
``wow.connected = False`` and a "last seen" line. They are also saved to disk,
so they survive an app restart. Anything the addon marked unknown (health is
secret in combat, positions are hidden in dungeons) is left out, never zeroed.

The player portrait is the one thing not decoded from the strip: the addon
reports where the game draws it, and about once a second that square is copied
straight off the screen. It is tied to the character it was taken of, so
logging in on an alt never shows the wrong face.

Metric names:

    wow.connected  wow.status_text  wow.last_seen_text  wow.last_seen_seconds_ago
    wow.player.{name,map_id,x,y,facing,level,class,class_color,health_pct,power_pct,
                hp,hp_max,power,power_max,power_type,power_color,
                combat,dead,ghost,instance,resting,afk,in_group,state_text}
    wow.player.{portrait (PIL image),portrait_source,class_icon (path)}
    wow.zone  wow.subzone  wow.coords_text  wow.location_text
    wow.party (list of dicts: slot name class color level health_pct online dead
               combat map_id x y distance_yd bearing zone shared)  wow.party.count
    wow.xp.{pct,rested_pct,text,max_level}  wow.gold  wow.gold_text
    wow.session.{seconds,played_text,xp,xp_per_hour,gold_text,deaths,level_ups}
    wow.durability.pct  wow.bags.free
    wow.target.{name,level,classification,reaction,text}
    wow.map.{id,name,aspect,image}
"""

from __future__ import annotations

import json
import logging
import math
import threading
import time
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import TYPE_CHECKING, Any

from PIL import Image

from fitzlcd.sources.stats import MetricProvider
from fitzlcd.sources.wow import protocol as p
from fitzlcd.sources.wow.capture import Grabber, ScreenGrabber
from fitzlcd.sources.wow.mapart import ClassIcon, MapArtCache, MapManifest

if TYPE_CHECKING:
    from fitzlcd.config import AppConfig

log = logging.getLogger(__name__)

#: ``classID`` -> (name, the game's own class colour).
CLASSES: dict[int, tuple[str, str]] = {
    1: ("Warrior", "#C69B6D"),
    2: ("Paladin", "#F48CBA"),
    3: ("Hunter", "#AAD372"),
    4: ("Rogue", "#FFF468"),
    5: ("Priest", "#FFFFFF"),
    6: ("Death Knight", "#C41E3A"),
    7: ("Shaman", "#0070DD"),
    8: ("Mage", "#3FC7EB"),
    9: ("Warlock", "#8788EE"),
    10: ("Monk", "#00FF98"),
    11: ("Druid", "#FF7C0A"),
    12: ("Demon Hunter", "#A330C9"),
    13: ("Evoker", "#33937F"),
}

#: ``UnitPowerType`` -> (name, the default UI's bar colour).
POWER_TYPES: dict[int, tuple[str, str]] = {
    0: ("Mana", "#0070DD"),
    1: ("Rage", "#FF0000"),
    2: ("Focus", "#FF8040"),
    3: ("Energy", "#FFFF00"),
    4: ("Combo Points", "#FFF569"),
    5: ("Runes", "#808080"),
    6: ("Runic Power", "#00D1FF"),
    7: ("Soul Shards", "#80528C"),
    8: ("Astral Power", "#4D85E6"),
    9: ("Holy Power", "#F2E699"),
    11: ("Maelstrom", "#0080FF"),
    13: ("Insanity", "#6600CC"),
    17: ("Fury", "#C942FD"),
    18: ("Pain", "#FF9C00"),
    19: ("Essence", "#4DBFB3"),
}

#: How often the portrait square is copied off the screen.
PORTRAIT_SECONDS = 1.0
#: A portrait outside this size (physical pixels) is not one the game drew.
PORTRAIT_SIZES = (12, 512)

#: No good frame for this long and the game counts as gone.
CONNECTED_SECONDS = 3.0
#: Transient decode failures (a tooltip sliding over the strip) are not news.
FAILURES_BEFORE_STATUS = 10
SAVE_EVERY_SECONDS = 30.0

_STATUS = {
    "not_running": "WOW NOT RUNNING",
    "minimized": "WOW IS MINIMISED",
    "unsupported": "WOW NEEDS WINDOWS",
    "error": "CAN'T SEE THE GAME",
    "hidden": "WOW IS BEHIND ANOTHER WINDOW",
    "not_found": "WAITING FOR THE FITZLCD ADDON",
    "calibration": "CAN'T READ THE STRIP · TURN OFF HDR",
    "checksum": "CAN'T READ THE STRIP",
    "version": "UPDATE THE WOW ADDON IN SETTINGS",
}

_REACTIONS = {1: "hostile", 2: "hostile", 3: "hostile", 4: "neutral"}


def _int(value: object) -> int | None:
    try:
        return int(float(str(value)))
    except (TypeError, ValueError):
        return None


def _float(value: object) -> float | None:
    try:
        number = float(str(value))
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def parse_rect(text: object) -> tuple[int, int, int, int] | None:
    """The addon's ``x,y,w,h`` portrait rect, or ``None`` if it is not a sane one."""
    if not isinstance(text, str):
        return None
    parts = [_int(v) for v in text.split(",")]
    if len(parts) != 4:
        return None
    x, y, w, h = parts
    if x is None or y is None or w is None or h is None or x < 0 or y < 0:
        return None
    low, high = PORTRAIT_SIZES
    if not (low <= w <= high and low <= h <= high):
        return None
    return x, y, w, h


def looks_drawn(image: Image.Image) -> bool:
    """False for a flat or black square: the portrait is covered, or the capture is blank."""
    extrema = image.convert("RGB").getextrema()
    return max(hi for _, hi in extrema) >= 16 and any(hi - lo >= 12 for lo, hi in extrema)


def format_money(copper: int) -> str:
    sign = "-" if copper < 0 else ""
    copper = abs(copper)
    gold, rest = divmod(copper, 10000)
    silver, copper = divmod(rest, 100)
    if gold:
        return f"{sign}{gold:,}g {silver}s {copper}c"
    if silver:
        return f"{sign}{silver}s {copper}c"
    return f"{sign}{copper}c"


def format_duration(seconds: float) -> str:
    seconds = max(0, int(seconds))
    if seconds < 60:
        return f"{seconds}s"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m"
    hours, minutes = divmod(minutes, 60)
    if hours < 48:
        return f"{hours}h {minutes:02d}m"
    return f"{hours // 24}d {hours % 24}h"


class WowProvider(MetricProvider):
    name = "wow"

    def __init__(
        self,
        grabber: Grabber | None = None,
        hz: float = 10.0,
        map_art: MapArtCache | None = None,
        state_path: Path | None = None,
        start: bool = True,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._grabber = grabber or ScreenGrabber()
        self._interval = 1.0 / max(1.0, min(30.0, float(hz)))
        self._map_art = map_art
        self._state_path = state_path
        self._clock = clock

        self._assembler = p.RecordAssembler()
        self._player: p.PlayerState | None = None
        self._party: tuple[p.MemberState, ...] = ()
        self._info: dict[str, str] = {}
        self._maps: dict[int, dict[str, str]] = {}
        self._last_good: float | None = None
        self._reason = "not_running"
        self._failures = 0
        self._saved_at = 0.0
        self._dirty = False
        self._portrait: Image.Image | None = None
        self._portrait_owner = ""
        self._portrait_at: float | None = None
        self._portrait_dirty = False

        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {}
        self._load_state()  # after every attribute it may fill in
        self._publish()

        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        if start:
            self._thread = threading.Thread(target=self._run, name="fitzlcd-wow", daemon=True)
            self._thread.start()

    @classmethod
    def from_config(cls, config: AppConfig) -> WowProvider:
        """The provider both entry points register: screen capture, map art, saved state."""
        from fitzlcd.config import app_dir
        from fitzlcd.sources.wow.install import open_storage

        home = app_dir() / "wow"
        art = MapArtCache(
            home / "maps",
            lambda: open_storage(config, cache_path=home / "casc_index.json"),
        )
        return cls(
            hz=config.wow_capture_hz,
            map_art=art,
            state_path=home / "last_state.json",
        )

    # -------------------------------------------------------------- thread

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                self.step()
            except Exception as exc:  # noqa: BLE001 - the capture thread must never die
                log.warning("wow capture step failed: %s", exc, exc_info=True)

    def step(self) -> None:
        """One grab-decode-publish cycle. Public for tests."""
        try:
            grab = self._grabber.grab()
        except OSError as exc:
            log.debug("wow grab failed: %s", exc)
            grab = None
        if grab is None or grab.status != "ok" or grab.image is None:
            self._fail(grab.status if grab else "error", immediate=True)
            return
        try:
            frame = p.decode_image(grab.image)
        except p.StripError as exc:
            reason = exc.reason
            if reason == "not_found" and not grab.foreground:
                reason = "hidden"
            self._fail(reason, immediate=reason == "version")
            return
        self._ingest(frame)
        self._grab_portrait()

    def _fail(self, reason: str, immediate: bool) -> None:
        self._failures += 1
        if immediate or self._failures >= FAILURES_BEFORE_STATUS:
            self._reason = reason
        self._publish()
        if self._dirty and not self._connected():
            self._save_state()  # the game just went away: keep what it showed

    def _ingest(self, frame: p.Frame) -> None:
        self._failures = 0
        self._last_good = self._clock()
        self._player = frame.player
        self._party = frame.party
        record = self._assembler.feed(frame.chunk)
        if record is not None:
            key, body = record
            values = p.parse_record(body)
            if key == p.RECORD_INFO:
                self._info = values
                icon = ClassIcon.from_record(values)
                if icon is not None and self._map_art is not None:
                    self._map_art.request(icon)
            elif key == p.RECORD_MAP:
                map_id = _int(values.get("id"))
                if map_id:
                    self._maps[map_id] = values
                    manifest = MapManifest.from_record(values)
                    if manifest is not None and self._map_art is not None:
                        self._map_art.request(manifest)
        self._dirty = True
        self._publish()
        if self._last_good - self._saved_at >= SAVE_EVERY_SECONDS:
            self._save_state()

    def _connected(self) -> bool:
        return self._last_good is not None and self._clock() - self._last_good <= CONNECTED_SECONDS

    def _grab_portrait(self) -> None:
        now = self._clock()
        if self._portrait_at is not None and 0 <= now - self._portrait_at < PORTRAIT_SECONDS:
            return
        self._portrait_at = now
        rect = parse_rect(self._info.get("pr"))
        grab_region = getattr(self._grabber, "grab_region", None)
        if rect is None or grab_region is None:
            return
        try:
            image = grab_region(*rect)
        except OSError as exc:
            log.debug("wow portrait grab failed: %s", exc)
            return
        if image is None or not looks_drawn(image):
            return
        self._portrait = image.convert("RGB")
        self._portrait_owner = self._info.get("name", "")
        self._portrait_dirty = True
        self._publish()

    def _current_portrait(self) -> Image.Image | None:
        """The last portrait, as long as it is of the character playing now."""
        name, owner = self._info.get("name", ""), self._portrait_owner
        if self._portrait is None or (name and owner and name != owner):
            return None
        return self._portrait

    # ------------------------------------------------------------- publish

    def _publish(self) -> None:
        values = self._metrics()
        with self._lock:
            self._snapshot = values

    def read(self) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self._snapshot)
            last_good = self._last_good
        connected = self._connected()
        snapshot["wow.connected"] = connected
        snapshot["wow.status_text"] = "" if connected else _STATUS.get(self._reason, "")
        # Text, not a missing key: a scene line showing it should be blank, not a dash.
        snapshot["wow.last_seen_text"] = ""
        if last_good is not None:
            age = max(0.0, self._clock() - last_good)
            snapshot["wow.last_seen_seconds_ago"] = age
            snapshot["wow.last_seen_text"] = (
                "" if connected else f"last seen {format_duration(age)} ago"
            )
        return snapshot

    def _metrics(self) -> dict[str, Any]:
        m: dict[str, Any] = {}
        player, info = self._player, self._info
        if player is not None:
            self._player_metrics(m, player)
        self._info_metrics(m, info)
        m["wow.party"] = self._party_list()
        m["wow.party.count"] = len(m["wow.party"])
        map_id = player.map_id if player is not None else 0
        if map_id:
            m["wow.map.id"] = map_id
            record = self._maps.get(map_id, {})
            if record.get("name"):
                m["wow.map.name"] = record["name"]
            width, height = _float(record.get("w")), _float(record.get("h"))
            if width and height:
                m["wow.map.aspect"] = width / height
            if self._map_art is not None:
                art = self._map_art.path_for(map_id)
                if art is not None:
                    m["wow.map.image"] = str(art)
        location = ", ".join(x for x in (m.get("wow.subzone"), m.get("wow.zone")) if x)
        if location:
            m["wow.location_text"] = location
        icon = ClassIcon.from_record(info)
        if icon is not None and self._map_art is not None:
            icon_path = self._map_art.path_for(icon.key)
            if icon_path is not None:
                m["wow.player.class_icon"] = str(icon_path)
        portrait = self._current_portrait()
        if portrait is not None:
            m["wow.player.portrait"] = portrait
            m["wow.player.portrait_source"] = "live"
        elif "wow.player.class_icon" in m:
            m["wow.player.portrait_source"] = "class"
        return m

    def _player_metrics(self, m: dict[str, Any], player: p.PlayerState) -> None:
        flags = player.flags
        if player.map_id:
            m["wow.player.map_id"] = player.map_id
        position = player.position
        if position is not None:
            m["wow.player.x"], m["wow.player.y"] = position
            m["wow.coords_text"] = f"{position[0] * 100:.1f}, {position[1] * 100:.1f}"
        facing = player.facing_radians
        if facing is not None:
            m["wow.player.facing"] = facing
        if player.level:
            m["wow.player.level"] = player.level
        if player.class_id in CLASSES:
            m["wow.player.class"], m["wow.player.class_color"] = CLASSES[player.class_id]
        if player.health != p.UNKNOWN:
            m["wow.player.health_pct"] = float(player.health)
        if player.power != p.UNKNOWN:
            m["wow.player.power_pct"] = float(player.power)
        for key, value in (
            ("hp", player.hp),
            ("hp_max", player.hp_max),
            ("power", player.pw),
            ("power_max", player.pw_max),
        ):
            if value != p.UNKNOWN32:
                m[f"wow.player.{key}"] = value
        if player.power_type in POWER_TYPES:
            name, color = POWER_TYPES[player.power_type]
            m["wow.player.power_type"], m["wow.player.power_color"] = name, color
        for key, bit in (
            ("combat", p.P_COMBAT),
            ("dead", p.P_DEAD),
            ("ghost", p.P_GHOST),
            ("instance", p.P_INSTANCE),
            ("resting", p.P_RESTING),
            ("afk", p.P_AFK),
            ("in_group", p.P_IN_GROUP),
        ):
            m[f"wow.player.{key}"] = bool(flags & bit)
        if flags & p.P_GHOST:
            state = "GHOST"
        elif flags & p.P_DEAD:
            state = "DEAD"
        elif flags & p.P_COMBAT:
            state = "IN COMBAT"
        elif flags & p.P_AFK:
            state = "AFK"
        elif flags & p.P_RESTING:
            state = "RESTING"
        else:
            state = ""
        m["wow.player.state_text"] = state

    def _info_metrics(self, m: dict[str, Any], info: dict[str, str]) -> None:
        for key, metric in (
            ("name", "wow.player.name"),
            ("zone", "wow.zone"),
            ("sub", "wow.subzone"),
        ):
            if info.get(key):
                m[metric] = info[key]

        xp, xp_max, rest = _int(info.get("xp")), _int(info.get("xpmax")), _int(info.get("rest"))
        if info.get("maxlvl"):
            m["wow.xp.max_level"] = True
        elif xp is not None and xp_max:
            m["wow.xp.pct"] = 100.0 * xp / xp_max
            m["wow.xp.text"] = f"{xp:,} / {xp_max:,}"
            if rest is not None:
                m["wow.xp.rested_pct"] = min(150.0, 100.0 * rest / xp_max)

        money = _int(info.get("money"))
        if money is not None:
            m["wow.gold"] = money / 10000
            m["wow.gold_text"] = format_money(money)
        gained = _int(info.get("moneygain"))
        if gained is not None:
            m["wow.session.gold_text"] = ("+" if gained >= 0 else "") + format_money(gained)

        seconds = _int(info.get("sess"))
        if seconds is not None:
            m["wow.session.seconds"] = seconds
            m["wow.session.played_text"] = format_duration(seconds)
        xp_gained = _int(info.get("xpgain"))
        if xp_gained is not None:
            m["wow.session.xp"] = xp_gained
            if seconds and seconds >= 60:
                m["wow.session.xp_per_hour"] = xp_gained * 3600 / seconds
        for key, metric in (
            ("deaths", "wow.session.deaths"),
            ("lvlups", "wow.session.level_ups"),
            ("dur", "wow.durability.pct"),
            ("bags", "wow.bags.free"),
        ):
            value = _int(info.get(key))
            if value is not None:
                m[metric] = value

        name = info.get("tname")
        if name:
            m["wow.target.name"] = name
            level = _int(info.get("tlvl"))
            level_text = "??" if level is not None and level < 0 else (str(level) if level else "")
            if level_text:
                m["wow.target.level"] = level_text
            classification = info.get("tcls", "")
            if classification and classification != "normal":
                m["wow.target.classification"] = classification
            reaction = _int(info.get("trx"))
            if reaction:
                m["wow.target.reaction"] = _REACTIONS.get(reaction, "friendly")
            tag = {"worldboss": "Boss", "rareelite": "Rare Elite"}.get(
                classification, classification.title() if classification != "normal" else ""
            )
            detail = " ".join(x for x in (level_text, tag) if x)
            m["wow.target.text"] = f"{name} · {detail}" if detail else name

    def _party_list(self) -> list[dict[str, Any]]:
        player = self._player
        here = player.position if player is not None else None
        yards = self._yards(player.map_id) if player is not None else None
        members: list[dict[str, Any]] = []
        for slot, member in enumerate(self._party, start=1):
            if not member.present:
                continue
            entry: dict[str, Any] = {
                "slot": slot,
                "online": bool(member.flags & p.M_ONLINE),
                "dead": bool(member.flags & (p.M_DEAD | p.M_GHOST)),
                "combat": bool(member.flags & p.M_COMBAT),
                "shared": bool(member.flags & p.M_SHARED),
            }
            zone = self._info.get(f"pz{slot}")
            if zone:
                entry["zone"] = zone
            far = self._info.get(f"pd{slot}", "").split(",")
            if len(far) == 2:
                distance, bearing = _float(far[0]), _float(far[1])
                if distance is not None and distance >= 0 and bearing is not None:
                    entry["distance_yd"], entry["bearing"] = distance, bearing
            name = self._info.get(f"p{slot}")
            if name:
                entry["name"] = name
            if member.class_id in CLASSES:
                entry["class"], entry["color"] = CLASSES[member.class_id]
            if member.level:
                entry["level"] = member.level
            if member.health != p.UNKNOWN:
                entry["health_pct"] = float(member.health)
            if member.map_id:
                entry["map_id"] = member.map_id
            position = member.position
            if position is not None:
                entry["x"], entry["y"] = position
                same_map = player is not None and member.map_id == player.map_id
                if same_map and here is not None and yards is not None:
                    dx = (position[0] - here[0]) * yards[0]
                    dy = (position[1] - here[1]) * yards[1]
                    entry["distance_yd"] = math.hypot(dx, dy)
                    # Map x grows east, y south; bearing is counter-clockwise from north.
                    entry["bearing"] = math.atan2(-dx, -dy)
            members.append(entry)
        return members

    def _yards(self, map_id: int) -> tuple[float, float] | None:
        record = self._maps.get(map_id, {})
        width, height = _float(record.get("yw")), _float(record.get("yh"))
        if width and height and width > 0 and height > 0:
            return width, height
        return None

    # -------------------------------------------------------- persistence

    @property
    def _portrait_path(self) -> Path | None:
        return self._state_path.with_name("portrait.png") if self._state_path else None

    def _save_portrait(self) -> None:
        path, portrait = self._portrait_path, self._portrait
        if path is None or portrait is None or not self._portrait_dirty:
            return
        self._portrait_dirty = False
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            partial = path.with_suffix(".part")
            portrait.save(partial, format="PNG")
            partial.replace(path)
        except OSError as exc:  # a convenience, like the rest of the saved state
            log.warning("could not save the WoW portrait to %s: %s", path, exc)

    def _load_portrait(self, owner: object) -> None:
        path = self._portrait_path
        if path is None or not isinstance(owner, str):
            return
        try:
            with Image.open(path) as raw:
                raw.load()
                image = raw.convert("RGB")
        except FileNotFoundError:
            return
        except Exception as exc:  # noqa: BLE001 - Pillow raises odd types on bad files
            log.warning("ignoring unreadable WoW portrait %s: %s", path, exc)
            return
        low, high = PORTRAIT_SIZES
        if low <= image.width <= high and low <= image.height <= high:
            self._portrait, self._portrait_owner = image, owner

    def _save_state(self) -> None:
        if self._state_path is None:
            return
        self._save_portrait()
        self._saved_at = self._last_good or self._clock()
        self._dirty = False
        state = {
            "last_good": self._last_good,
            "player": asdict(self._player) if self._player else None,
            "party": [asdict(m) for m in self._party],
            "info": self._info,
            "maps": {str(k): v for k, v in self._maps.items()},
            "portrait_owner": self._portrait_owner if self._portrait is not None else None,
        }
        try:
            self._state_path.parent.mkdir(parents=True, exist_ok=True)
            partial = self._state_path.with_suffix(".part")
            partial.write_text(json.dumps(state), encoding="utf-8")
            partial.replace(self._state_path)
        except OSError as exc:  # a convenience; the live display does not depend on it
            log.warning("could not save the last WoW state to %s: %s", self._state_path, exc)

    def _load_state(self) -> None:
        if self._state_path is None:
            return
        try:
            data = json.loads(self._state_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (OSError, ValueError) as exc:
            log.warning("ignoring unreadable WoW state %s: %s", self._state_path, exc)
            return
        if not isinstance(data, dict):
            return
        try:
            player = data.get("player")
            if isinstance(player, dict):
                self._player = p.PlayerState(**{k: int(v) for k, v in player.items()})
            party = data.get("party")
            if isinstance(party, list):
                self._party = tuple(
                    p.MemberState(**{k: int(v) for k, v in m.items()})
                    for m in party[: p.PARTY_SLOTS]
                    if isinstance(m, dict)
                )
            info = data.get("info")
            if isinstance(info, dict):
                self._info = {str(k): str(v) for k, v in info.items()}
            maps = data.get("maps")
            if isinstance(maps, dict):
                for key, record in maps.items():
                    map_id = _int(key)
                    if map_id and isinstance(record, dict):
                        self._maps[map_id] = {str(k): str(v) for k, v in record.items()}
            self._load_portrait(data.get("portrait_owner"))
            last_good = _float(data.get("last_good"))
            self._last_good = last_good
            self._saved_at = last_good or 0.0
        except (TypeError, ValueError) as exc:
            log.warning("ignoring malformed WoW state %s: %s", self._state_path, exc)
            self._player, self._party, self._info, self._maps = None, (), {}, {}
            self._portrait, self._portrait_owner = None, ""
            self._last_good = None

    def close(self) -> None:
        self._stop.set()
        if self._thread is not None and self._thread.is_alive():
            self._thread.join(timeout=2.0)
        for step in (self._save_state if self._dirty else None, self._grabber.close):
            if step is None:
                continue
            try:
                step()
            except Exception as exc:  # noqa: BLE001 - teardown steps are isolated
                log.debug("wow provider close step failed: %s", exc)
        if self._map_art is not None:
            try:
                self._map_art.close()
            except Exception as exc:  # noqa: BLE001 - teardown steps are isolated
                log.debug("wow map art close failed: %s", exc)
