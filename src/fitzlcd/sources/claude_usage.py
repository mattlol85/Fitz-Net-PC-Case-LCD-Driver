"""Local Claude Code usage tracker.

Claude Code already writes every session's transcript to JSONL files under
``~/.claude/projects/**/*.jsonl`` (or ``$CLAUDE_CONFIG_DIR`` if set) - there is
no separate usage API to poll. Each assistant turn in those files carries a
``message.usage`` block with token counts, so :class:`ClaudeUsageProvider`
tails that tree instead of talking to a service.

Re-parsing every transcript on every tick would get expensive as history
grows, so a file is only reread when its mtime/size changes, and the whole
tree is only rescanned every :attr:`refresh_seconds` rather than on every
:meth:`read`.

Metric names, dotted like the rest of ``sources/*``:

    claude.today.tokens  claude.today.input_tokens  claude.today.output_tokens
    claude.today.cache_tokens  claude.today.cost_usd  claude.today.messages
    claude.today.sessions
    claude.week.tokens  claude.week.cost_usd
    claude.total.tokens  claude.total.cost_usd  claude.total.sessions
    claude.model  claude.last_active_seconds_ago  claude.status_text

Cost is a notional "if this were metered API usage" estimate from list
pricing below - most Claude Code usage is billed against a Pro/Max
subscription rather than per token, so treat ``*.cost_usd`` as a rough
sense of scale, not a bill.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from fitzlcd.sources.stats import MetricProvider

log = logging.getLogger(__name__)

#: (input, output, cache_write, cache_read) USD per 1M tokens, keyed by a
#: substring of the model id. First match wins; order matters.
_PRICING: tuple[tuple[str, tuple[float, float, float, float]], ...] = (
    ("opus", (15.0, 75.0, 18.75, 1.50)),
    ("sonnet", (3.0, 15.0, 3.75, 0.30)),
    ("haiku", (0.80, 4.0, 1.0, 0.08)),
)
_DEFAULT_PRICING = (3.0, 15.0, 3.75, 0.30)  # sonnet-tier, for unrecognised/new models


def _pricing_for(model: str) -> tuple[float, float, float, float]:
    lowered = (model or "").lower()
    for needle, price in _PRICING:
        if needle in lowered:
            return price
    return _DEFAULT_PRICING


def _default_claude_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    if override:
        return Path(override)
    return Path.home() / ".claude"


def _parse_epoch(value: object) -> float | None:
    if not isinstance(value, str):
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


@dataclass
class _DayTotals:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    cost_usd: float = 0.0
    messages: int = 0
    sessions: set[str] = field(default_factory=set)

    @property
    def total_tokens(self) -> int:
        return (
            self.input_tokens
            + self.output_tokens
            + self.cache_write_tokens
            + self.cache_read_tokens
        )

    def add(self, other: _DayTotals) -> None:
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cache_write_tokens += other.cache_write_tokens
        self.cache_read_tokens += other.cache_read_tokens
        self.cost_usd += other.cost_usd
        self.messages += other.messages
        self.sessions |= other.sessions


_FileMeta = tuple[dict[str, _DayTotals], "tuple[float, str] | None"]


def _ingest_file(path: Path) -> _FileMeta:
    """Parse one transcript into per-day totals, plus its latest (epoch, model)."""
    days: dict[str, _DayTotals] = {}
    last: tuple[float, str] | None = None

    try:
        text = path.read_text(encoding="utf-8", errors="replace")
    except OSError as exc:
        log.debug("claude usage: could not read %s: %s", path, exc)
        return days, last

    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            record = json.loads(line)
        except ValueError:
            continue
        if not isinstance(record, dict):
            continue

        message = record.get("message")
        if not isinstance(message, dict):
            continue
        usage = message.get("usage")
        if not isinstance(usage, dict):
            continue
        epoch = _parse_epoch(record.get("timestamp"))
        if epoch is None:
            continue

        try:
            input_tokens = int(usage.get("input_tokens") or 0)
            output_tokens = int(usage.get("output_tokens") or 0)
            cache_write = int(usage.get("cache_creation_input_tokens") or 0)
            cache_read = int(usage.get("cache_read_input_tokens") or 0)
            day = time.strftime("%Y-%m-%d", time.localtime(epoch))
        except (TypeError, ValueError, OverflowError, OSError):
            continue  # a corrupt record costs that record, not the whole file
        totals = days.setdefault(day, _DayTotals())

        totals.input_tokens += input_tokens
        totals.output_tokens += output_tokens
        totals.cache_write_tokens += cache_write
        totals.cache_read_tokens += cache_read
        totals.messages += 1

        session_id = record.get("sessionId") or record.get("session_id")
        if isinstance(session_id, str) and session_id:
            totals.sessions.add(session_id)

        model = message.get("model")
        model = model if isinstance(model, str) else ""
        p_in, p_out, p_cw, p_cr = _pricing_for(model)
        totals.cost_usd += (
            input_tokens * p_in + output_tokens * p_out + cache_write * p_cw + cache_read * p_cr
        ) / 1e6

        if last is None or epoch > last[0]:
            last = (epoch, model)

    return days, last


class ClaudeUsageProvider(MetricProvider):
    """Aggregates local Claude Code transcript usage into ``claude.*`` metrics."""

    name = "claude-usage"

    def __init__(self, root: Path | None = None, refresh_seconds: float = 20.0) -> None:
        self._root = root or _default_claude_dir()
        self._refresh_seconds = refresh_seconds

        self._lock = threading.Lock()
        self._file_state: dict[Path, tuple[float, int]] = {}
        self._file_meta: dict[Path, _FileMeta] = {}
        self._last_scan = 0.0
        self._last_event_epoch: float | None = None
        self._last_model: str = ""
        self._snapshot: dict[str, Any] = {}

        self._scan()

    @property
    def available(self) -> bool:
        return self._root.is_dir()

    def read(self) -> dict[str, Any]:
        if time.monotonic() - self._last_scan >= self._refresh_seconds:
            self._scan()

        with self._lock:
            snapshot = dict(self._snapshot)

        if self._last_event_epoch is None:
            snapshot["claude.status_text"] = "NO CLAUDE CODE ACTIVITY FOUND"
        else:
            snapshot["claude.last_active_seconds_ago"] = max(
                0.0, time.time() - self._last_event_epoch
            )
            snapshot["claude.status_text"] = ""
        return snapshot

    # ------------------------------------------------------------- scanning

    def _scan(self) -> None:
        self._last_scan = time.monotonic()
        projects_dir = self._root / "projects"
        if not projects_dir.is_dir():
            return

        changed = False
        seen: set[Path] = set()
        for path in projects_dir.glob("*/*.jsonl"):
            seen.add(path)
            try:
                stat = path.stat()
            except OSError:
                continue
            state = (stat.st_mtime, stat.st_size)
            if self._file_state.get(path) == state:
                continue
            self._file_state[path] = state
            self._file_meta[path] = _ingest_file(path)
            changed = True

        stale = [p for p in self._file_meta if p not in seen]
        for p in stale:
            del self._file_meta[p]
            del self._file_state[p]
        changed = changed or bool(stale)

        if changed:
            self._publish()

    def _publish(self) -> None:
        day_totals: dict[str, _DayTotals] = {}
        last_event: tuple[float, str] | None = None
        for days, last in self._file_meta.values():
            for day, totals in days.items():
                day_totals.setdefault(day, _DayTotals()).add(totals)
            if last is not None and (last_event is None or last[0] > last_event[0]):
                last_event = last

        today_key = date.today().isoformat()
        today = day_totals.get(today_key, _DayTotals())

        week = _DayTotals()
        all_time = _DayTotals()
        for day_key, totals in day_totals.items():
            all_time.add(totals)
            try:
                day_date = date.fromisoformat(day_key)
            except ValueError:
                continue
            if 0 <= (date.today() - day_date).days < 7:
                week.add(totals)

        model = last_event[1] if last_event is not None else self._last_model
        snapshot = {
            "claude.today.tokens": today.total_tokens,
            "claude.today.input_tokens": today.input_tokens,
            "claude.today.output_tokens": today.output_tokens,
            "claude.today.cache_tokens": today.cache_write_tokens + today.cache_read_tokens,
            "claude.today.cost_usd": today.cost_usd,
            "claude.today.messages": today.messages,
            "claude.today.sessions": len(today.sessions),
            "claude.week.tokens": week.total_tokens,
            "claude.week.cost_usd": week.cost_usd,
            "claude.total.tokens": all_time.total_tokens,
            "claude.total.cost_usd": all_time.cost_usd,
            "claude.total.sessions": len(all_time.sessions),
            "claude.model": model,
        }

        with self._lock:
            self._snapshot = snapshot
            self._last_model = model
            if last_event is not None:
                self._last_event_epoch = last_event[0]
