-- Party members who also run FitzLCD tell each other where they are, as a
-- fallback for when the game will not say (secret party identities, and
-- anywhere else C_Map.GetPlayerMapPosition answers for you but not for them).
-- A member's addon only shares what its own game lets it read, so this cannot
-- reveal anything the game hides from that player.
--
-- Message: "1:<mapID>:<x>:<y>", x and y as 0..65535 fractions of the map.

local _, ns = ...
local P = ns.Protocol
local Share = {}
ns.Share = Share

local PREFIX = "FitzLCD"
local FORMAT = 1
local FRESH_SECONDS = 10
local floor, tonumber = math.floor, tonumber

local heard = {} -- sender name -> { map, x, y, at }

local function plain(v)
  if v == nil or (issecretvalue and issecretvalue(v)) then
    return nil
  end
  return v
end

local function canSend()
  if IsTrialAccount and plain(IsTrialAccount()) then
    return false
  end
  if IsVeteranTrialAccount and plain(IsVeteranTrialAccount()) then
    return false
  end
  return C_ChatInfo and C_ChatInfo.SendAddonMessage ~= nil
end

local function channel()
  if LE_PARTY_CATEGORY_INSTANCE and IsInGroup(LE_PARTY_CATEGORY_INSTANCE) then
    return "INSTANCE_CHAT"
  end
  if IsInRaid and IsInRaid() then
    return "RAID"
  end
  return "PARTY"
end

function Share:Send(mapID, x, y)
  if not (mapID and x and y) or not canSend() then
    return
  end
  local msg = ("%d:%d:%d:%d"):format(FORMAT, mapID, P.Position(x), P.Position(y))
  pcall(C_ChatInfo.SendAddonMessage, PREFIX, msg, channel())
end

-- "Name-Realm" and "Name" both match a unit whose UnitName is "Name".
local function short(name)
  if type(name) ~= "string" then
    return nil
  end
  return (name:match("^([^%-]+)") or name)
end

function Share:Hear(prefix, msg, _channel, sender)
  prefix, msg, sender = plain(prefix), plain(msg), plain(sender)
  if prefix ~= PREFIX or type(msg) ~= "string" or #msg > 64 then
    return
  end
  local fmt, map, x, y = msg:match("^(%d+):(%d+):(%d+):(%d+)$")
  if tonumber(fmt) ~= FORMAT then
    return
  end
  map, x, y = tonumber(map), tonumber(x), tonumber(y)
  local name = short(sender)
  if not name or map < 1 or map > 65535 or x > P.POSITION_SCALE or y > P.POSITION_SCALE then
    return
  end
  heard[name] = { map = map, x = x / P.POSITION_SCALE, y = y / P.POSITION_SCALE, at = GetTime() }
end

-- mapID, x, y (0..1) the unit last shared, or nil when stale or never heard.
function Share:Get(unit)
  local name = short(plain(UnitName(unit)))
  local entry = name and heard[name]
  if not entry or GetTime() - entry.at > FRESH_SECONDS then
    return nil
  end
  return entry.map, entry.x, entry.y
end

local events = CreateFrame("Frame")
events:RegisterEvent("PLAYER_LOGIN")
events:RegisterEvent("CHAT_MSG_ADDON")
events:SetScript("OnEvent", function(_, event, ...)
  if event == "PLAYER_LOGIN" then
    if C_ChatInfo and C_ChatInfo.RegisterAddonMessagePrefix then
      pcall(C_ChatInfo.RegisterAddonMessagePrefix, PREFIX)
    end
  elseif event == "CHAT_MSG_ADDON" then
    pcall(Share.Hear, Share, ...)
  end
end)
