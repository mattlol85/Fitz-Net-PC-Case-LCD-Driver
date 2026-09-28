"""Undo and redo for scene edits.

Every edit in the GUI saves immediately, so there is no "Cancel" to fall back
on; this is the safety net instead. History is a stack of whole-scene JSON
snapshots - scenes are small, and a snapshot can't drift out of step with the
model the way per-field inverse operations could.
"""

from __future__ import annotations

import copy
import time
from typing import Any

#: Consecutive edits to the same field within this window are one undo step,
#: so dragging a slider or typing a word doesn't need fifty presses of Ctrl+Z.
COALESCE_SECONDS = 1.5


class SceneHistory:
    def __init__(self, limit: int = 100) -> None:
        self.limit = limit
        self._undo: list[dict[str, Any]] = []
        self._redo: list[dict[str, Any]] = []
        self._last_key: str | None = None
        self._last_time = 0.0

    @property
    def can_undo(self) -> bool:
        return bool(self._undo)

    @property
    def can_redo(self) -> bool:
        return bool(self._redo)

    def reset(self) -> None:
        self._undo.clear()
        self._redo.clear()
        self._last_key = None

    def record(self, snapshot: dict[str, Any], key: str | None = None) -> None:
        """Remember the scene as it was *before* an edit.

        ``key`` names the kind of edit; repeats of the same key in quick
        succession collapse into the step already recorded.
        """
        now = time.monotonic()
        if key is not None and key == self._last_key and now - self._last_time < COALESCE_SECONDS:
            self._last_time = now
            return
        self._undo.append(copy.deepcopy(snapshot))
        del self._undo[: -self.limit]
        self._redo.clear()
        self._last_key = key
        self._last_time = now

    def undo(self, current: dict[str, Any]) -> dict[str, Any] | None:
        if not self._undo:
            return None
        self._redo.append(copy.deepcopy(current))
        self._last_key = None
        return self._undo.pop()

    def redo(self, current: dict[str, Any]) -> dict[str, Any] | None:
        if not self._redo:
            return None
        self._undo.append(copy.deepcopy(current))
        self._last_key = None
        return self._redo.pop()
