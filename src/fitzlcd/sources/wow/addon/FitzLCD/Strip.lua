-- Draws the strip: 64 x 2 flat-coloured cells, each CELL_PX physical pixels
-- square, at the very top-left of the screen. The FitzLCD app reads the cell
-- centres back off the screen, so the cells must land on whole pixels.

local _, ns = ...
local P = ns.Protocol
local Strip = {}
ns.Strip = Strip

local CELL_PX = 3

-- Unparented so that hiding the UI (Alt+Z) does not hide the strip; anchored
-- to UIParent, which always covers the whole game window.
local frame = CreateFrame("Frame", "FitzLCDStrip", nil)
frame:SetFrameStrata("TOOLTIP")
frame:SetFrameLevel(9000)
frame:SetSize(P.COLUMNS * CELL_PX, P.ROWS * CELL_PX)
frame:SetPoint("TOPLEFT", UIParent, "TOPLEFT", 0, 0)
frame:EnableMouse(false)
Strip.frame = frame

local cells, shown = {}, {}
for i = 0, P.CELLS - 1 do
  local tex = frame:CreateTexture(nil, "OVERLAY")
  local col, row = i % P.COLUMNS, math.floor(i / P.COLUMNS)
  tex:SetSize(CELL_PX, CELL_PX)
  tex:SetPoint("TOPLEFT", frame, "TOPLEFT", col * CELL_PX, -row * CELL_PX)
  if tex.SetSnapToPixelGrid then
    tex:SetSnapToPixelGrid(false)
    tex:SetTexelSnappingBias(0)
  end
  tex:SetColorTexture(0, 0, 0, 1)
  cells[i + 1] = tex
end

-- One UI unit = one physical pixel, whatever the UI scale is.
function Strip:FitToPixels()
  local _, height = GetPhysicalScreenSize()
  if height and height > 0 then
    frame:SetScale(768 / height)
  end
end

-- levels: the flat list from Protocol.Levels.
function Strip:Draw(levels)
  for i = 1, P.CELLS do
    local base = (i - 1) * 3
    local r, g, b = levels[base + 1], levels[base + 2], levels[base + 3]
    local key = r * 256 + g * 16 + b
    if shown[i] ~= key then
      shown[i] = key
      cells[i]:SetColorTexture(r * 17 / 255, g * 17 / 255, b * 17 / 255, 1)
    end
  end
end

function Strip:SetShown(on)
  frame:SetShown(on)
end

function Strip:IsShown()
  return frame:IsShown()
end

local watcher = CreateFrame("Frame")
watcher:RegisterEvent("PLAYER_LOGIN")
watcher:RegisterEvent("DISPLAY_SIZE_CHANGED")
watcher:RegisterEvent("UI_SCALE_CHANGED")
watcher:SetScript("OnEvent", function()
  Strip:FitToPixels()
end)
Strip:FitToPixels()
