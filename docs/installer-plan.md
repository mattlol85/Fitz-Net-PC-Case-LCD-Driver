# Windows installer plan

## Goal

Ship FitzLCD as a double-click Windows installer alongside the existing
standalone zip, without disturbing the in-app self-update mechanism.

## Why Inno Setup

- Free, no runtime, single self-contained `Setup.exe` output.
- Pre-installed on GitHub's `windows-latest` runners (`iscc` on PATH) — no extra
  toolchain step.
- Plain-text `.iss` script, diffable and reviewable.
- Handles the one-dir layout (`FitzLCD.exe` + `_internal/`) natively.

Rejected: MSI/WiX (heavier authoring, no real gain here), one-file PyInstaller
(slower cold start — the spec comment explicitly chose one-dir for fast login).

## Design decisions

| Decision | Rationale |
|---|---|
| Per-user install by default (`{localappdata}\Programs\FitzLCD`) | Keeps the install dir writable so `updater.install_writable()` stays true and self-update keeps working. |
| Offer all-users / Program Files via the privileges dialog | Users who want it can have it; self-update then steps aside to the release page, which is already the documented behaviour. |
| Autostart task writes the **same** HKCU `Run` value as `platform/autostart.py` | Installer checkbox and the tray toggle stay in sync; no second competing mechanism. |
| `CloseApplications=yes` | Restart Manager shuts a running FitzLCD before overwrite; the app has no named mutex to key on. |
| Leave `%APPDATA%\FitzLCD` on uninstall | Scenes and config are user data. |
| Keep shipping the zip | The self-updater consumes `FitzLCD-<version>-windows.zip`; the installer is an *additional* asset, not a replacement. |

## Files

- `packaging/fitzlcd.iss` — the Inno script. Version injected via
  `/DMyAppVersion=`; falls back to `0.0.0-dev` for local compiles.
- `.github/workflows/publish.yml` — after the PyInstaller build: compile the
  installer with `iscc`, upload it in the artifact, attach it to the Release.

## Release flow after this change

`version` → `build` (PyInstaller → zip **+ `iscc` → Setup.exe**, both uploaded)
→ `release` (download artifact, attach `FitzLCD-<v>-windows.zip` **and
`FitzLCD-<v>-Setup.exe`**).

Job order (version before build) is unchanged and still load-bearing.

## Local build

```powershell
.\.venv\Scripts\python.exe -m PyInstaller packaging\fitzlcd.spec --noconfirm
iscc packaging\fitzlcd.iss /DMyAppVersion=1.1.0
# -> dist\installer\FitzLCD-1.1.0-Setup.exe
```

## Out of scope

- Code signing (no cert available; SmartScreen warning remains).
- winget / Chocolatey manifests.
- Auto-switching the self-updater to consume the installer.
