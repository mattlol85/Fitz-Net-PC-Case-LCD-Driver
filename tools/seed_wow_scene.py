"""One-shot installer for the WoW scenes (and, optionally, the addon).

`SceneLibrary.ensure_defaults()` only seeds `DEFAULT_SCENES` into an empty
scenes directory, so an existing install never picks up a newly added default
scene on its own. Run this once to add every WoW scene (WoW Forever, WoW Unit
Frame, WoW Party Map) alongside whatever scenes are already there:

    python tools/seed_wow_scene.py            # the scenes only
    python tools/seed_wow_scene.py --addon    # and copy the addon into the game

The addon can also be installed from Settings > Integrations in the app.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fitzlcd.config import AppConfig, scenes_dir  # noqa: E402
from fitzlcd.scenes_builtin import DEFAULT_SCENES  # noqa: E402
from fitzlcd.sources.wow import install  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--addon", action="store_true", help="also install the addon into the game")
    args = ap.parse_args(argv)

    for key, scene in DEFAULT_SCENES.items():
        if not key.startswith("wow-"):
            continue
        scene_path = scenes_dir() / f"{key}.json"
        scene_path.parent.mkdir(parents=True, exist_ok=True)
        scene_path.write_text(json.dumps(scene, indent=2), encoding="utf-8")
        print(f"{scene['name']} scene written to {scene_path}")
    print("Other scenes were not touched.")

    if args.addon:
        try:
            target = install.install_addon(AppConfig.load())
        except OSError as exc:
            print(f"Could not install the addon: {exc}", file=sys.stderr)
            return 1
        print(f"Addon installed to {target}. Type /reload in game.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
