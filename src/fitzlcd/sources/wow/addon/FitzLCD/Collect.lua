-- Gathers what the case display shows and hands it to the strip, ten times a
-- second. Everything read from the game goes through plain(): a value that is
-- secret (Forever hides health and more in combat) or missing is sent as
-- "unknown", never as 0, so the display shows a dash instead of a wrong number.

local _, ns = ...
local P, Strip, Share = ns.Protocol, ns.Strip, ns.Share

local floor, max, min = math.floor, math.max, math.min
local atan2, sqrt = math.atan2 or math.atan, math.sqrt
local INTERVAL = 0.1
local INFO_INTERVAL = 1.0
local SHARE_INTERVAL = 2.0

local function plain(v)
  if v == nil then
    return nil
  end
  if issecretvalue and issecretvalue(v) then
    return nil
  end
  return v
end

local function truthy(v)
  return plain(v) and true or false
end

local function percent(cur, top)
  cur, top = plain(cur), plain(top)
  if type(cur) ~= "number" or type(top) ~= "number" or top <= 0 then
    return P.UNKNOWN
  end
  return max(0, min(100, floor(cur / top * 100 + 0.5)))
end

-- An absolute value the strip carries in 32 bits, or unknown.
local function amount(v)
  v = plain(v)
  if type(v) ~= "number" or v < 0 then
    return P.UNKNOWN32
  end
  return floor(v + 0.5)
end

local function classID(unit)
  local _, _, id = UnitClass(unit)
  return plain(id) or 0
end

local function level(unit)
  local lv = plain(UnitLevel(unit))
  if type(lv) ~= "number" or lv < 0 then
    return 0
  end
  return lv
end

-- mapID, x, y; x and y are nil when the game will not say (instances).
local function position(unit)
  local mapID = plain(C_Map.GetBestMapForUnit(unit))
  if not mapID then
    return nil
  end
  local pos = C_Map.GetPlayerMapPosition(mapID, unit)
  if not pos then
    return mapID
  end
  local x, y = pos:GetXY()
  x, y = plain(x), plain(y)
  if type(x) ~= "number" or type(y) ~= "number" or (x == 0 and y == 0) then
    return mapID
  end
  return mapID, x, y
end

-- ------------------------------------------------------------- fast lane

local function gatherPlayer()
  local p = { flags = 0 }
  local mapID, x, y = position("player")
  p.map = mapID or 0
  if x then
    p.flags = p.flags + P.P_HAS_POSITION
    p.x, p.y = P.Position(x), P.Position(y)
  end
  local facing = plain(GetPlayerFacing())
  if type(facing) == "number" then
    p.flags = p.flags + P.P_HAS_FACING
    p.facing = P.Facing(facing)
  end
  if truthy(UnitAffectingCombat("player")) then p.flags = p.flags + P.P_COMBAT end
  if truthy(UnitIsDead("player")) then p.flags = p.flags + P.P_DEAD end
  if truthy(UnitIsGhost("player")) then p.flags = p.flags + P.P_GHOST end
  if truthy((IsInInstance())) then p.flags = p.flags + P.P_INSTANCE end
  if truthy(IsResting()) then p.flags = p.flags + P.P_RESTING end
  if truthy(UnitIsAFK("player")) then p.flags = p.flags + P.P_AFK end
  if truthy(IsInGroup()) then p.flags = p.flags + P.P_IN_GROUP end
  if truthy(IsInRaid()) then p.flags = p.flags + P.P_IN_RAID end
  p.level = level("player")
  p.class = classID("player")
  local hp, hpMax = UnitHealth("player"), UnitHealthMax("player")
  local pw, pwMax = UnitPower("player"), UnitPowerMax("player")
  p.health = percent(hp, hpMax)
  p.power = percent(pw, pwMax)
  p.hp, p.hpMax = amount(hp), amount(hpMax)
  p.pw, p.pwMax = amount(pw), amount(pwMax)
  local powerType = plain((UnitPowerType("player")))
  p.powerType = type(powerType) == "number" and powerType >= 0 and powerType or P.UNKNOWN
  return p
end

local function gatherMember(unit)
  if not truthy(UnitExists(unit)) then
    return { flags = 0 }
  end
  local m = { flags = P.M_PRESENT }
  if truthy(UnitIsConnected(unit)) then m.flags = m.flags + P.M_ONLINE end
  if truthy(UnitIsDead(unit)) then m.flags = m.flags + P.M_DEAD end
  if truthy(UnitIsGhost(unit)) then m.flags = m.flags + P.M_GHOST end
  if truthy(UnitAffectingCombat(unit)) then m.flags = m.flags + P.M_COMBAT end
  local mapID, x, y = position(unit)
  if not x and Share then
    local sMap, sx, sy = Share:Get(unit)
    if sMap then
      mapID, x, y = sMap, sx, sy
      m.flags = m.flags + P.M_SHARED
    end
  end
  m.map = mapID or 0
  if x then
    m.flags = m.flags + P.M_HAS_POSITION
    m.x, m.y = P.Position(x), P.Position(y)
  end
  m.class = classID(unit)
  m.level = level(unit)
  m.health = percent(UnitHealth(unit), UnitHealthMax(unit))
  return m
end

-- ------------------------------------------------------------- slow lane

local session = {
  started = GetTime(),
  deaths = 0,
  levelups = 0,
  xpGained = 0,
  lastXP = nil,
  lastXPMax = nil,
  moneyAtLogin = nil,
}

local function lowestDurability()
  local lowest
  for slot = 1, 18 do
    local cur, top = GetInventoryItemDurability(slot)
    cur, top = plain(cur), plain(top)
    if type(cur) == "number" and type(top) == "number" and top > 0 then
      local pct = floor(cur / top * 100 + 0.5)
      lowest = lowest and min(lowest, pct) or pct
    end
  end
  return lowest
end

local function freeBagSlots()
  if not (C_Container and C_Container.GetContainerNumFreeSlots) then
    return nil
  end
  local free = 0
  for bag = 0, 4 do
    local slots, bagType = C_Container.GetContainerNumFreeSlots(bag)
    if plain(bagType) == 0 and type(plain(slots)) == "number" then
      free = free + slots
    end
  end
  return free
end

local function add(pairs_, key, value)
  value = plain(value)
  if value ~= nil and value ~= "" then
    pairs_[#pairs_ + 1] = { key, value }
  end
end

-- Where the game's own player portrait sits, in physical pixels from the
-- window's top-left: the app copies that square off the screen.
local function portraitRect()
  local container = PlayerFrame and PlayerFrame.PlayerFrameContainer
  local tex = (container and container.PlayerPortrait) or PlayerPortrait
  if not (tex and tex.IsVisible and tex:IsVisible()) then
    return nil
  end
  local scale = plain(tex:GetEffectiveScale())
  local left, top = plain(tex:GetLeft()), plain(tex:GetTop())
  local width, height = plain(tex:GetWidth()), plain(tex:GetHeight())
  local _, screenH = GetPhysicalScreenSize()
  for _, v in ipairs({ scale, left, top, width, height, screenH }) do
    if type(v) ~= "number" then
      return nil
    end
  end
  local px = scale * screenH / 768 -- UI units at scale 1 -> physical pixels
  return ("%d,%d,%d,%d"):format(
    floor(left * px + 0.5),
    floor(screenH - top * px + 0.5),
    floor(width * px + 0.5),
    floor(height * px + 0.5)
  )
end

-- The class portrait icon as a file ID plus texture coordinates, so the app can
-- cut it out of the game's own files when the live portrait cannot be seen.
local function classIcon()
  local _, classFile = UnitClass("player")
  classFile = plain(classFile)
  if type(classFile) ~= "string" then
    return nil
  end
  if C_Texture and C_Texture.GetAtlasInfo then
    local info = C_Texture.GetAtlasInfo("classicon-" .. classFile:lower())
    local file = info and plain(info.file)
    if type(file) == "number" then
      return file, ("%.4f,%.4f,%.4f,%.4f"):format(
        info.leftTexCoord, info.rightTexCoord, info.topTexCoord, info.bottomTexCoord
      )
    end
  end
  local coords = CLASS_ICON_TCOORDS and CLASS_ICON_TCOORDS[classFile]
  local file = GetFileIDFromPath and GetFileIDFromPath("Interface\TargetingFrame\UI-Classes-Circles")
  if coords and type(file) == "number" then
    return file, ("%.4f,%.4f,%.4f,%.4f"):format(coords[1], coords[2], coords[3], coords[4])
  end
  return nil
end

-- A member on another map: that map's name, plus yards and bearing (radians,
-- counter-clockwise from north like GetPlayerFacing) when both maps share a
-- continent.
local function worldPos(mapID, x, y)
  if not (C_Map.GetWorldPosFromMapPos and CreateVector2D) then
    return nil
  end
  local continent, pos = C_Map.GetWorldPosFromMapPos(mapID, CreateVector2D(x, y))
  continent = plain(continent)
  if not (continent and pos) then
    return nil
  end
  local wx, wy = pos:GetXY()
  wx, wy = plain(wx), plain(wy)
  if type(wx) ~= "number" or type(wy) ~= "number" then
    return nil
  end
  return continent, wx, wy
end

local function addElsewhere(r, i, unit, here)
  local mapID, x, y = position(unit)
  if not mapID or not here or mapID == here.map then
    return
  end
  local info = C_Map.GetMapInfo(mapID)
  if info then
    add(r, "pz" .. i, info.name)
  end
  if x and here.x then
    local c1, ax, ay = worldPos(here.map, here.x, here.y)
    local c2, bx, by = worldPos(mapID, x, y)
    if c1 and c1 == c2 then
      -- World x grows north, world y grows west.
      local north, west = bx - ax, by - ay
      add(r, "pd" .. i, ("%d,%.3f"):format(floor(sqrt(north * north + west * west) + 0.5), atan2(west, north)))
    end
  end
end

local function infoRecord()
  local r = {}
  add(r, "name", UnitName("player"))
  add(r, "zone", GetRealZoneText())
  add(r, "sub", GetSubZoneText())
  add(r, "xp", UnitXP("player"))
  add(r, "xpmax", UnitXPMax("player"))
  add(r, "rest", GetXPExhaustion() or 0)
  if IsPlayerAtEffectiveMaxLevel and truthy(IsPlayerAtEffectiveMaxLevel()) then
    add(r, "maxlvl", 1)
  end
  local money = plain(GetMoney())
  add(r, "money", money)
  if money and session.moneyAtLogin then
    add(r, "moneygain", money - session.moneyAtLogin)
  end
  add(r, "xpgain", session.xpGained)
  add(r, "deaths", session.deaths)
  add(r, "lvlups", session.levelups)
  add(r, "sess", floor(GetTime() - session.started))
  add(r, "dur", lowestDurability())
  add(r, "bags", freeBagSlots())
  if truthy(UnitExists("target")) then
    add(r, "tname", UnitName("target"))
    add(r, "tlvl", UnitLevel("target"))
    add(r, "tcls", UnitClassification("target"))
    add(r, "trx", UnitReaction("player", "target"))
    if truthy(UnitIsPlayer("target")) then
      add(r, "tclass", classID("target"))
    end
  end
  add(r, "pr", portraitRect())
  local iconFile, iconCoords = classIcon()
  if iconFile then
    add(r, "ci", iconFile)
    add(r, "cc", iconCoords)
  end
  local hereMap, hereX, hereY = position("player")
  local here = hereMap and { map = hereMap, x = hereX, y = hereY }
  for i = 1, P.PARTY_SLOTS do
    local unit = "party" .. i
    if truthy(UnitExists(unit)) then
      add(r, "p" .. i, UnitName(unit))
      addElsewhere(r, i, unit, here)
    end
  end
  return P.EncodeRecord(r)
end

local function mapRecord(mapID)
  local r = { { "id", mapID } }
  local info = C_Map.GetMapInfo(mapID)
  if info then
    add(r, "name", info.name)
    add(r, "type", info.mapType)
    add(r, "parent", info.parentMapID)
  end
  local layers = C_Map.GetMapArtLayers(mapID)
  local layer = layers and layers[1]
  if layer then
    add(r, "w", layer.layerWidth)
    add(r, "h", layer.layerHeight)
    add(r, "tw", layer.tileWidth)
    add(r, "th", layer.tileHeight)
    local tiles = C_Map.GetMapArtLayerTextures(mapID, 1)
    if tiles and #tiles > 0 then
      add(r, "t", table.concat(tiles, ","))
    end
  end
  if C_Map.GetMapWorldSize then
    local yw, yh = C_Map.GetMapWorldSize(mapID)
    add(r, "yw", yw)
    add(r, "yh", yh)
  end
  return P.EncodeRecord(r)
end

-- Records, and the rotation of their chunks across frames.
local records = {}
local schedule, cursor = {}, 0
-- How many of the schedule's leading chunks are the changed record, and how far
-- into everything else the rotation had got. The info record changes every
-- second, so restarting the others from their first chunk each time would
-- starve a long map manifest; it resumes where it left off instead.
local lead, offset = 0, 0

local function setRecord(key, data)
  if records[key] == data then
    return
  end
  records[key] = data
  if cursor > lead then
    offset = offset + (cursor - lead)
  end
  local keys = {}
  for other in pairs(records) do
    if other ~= key then
      keys[#keys + 1] = other
    end
  end
  table.sort(keys)
  local others = {}
  for _, other in ipairs(keys) do
    for _, chunk in ipairs(P.ChunkRecord(other, records[other])) do
      others[#others + 1] = chunk
    end
  end
  -- The changed record goes out first, then everything else.
  schedule, cursor = P.ChunkRecord(key, data), 0
  lead = #schedule
  for i = 1, #others do
    schedule[#schedule + 1] = others[(offset + i - 1) % #others + 1]
  end
end

local function nextChunk()
  if #schedule == 0 then
    return nil
  end
  cursor = cursor % #schedule + 1
  return schedule[cursor]
end

local currentMap
local function refreshInfo()
  setRecord(P.RECORD_INFO, infoRecord())
  local mapID = plain(C_Map.GetBestMapForUnit("player"))
  if mapID and mapID ~= currentMap then
    currentMap = mapID
    setRecord(P.RECORD_MAP, mapRecord(mapID))
  end
end

-- ------------------------------------------------------------------ loop

local seq, elapsed, sinceInfo, sinceShare = 0, 0, INFO_INTERVAL, SHARE_INTERVAL
local enabled = true
local reported = false

local function tick()
  if sinceInfo >= INFO_INTERVAL then
    sinceInfo = 0
    refreshInfo()
  end
  if Share and sinceShare >= SHARE_INTERVAL then
    sinceShare = 0
    if truthy(IsInGroup()) then
      Share:Send(position("player"))
    end
  end
  local party = {}
  for i = 1, P.PARTY_SLOTS do
    party[i] = gatherMember("party" .. i)
  end
  seq = (seq + 1) % 256
  Strip:Draw(P.Levels(P.PackFrame(seq, gatherPlayer(), party, nextChunk())))
end

local driver = CreateFrame("Frame")
driver:SetScript("OnUpdate", function(_, dt)
  if not enabled then
    return
  end
  elapsed = elapsed + dt
  sinceInfo = sinceInfo + dt
  sinceShare = sinceShare + dt
  if elapsed < INTERVAL then
    return
  end
  elapsed = 0
  local ok, err = pcall(tick)
  if not ok and not reported then
    reported = true -- once: an error ten times a second would flood the chat
    print("|cffd97757FitzLCD|r: " .. tostring(err))
  end
end)

local function onXP()
  local xp, top = plain(UnitXP("player")), plain(UnitXPMax("player"))
  if type(xp) ~= "number" or type(top) ~= "number" then
    return
  end
  if session.lastXP then
    local gained = xp - session.lastXP
    if gained < 0 and session.lastXPMax then -- dinged: finish the old bar first
      gained = session.lastXPMax - session.lastXP + xp
    end
    session.xpGained = session.xpGained + max(0, gained)
  end
  session.lastXP, session.lastXPMax = xp, top
end

local events = CreateFrame("Frame")
for _, name in ipairs({
  "PLAYER_LOGIN",
  "PLAYER_ENTERING_WORLD",
  "ZONE_CHANGED",
  "ZONE_CHANGED_INDOORS",
  "ZONE_CHANGED_NEW_AREA",
  "GROUP_ROSTER_UPDATE",
  "PLAYER_TARGET_CHANGED",
  "PLAYER_XP_UPDATE",
  "PLAYER_LEVEL_UP",
  "PLAYER_DEAD",
}) do
  events:RegisterEvent(name)
end
events:SetScript("OnEvent", function(_, event)
  if event == "PLAYER_LOGIN" then
    session.moneyAtLogin = plain(GetMoney())
    onXP()
  elseif event == "PLAYER_XP_UPDATE" then
    onXP()
  elseif event == "PLAYER_LEVEL_UP" then
    session.levelups = session.levelups + 1
  elseif event == "PLAYER_DEAD" then
    session.deaths = session.deaths + 1
  elseif event == "PLAYER_ENTERING_WORLD" or event == "ZONE_CHANGED_NEW_AREA" then
    currentMap = nil -- re-send the map manifest
  end
  sinceInfo = INFO_INTERVAL -- refresh the slow lane on the next frame
end)

SLASH_FITZLCD1 = "/fitzlcd"
SlashCmdList.FITZLCD = function(msg)
  msg = (msg or ""):lower()
  if msg == "off" or msg == "hide" then
    enabled = false
    Strip:SetShown(false)
  elseif msg == "on" or msg == "show" then
    enabled = true
    Strip:SetShown(true)
  else
    local mapID, x, y = position("player")
    print(("|cffd97757FitzLCD|r: strip %s, map %s, position %s"):format(
      enabled and "on" or "off",
      tostring(mapID or "unknown"),
      x and ("%.1f, %.1f"):format(x * 100, y * 100) or "hidden by the game"
    ))
    print("  /fitzlcd off  hides the strip,  /fitzlcd on  shows it again.")
  end
end
