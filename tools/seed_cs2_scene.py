"""One-shot installer for the CS2 HUD scene and GSI config.

`SceneLibrary.ensure_defaults()` only seeds `DEFAULT_SCENES` into a
completely empty scenes directory, so an existing install (this one already
has Clock/Rig Stats/Wallpaper on disk) never picks up a newly added default
scene on its own. Run this once to add just the CS2 HUD scene alongside
whatever scenes are already there, and to write the ready-made
`gamestate_integration_fitzlcd.cfg` for CS2 itself.

    python tools/seed_cs2_scene.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fitzlcd.config import AppConfig, ensure_cs2_gsi_token, scenes_dir
from fitzlcd.scenes_builtin import DEFAULT_SCENES
from fitzlcd.sources.cs2gsi import write_cs2_gsi_cfg


def main() -> int:
    config = AppConfig.load()
    config = ensure_cs2_gsi_token(config)
    cfg_path = write_cs2_gsi_cfg(config)
    print(f"GSI config written to {cfg_path}")
    print(
        "Copy it into <Steam library>\\steamapps\\common\\Counter-Strike Global "
        "Offensive\\game\\csgo\\cfg\\ and restart CS2."
    )

    scene_path = scenes_dir() / "cs2-hud.json"
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    scene_path.write_text(json.dumps(DEFAULT_SCENES["cs2-hud"], indent=2), encoding="utf-8")
    print(f"CS2 HUD scene written to {scene_path}")
    print("Existing scenes were not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
