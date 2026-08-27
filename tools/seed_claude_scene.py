"""One-shot installer for the Claude Usage scene.

`SceneLibrary.ensure_defaults()` seeds `DEFAULT_SCENES` only into a completely
empty scenes directory, so an existing install never picks up a new or
redesigned default scene on its own. Run this to drop just the Claude Usage
scene alongside whatever scenes are already there.

**This overwrites `claude-usage.json` if one is already present.** Every other
scene is left alone.

    python tools/seed_claude_scene.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from fitzlcd.config import scenes_dir
from fitzlcd.scenes_builtin import DEFAULT_SCENES


def main() -> int:
    scene_path = scenes_dir() / "claude-usage.json"
    scene_path.parent.mkdir(parents=True, exist_ok=True)
    existed = scene_path.exists()
    scene_path.write_text(json.dumps(DEFAULT_SCENES["claude-usage"], indent=2), encoding="utf-8")
    print(f"Claude Usage scene {'overwritten at' if existed else 'written to'} {scene_path}")
    print("Existing scenes were not touched.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
