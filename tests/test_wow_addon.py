"""Runs the real FitzLCD addon under Lua 5.1 against a stubbed game API.

The cells the addon colours are rendered into a screenshot and decoded by the
app's own decoder, so this pins the two halves of the protocol together, and
catches Lua errors in the collector without launching the game.
"""

from __future__ import annotations

import math
from pathlib import Path

import pytest
from PIL import Image

from fitzlcd.sources.wow import protocol as p

lupa = pytest.importorskip("lupa.lua51")

ADDON = Path(__file__).parents[1] / "src" / "fitzlcd" / "sources" / "wow" / "addon" / "FitzLCD"

#: A minimal game: frames that remember scripts and texture colours, and unit
#: functions answering from the ``world`` table that tests fill in.
STUBS = r"""
-- A secret the addon cannot read. Secret(n) still draws as n in a StatusBar, the
-- way the game renders values it hides; the bare SECRET cannot be drawn at all.
local secret_mt = { __tostring = function() return "<secret>" end }
function Secret(n) return setmetatable({ hidden = n }, secret_mt) end
SECRET = Secret(nil)
frames, printed, clock = {}, {}, 100

local function noop_meta(t)
  return setmetatable(t, { __index = function() return function() end end })
end

function CreateFrame(kind, name, parent)
  local f = noop_meta({ kind = kind, scripts = {}, events = {}, textures = {}, shown = true })
  function f:SetShown(on) self.shown = on end
  function f:SetMinMaxValues(lo, hi) self.max = hi end
  function f:SetValue(v)
    if issecretvalue(v) then
      if v.hidden == nil then error("cannot draw this secret") end
      v = v.hidden
    end
    self.value = v
  end
  function f:SetScript(n, fn) self.scripts[n] = fn end
  function f:RegisterEvent(e) self.events[e] = true end
  function f:CreateTexture()
    local t = noop_meta({})
    function t:SetColorTexture(r, g, b) self.rgb = { r, g, b } end
    self.textures[#self.textures + 1] = t
    return t
  end
  frames[#frames + 1] = f
  return f
end

UIParent = {}
function GetPhysicalScreenSize() return 2560, 1440 end
function GetTime() return clock end
function print(s) printed[#printed + 1] = s end
function issecretvalue(v) return getmetatable(v) == secret_mt end
SlashCmdList = {}

world = { units = {}, info = {} }
local function u(unit) return world.units[unit] or {} end

C_Map = {}
function C_Map.GetBestMapForUnit(unit) return u(unit).map end
function C_Map.GetPlayerMapPosition(map, unit)
  local x, y = u(unit).x, u(unit).y
  if x == nil then return nil end
  return { GetXY = function() return x, y end }
end
function C_Map.GetMapInfo(id) return { name = "Elwynn Forest", mapType = 3, parentMapID = 1415 } end
function C_Map.GetMapArtLayers(id)
  return { { layerWidth = 1002, layerHeight = 668, tileWidth = 256, tileHeight = 256 } }
end
function C_Map.GetMapArtLayerTextures(id, layer) return world.tiles or {} end
function C_Map.GetMapWorldSize(id) return 3470.8, 2314.6 end
C_Container = {}
function C_Container.GetContainerNumFreeSlots(bag) return 3, 0 end

function UnitExists(unit) return world.units[unit] ~= nil end
function UnitIsConnected(unit) return u(unit).online ~= false end
function UnitIsDead(unit) return u(unit).dead end
function UnitIsGhost(unit) return false end
function UnitAffectingCombat(unit) return u(unit).combat end
function UnitClass(unit) return "Mage", "MAGE", u(unit).class end
function UnitLevel(unit) return u(unit).level end
function UnitHealth(unit) return u(unit).hp end
function UnitHealthMax(unit) return u(unit).hpmax end
function UnitPower(unit) return 50 end
function UnitPowerMax(unit) return 100 end
function UnitName(unit) return u(unit).name end
function UnitXP() return world.info.xp or 0 end
function UnitXPMax() return 1000 end
function GetXPExhaustion() return nil end
function GetMoney() return world.info.money or 0 end
function GetRealZoneText() return "Elwynn Forest" end
function GetSubZoneText() return "Goldshire" end
function GetInventoryItemDurability(slot) if slot == 5 then return 30, 100 end end
function UnitClassification() return "elite" end
function UnitReaction() return 2 end
function UnitIsPlayer() return false end
function IsInInstance() return world.instance or false, "none" end
function IsResting() return false end
function UnitIsAFK() return false end
function IsInGroup() return world.units.party1 ~= nil end
function IsInRaid() return false end
function GetPlayerFacing() return u("player").facing end
function UnitPowerType(unit) return u(unit).ptype, "MANA" end

-- Cross-zone distances: each map sits 1000 yards further north than the last.
function CreateVector2D(x, y) return { x = x, y = y } end
function C_Map.GetWorldPosFromMapPos(map, v)
  local pos = { map * 1000 + v.x * 1000, v.y * 1000 }
  return 0, { GetXY = function() return pos[1], pos[2] end }
end

-- The default PlayerFrame's portrait, 64 UI units square near the top-left.
local portrait = {}
function portrait:IsVisible() return world.portrait ~= false end
function portrait:GetEffectiveScale() return 1 end
function portrait:GetLeft() return 20 end
function portrait:GetTop() return 748 end
function portrait:GetWidth() return 64 end
function portrait:GetHeight() return 64 end
PlayerFrame = { PlayerFrameContainer = { PlayerPortrait = portrait } }
C_Texture = {}
function C_Texture.GetAtlasInfo(name)
  if name == "classicon-mage" then
    return { file = 4567, leftTexCoord = 0.25, rightTexCoord = 0.5, topTexCoord = 0, bottomTexCoord = 0.25 }
  end
end

sent = {}
C_ChatInfo = {}
function C_ChatInfo.RegisterAddonMessagePrefix(prefix) return true end
function C_ChatInfo.SendAddonMessage(prefix, msg, channel)
  sent[#sent + 1] = { prefix = prefix, msg = msg, channel = channel }
end
"""


class Game:
    def __init__(self) -> None:
        self.lua = lupa.LuaRuntime(unpack_returned_tuples=True)
        self.lua.execute(STUBS)
        self.world = self.lua.globals().world
        ns = self.lua.table()
        load = self.lua.eval(
            "function(src, name, ns) return assert(loadstring(src, '@' .. name))('FitzLCD', ns) end"
        )
        for name in ("Protocol.lua", "Strip.lua", "Share.lua", "Collect.lua"):
            load((ADDON / name).read_text(encoding="utf-8"), name, ns)
        self.ns = ns

    def unit(self, unit: str, **values) -> None:
        self.world.units[unit] = self.lua.table_from(values)

    def fire(self, event: str, *args) -> None:
        for frame in self.lua.globals().frames.values():
            handler = frame.scripts["OnEvent"]
            if handler is not None and frame.events[event]:
                handler(frame, event, *args)

    def records(self, frames: int = 20) -> dict[int, dict[str, str]]:
        asm = p.RecordAssembler()
        found: dict[int, dict[str, str]] = {}
        for _ in range(frames):
            self.tick()
            done = asm.feed(self.frame().chunk)
            if done:
                found[done[0]] = p.parse_record(done[1])
        return found

    def tick(self, frames: int = 1) -> None:
        for frame in self.lua.globals().frames.values():
            handler = frame.scripts["OnUpdate"]
            if handler is not None:
                for _ in range(frames):
                    handler(frame, 0.11)

    def screenshot(self) -> Image.Image:
        strip = self.ns.Strip.frame
        textures = list(strip.textures.values())[: p.CELLS]
        levels = [tuple(round(c * 255 / 17) for c in t.rgb.values()) for t in textures]
        return p.render_strip(levels, pitch=3, gauges=self.gauges())

    def gauges(self) -> list[float | None]:
        """What each gauge StatusBar would draw: its fill, or None while hidden."""
        bars = [f for f in self.lua.globals().frames.values() if f.kind == "StatusBar"]
        assert len(bars) == p.GAUGES
        fills: list[float | None] = []
        for bar in bars:
            if not bar.shown or bar.value is None or not bar.max:
                fills.append(None)
            else:
                fills.append(bar.value / bar.max)
        return fills

    def frame(self) -> p.Frame:
        return p.decode_image(self.screenshot())

    @property
    def printed(self) -> list[str]:
        return list(self.lua.globals().printed.values())


@pytest.fixture
def game() -> Game:
    g = Game()
    g.unit(
        "player",
        map=1429,
        x=0.42,
        y=0.65,
        facing=3.14159,
        level=12,
        **{"class": 8},
        hp=300,
        hpmax=400,
        name="Fitz",
    )
    g.fire("PLAYER_LOGIN")
    return g


def test_player_round_trips_through_the_real_addon(game):
    game.tick()
    player = game.frame().player
    assert player.map_id == 1429
    assert player.position == pytest.approx((0.42, 0.65), abs=1e-4)
    assert player.facing_radians == pytest.approx(3.14159, abs=0.03)
    assert (player.level, player.class_id, player.health, player.power) == (12, 8, 75, 50)
    assert not game.printed, game.printed


def test_secret_health_is_unknown_not_zero(game):
    game.unit("player", map=1429, x=0.5, y=0.5, hp=game.lua.globals().SECRET, hpmax=400)
    game.tick()
    assert game.frame().player.health == p.UNKNOWN
    assert not game.printed, game.printed


def test_instance_hides_position_without_error(game):
    game.unit("player", map=1429, level=12)  # the game returns no position
    game.world.instance = True
    game.tick()
    player = game.frame().player
    assert player.position is None
    assert player.flags & p.P_INSTANCE
    assert not game.printed


def test_party_members(game):
    game.unit("party1", map=1429, x=0.4, y=0.6, level=11, **{"class": 1}, hp=10, hpmax=100)
    game.unit("party2", map=1429, level=13, online=False)
    game.tick()
    party = game.frame().party
    assert party[0].position == pytest.approx((0.4, 0.6), abs=1e-4)
    assert (party[0].class_id, party[0].level, party[0].health) == (1, 11, 10)
    assert party[1].present and not party[1].flags & p.M_ONLINE and party[1].position is None
    assert not party[2].present


def test_slow_lane_records_reassemble(game):
    game.lua.globals().world.tiles = game.lua.table_from(list(range(200000, 200012)))
    game.unit("party1", map=1429, x=0.4, y=0.6, name="Ally")
    game.unit("target", level=14, name="Hogger")
    game.fire("GROUP_ROSTER_UPDATE")
    asm = p.RecordAssembler()
    records = {}
    for _ in range(12):
        game.tick()
        done = asm.feed(game.frame().chunk)
        if done:
            records[done[0]] = p.parse_record(done[1])
    info, manifest = records[p.RECORD_INFO], records[p.RECORD_MAP]
    assert info["zone"] == "Elwynn Forest" and info["sub"] == "Goldshire"
    assert info["name"] == "Fitz" and info["p1"] == "Ally"
    assert info["dur"] == "30" and info["bags"] == "15"
    assert (info["tname"], info["tlvl"], info["tcls"]) == ("Hogger", "14", "elite")
    assert manifest["id"] == "1429" and manifest["w"] == "1002"
    assert manifest["t"].split(",") == [str(n) for n in range(200000, 200012)]


def test_session_xp_counts_across_a_level_up(game):
    game.world.info.xp = 900
    game.fire("PLAYER_XP_UPDATE")
    game.world.info.xp = 100  # dinged: 100 to finish the bar, 100 into the next
    game.fire("PLAYER_XP_UPDATE")
    asm = p.RecordAssembler()
    info = None
    for _ in range(4):
        game.tick()
        done = asm.feed(game.frame().chunk)
        if done and done[0] == p.RECORD_INFO:
            info = p.parse_record(done[1])
    assert info is not None and info["xpgain"] == "1100"


def test_a_lua_error_is_reported_once_and_the_loop_survives(game):
    game.lua.execute("C_Map.GetBestMapForUnit = function() error('boom') end")
    game.tick(5)
    assert len(game.printed) == 1 and "boom" in game.printed[0]


def test_slash_command_toggles_the_strip(game):
    slash = game.lua.globals().SlashCmdList.FITZLCD
    slash("off")
    game.tick()
    slash("")
    assert any("strip off" in line for line in game.printed)


def test_absolute_health_power_and_power_type(game):
    game.unit("player", map=1429, x=0.4, y=0.6, level=12, hp=300, hpmax=400, ptype=0)
    game.tick()
    player = game.frame().player
    assert (player.hp, player.hp_max, player.pw, player.pw_max) == (300, 400, 50, 100)
    assert player.power_type == 0


def test_secret_absolute_health_is_unknown_not_zero(game):
    game.unit("player", map=1429, hp=game.lua.globals().SECRET, hpmax=400)
    game.tick()
    player = game.frame().player
    assert player.hp == p.UNKNOWN32 and player.hp_max == 400
    assert player.power_type == p.UNKNOWN  # the stub reports none
    assert not game.printed, game.printed


def test_secret_health_still_draws_as_a_gauge(game):
    """Forever hides current health even out of combat; a StatusBar still shows it."""
    secret = game.lua.globals().Secret
    game.unit("player", map=1429, hp=secret(300), hpmax=400)
    game.tick()
    frame = game.frame()
    assert frame.player.hp == p.UNKNOWN32  # still never read as a number
    assert frame.player.flags & p.P_HP_GAUGE and frame.player.flags & p.P_POWER_GAUGE
    assert frame.gauges[0] == pytest.approx(0.75, abs=0.01)
    assert frame.gauges[1] == pytest.approx(0.5, abs=0.01)
    assert not game.printed, game.printed


def test_an_undrawable_value_hides_its_gauge_and_clears_the_flag(game):
    game.unit("player", map=1429, hp=game.lua.globals().SECRET, hpmax=400)
    game.tick()
    frame = game.frame()
    assert not frame.player.flags & p.P_HP_GAUGE
    assert frame.gauges[0] is None
    assert game.gauges()[0] is None  # the bar itself is hidden, not left stale
    assert frame.player.flags & p.P_POWER_GAUGE  # power was fine
    assert not game.printed, game.printed


def test_portrait_rect_and_class_icon(game):
    info = game.records()[p.RECORD_INFO]
    # 64 UI units at 1440 / 768 physical pixels each, measured from the top.
    assert info["pr"] == "38,38,120,120"
    assert info["ci"] == "4567" and info["cc"] == "0.2500,0.5000,0.0000,0.2500"


def test_a_hidden_portrait_is_not_reported(game):
    game.world.portrait = False
    assert "pr" not in game.records()[p.RECORD_INFO]


def test_a_member_in_another_zone_gets_its_zone_and_bearing(game):
    game.unit("party1", map=1436, x=0.5, y=0.5, name="Ally")
    info = game.records()[p.RECORD_INFO]
    assert info["pz1"] == "Elwynn Forest"  # the stub names every map the same
    yards, bearing = (float(v) for v in info["pd1"].split(","))
    assert yards == pytest.approx(math.hypot(7080, 150), abs=1)  # 7 maps north, 150 east
    assert bearing == pytest.approx(math.atan2(-150, 7080), abs=1e-3)


def test_a_member_on_the_same_map_has_no_far_away_keys(game):
    game.unit("party1", map=1429, x=0.5, y=0.5, name="Ally")
    info = game.records()[p.RECORD_INFO]
    assert "pz1" not in info and "pd1" not in info


def test_a_shared_position_fills_in_what_the_map_api_hides(game):
    game.unit("party1", map=1429, name="Ally")  # C_Map gives no position
    game.fire("CHAT_MSG_ADDON", "FitzLCD", "1:1429:32768:16384", "PARTY", "Ally-Realm")
    game.tick()
    member = game.frame().party[0]
    assert member.flags & p.M_SHARED
    assert member.position == pytest.approx((0.5, 0.25), abs=1e-3)
    game.lua.globals().clock = 200  # ten seconds is the limit
    game.tick()
    assert game.frame().party[0].position is None


@pytest.mark.parametrize(
    "message",
    ["2:1429:1:1", "1:1429:70000:1", "1:0:1:1", "garbage", "1:1429:1:1:extra", ""],
)
def test_malformed_shared_positions_are_ignored(game, message):
    game.unit("party1", map=1429, name="Ally")
    game.fire("CHAT_MSG_ADDON", "FitzLCD", message, "PARTY", "Ally")
    game.fire("CHAT_MSG_ADDON", "OtherAddon", "1:1429:1:1", "PARTY", "Ally")
    game.tick()
    assert game.frame().party[0].position is None
    assert not game.printed, game.printed


def test_own_position_is_shared_with_the_group(game):
    game.unit("party1", map=1429, x=0.4, y=0.6, name="Ally")
    game.tick(3)
    sent = [dict(m.items()) for m in game.lua.globals().sent.values()]
    assert sent and sent[0]["prefix"] == "FitzLCD" and sent[0]["channel"] == "PARTY"
    assert sent[0]["msg"] == f"1:1429:{p.POSITION_SCALE * 42 // 100 + 1}:{round(0.65 * 65535)}"


def test_nothing_is_shared_when_alone(game):
    game.tick(30)
    assert not list(game.lua.globals().sent.values())


def test_a_long_map_manifest_completes_while_the_info_record_keeps_changing(game):
    # Enough tiles for several chunks; the session clock changes the info record
    # every second, which used to restart the rotation before the map finished.
    game.lua.globals().world.tiles = game.lua.table_from(list(range(1_000_000, 1_000_080)))
    game.fire("PLAYER_ENTERING_WORLD")
    asm = p.RecordAssembler()
    got_map = False
    for second in range(12):
        game.lua.globals().clock = 100 + second
        for _ in range(10):
            game.tick()
            done = asm.feed(game.frame().chunk)
            got_map = got_map or bool(done and done[0] == p.RECORD_MAP)
    assert got_map
