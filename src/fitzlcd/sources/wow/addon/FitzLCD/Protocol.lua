-- The pixel strip's wire format. Pure Lua 5.1, no game API: the FitzLCD test
-- suite runs this file under a real Lua and decodes its output with the app's
-- decoder (src/fitzlcd/sources/wow/protocol.py), so keep the two in step.

local _, ns = ...
local P = {}
ns.Protocol = P

local floor, byte, sub = math.floor, string.byte, string.sub

P.VERSION = 2
P.COLUMNS = 64
P.ROWS = 2
P.CELLS = 128
P.BODY_BYTES = 178
P.SLOW_CAPACITY = 100
P.PARTY_SLOTS = 4
P.UNKNOWN = 255
P.UNKNOWN32 = 4294967295
P.POSITION_SCALE = 65535

-- Player flags.
P.P_HAS_POSITION = 1
P.P_COMBAT = 2
P.P_DEAD = 4
P.P_GHOST = 8
P.P_INSTANCE = 16
P.P_RESTING = 32
P.P_AFK = 64
P.P_HAS_FACING = 128
P.P_IN_GROUP = 256
P.P_IN_RAID = 512

-- Party member flags.
P.M_PRESENT = 1
P.M_ONLINE = 2
P.M_DEAD = 4
P.M_GHOST = 8
P.M_COMBAT = 16
P.M_HAS_POSITION = 32
P.M_SHARED = 64

-- Slow-lane record keys.
P.RECORD_INFO = 1
P.RECORD_MAP = 2

local function clamp(v, top)
  v = floor(tonumber(v) or 0)
  if v < 0 then return 0 end
  if v > top then return top end
  return v
end

local function u8(out, v)
  out[#out + 1] = clamp(v, 255)
end

local function u16(out, v)
  v = clamp(v, 65535)
  out[#out + 1] = floor(v / 256)
  out[#out + 1] = v % 256
end

local function u32(out, v)
  v = clamp(v, P.UNKNOWN32)
  out[#out + 1] = floor(v / 16777216) % 256
  out[#out + 1] = floor(v / 65536) % 256
  out[#out + 1] = floor(v / 256) % 256
  out[#out + 1] = v % 256
end

function P.Fletcher16(bytes, n)
  local s1, s2 = 0, 0
  for i = 1, n or #bytes do
    s1 = (s1 + bytes[i]) % 255
    s2 = (s2 + s1) % 255
  end
  return s2 * 256 + s1
end

function P.StringFletcher16(s)
  local s1, s2 = 0, 0
  for i = 1, #s do
    s1 = (s1 + byte(s, i)) % 255
    s2 = (s2 + s1) % 255
  end
  return s2 * 256 + s1
end

-- One record -> its chunks: { key, digest, index (0-based), count, data }.
function P.ChunkRecord(key, data)
  local digest = P.StringFletcher16(data)
  local chunks, size = {}, P.SLOW_CAPACITY
  local count = math.max(1, math.ceil(#data / size))
  for i = 0, count - 1 do
    chunks[#chunks + 1] = {
      key = key,
      digest = digest,
      index = i,
      count = count,
      data = sub(data, i * size + 1, (i + 1) * size),
    }
  end
  return chunks
end

-- An ordered list of { key, value } pairs -> "key=value" lines.
function P.EncodeRecord(pairs_)
  local lines = {}
  for _, kv in ipairs(pairs_) do
    local value = tostring(kv[2]):gsub("[\r\n]", " ")
    lines[#lines + 1] = kv[1] .. "=" .. value
  end
  return table.concat(lines, "\n")
end

local EMPTY = {}

-- The 180 data bytes of one frame.
function P.PackFrame(seq, player, party, chunk)
  local out = {}
  u8(out, P.VERSION)
  u8(out, (seq or 0) % 256)

  u16(out, player.flags)
  u16(out, player.map)
  u16(out, player.x)
  u16(out, player.y)
  u8(out, player.facing)
  u8(out, player.level)
  u8(out, player.class)
  u8(out, player.health or P.UNKNOWN)
  u8(out, player.power or P.UNKNOWN)
  u8(out, player.powerType or P.UNKNOWN)
  u32(out, player.hp or P.UNKNOWN32)
  u32(out, player.hpMax or P.UNKNOWN32)
  u32(out, player.pw or P.UNKNOWN32)
  u32(out, player.pwMax or P.UNKNOWN32)

  for i = 1, P.PARTY_SLOTS do
    local m = (party and party[i]) or EMPTY
    u8(out, m.flags)
    u8(out, m.class)
    u8(out, m.level)
    u8(out, m.health or P.UNKNOWN)
    u16(out, m.map)
    u16(out, m.x)
    u16(out, m.y)
  end

  if chunk then
    local data = sub(chunk.data, 1, P.SLOW_CAPACITY)
    u8(out, chunk.key)
    u16(out, chunk.digest)
    u8(out, chunk.index)
    u8(out, chunk.count)
    u8(out, #data)
    for j = 1, #data do
      out[#out + 1] = byte(data, j)
    end
  else
    for _ = 1, 6 do
      out[#out + 1] = 0
    end
  end

  while #out < P.BODY_BYTES do
    out[#out + 1] = 0
  end
  u16(out, P.Fletcher16(out, P.BODY_BYTES))
  return out
end

-- Frame bytes -> every cell's (r, g, b) levels, flat: 128 cells x 3 = 384.
function P.Levels(bytes)
  local lv = { 15, 0, 15, 0, 15, 0 } -- sentinels A and B
  for s = 0, 17 do
    lv[#lv + 1] = s % 16 -- calibration
  end
  for i = 1, #bytes do
    local b = bytes[i]
    lv[#lv + 1] = floor(b / 16)
    lv[#lv + 1] = b % 16
  end
  return lv
end

-- 0..1 map position -> u16.
function P.Position(v)
  return floor(v * P.POSITION_SCALE + 0.5)
end

-- Radians -> u8 (1/256 of a turn).
function P.Facing(radians)
  return floor(radians / (2 * math.pi) * 256 + 0.5) % 256
end
