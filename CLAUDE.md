# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

FitzLCD is a Windows desktop app (PySide6) that drives PC-case USB LCD panels
(built for the Jonsbo DS916, 1920×462) with user-composed scenes: media, live
system metrics, gauges, sparklines, a clock. It talks to the panel over a
custom serial protocol on its own thread, paced deliberately because flooding
the DS916 firmware wedges it until a physical power-cycle.

## Commands

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"

.\.venv\Scripts\python.exe -m fitzlcd                      # run the GUI
.\.venv\Scripts\python.exe -m fitzlcd --panel virtual       # no hardware needed
.\.venv\Scripts\python.exe -m fitzlcd --headless --scene Clock
.\.venv\Scripts\python.exe -m fitzlcd --list-panels

.\.venv\Scripts\python.exe -m pytest -q                     # all tests
.\.venv\Scripts\python.exe -m pytest tests/test_render.py -q
.\.venv\Scripts\python.exe -m pytest tests/test_render.py::TestSomeClass::test_name -q

.\.venv\Scripts\python.exe -m ruff check .                  # lint (CI runs this repo-wide)
.\.venv\Scripts\python.exe -m black --check .                # format check
.\.venv\Scripts\python.exe -m black .                        # format

.\.venv\Scripts\python.exe tools/check.py                    # format check + lint + tests, one process
.\.venv\Scripts\python.exe tools/ui_shot.py                  # screenshot the real window offscreen
.\.venv\Scripts\python.exe tools/scene_sheet.py               # every scene x every orientation, one contact sheet
.\.venv\Scripts\python.exe -m PyInstaller packaging/fitzlcd.spec --noconfirm  # standalone build -> dist/FitzLCD/
```

`tools/scene_sheet.py` is the fastest way to check a layout change: it renders
the whole scene library at 0/90/180/270° with no hardware involved.

The IntelliJ project has run configurations mirroring all of the above (`App:
FitzLCD (virtual panel)`, `Check: all (fix)`, `Scenes: contact sheet`, etc.) —
prefer those names when telling a human what to click, since they're the
project's actual entry points.

`ruff check .` runs across the whole repo in CI (`.github/workflows/python-build.yaml`),
not just changed files — a pre-existing violation anywhere fails the build, so
run it repo-wide locally before pushing, not just on touched files.

## Architecture

```
sources/          media decoding, system metrics (psutil/NVML), CS2 GSI listener
render/           scene model, compositor, layers, geometry, JPEG encode
render/geometry   percentage/anchor length resolution
engine.py         paced render loop, dirty-frame skip, reconnect watchdog
panels/           Panel interface, registry, DS916 serial driver, virtual panel
scenes_builtin.py the seeded scene library
ui/               preview-first main window, properties pane, tray
```

**Scenes are data, not code.** A scene is JSON (`Scene` in `render/scene.py`):
a background plus an ordered stack of layer dicts, drawn bottom-up. Users edit
them as JSON under `%APPDATA%\FitzLCD\scenes\`, or through the GUI's
properties pane. The built-in library lives in `scenes_builtin.py` as Python
functions returning that same JSON shape, and is only seeded into
`%APPDATA%\FitzLCD\scenes\` when that directory is empty — adding a new
built-in scene there does **not** retroactively appear for existing installs;
it has to be dropped into the user's scenes folder directly (or the user
deletes their scenes dir to re-seed).

**Layers self-register.** Each layer type (`render/layers/*.py`) is a
dataclass decorated with `@layer_type("name")`, declaring its own `FIELDS`
(name, kind, default, choices). The properties pane in the GUI is generated
from those `FIELDS` — a new layer type needs zero UI code, just the dataclass
and its `draw()`. `is_dynamic` marks a layer as needing per-frame redraw
(metrics, clocks) versus static content the compositor can cache.

**Metrics are a flat dotted namespace**, e.g. `cpu.load`, `gpu.temp`,
`claude.today.tokens`. `MetricProvider` subclasses in `sources/*.py` are
polled on one background thread by `StatsRegistry` (`sources/stats.py`) and
publish an immutable snapshot; a provider that can't read a value just omits
that key rather than erroring, and text layers render a missing metric as `—`
(`render/tokens.py`) rather than failing the frame. **Omitting beats guessing**
— never publish a zero to stand in for "unknown", or a ring will confidently
report 0% when it means "no idea". `StatsRegistry.with_defaults()` registers
the providers that need no configuration; ones that read `AppConfig` (CS2 GSI,
Claude limits) are added conditionally in `ui/app.py` *and* `__main__.py`
instead — remember both entry points.

**Two unrelated Claude sources, easily confused.** `sources/claude_usage.py`
adds up local transcripts (tokens/cost/messages, this machine only, cost is a
notional estimate). `sources/claude_limits.py` polls an **undocumented**
endpoint for the real server-side `/usage` percentages. For the latter: the
`User-Agent: claude-code/<version>` header is load-bearing — without it the
endpoint throttles permanently at any interval — and there is deliberately no
OAuth refresh flow, because rotating the refresh token could invalidate the
user's real Claude Code login.

**Orientation is one number, resolved in two places.**
`PanelCaps.rotated(degrees)` swaps the frame geometry for scene composition,
and the mounting angle is folded into the panel's own scan-out transform
(rotations about the same centre add) — the bytes reaching the panel are
always its native 462×1920 buffer regardless of mounting. Scenes stay correct
across orientations by using percentage/anchor positioning (see README
"Positioning that survives rotation") rather than fixed pixels, and by tagging
orientation-specific layer variants with `"orientation": "landscape"` /
`"portrait"` when one arrangement genuinely can't serve both (see the
`_terminal`, `_rig_stats`, `_vitals` scenes for the pattern — build a `wide`
list and a `tall` list, concatenate them with any orientation-agnostic
`shared` layers).

**Panels are pluggable.** `panels/base.py` defines the `Panel` interface;
`panels/registry.py` does USB VID:PID autodetection and dispatch. Adding
hardware support is one new driver module plus one `register()` call —
nothing above the driver layer changes. `panels/virtual.py` is a
software-only panel used for development/testing/CI with no hardware
attached (`--panel virtual`).

**The render loop never blocks the UI and never floods the panel.** `engine.py`
runs on its own thread, communicates through immutable snapshots/callbacks,
paces frames to `min(scene.fps, caps.max_fps)` and drops rather than queues.
This is a hardware constraint, not a style preference — see "Working with this
panel safely" in the README before changing anything in `engine.py` or
`panels/ds916/`.

## Releases

`.github/workflows/python-build.yaml` runs `ruff check .` + `pytest` on every
push/PR to `main`/`master`. `.github/workflows/publish.yml` is a manual
`workflow_dispatch` (pick `major`/`minor`/`patch`) that bumps the version in
`pyproject.toml` **and `src/fitzlcd/__init__.py`**, tags, builds the EXE via
`packaging/fitzlcd.spec` (PyInstaller, one-dir build), then produces two
release assets from that same `dist/FitzLCD/` output and attaches both to a
GitHub Release: `FitzLCD-<version>-windows.zip` (portable, unzip-and-run) and
`FitzLCD-<version>-Setup.exe` (classic installer, built by compiling
`packaging/installer.iss` with Inno Setup — `ISCC.exe packaging\installer.iss
/DMyAppVersion=<version>`).

**The installer's default install directory is load-bearing, not cosmetic.**
It installs per-user to `%LocalAppData%\Programs\FitzLCD` with
`PrivilegesRequired=lowest` (no UAC) specifically so the existing self-update
flow below keeps working unmodified — that flow assumes it can write to its
own install directory without elevating. The in-app auto-updater was
deliberately *not* changed to fetch/run the installer; it still only ever
looks for the `-windows.zip` asset (`updater._ASSET_RE`) and updates in place
regardless of whether the app was set up via the installer or unzipped by
hand. If you ever change the installer's default directory to somewhere that
needs admin (e.g. `Program Files`), the self-update path breaks for anyone
who accepts that default — see `updater.install_writable()`.

`src/fitzlcd/__init__.py.__version__` is the only version the running app can
see — there is no dist-info in the frozen build, so `importlib.metadata` raises
there. It is rewritten by `publish.yml`; don't edit it by hand. **Job order in
that workflow is version → build → release, and must stay that way**: the
version is baked into the binary and `updater.check()` compares it against the
latest release, so building before the bump ships an EXE that believes it is the
previous version and offers itself an endless update.

**Self-update never overwrites the app in-process.** The one-dir build means the
running `FitzLCD.exe` and every DLL under `_internal` are locked by Windows.
`updater.py` downloads the release zip, stages it under
`%APPDATA%\FitzLCD\updates\`, refuses anything that doesn't contain
`FitzLCD.exe` + `_internal/`, then writes a batch helper and launches it
detached; the helper waits on our PID, `robocopy /MIR`s the staged build over
the install directory, relaunches and deletes itself. The helper calls every
tool by absolute `System32` path — a Git-for-Windows `find`/`sort` on PATH would
otherwise shadow them and silently break the wait loop. Launch it with
`CREATE_NO_WINDOW`, never `DETACHED_PROCESS`: the two flags are mutually
exclusive and detaching leaves the helper without usable standard handles, which
wedges its piped `tasklist`. `ui/app.py` ends in `os._exit`, so nothing hooked
to `atexit` would ever run — the detached child is the whole mechanism.
