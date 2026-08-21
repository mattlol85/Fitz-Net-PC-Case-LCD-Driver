# FitzLCD

A desktop app for driving PC-case USB LCD panels with your own content — images,
GIFs, video and live system stats — without the vendor's software.

Built for the **Jonsbo DS916** ("Jungle Leopard") 9.16" 1920×462 panel
(USB `33C3:7788`), on a pluggable driver architecture so other panels can be
added without touching anything above the driver layer.

![FitzLCD main window](docs/screenshots/main-window.png)

## What it does

- **Autodetects** the panel by USB VID:PID and confirms it with the device's own
  `getDeviceInfo` handshake — no port to configure by hand.
- **Composes scenes** from a stack of layers: media (image / GIF / video), text
  with live `{cpu.load:.0f}` style metric tokens, bar gauges, sparklines, a
  clock, and solid/gradient fills.
- **Live system data** from psutil and NVML: CPU, memory, disk, network, and GPU
  load / temperature / VRAM / power.
- **Runs in the tray** with optional start-with-Windows, so the panel stays live
  when the window is closed.
- **Works without hardware** via a built-in virtual panel, so you can develop,
  debug and screenshot the whole app with nothing plugged in.

## Quick start

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m fitzlcd
```

Useful flags:

```powershell
python -m fitzlcd --list-panels           # what can I see?
python -m fitzlcd --panel virtual         # run with no hardware attached
python -m fitzlcd --headless --scene Clock  # no GUI
```

## Scenes

Scenes are JSON in `%APPDATA%\FitzLCD\scenes\`, editable in the GUI or by hand:

```json
{
  "name": "Rig Stats",
  "fps": 10,
  "background": "#05060f",
  "layers": [
    { "type": "media", "source": "C:/media/loop.gif", "fit": "cover", "pan": 0.5 },
    { "type": "text", "text": "CPU {cpu.load:.0f}%", "size": 92,
      "pos": [48, 100], "color": "#ffcc00" },
    { "type": "gauge", "metric": "gpu.temp", "rect": [48, 230, 420, 26],
      "maximum": 100 }
  ]
}
```

Layers draw bottom-up: the first entry is the backdrop.

### Metric tokens

`cpu.load` `cpu.freq` `cpu.cores` · `mem.used_pct` `mem.used_gb` `mem.total_gb` ·
`gpu.load` `gpu.temp` `gpu.vram_pct` `gpu.vram_used_gb` `gpu.power` `gpu.name` ·
`net.up` `net.down` `disk.used_pct` · `time.now` `time.date` `time.uptime`

A metric with no value renders as `—` rather than failing the frame. CPU
temperature is not exposed by psutil on Windows, so `cpu.temp` needs the optional
LibreHardwareMonitor provider (not enabled by default — it wants admin rights).

### Fit modes

The panel is 4.16:1 and nearly all source media is 16:9, so `fit` matters:
`cover` (fill and crop), `contain` (letterbox), `stretch`, `tile`, `none`. Use
`pan` / `pan_x` (0.0–1.0) to choose which part of the source survives the crop.

## Development

The IntelliJ project ships with run configurations for everything:

| Configuration | What it does |
|---|---|
| `App: FitzLCD` | Run against the real panel |
| `App: FitzLCD (virtual panel)` | **Run/debug with no hardware** |
| `App: FitzLCD (headless)` | No GUI |
| `Probe: detect` / `info` / `push image` / `bench` / `brightness` | Talk to the panel directly |
| `Tests: all` | pytest |
| `Lint: ruff` / `Format: black` | Style |
| `UI: screenshot` | Render the real window offscreen to a PNG |
| `Check: all` / `Check: all (fix)` | Format check + lint + tests in one process |
| `Build: PyInstaller` | Standalone build into `dist/FitzLCD/` |

All are ordinary Python configurations, so the Debug button gives you breakpoints
in the render loop, the engine thread and the serial driver.

```powershell
.\.venv\Scripts\python.exe tools/check.py        # everything
.\.venv\Scripts\python.exe tools/ui_shot.py      # screenshot the GUI
```

## Architecture

```
sources/   media decoding, system metrics
render/    scene model, compositor, layers, fit, JPEG encode + transform
engine.py  paced render loop, dirty-frame skip, reconnect watchdog
panels/    Panel interface, registry, DS916 serial driver, virtual panel
ui/        preview-first main window, properties pane, tray
```

Layers declare their own editable fields, so the properties pane is generated
rather than hand-written — a new layer type needs no UI code.

## Working with this panel safely

Two things about the DS916 firmware are worth knowing before you experiment; both
are documented in full in [the protocol spec](docs/Jonsbo_DS916_Protocol_Spec.md#8-findings-from-building-fitzlcd-2026-08-21):

- **Do not free-run frames at it.** The USB link happily accepts ~350 fps, but
  the panel does not render anywhere near that. Flooding it wedges the firmware
  until you physically power-cycle it — at which point even opening the port
  blocks forever. This driver paces frames and drops rather than queues.
- **Only one process can hold the port.** Close the vendor app (and the probe
  CLI) before running FitzLCD. Use `--panel virtual` to develop alongside them.

The OTA firmware command is deliberately not implemented.

## Licence

Personal project; no licence granted yet.
