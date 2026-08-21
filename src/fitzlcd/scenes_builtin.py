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


#: Seeded on first run, in the order they appear in the UI.
DEFAULT_SCENES: dict[str, dict[str, Any]] = {
    "rig-stats": _rig_stats(),
    "terminal": _terminal(),
    "vitals": _vitals(),
    "pulse": _pulse(),
    "network": _network(),
    "clock": _clock(),
    "wallpaper": _wallpaper(),
}
