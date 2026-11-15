"""The built-in scene library.

Every scene here is authored with **responsive geometry** - percentages, anchors
and negative offsets rather than fixed pixels - so the same JSON lays out
correctly whether the panel is mounted flat (1920x462) or on its side
(462x1920). Font sizes stay in pixels on purpose: the panel is one physical
device being rotated, so a 92 px glyph is the same physical size either way.

Two conventions make that work:

* anchor a layer to the edge it should hug, then position it in percentages
* give text an explicit ``align`` when it is anchored right or centre

Scenes are seeded into ``%APPDATA%/FitzLCD/scenes`` on first run and are the
user's from then on; editing these definitions never overwrites their copies.
"""

from __future__ import annotations

from typing import Any

# Shared palette, so the scenes look like a set rather than five unrelated ideas.
INK = "#05060f"
PANEL = "#0d1030"
DIM = "#8892b0"
BRIGHT = "#e8edfb"
AMBER = "#ffcc00"
CYAN = "#00e5ff"
RED = "#ff5f6d"
GREEN = "#39d353"
TERMINAL_GREEN = "#33ff66"

# Claude Code's own palette, deliberately outside the shared set above. Every
# other scene borrows that set so the library looks like one family; this scene's
# entire job is to look like Claude Code instead, so it breaks ranks on purpose.
# Orange is the brand accent, not a severity signal -- warnings escalate through
# CLAUDE_WARN/CLAUDE_CRIT rather than by shifting the identity colour.
CLAUDE_ORANGE = "#d97757"
CLAUDE_CREAM = "#f0eee6"
CLAUDE_DIM = "#8c8781"
CLAUDE_INK = "#1a1917"
CLAUDE_PANEL = "#2a2724"
# Alpha lives in the hex: Layer.opacity is serialised and shown in the properties
# pane, but no layer's draw() reads it.
CLAUDE_TRACK = "#f0eee61a"
CLAUDE_WARN = "#e8b04b"
CLAUDE_CRIT = "#e05252"


def _rig_stats() -> dict[str, Any]:
    """Two big readouts with gauges, plus the time.

    Side by side across a wide panel; stacked down a tall one. Cramming the
    landscape arrangement into 462 px would overlap the clock and the CPU block,
    so the two arrangements are tagged by orientation and only one is drawn.
    """
    wide = [
        {
            "type": "text",
            "name": "cpu label",
            "text": "CPU",
            "orientation": "landscape",
            "font": "sans",
            "size": 34,
            "pos": ["3%", "12%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "cpu value",
            "text": "{cpu.load:.0f}%",
            "orientation": "landscape",
            "font": "sans",
            "size": 88,
            "pos": ["3%", "20%"],
            "color": AMBER,
        },
        {
            "type": "gauge",
            "name": "cpu gauge",
            "metric": "cpu.load",
            "orientation": "landscape",
            "rect": ["3%", "50%", "30%", 22],
            "color": AMBER,
        },
        {
            "type": "text",
            "name": "gpu label",
            "text": "GPU",
            "orientation": "landscape",
            "font": "sans",
            "size": 34,
            "pos": ["3%", "62%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "gpu value",
            "text": "{gpu.load:.0f}%  {gpu.temp:.0f}°C",
            "orientation": "landscape",
            "font": "sans",
            "size": 88,
            "pos": ["3%", "70%"],
            "color": CYAN,
        },
        {
            "type": "sparkline",
            "name": "gpu history",
            "metric": "gpu.load",
            "orientation": "landscape",
            "rect": ["36%", "45%", "28%", "45%"],
            "color": CYAN,
            "fill_color": "#00e5ff33",
            "samples": 90,
        },
        {
            "type": "clock",
            "name": "clock",
            "orientation": "landscape",
            "font": "sans",
            "size": 92,
            "pos": ["-3%", "12%"],
            "anchor": "top-right",
            "align": "right",
            "color": BRIGHT,
        },
        {
            "type": "text",
            "name": "memory",
            "text": "RAM {mem.used_gb:.1f} / {mem.total_gb:.0f} GB",
            "orientation": "landscape",
            "font": "sans",
            "size": 38,
            "pos": ["-3%", "35%"],
            "anchor": "top-right",
            "align": "right",
            "color": DIM,
        },
    ]

    tall = [
        {
            "type": "text",
            "name": "cpu label (tall)",
            "text": "CPU",
            "orientation": "portrait",
            "font": "sans",
            "size": 34,
            "pos": ["6%", "5%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "cpu value (tall)",
            "text": "{cpu.load:.0f}%",
            "orientation": "portrait",
            "font": "sans",
            "size": 96,
            "pos": ["6%", "8%"],
            "color": AMBER,
        },
        {
            "type": "gauge",
            "name": "cpu gauge (tall)",
            "metric": "cpu.load",
            "orientation": "portrait",
            "rect": ["6%", "15%", "88%", 26],
            "color": AMBER,
        },
        {
            "type": "text",
            "name": "gpu label (tall)",
            "text": "GPU",
            "orientation": "portrait",
            "font": "sans",
            "size": 34,
            "pos": ["6%", "24%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "gpu value (tall)",
            "text": "{gpu.load:.0f}%",
            "orientation": "portrait",
            "font": "sans",
            "size": 96,
            "pos": ["6%", "27%"],
            "color": CYAN,
        },
        {
            "type": "text",
            "name": "gpu temp (tall)",
            "text": "{gpu.temp:.0f}°C",
            "orientation": "portrait",
            "font": "sans",
            "size": 48,
            "pos": ["-6%", "29%"],
            "anchor": "top-right",
            "align": "right",
            "color": CYAN,
        },
        {
            "type": "sparkline",
            "name": "gpu history (tall)",
            "metric": "gpu.load",
            "orientation": "portrait",
            "rect": ["6%", "34%", "88%", "10%"],
            "color": CYAN,
            "fill_color": "#00e5ff33",
            "samples": 90,
        },
        {
            "type": "text",
            "name": "memory (tall)",
            "text": "RAM",
            "orientation": "portrait",
            "font": "sans",
            "size": 34,
            "pos": ["6%", "50%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "memory value (tall)",
            "text": "{mem.used_gb:.1f} / {mem.total_gb:.0f} GB",
            "orientation": "portrait",
            "font": "sans",
            "size": 56,
            "pos": ["6%", "53%"],
            "color": GREEN,
        },
        {
            "type": "gauge",
            "name": "memory gauge (tall)",
            "metric": "mem.used_pct",
            "orientation": "portrait",
            "rect": ["6%", "60%", "88%", 26],
            "color": GREEN,
        },
        {
            "type": "clock",
            "name": "clock (tall)",
            "orientation": "portrait",
            "font": "sans",
            "size": 96,
            "pos": ["center", "-12%"],
            "anchor": "bottom-center",
            "align": "center",
            "color": BRIGHT,
        },
        {
            "type": "text",
            "name": "date (tall)",
            "text": "{time.date}",
            "orientation": "portrait",
            "font": "sans",
            "size": 40,
            "pos": ["center", "-7%"],
            "anchor": "bottom-center",
            "align": "center",
            "color": DIM,
        },
    ]

    return {
        "version": 1,
        "name": "Rig Stats",
        "fps": 10,
        "background": INK,
        "layers": [{"type": "solid", "color": PANEL, "color2": INK}, *wide, *tall],
    }


def _terminal() -> dict[str, Any]:
    """A console readout: monospace, prompt lines, live values."""
    return {
        "version": 1,
        "name": "Terminal",
        "fps": 4,
        "background": "#02040a",
        "layers": [
            {"type": "solid", "color": "#02120a", "color2": "#02040a"},
            {
                "type": "solid",
                "name": "title bar",
                "color": "#0b2a17",
                "rect": [0, 0, "100%", 34],
            },
            {
                "type": "text",
                "name": "title",
                "text": "fitznet@rig: ~",
                "font": "mono",
                "size": 22,
                "pos": [14, 6],
                "color": TERMINAL_GREEN,
            },
            {
                "type": "text",
                "name": "session",
                "text": "up {time.uptime}  {time.now}",
                "font": "mono",
                "size": 22,
                "pos": ["-14", 6],
                "anchor": "top-right",
                "align": "right",
                "color": "#1f7a3f",
            },
            {
                # Wide layout: room for a label, a value and a note per line.
                "type": "text",
                "name": "readout (wide)",
                "orientation": "landscape",
                "text": (
                    "$ sysinfo --watch\n"
                    "  cpu   {cpu.load:5.1f}%   {cpu.cores} threads @ {cpu.freq:.0f} MHz\n"
                    "  mem   {mem.used_pct:5.1f}%   {mem.used_gb:.1f} / {mem.total_gb:.0f} GiB\n"
                    "  gpu   {gpu.load:5.1f}%   {gpu.temp:.0f}°C   {gpu.power:.0f} W\n"
                    "  vram  {gpu.vram_pct:5.1f}%   {gpu.vram_used_gb:.1f} GiB\n"
                    "  net   up {net.up:.2f} MB/s   down {net.down:.2f} MB/s\n"
                    "  disk  {disk.used_pct:5.1f}%\n"
                    "$ _"
                ),
                "font": "mono",
                "size": 34,
                "pos": [14, 52],
                "color": TERMINAL_GREEN,
            },
            {
                # Tall layout: same data, narrow columns, more lines. Shrinking
                # the wide block to 462 px would leave it unreadably small.
                "type": "text",
                "name": "readout (tall)",
                "orientation": "portrait",
                "text": (
                    "$ sysinfo\n"
                    "\n"
                    " cpu  {cpu.load:5.1f}%\n"
                    " thr  {cpu.cores}\n"
                    " clk  {cpu.freq:.0f}MHz\n"
                    "\n"
                    " mem  {mem.used_pct:5.1f}%\n"
                    " used {mem.used_gb:.1f}G\n"
                    " tot  {mem.total_gb:.0f}G\n"
                    "\n"
                    " gpu  {gpu.load:5.1f}%\n"
                    " temp {gpu.temp:.0f}C\n"
                    " pwr  {gpu.power:.0f}W\n"
                    " vram {gpu.vram_pct:5.1f}%\n"
                    "\n"
                    " up   {net.up:.2f}M/s\n"
                    " down {net.down:.2f}M/s\n"
                    " disk {disk.used_pct:5.1f}%\n"
                    "\n"
                    " time {time.now}\n"
                    " date {time.date}\n"
                    " up   {time.uptime}\n"
                    "\n"
                    "$ _"
                ),
                "font": "mono",
                "size": 58,
                "pos": [14, 52],
                "color": TERMINAL_GREEN,
            },
        ],
    }


def _clock() -> dict[str, Any]:
    """Big centred clock. Cheap to render and legible across the room."""
    return {
        "version": 1,
        "name": "Clock",
        "fps": 2,
        "background": "#000000",
        "layers": [
            {
                "type": "clock",
                "font": "sans",
                "size": 200,
                "pos": ["center", "40%"],
                "anchor": "middle-center",
                "align": "center",
                "color": BRIGHT,
            },
            {
                "type": "text",
                "text": "{time.date}",
                "font": "sans",
                "size": 48,
                "pos": ["center", "70%"],
                "anchor": "middle-center",
                "align": "center",
                "color": DIM,
            },
        ],
    }


def _vitals() -> dict[str, Any]:
    """Four labelled bars. The densest readable summary of the whole machine."""
    rows = (
        ("CPU", "cpu.load", AMBER),
        ("GPU", "gpu.load", CYAN),
        ("RAM", "mem.used_pct", GREEN),
        ("VRAM", "gpu.vram_pct", RED),
    )
    layers: list[dict[str, Any]] = [{"type": "solid", "color": PANEL, "color2": INK}]
    for index, (label, metric, color) in enumerate(rows):
        # Wide: label, bar and value share one line. Tall: the bar gets a line of
        # its own under the label, because 462 px cannot hold all three.
        top = 8 + index * 23
        layers += [
            {
                "type": "text",
                "name": f"{label.lower()} label",
                "text": label,
                "orientation": "landscape",
                "font": "sans",
                "size": 34,
                "pos": ["3%", f"{top}%"],
                "color": DIM,
            },
            {
                "type": "gauge",
                "name": f"{label.lower()} bar",
                "metric": metric,
                "orientation": "landscape",
                "rect": ["14%", f"{top}%", "68%", 34],
                "color": color,
                "radius": 6,
            },
            {
                "type": "text",
                "name": f"{label.lower()} value",
                "text": f"{{{metric}:.0f}}%",
                "orientation": "landscape",
                "font": "mono",
                "size": 34,
                "pos": ["-3%", f"{top}%"],
                "anchor": "top-right",
                "align": "right",
                "color": BRIGHT,
            },
        ]

        tall_top = 6 + index * 24
        layers += [
            {
                "type": "text",
                "name": f"{label.lower()} label (tall)",
                "text": label,
                "orientation": "portrait",
                "font": "sans",
                "size": 42,
                "pos": ["6%", f"{tall_top}%"],
                "color": DIM,
            },
            {
                "type": "text",
                "name": f"{label.lower()} value (tall)",
                "text": f"{{{metric}:.0f}}%",
                "orientation": "portrait",
                "font": "mono",
                "size": 64,
                "pos": ["-6%", f"{tall_top - 1}%"],
                "anchor": "top-right",
                "align": "right",
                "color": BRIGHT,
            },
            {
                "type": "gauge",
                "name": f"{label.lower()} bar (tall)",
                "metric": metric,
                "orientation": "portrait",
                "rect": ["6%", f"{tall_top + 6}%", "88%", 30],
                "color": color,
                "radius": 6,
            },
        ]
    return {
        "version": 1,
        "name": "Vitals",
        "fps": 5,
        "background": INK,
        "layers": layers,
    }


def _pulse() -> dict[str, Any]:
    """Full-bleed history graphs; reads well from a distance."""
    return {
        "version": 1,
        "name": "Pulse",
        "fps": 10,
        "background": "#04050d",
        "layers": [
            {"type": "solid", "color": "#0a1030", "color2": "#04050d"},
            {
                "type": "sparkline",
                "name": "cpu history",
                "metric": "cpu.load",
                "rect": [0, 0, "100%", "50%"],
                "color": AMBER,
                "fill_color": "#ffcc0022",
                "samples": 120,
                "width": 3,
            },
            {
                "type": "sparkline",
                "name": "gpu history",
                "metric": "gpu.load",
                "rect": [0, "50%", "100%", "50%"],
                "color": CYAN,
                "fill_color": "#00e5ff22",
                "samples": 120,
                "width": 3,
            },
            {
                "type": "text",
                "text": "CPU {cpu.load:.0f}%",
                "font": "sans",
                "size": 40,
                "pos": ["2%", "3%"],
                "color": AMBER,
                "shadow": True,
            },
            {
                "type": "text",
                "text": "GPU {gpu.load:.0f}%  {gpu.temp:.0f}°C",
                "font": "sans",
                "size": 40,
                "pos": ["2%", "53%"],
                "color": CYAN,
                "shadow": True,
            },
        ],
    }


def _network() -> dict[str, Any]:
    """Throughput, for when you want to see the pipe rather than the machine."""
    return {
        "version": 1,
        "name": "Network",
        "fps": 5,
        "background": INK,
        "layers": [
            {"type": "solid", "color": "#101a30", "color2": INK},
            {
                "type": "text",
                "text": "DOWN",
                "font": "sans",
                "size": 30,
                "pos": ["3%", "10%"],
                "color": DIM,
            },
            {
                "type": "text",
                "text": "{net.down:.2f} MB/s",
                "font": "mono",
                "size": 76,
                "pos": ["3%", "22%"],
                "color": GREEN,
            },
            {
                "type": "sparkline",
                "metric": "net.down",
                "rect": ["3%", "48%", "94%", "18%"],
                "minimum": 0.0,
                "maximum": 12.0,
                "color": GREEN,
                "fill_color": "#39d35322",
                "samples": 120,
            },
            {
                "type": "text",
                "text": "UP",
                "font": "sans",
                "size": 30,
                "pos": ["3%", "70%"],
                "color": DIM,
            },
            {
                "type": "text",
                "text": "{net.up:.2f} MB/s",
                "font": "mono",
                "size": 52,
                "pos": ["-3%", "68%"],
                "anchor": "top-right",
                "align": "right",
                "color": CYAN,
            },
            {
                "type": "sparkline",
                "metric": "net.up",
                "rect": ["3%", "-3%", "94%", "12%"],
                "anchor": "bottom-left",
                "minimum": 0.0,
                "maximum": 6.0,
                "color": CYAN,
                "fill_color": "#00e5ff22",
                "samples": 120,
            },
        ],
    }


def _wallpaper() -> dict[str, Any]:
    """Empty media layer: point it at an image, GIF or video and go."""
    return {
        "version": 1,
        "name": "Wallpaper",
        "fps": 30,
        "background": "#000000",
        "layers": [
            {"type": "media", "source": "", "fit": "cover", "pan": 0.5},
            {
                "type": "solid",
                "name": "scrim",
                "color": "#00000000",
                "color2": "#000000cc",
                "rect": [0, "-30%", "100%", "30%"],
                "anchor": "bottom-left",
            },
            {
                "type": "clock",
                "font": "sans",
                "size": 64,
                "pos": ["-3%", "-6%"],
                "anchor": "bottom-right",
                "align": "right",
                "color": BRIGHT,
            },
        ],
    }


def _cs2_hud() -> dict[str, Any]:
    """Live Counter-Strike 2 match state via Game State Integration.

    Four zones across a wide panel (health/armor, hit history, loadout,
    scoreboard); the same content stacked down a tall one. A hit-flash border
    and a "waiting for match" overlay are shared between both orientations
    since neither depends on which way the panel is mounted.
    """
    wide = [
        {
            "type": "text",
            "name": "health label",
            "text": "HEALTH",
            "orientation": "landscape",
            "font": "sans",
            "size": 26,
            "pos": ["2%", "6%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "health value",
            "text": "{cs2.player.state.health:.0f}",
            "orientation": "landscape",
            "font": "sans",
            "size": 64,
            "pos": ["2%", "16%"],
            "color": RED,
        },
        {
            "type": "gauge",
            "name": "health gauge",
            "metric": "cs2.player.state.health",
            "orientation": "landscape",
            "rect": ["2%", "46%", "18%", 20],
            "color": RED,
        },
        {
            "type": "text",
            "name": "armor label",
            "text": "ARMOR",
            "orientation": "landscape",
            "font": "sans",
            "size": 22,
            "pos": ["2%", "58%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "armor value",
            "text": "{cs2.player.state.armor:.0f}",
            "orientation": "landscape",
            "font": "sans",
            "size": 44,
            "pos": ["2%", "66%"],
            "color": AMBER,
        },
        {
            "type": "gauge",
            "name": "armor gauge",
            "metric": "cs2.player.state.armor",
            "orientation": "landscape",
            "rect": ["2%", "90%", "18%", 14],
            "color": AMBER,
        },
        {
            "type": "text",
            "name": "status labels",
            "text": "{cs2.player.helmet_label}\n"
            "{cs2.player.flashed_label}\n"
            "{cs2.player.burning_label}",
            "orientation": "landscape",
            "font": "sans",
            "size": 18,
            "pos": ["-2%", "6%"],
            "anchor": "top-right",
            "align": "right",
            "color": CYAN,
        },
        {
            "type": "text",
            "name": "timeline caption",
            "text": "LAST 10s",
            "orientation": "landscape",
            "font": "sans",
            "size": 20,
            "pos": ["23%", "4%"],
            "color": DIM,
        },
        {
            "type": "cs2_hit_timeline",
            "name": "hit timeline",
            "orientation": "landscape",
            "rect": ["23%", "12%", "22%", "78%"],
            "window_seconds": 10.0,
        },
        {
            "type": "text",
            "name": "weapon",
            "text": "{cs2.player.active_weapon.name}",
            "orientation": "landscape",
            "font": "sans",
            "size": 38,
            "pos": ["49%", "6%"],
            "color": CYAN,
        },
        {
            "type": "text",
            "name": "ammo",
            "text": "{cs2.player.active_weapon.ammo_clip:.0f} / "
            "{cs2.player.active_weapon.ammo_reserve:.0f}",
            "orientation": "landscape",
            "font": "sans",
            "size": 56,
            "pos": ["49%", "28%"],
            "color": BRIGHT,
        },
        {
            "type": "text",
            "name": "money",
            "text": "$ {cs2.player.state.money:.0f}",
            "orientation": "landscape",
            "font": "sans",
            "size": 34,
            "pos": ["49%", "58%"],
            "color": GREEN,
        },
        {
            "type": "text",
            "name": "equip value",
            "text": "EQUIP $ {cs2.player.state.equip_value:.0f}",
            "orientation": "landscape",
            "font": "sans",
            "size": 22,
            "pos": ["49%", "78%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "map",
            "text": "{cs2.map.name}",
            "orientation": "landscape",
            "font": "sans",
            "size": 22,
            "pos": ["-2%", "4%"],
            "anchor": "top-right",
            "align": "right",
            "color": DIM,
        },
        {
            "type": "text",
            "name": "score",
            "text": "{cs2.map.team_ct.score:.0f}  :  {cs2.map.team_t.score:.0f}",
            "orientation": "landscape",
            "font": "sans",
            "size": 46,
            "pos": ["-2%", "16%"],
            "anchor": "top-right",
            "align": "right",
            "color": BRIGHT,
        },
        {
            "type": "text",
            "name": "kda label",
            "text": "K / D / A",
            "orientation": "landscape",
            "font": "sans",
            "size": 18,
            "pos": ["-2%", "42%"],
            "anchor": "top-right",
            "align": "right",
            "color": DIM,
        },
        {
            "type": "text",
            "name": "kda",
            "text": "{cs2.player.match_stats.kills:.0f} / "
            "{cs2.player.match_stats.deaths:.0f} / "
            "{cs2.player.match_stats.assists:.0f}   "
            "MVP {cs2.player.match_stats.mvps:.0f}",
            "orientation": "landscape",
            "font": "sans",
            "size": 30,
            "pos": ["-2%", "50%"],
            "anchor": "top-right",
            "align": "right",
            "color": CYAN,
        },
        {
            "type": "text",
            "name": "round phase",
            "text": "{cs2.round.phase}   {cs2.phase.seconds_left:.0f}s",
            "orientation": "landscape",
            "font": "sans",
            "size": 26,
            "pos": ["-2%", "70%"],
            "anchor": "top-right",
            "align": "right",
            "color": AMBER,
        },
        {
            "type": "text",
            "name": "bomb",
            "text": "{cs2.bomb.state}",
            "orientation": "landscape",
            "font": "sans",
            "size": 26,
            "pos": ["-2%", "82%"],
            "anchor": "top-right",
            "align": "right",
            "color": RED,
        },
    ]

    tall = [
        {
            "type": "text",
            "name": "map (tall)",
            "text": "{cs2.map.name}",
            "orientation": "portrait",
            "font": "sans",
            "size": 26,
            "pos": ["center", "1%"],
            "anchor": "top-center",
            "align": "center",
            "color": DIM,
        },
        {
            "type": "text",
            "name": "score (tall)",
            "text": "{cs2.map.team_ct.score:.0f}  :  {cs2.map.team_t.score:.0f}",
            "orientation": "portrait",
            "font": "sans",
            "size": 40,
            "pos": ["center", "4%"],
            "anchor": "top-center",
            "align": "center",
            "color": BRIGHT,
        },
        {
            "type": "text",
            "name": "health label (tall)",
            "text": "HEALTH",
            "orientation": "portrait",
            "font": "sans",
            "size": 28,
            "pos": ["6%", "11%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "health value (tall)",
            "text": "{cs2.player.state.health:.0f}",
            "orientation": "portrait",
            "font": "sans",
            "size": 88,
            "pos": ["6%", "14%"],
            "color": RED,
        },
        {
            "type": "gauge",
            "name": "health gauge (tall)",
            "metric": "cs2.player.state.health",
            "orientation": "portrait",
            "rect": ["6%", "21%", "88%", 26],
            "color": RED,
        },
        {
            "type": "text",
            "name": "armor label (tall)",
            "text": "ARMOR",
            "orientation": "portrait",
            "font": "sans",
            "size": 28,
            "pos": ["6%", "25%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "armor value (tall)",
            "text": "{cs2.player.state.armor:.0f}",
            "orientation": "portrait",
            "font": "sans",
            "size": 64,
            "pos": ["6%", "28%"],
            "color": AMBER,
        },
        {
            "type": "gauge",
            "name": "armor gauge (tall)",
            "metric": "cs2.player.state.armor",
            "orientation": "portrait",
            "rect": ["6%", "34%", "88%", 22],
            "color": AMBER,
        },
        {
            "type": "text",
            "name": "status labels (tall)",
            "text": "{cs2.player.helmet_label}  "
            "{cs2.player.flashed_label}  "
            "{cs2.player.burning_label}",
            "orientation": "portrait",
            "font": "sans",
            "size": 20,
            "pos": ["6%", "37%"],
            "color": CYAN,
        },
        {
            "type": "text",
            "name": "timeline caption (tall)",
            "text": "LAST 10s",
            "orientation": "portrait",
            "font": "sans",
            "size": 22,
            "pos": ["6%", "41%"],
            "color": DIM,
        },
        {
            "type": "cs2_hit_timeline",
            "name": "hit timeline (tall)",
            "orientation": "portrait",
            "rect": ["6%", "44%", "88%", "10%"],
            "window_seconds": 10.0,
        },
        {
            "type": "text",
            "name": "weapon (tall)",
            "text": "{cs2.player.active_weapon.name}",
            "orientation": "portrait",
            "font": "sans",
            "size": 34,
            "pos": ["6%", "56%"],
            "color": CYAN,
        },
        {
            "type": "text",
            "name": "ammo (tall)",
            "text": "{cs2.player.active_weapon.ammo_clip:.0f} / "
            "{cs2.player.active_weapon.ammo_reserve:.0f}",
            "orientation": "portrait",
            "font": "sans",
            "size": 48,
            "pos": ["6%", "59%"],
            "color": BRIGHT,
        },
        {
            "type": "text",
            "name": "money (tall)",
            "text": "$ {cs2.player.state.money:.0f}   EQUIP $ {cs2.player.state.equip_value:.0f}",
            "orientation": "portrait",
            "font": "sans",
            "size": 28,
            "pos": ["6%", "66%"],
            "color": GREEN,
        },
        {
            "type": "text",
            "name": "kda label (tall)",
            "text": "K / D / A",
            "orientation": "portrait",
            "font": "sans",
            "size": 22,
            "pos": ["6%", "71%"],
            "color": DIM,
        },
        {
            "type": "text",
            "name": "kda (tall)",
            "text": "{cs2.player.match_stats.kills:.0f} / "
            "{cs2.player.match_stats.deaths:.0f} / "
            "{cs2.player.match_stats.assists:.0f}   "
            "MVP {cs2.player.match_stats.mvps:.0f}",
            "orientation": "portrait",
            "font": "sans",
            "size": 34,
            "pos": ["6%", "74%"],
            "color": CYAN,
        },
        {
            "type": "text",
            "name": "round phase (tall)",
            "text": "{cs2.round.phase}   {cs2.phase.seconds_left:.0f}s",
            "orientation": "portrait",
            "font": "sans",
            "size": 30,
            "pos": ["6%", "81%"],
            "color": AMBER,
        },
        {
            "type": "text",
            "name": "bomb (tall)",
            "text": "{cs2.bomb.state}",
            "orientation": "portrait",
            "font": "sans",
            "size": 30,
            "pos": ["6%", "87%"],
            "color": RED,
        },
    ]

    shared = [
        {
            "type": "text",
            "name": "waiting overlay",
            "text": "{cs2.status_text}",
            "font": "sans",
            "size": 48,
            "pos": ["center", "center"],
            "anchor": "middle-center",
            "align": "center",
            "color": DIM,
        },
        {"type": "cs2_hit_flash", "name": "hit flash"},
    ]

    return {
        "version": 1,
        "name": "CS2 HUD",
        "fps": 15,
        "background": INK,
        "layers": [{"type": "solid", "color": PANEL, "color2": INK}, *wide, *tall, *shared],
    }


def _claude_usage() -> dict[str, Any]:
    """Claude Code subscription usage: two limit rings plus local token totals.

    The rings are the point. ``claude.limits.*`` comes from the same endpoint
    ``/usage`` queries, so "62% of my week" is the real server-side figure rather
    than anything inferred locally; the token and cost readouts beside them come
    from :mod:`fitzlcd.sources.claude_usage` reading transcripts on this machine.
    Thresholds live on the rings themselves (amber at 75%, red at 90%), so the
    colour carries the warning without a second layer.

    Styled to look like Claude Code rather than like the rest of the library:
    Claude's own warm palette, monospace throughout because the thing being
    measured is a terminal, and the spark mark beside the title. The rings sweep
    in when the scene starts and glide to each new reading, and a rise lights up
    the slice it just added before it cools. The spark turns slowly at rest and
    faster while Claude Code is working.
    """
    # Both rings are the same orange: they are one brand, told apart by their
    # labels rather than by hue, which leaves colour free to mean "getting close
    # to the limit" and nothing else.
    ring = {
        "type": "donut",
        "font": "mono",
        "color": CLAUDE_ORANGE,
        "track_color": CLAUDE_TRACK,
        "text_color": CLAUDE_CREAM,
        "sub_color": CLAUDE_DIM,
        "warn_color": CLAUDE_WARN,
        "critical_color": CLAUDE_CRIT,
        # Motion. Fill in on start, glide to new readings, highlight the slice a
        # rise has just added, and dim past warn. The sweep stays under a second
        # so the gallery thumbnail (sampled at t=1.1s) shows a finished ring.
        "sweep_in": 0.9,
        "glide": 1.2,
        "gain_flash": 2.5,
        "gain_color": CLAUDE_CREAM,
        "pulse": 0.5,
    }
    session_ring = {
        **ring,
        "metric": "claude.limits.session.pct",
        "text": "{claude.limits.session.pct:.0f}%",
        "sub_text": "SESSION",
    }
    week_ring = {
        **ring,
        "metric": "claude.limits.week.pct",
        "text": "{claude.limits.week.pct:.0f}%",
        "sub_text": "WEEK",
    }

    # Idle, the mark turns slowly and its arms breathe. While Claude Code is
    # working (claude.active), it turns faster and lifts toward white.
    spark_motion = {
        "spin": 8,
        "active_metric": "claude.active",
        "active_spin": 90,
        "active_glow": 0.35,
        "breathe": 0.12,
    }

    wide = [
        {
            **session_ring,
            "name": "session ring",
            "orientation": "landscape",
            "rect": ["5%", "center", 250, 250],
            "anchor": "middle-left",
            "thickness": 24,
            "text_size": 62,
            "sub_size": 22,
        },
        {
            **week_ring,
            "name": "week ring",
            "orientation": "landscape",
            "rect": ["20%", "center", 250, 250],
            "anchor": "middle-left",
            "thickness": 24,
            "text_size": 62,
            "sub_size": 22,
        },
        {
            "type": "text",
            "name": "session resets",
            "text": "resets {claude.limits.session.resets_text}",
            "orientation": "landscape",
            "font": "mono",
            "size": 20,
            "pos": ["5%", "-8%"],
            "anchor": "bottom-left",
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "week resets",
            "text": "resets {claude.limits.week.resets_text}",
            "orientation": "landscape",
            "font": "mono",
            "size": 20,
            "pos": ["20%", "-8%"],
            "anchor": "bottom-left",
            "color": CLAUDE_DIM,
        },
        {
            "type": "spark",
            "name": "spark",
            "orientation": "landscape",
            "rect": ["36%", "6%", 38, 38],
            "color": CLAUDE_ORANGE,
            "arm_width": 9,
            "taper": 0.25,
            **spark_motion,
        },
        {
            "type": "text",
            "name": "title",
            "text": "CLAUDE CODE",
            "orientation": "landscape",
            "font": "mono",
            "size": 30,
            "pos": ["39%", "8%"],
            "color": CLAUDE_CREAM,
        },
        {
            "type": "text",
            "name": "model",
            "text": "{claude.model}",
            "orientation": "landscape",
            "font": "mono",
            "size": 20,
            "pos": ["39%", "19%"],
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "tokens label",
            "text": "TOKENS TODAY",
            "orientation": "landscape",
            "font": "mono",
            "size": 22,
            "pos": ["36%", "36%"],
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "tokens value",
            "text": "{claude.today.tokens:,}",
            "orientation": "landscape",
            "font": "mono",
            "size": 54,
            "pos": ["36%", "44%"],
            "color": CLAUDE_ORANGE,
        },
        {
            "type": "text",
            "name": "cost label",
            "text": "EST. COST TODAY",
            "orientation": "landscape",
            "font": "mono",
            "size": 22,
            "pos": ["36%", "68%"],
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "cost value",
            "text": "$ {claude.today.cost_usd:.2f}",
            "orientation": "landscape",
            "font": "mono",
            "size": 44,
            "pos": ["36%", "76%"],
            "color": CLAUDE_CREAM,
        },
        {
            "type": "text",
            "name": "clock",
            "text": "{time.now}",
            "orientation": "landscape",
            "font": "mono",
            "size": 30,
            "pos": ["-3%", "8%"],
            "anchor": "top-right",
            "align": "right",
            "color": CLAUDE_CREAM,
        },
        {
            "type": "text",
            "name": "activity",
            "text": "{claude.today.messages:,} msgs  ·  {claude.today.sessions:.0f} sessions",
            "orientation": "landscape",
            "font": "mono",
            "size": 24,
            "pos": ["-3%", "36%"],
            "anchor": "top-right",
            "align": "right",
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "week summary",
            "text": "7 DAYS   {claude.week.tokens:,} tok   $ {claude.week.cost_usd:.2f}",
            "orientation": "landscape",
            "font": "mono",
            "size": 20,
            "pos": ["-3%", "52%"],
            "anchor": "top-right",
            "align": "right",
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "total summary",
            "text": "ALL TIME   {claude.total.tokens:,} tok   $ {claude.total.cost_usd:.2f}",
            "orientation": "landscape",
            "font": "mono",
            "size": 20,
            "pos": ["-3%", "62%"],
            "anchor": "top-right",
            "align": "right",
            "color": CLAUDE_DIM,
        },
        # No per-model row here on purpose: seven_day_sonnet / seven_day_opus are
        # null on plans that do not break the weekly window down that way, so the
        # line would sit there reading "—%" forever. The metrics still exist for
        # anyone whose account reports them.
    ]

    tall = [
        {
            "type": "spark",
            "name": "spark (tall)",
            "orientation": "portrait",
            "rect": ["center", "1%", 38, 38],
            "anchor": "top-center",
            "color": CLAUDE_ORANGE,
            "arm_width": 9,
            "taper": 0.25,
            **spark_motion,
        },
        {
            "type": "text",
            "name": "title (tall)",
            "text": "CLAUDE CODE",
            "orientation": "portrait",
            "font": "mono",
            "size": 32,
            "pos": ["center", "4%"],
            "anchor": "top-center",
            "align": "center",
            "color": CLAUDE_CREAM,
        },
        {
            **session_ring,
            "name": "session ring (tall)",
            "orientation": "portrait",
            "rect": ["center", "6%", 320, 320],
            "anchor": "top-center",
            "thickness": 30,
            "text_size": 76,
            "sub_size": 26,
        },
        {
            "type": "text",
            "name": "session resets (tall)",
            "text": "resets {claude.limits.session.resets_text}",
            "orientation": "portrait",
            "font": "mono",
            "size": 24,
            "pos": ["center", "24%"],
            "anchor": "top-center",
            "align": "center",
            "color": CLAUDE_DIM,
        },
        {
            **week_ring,
            "name": "week ring (tall)",
            "orientation": "portrait",
            "rect": ["center", "28%", 320, 320],
            "anchor": "top-center",
            "thickness": 30,
            "text_size": 76,
            "sub_size": 26,
        },
        {
            "type": "text",
            "name": "week resets (tall)",
            "text": "resets {claude.limits.week.resets_text}",
            "orientation": "portrait",
            "font": "mono",
            "size": 24,
            "pos": ["center", "46%"],
            "anchor": "top-center",
            "align": "center",
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "tokens label (tall)",
            "text": "TOKENS TODAY",
            "orientation": "portrait",
            "font": "mono",
            "size": 26,
            "pos": ["6%", "53%"],
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "tokens value (tall)",
            "text": "{claude.today.tokens:,}",
            "orientation": "portrait",
            "font": "mono",
            "size": 58,
            "pos": ["6%", "56%"],
            "color": CLAUDE_ORANGE,
        },
        {
            "type": "text",
            "name": "cost label (tall)",
            "text": "EST. COST TODAY",
            "orientation": "portrait",
            "font": "mono",
            "size": 26,
            "pos": ["6%", "63%"],
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "cost value (tall)",
            "text": "$ {claude.today.cost_usd:.2f}",
            "orientation": "portrait",
            "font": "mono",
            "size": 52,
            "pos": ["6%", "66%"],
            "color": CLAUDE_CREAM,
        },
        {
            "type": "text",
            "name": "activity (tall)",
            "text": "{claude.today.messages:,} msgs\n{claude.today.sessions:.0f} sessions",
            "orientation": "portrait",
            "font": "mono",
            "size": 30,
            "pos": ["6%", "74%"],
            "color": CLAUDE_DIM,
        },
        # Stacked, not side by side. A ten-digit token count in a monospace face
        # is ~250 px at this size, and two of those do not fit across a 462 px
        # column -- they used to overlap in the middle.
        {
            "type": "text",
            "name": "week summary (tall)",
            "text": "7 DAYS\n{claude.week.tokens:,} tok\n$ {claude.week.cost_usd:.2f}",
            "orientation": "portrait",
            "font": "mono",
            "size": 22,
            "pos": ["6%", "81%"],
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "total summary (tall)",
            "text": "ALL TIME\n{claude.total.tokens:,} tok\n$ {claude.total.cost_usd:.2f}",
            "orientation": "portrait",
            "font": "mono",
            "size": 22,
            "pos": ["6%", "87%"],
            "color": CLAUDE_DIM,
        },
        {
            "type": "text",
            "name": "clock (tall)",
            "text": "{time.now}",
            "orientation": "portrait",
            "font": "mono",
            "size": 40,
            "pos": ["center", "-2%"],
            "anchor": "bottom-center",
            "align": "center",
            "color": CLAUDE_CREAM,
        },
    ]

    # Only ever non-empty when the rings cannot be trusted: not signed in, rate
    # limited, or serving a reading old enough to mislead. Placed per-orientation
    # rather than shared, because the one gap that is free in a 1920x462 strip is
    # not the one that is free in a 462x1920 column.
    status = {
        "type": "text",
        "name": "limits status",
        "text": "{claude.limits.status_text}",
        "font": "mono",
        "size": 30,
        "align": "center",
        "color": CLAUDE_CRIT,
        "shadow": True,
    }
    shared = [
        {
            **status,
            "name": "limits status (wide)",
            "orientation": "landscape",
            "pos": ["center", "-6%"],
            "anchor": "bottom-center",
        },
        {
            **status,
            "name": "limits status (tall)",
            "orientation": "portrait",
            "pos": ["center", "50%"],
            "anchor": "middle-center",
        },
    ]

    return {
        "version": 1,
        "name": "Claude Usage",
        # 15 rather than the 2 the readouts need, so the ring glides and the
        # spark's turn read as motion instead of stutter. Well inside the DS916's
        # 30 fps ceiling, and the engine drops frames rather than queueing them,
        # so a slow frame costs smoothness only. To restore the static scene, set
        # the rings' "sweep_in", "glide", "gain_flash" and "pulse" to 0 and the
        # sparks' "spin", "breathe" and "active_metric" to 0 / "", and this to 2.
        "fps": 15,
        "background": CLAUDE_INK,
        "layers": [
            {"type": "solid", "color": CLAUDE_PANEL, "color2": CLAUDE_INK},
            *wide,
            *tall,
            *shared,
        ],
    }


#: World of Warcraft palette: the game's own gold on dark leather and parchment.
WOW_GOLD = "#ffd100"
WOW_PARCHMENT = "#f5ebdd"
WOW_DIM = "#b9a98ccc"
WOW_INK = "#0c0a08"
WOW_PANEL = "#1d1712"
WOW_RESTED = "#4aa3ff"
WOW_HOSTILE = "#ff7a66"


def _wow_forever() -> dict[str, Any]:
    """World of Warcraft: Forever: the live zone map with your party on it.

    Fed by the FitzLCD addon's pixel strip (``sources/wow``). The map fills
    one side; zone, level and experience sit beside it, with the party roster,
    target and session numbers last. When the game is closed everything stays
    up as it was last seen, with a status and "last seen" line on the map.
    """
    text = {"type": "text", "font": "sans", "color": WOW_PARCHMENT}
    dim = {**text, "color": WOW_DIM}
    map_layer = {
        "type": "wow_map",
        "name": "map",
        "view": "party",
        "zoom": 3.0,
        "font": "sans",
        "player_color": WOW_GOLD,
        "name_color": WOW_PARCHMENT,
        "backdrop_color": "#14110d",
        "grid_color": "#ffd10022",
        "border_color": "#ffd10033",
        "glide": 0.9,
        "ping": 3.0,
    }
    xp_bar = {
        "type": "gauge",
        "metric": "wow.xp.pct",
        "color": WOW_GOLD,
        "track_color": "#ffffff14",
    }
    rested_bar = {
        "type": "gauge",
        "metric": "wow.xp.rested_pct",
        "color": WOW_RESTED,
        "track_color": "#00000000",
        "radius": 3,
    }
    party = {
        "type": "wow_party",
        "name": "party",
        "font": "sans",
        "text_color": WOW_PARCHMENT,
        "dim_color": WOW_DIM,
    }
    level = "LEVEL {wow.player.level}  {wow.player.class}"
    xp_detail = "{wow.xp.pct:.1f}% XP   {wow.xp.rested_pct:.0f}% rested"

    def wide_(layer: dict[str, Any]) -> dict[str, Any]:
        return {**layer, "orientation": "landscape"}

    def tall_(layer: dict[str, Any]) -> dict[str, Any]:
        return {
            **layer,
            "name": f"{layer.get('name', layer['type'])} (tall)",
            "orientation": "portrait",
        }

    wide = [
        wide_({**map_layer, "rect": ["1%", "4%", "42%", "92%"]}),
        wide_(
            {
                **text,
                "name": "zone",
                "text": "{wow.zone}",
                "size": 46,
                "pos": ["45%", "5%"],
                "color": WOW_GOLD,
                "shadow": True,
            }
        ),
        wide_(
            {**text, "name": "subzone", "text": "{wow.subzone}", "size": 28, "pos": ["45%", "19%"]}
        ),
        wide_(
            {
                **dim,
                "name": "coordinates",
                "text": "{wow.coords_text}",
                "font": "mono",
                "size": 24,
                "pos": ["45%", "28%"],
            }
        ),
        wide_({**text, "name": "level", "text": level, "size": 30, "pos": ["45%", "41%"]}),
        wide_({**xp_bar, "name": "xp bar", "rect": ["45%", "53%", "24%", 16]}),
        wide_({**rested_bar, "name": "rested bar", "rect": ["45%", "58%", "24%", 6]}),
        wide_({**dim, "name": "xp detail", "text": xp_detail, "size": 22, "pos": ["45%", "63%"]}),
        wide_(
            {
                **text,
                "name": "gold",
                "text": "{wow.gold_text}",
                "size": 30,
                "pos": ["45%", "75%"],
                "color": WOW_GOLD,
            }
        ),
        wide_(
            {
                **dim,
                "name": "state",
                "text": "{wow.player.state_text}",
                "size": 22,
                "pos": ["45%", "86%"],
                "color": WOW_HOSTILE,
            }
        ),
        wide_({**dim, "name": "party label", "text": "PARTY", "size": 22, "pos": ["72%", "5%"]}),
        wide_(
            {
                **dim,
                "name": "clock",
                "text": "{time.now}",
                "size": 22,
                "pos": ["-1.5%", "5%"],
                "anchor": "top-right",
            }
        ),
        wide_({**party, "rect": ["72%", "13%", "26.5%", "50%"], "size": 26}),
        wide_(
            {
                **text,
                "name": "target",
                "text": "{wow.target.text}",
                "size": 24,
                "pos": ["72%", "66%"],
                "color": WOW_HOSTILE,
            }
        ),
        wide_(
            {
                **dim,
                "name": "session",
                "text": "{wow.session.played_text} played   {wow.session.xp_per_hour:,.0f} XP/h",
                "size": 21,
                "pos": ["72%", "77%"],
            }
        ),
        wide_(
            {
                **dim,
                "name": "gear",
                "text": "{wow.durability.pct:.0f}% durability   {wow.bags.free} bag slots free",
                "size": 21,
                "pos": ["72%", "86%"],
            }
        ),
    ]

    centred = {"anchor": "top-center", "align": "center"}
    tall = [
        tall_({**map_layer, "rect": ["4%", "1.5%", "92%", "40%"]}),
        tall_(
            {
                **text,
                **centred,
                "name": "zone",
                "text": "{wow.zone}",
                "size": 40,
                "pos": ["center", "43%"],
                "color": WOW_GOLD,
                "shadow": True,
            }
        ),
        tall_(
            {
                **text,
                **centred,
                "name": "subzone",
                "text": "{wow.subzone}",
                "size": 26,
                "pos": ["center", "46%"],
            }
        ),
        tall_(
            {
                **dim,
                **centred,
                "name": "coordinates",
                "text": "{wow.coords_text}",
                "font": "mono",
                "size": 22,
                "pos": ["center", "48.3%"],
            }
        ),
        tall_({**text, "name": "level", "text": level, "size": 28, "pos": ["6%", "52%"]}),
        tall_({**xp_bar, "name": "xp bar", "rect": ["6%", "54.6%", "88%", 16]}),
        tall_({**rested_bar, "name": "rested bar", "rect": ["6%", "55.8%", "88%", 6]}),
        tall_({**dim, "name": "xp detail", "text": xp_detail, "size": 20, "pos": ["6%", "57%"]}),
        tall_(
            {
                **text,
                "name": "gold",
                "text": "{wow.gold_text}",
                "size": 28,
                "pos": ["6%", "60.5%"],
                "color": WOW_GOLD,
            }
        ),
        tall_(
            {
                **dim,
                "name": "state",
                "text": "{wow.player.state_text}",
                "size": 20,
                "pos": ["-6%", "61%"],
                "anchor": "top-right",
                "color": WOW_HOSTILE,
            }
        ),
        tall_({**dim, "name": "party label", "text": "PARTY", "size": 20, "pos": ["6%", "65.5%"]}),
        tall_({**party, "rect": ["6%", "67.5%", "88%", "17%"], "size": 24}),
        tall_(
            {
                **text,
                "name": "target",
                "text": "{wow.target.text}",
                "size": 22,
                "pos": ["6%", "86%"],
                "color": WOW_HOSTILE,
            }
        ),
        tall_(
            {
                **dim,
                "name": "session",
                "text": "{wow.session.played_text} played\n{wow.session.xp_per_hour:,.0f} XP/h",
                "size": 20,
                "pos": ["6%", "89%"],
            }
        ),
        tall_(
            {
                **dim,
                "name": "clock",
                "text": "{time.now}",
                "size": 22,
                "pos": ["center", "-1.5%"],
                "anchor": "bottom-center",
            }
        ),
    ]

    # Why the map is showing old news, on the map itself.
    status = {
        **text,
        "name": "status",
        "text": "{wow.status_text}",
        "size": 26,
        "align": "center",
        "anchor": "bottom-center",
        "shadow": True,
        "color": WOW_GOLD,
    }
    seen = {
        **dim,
        "name": "last seen",
        "text": "{wow.last_seen_text}",
        "size": 20,
        "align": "center",
        "anchor": "bottom-center",
    }
    shared = [
        wide_({**status, "pos": ["22%", "-15%"]}),
        wide_({**seen, "pos": ["22%", "-8%"]}),
        tall_({**status, "pos": ["center", "37%"]}),
        tall_({**seen, "pos": ["center", "39.5%"]}),
    ]

    return {
        "version": 1,
        "name": "WoW Forever",
        # The map glides its dots between readings; 15 fps keeps that smooth.
        "fps": 15,
        "background": WOW_INK,
        "layers": [
            {"type": "solid", "color": WOW_PANEL, "color2": WOW_INK},
            *wide,
            *tall,
            *shared,
        ],
    }


def _wow_orient(layer: dict[str, Any], orientation: str) -> dict[str, Any]:
    """Tag a layer for one orientation; the portrait copy gets a distinct name."""
    if orientation == "portrait":
        name = f"{layer.get('name', layer['type'])} (tall)"
        return {**layer, "name": name, "orientation": "portrait"}
    return {**layer, "orientation": "landscape"}


def _wow_status_lines(wide_pos: tuple[str, str], tall_pos: tuple[str, str]) -> list[dict]:
    """Why the display is showing old news, and since when."""
    status = {
        "type": "text",
        "font": "sans",
        "name": "status",
        "text": "{wow.status_text}",
        "size": 26,
        "align": "center",
        "anchor": "bottom-center",
        "shadow": True,
        "color": WOW_GOLD,
    }
    seen = {
        **status,
        "name": "last seen",
        "text": "{wow.last_seen_text}",
        "size": 20,
        "shadow": False,
        "color": WOW_DIM,
    }

    def below(pos: tuple[str, str], step: float) -> list[str]:
        x, y = pos
        number = float(y.rstrip("%"))
        return [x, f"{number + step:g}%"]

    return [
        _wow_orient({**status, "pos": list(wide_pos)}, "landscape"),
        _wow_orient({**seen, "pos": below(wide_pos, 7)}, "landscape"),
        _wow_orient({**status, "pos": list(tall_pos)}, "portrait"),
        _wow_orient({**seen, "pos": below(tall_pos, 2.5)}, "portrait"),
    ]


def _wow_unit_frame() -> dict[str, Any]:
    """World of Warcraft: Forever: your own unit frame, as big as the panel allows.

    The portrait is the game's own, copied off the screen about once a second
    (the class icon from the game files when the frame cannot be seen), with
    health and power bars that ease like the real ones and leave a red slice
    for damage just taken. Experience, zone, gold and target fill the rest.
    """
    text = {"type": "text", "font": "sans", "color": WOW_PARCHMENT}
    dim = {**text, "color": WOW_DIM}
    frame = {
        "type": "wow_unit_frame",
        "name": "unit frame",
        "font": "sans",
        "name_color": WOW_GOLD,
        "ring_color": "#c9a343",
        "panel_color": "#000000b4",
        "glide": 0.45,
    }
    xp_bar = {
        "type": "gauge",
        "metric": "wow.xp.pct",
        "color": "#a335ee",
        "track_color": "#ffffff14",
    }
    rested_bar = {
        "type": "gauge",
        "metric": "wow.xp.rested_pct",
        "color": WOW_RESTED,
        "track_color": "#00000000",
        "radius": 3,
    }
    level = "LEVEL {wow.player.level} {wow.player.class}"
    xp_detail = "{wow.xp.text}   {wow.xp.pct:.1f}%   {wow.xp.rested_pct:.0f}% rested"

    def wide(layer: dict[str, Any]) -> dict[str, Any]:
        return _wow_orient(layer, "landscape")

    def tall(layer: dict[str, Any]) -> dict[str, Any]:
        return _wow_orient(layer, "portrait")

    landscape = [
        wide({**frame, "rect": ["1.5%", "4%", "56%", "78%"]}),
        wide(
            {
                **text,
                "name": "zone",
                "text": "{wow.zone}",
                "size": 40,
                "pos": ["60%", "8%"],
                "color": WOW_GOLD,
                "shadow": True,
            }
        ),
        wide(
            {
                **dim,
                "name": "subzone",
                "text": "{wow.location_text}",
                "size": 24,
                "pos": ["60%", "21%"],
            }
        ),
        wide({**text, "name": "level", "text": level, "size": 28, "pos": ["60%", "36%"]}),
        wide({**xp_bar, "name": "xp bar", "rect": ["60%", "48%", "38%", 16]}),
        wide({**rested_bar, "name": "rested bar", "rect": ["60%", "53%", "38%", 6]}),
        wide({**dim, "name": "xp detail", "text": xp_detail, "size": 20, "pos": ["60%", "58%"]}),
        wide(
            {
                **text,
                "name": "gold",
                "text": "{wow.gold_text}",
                "size": 28,
                "pos": ["60%", "71%"],
                "color": WOW_GOLD,
            }
        ),
        wide(
            {
                **text,
                "name": "target",
                "text": "{wow.target.text}",
                "size": 24,
                "pos": ["60%", "84%"],
                "color": WOW_HOSTILE,
            }
        ),
        wide(
            {
                **dim,
                "name": "clock",
                "text": "{time.now}",
                "size": 22,
                "pos": ["-1.5%", "8%"],
                "anchor": "top-right",
            }
        ),
    ]
    centred = {"anchor": "top-center", "align": "center"}
    portrait = [
        tall({**frame, "rect": ["3%", "3%", "94%", "15%"]}),
        tall(
            {
                **text,
                **centred,
                "name": "zone",
                "text": "{wow.zone}",
                "size": 38,
                "pos": ["center", "22%"],
                "color": WOW_GOLD,
                "shadow": True,
            }
        ),
        tall(
            {
                **dim,
                **centred,
                "name": "subzone",
                "text": "{wow.subzone}",
                "size": 24,
                "pos": ["center", "25%"],
            }
        ),
        tall({**text, "name": "level", "text": level, "size": 26, "pos": ["6%", "32%"]}),
        tall({**xp_bar, "name": "xp bar", "rect": ["6%", "35%", "88%", 16]}),
        tall({**rested_bar, "name": "rested bar", "rect": ["6%", "36.2%", "88%", 6]}),
        tall(
            {
                **dim,
                "name": "xp detail",
                "text": "{wow.xp.pct:.1f}% XP   {wow.xp.rested_pct:.0f}% rested",
                "size": 20,
                "pos": ["6%", "37.5%"],
            }
        ),
        tall(
            {
                **text,
                "name": "gold",
                "text": "{wow.gold_text}",
                "size": 28,
                "pos": ["6%", "44%"],
                "color": WOW_GOLD,
            }
        ),
        tall(
            {
                **dim,
                "name": "session",
                "text": "{wow.session.played_text} played\n{wow.session.gold_text} this session",
                "size": 20,
                "pos": ["6%", "47.5%"],
            }
        ),
        tall({**dim, "name": "party label", "text": "PARTY", "size": 20, "pos": ["6%", "62%"]}),
        tall(
            {
                "type": "wow_party",
                "name": "party",
                "font": "sans",
                "text_color": WOW_PARCHMENT,
                "dim_color": WOW_DIM,
                "rect": ["6%", "64%", "88%", "22%"],
                "size": 24,
            }
        ),
        tall(
            {
                **text,
                "name": "target",
                "text": "{wow.target.text}",
                "size": 24,
                "pos": ["6%", "55%"],
                "color": WOW_HOSTILE,
            }
        ),
        tall(
            {
                **dim,
                "name": "clock",
                "text": "{time.now}",
                "size": 22,
                "pos": ["center", "-1.5%"],
                "anchor": "bottom-center",
            }
        ),
    ]
    return {
        "version": 1,
        "name": "WoW Unit Frame",
        # Bars ease and the combat ring pulses; 15 fps keeps both smooth.
        "fps": 15,
        "background": WOW_INK,
        "layers": [
            {"type": "solid", "color": WOW_PANEL, "color2": WOW_INK},
            *landscape,
            *portrait,
            *_wow_status_lines(("29%", "-8%"), ("center", "-6%")),
        ],
    }


def _wow_party_map() -> dict[str, Any]:
    """World of Warcraft: Forever: where your party is, on the whole panel.

    The zone map fills the display with everyone's dot on it. Members in
    another zone get an arrow on the map's edge pointing their way, with their
    zone and distance, and the roster says where each one is. Positions a
    member's own FitzLCD addon shared (when the game hides them) get a ring.
    """
    text = {"type": "text", "font": "sans", "color": WOW_PARCHMENT}
    dim = {**text, "color": WOW_DIM}
    map_layer = {
        "type": "wow_map",
        "name": "map",
        "view": "party",
        "zoom": 4.0,
        "font": "sans",
        "dot_size": 13,
        "player_color": WOW_GOLD,
        "name_color": WOW_PARCHMENT,
        "backdrop_color": "#14110d",
        "backdrop_dim": 0.15,
        "grid_color": "#ffd10022",
        "border_color": "#ffd10033",
        "glide": 0.9,
        "ping": 3.0,
    }
    scrim = {"type": "solid", "color": "#0c0a08d0", "color2": "#0c0a08a0", "radius": 16}
    party = {
        "type": "wow_party",
        "name": "party",
        "font": "sans",
        "text_color": WOW_PARCHMENT,
        "dim_color": WOW_DIM,
    }

    def wide(layer: dict[str, Any]) -> dict[str, Any]:
        return _wow_orient(layer, "landscape")

    def tall(layer: dict[str, Any]) -> dict[str, Any]:
        return _wow_orient(layer, "portrait")

    landscape = [
        wide({**map_layer, "rect": ["0.6%", "2.5%", "98.8%", "95%"]}),
        wide({**scrim, "name": "roster backdrop", "rect": ["71%", "6%", "27.5%", "70%"]}),
        wide(
            {
                **text,
                "name": "zone",
                "text": "{wow.zone}",
                "size": 30,
                "pos": ["72.5%", "9%"],
                "color": WOW_GOLD,
                "shadow": True,
            }
        ),
        wide({**party, "rect": ["72.5%", "21%", "24.5%", "52%"], "size": 24}),
        wide(
            {
                **dim,
                "name": "coordinates",
                "text": "{wow.coords_text}",
                "font": "mono",
                "size": 20,
                "pos": ["2%", "-6%"],
                "anchor": "bottom-left",
                "shadow": True,
            }
        ),
    ]
    portrait = [
        tall({**map_layer, "rect": ["3%", "1.5%", "94%", "68%"]}),
        tall(
            {
                **text,
                "name": "zone",
                "text": "{wow.zone}",
                "size": 36,
                "pos": ["6%", "71.5%"],
                "color": WOW_GOLD,
                "shadow": True,
            }
        ),
        tall(
            {
                **dim,
                "name": "coordinates",
                "text": "{wow.coords_text}",
                "font": "mono",
                "size": 22,
                "pos": ["-6%", "72%"],
                "anchor": "top-right",
            }
        ),
        tall({**party, "rect": ["6%", "75.5%", "88%", "20%"], "size": 26}),
        tall(
            {
                **dim,
                "name": "clock",
                "text": "{time.now}",
                "size": 22,
                "pos": ["center", "-1.2%"],
                "anchor": "bottom-center",
            }
        ),
    ]
    return {
        "version": 1,
        "name": "WoW Party Map",
        "fps": 15,
        "background": WOW_INK,
        "layers": [
            {"type": "solid", "color": WOW_PANEL, "color2": WOW_INK},
            *landscape,
            *portrait,
            *_wow_status_lines(("36%", "-14%"), ("center", "62%")),
        ],
    }


#: Seeded on first run, in the order they appear in the UI.
DEFAULT_SCENES: dict[str, dict[str, Any]] = {
    "rig-stats": _rig_stats(),
    "terminal": _terminal(),
    "vitals": _vitals(),
    "pulse": _pulse(),
    "network": _network(),
    "clock": _clock(),
    "wallpaper": _wallpaper(),
    "cs2-hud": _cs2_hud(),
    "claude-usage": _claude_usage(),
    "wow-forever": _wow_forever(),
    "wow-unit-frame": _wow_unit_frame(),
    "wow-party-map": _wow_party_map(),
}
