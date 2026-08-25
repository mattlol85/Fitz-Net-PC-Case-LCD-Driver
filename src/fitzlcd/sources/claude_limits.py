"""Claude subscription rate-limit windows, from the endpoint ``/usage`` itself uses.

:mod:`fitzlcd.sources.claude_usage` answers "how much have I used" by adding up
local transcripts. It cannot answer "am I near my limit", because the limits are
enforced server-side and never appear in the transcripts - the only trace is a
``quotaLimits`` blob attached to an *error* record once you have already been
blocked, carrying a reset time but no ceiling and no percentage.

So this provider asks the backend directly:

    GET https://api.anthropic.com/api/oauth/usage

which returns the same five-hour and seven-day utilisation percentages that
``/usage`` displays. It is a metadata call, not inference, so it bills no tokens.

.. warning::
   This endpoint is **undocumented**. Its path, headers and response shape can
   change without notice. Every field access here is defensive and any failure
   degrades to "serve the cached value and say so" rather than a broken scene.

Metric names, dotted like the rest of ``sources/*``:

    claude.limits.session.pct  .resets_at  .resets_in  .resets_text
    claude.limits.week.pct     .resets_at  .resets_in  .resets_text
    claude.limits.week_opus.pct  claude.limits.week_sonnet.pct
    claude.limits.extra.enabled  .used_credits  .pct
    claude.limits.age_seconds  claude.limits.status_text  claude.limits.ok

Not staying under the rate limit is the main way this goes wrong. See
:data:`_MIN_POLL_SECONDS` and :data:`_BACKOFF_SECONDS`, and note that the
``User-Agent`` header is not cosmetic: without ``claude-code/<version>`` the
endpoint drops you into an aggressively throttled bucket that 429s at any
polling interval and does not recover.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import os
import threading
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fitzlcd.sources.stats import MetricProvider

log = logging.getLogger(__name__)

USAGE_URL = "https://api.anthropic.com/api/oauth/usage"
OAUTH_BETA = "oauth-2025-04-20"

#: Utilisation moves slowly; polling faster buys nothing and risks the bucket.
_MIN_POLL_SECONDS = 180.0
#: 3 -> 6 -> 12 -> 15 minutes, then held, until a success resets it.
_BACKOFF_SECONDS = (180.0, 360.0, 720.0, 900.0)
#: Past this age a cached reading is labelled stale rather than shown as current.
_STALE_AFTER_SECONDS = 900.0
#: Only used if the real client version cannot be discovered locally.
_FALLBACK_VERSION = "2.1.0"

#: A fetch returns ``(status, body)``. Injected in tests so they never touch the network.
Fetch = Callable[[str, dict], "tuple[int, str]"]


def _claude_dir() -> Path:
    override = os.environ.get("CLAUDE_CONFIG_DIR")
    return Path(override) if override else Path.home() / ".claude"


def _http_get(url: str, headers: dict) -> tuple[int, str]:
    request = urllib.request.Request(url, method="GET", headers=headers)  # noqa: S310 - fixed https URL
    try:
        with urllib.request.urlopen(request, timeout=15) as response:  # noqa: S310
            return response.status, response.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as exc:
        # A 429 body is still worth reading; the caller decides what to do.
        return exc.code, exc.read().decode("utf-8", "replace")


def _humanise(seconds: float) -> str:
    if seconds <= 0:
        return "now"
    minutes = int(seconds // 60)
    if minutes < 1:
        return "<1m"
    if minutes < 60:
        return f"{minutes}m"
    return f"{minutes // 60}h {minutes % 60:02d}m"


def _parse_iso(value: object) -> float | None:
    """ISO 8601 (with ``Z`` or an offset) to a POSIX timestamp."""
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=dt.UTC)
    return parsed.timestamp()


def detect_client_version(root: Path | None = None) -> str:
    """Best-effort Claude Code version, read from the newest local transcript.

    The ``User-Agent`` must look like the real client, and transcripts already
    record the version that wrote them, so this avoids hard-coding a number that
    silently goes stale.
    """
    projects = (root or _claude_dir()) / "projects"
    newest: tuple[float, Path] | None = None
    try:
        for path in projects.glob("*/*.jsonl"):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            if newest is None or mtime > newest[0]:
                newest = (mtime, path)
    except OSError:
        return _FALLBACK_VERSION
    if newest is None:
        return _FALLBACK_VERSION

    # Only the tail is needed, and these files reach tens of MB.
    try:
        with newest[1].open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            back = min(handle.tell(), 65536)
            handle.seek(-back, os.SEEK_END)
            tail = handle.read().decode("utf-8", "replace")
    except OSError:
        return _FALLBACK_VERSION

    for line in reversed(tail.splitlines()):
        if '"version"' not in line:
            continue
        try:
            version = json.loads(line).get("version")
        except json.JSONDecodeError:
            continue
        if isinstance(version, str) and version:
            return version
    return _FALLBACK_VERSION


def read_access_token(root: Path | None = None) -> tuple[str | None, float | None]:
    """Return ``(token, expires_at_epoch_seconds)`` without ever logging the token.

    macOS keeps this in the Keychain instead of a file; there we simply find
    nothing and report "not signed in" rather than failing.
    """
    env = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN")
    if env:
        return env, None

    path = (root or _claude_dir()) / ".credentials.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None, None

    oauth = data.get("claudeAiOauth")
    if not isinstance(oauth, dict):
        return None, None
    token = oauth.get("accessToken")
    if not isinstance(token, str) or not token:
        return None, None

    expires_at = oauth.get("expiresAt")
    if isinstance(expires_at, (int, float)):
        # Claude Code stores milliseconds; tolerate seconds just in case.
        seconds = float(expires_at) / 1000.0 if expires_at > 1e11 else float(expires_at)
    else:
        seconds = None
    return token, seconds


class ClaudeLimitsProvider(MetricProvider):
    """Publishes ``claude.limits.*`` from the OAuth usage endpoint, gently."""

    name = "claude-limits"

    def __init__(
        self,
        root: Path | None = None,
        poll_seconds: float = 300.0,
        cache_path: Path | None = None,
        fetch: Fetch | None = None,
        version: str | None = None,
    ) -> None:
        self._root = root or _claude_dir()
        self._poll_seconds = max(_MIN_POLL_SECONDS, float(poll_seconds))
        self._fetch = fetch or _http_get
        self._version = version

        self._lock = threading.Lock()
        self._payload: dict[str, Any] | None = None
        self._fetched_at: float | None = None  # wall clock, so it survives a restart
        self._next_attempt = 0.0  # monotonic
        self._backoff_index = -1
        self._status = ""

        self._cache_path = cache_path if cache_path is not None else self._default_cache_path()
        self._load_cache()

    # ----------------------------------------------------------------- cache

    def _default_cache_path(self) -> Path | None:
        try:
            from fitzlcd.config import app_dir  # noqa: PLC0415 - avoids an import cycle

            return app_dir() / "claude_limits_cache.json"
        except Exception:  # noqa: BLE001 - a cache is a nicety, never a requirement
            return None

    def _load_cache(self) -> None:
        """Warm from disk so a restart does not immediately spend a request."""
        if self._cache_path is None or not self._cache_path.exists():
            return
        try:
            cached = json.loads(self._cache_path.read_text(encoding="utf-8"))
            payload = cached.get("payload")
            fetched_at = cached.get("fetched_at")
            if isinstance(payload, dict) and isinstance(fetched_at, (int, float)):
                self._payload = payload
                self._fetched_at = float(fetched_at)
                # Resume the normal cadence from the cached reading's age.
                remaining = self._poll_seconds - (time.time() - float(fetched_at))
                self._next_attempt = time.monotonic() + max(0.0, remaining)
        except (OSError, json.JSONDecodeError, AttributeError) as exc:
            log.debug("claude limits: unusable cache at %s (%s)", self._cache_path, exc)

    def _save_cache(self) -> None:
        if self._cache_path is None:
            return
        try:
            self._cache_path.parent.mkdir(parents=True, exist_ok=True)
            self._cache_path.write_text(
                json.dumps({"payload": self._payload, "fetched_at": self._fetched_at}),
                encoding="utf-8",
            )
        except OSError as exc:
            log.debug("claude limits: could not write cache (%s)", exc)

    # ---------------------------------------------------------------- fetch

    @property
    def available(self) -> bool:
        token, _ = read_access_token(self._root)
        return token is not None

    def _headers(self, token: str) -> dict:
        if self._version is None:
            self._version = detect_client_version(self._root)
        return {
            "Authorization": f"Bearer {token}",
            "anthropic-beta": OAUTH_BETA,
            # Not cosmetic: without this the endpoint throttles relentlessly.
            "User-Agent": f"claude-code/{self._version}",
            "Content-Type": "application/json",
        }

    def _back_off(self) -> None:
        self._backoff_index = min(self._backoff_index + 1, len(_BACKOFF_SECONDS) - 1)
        delay = _BACKOFF_SECONDS[self._backoff_index]
        self._next_attempt = time.monotonic() + delay
        log.warning("claude limits: backing off %.0fs", delay)

    def _poll(self) -> None:
        token, expires_at = read_access_token(self._root)
        if token is None:
            self._status = "NOT SIGNED IN"
            self._next_attempt = time.monotonic() + self._poll_seconds
            return

        if expires_at is not None and expires_at <= time.time():
            # Deliberately no refresh: rotating the refresh token here could
            # invalidate the user's real Claude Code login. Claude Code renews it
            # during normal use and we re-read the file next tick.
            self._status = "STALE"
            self._next_attempt = time.monotonic() + self._poll_seconds
            log.debug("claude limits: access token expired; waiting for Claude Code to renew")
            return

        try:
            status, body = self._fetch(USAGE_URL, self._headers(token))
        except Exception as exc:  # noqa: BLE001 - network failures must never propagate
            log.debug("claude limits: fetch failed (%s)", exc)
            self._status = "OFFLINE"
            self._back_off()
            return

        if status == 429:
            self._status = "RATE LIMITED"
            self._back_off()
            return
        if status != 200:
            log.debug("claude limits: HTTP %s", status)
            self._status = "OFFLINE"
            self._back_off()
            return

        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self._status = "OFFLINE"
            self._back_off()
            return
        if not isinstance(payload, dict):
            self._status = "OFFLINE"
            self._back_off()
            return

        with self._lock:
            self._payload = payload
            self._fetched_at = time.time()
        self._status = ""
        self._backoff_index = -1
        self._next_attempt = time.monotonic() + self._poll_seconds
        self._save_cache()

    # ----------------------------------------------------------------- read

    def read(self) -> dict[str, Any]:
        if time.monotonic() >= self._next_attempt:
            self._poll()

        with self._lock:
            payload = self._payload
            fetched_at = self._fetched_at

        if payload is None:
            return {
                "claude.limits.ok": False,
                "claude.limits.status_text": self._status or "NO USAGE DATA",
            }

        values = _flatten(payload)
        age = time.time() - fetched_at if fetched_at is not None else None
        if age is not None:
            values["claude.limits.age_seconds"] = age

        status = self._status
        if not status and age is not None and age > _STALE_AFTER_SECONDS:
            status = "STALE"
        values["claude.limits.status_text"] = status
        values["claude.limits.ok"] = not status
        return values


def _window(values: dict[str, Any], prefix: str, block: object, now: float) -> None:
    """Project one ``{utilization, resets_at}`` block into ``claude.limits.<prefix>.*``.

    Absent rather than wrong: a window the account does not have (``seven_day_opus``
    is ``null`` on some plans) contributes no keys at all, so text layers show the
    placeholder instead of a confident zero.
    """
    if not isinstance(block, dict):
        return

    utilisation = block.get("utilization")
    if isinstance(utilisation, (int, float)):
        values[f"claude.limits.{prefix}.pct"] = float(utilisation)

    resets_at = _parse_iso(block.get("resets_at"))
    if resets_at is not None:
        values[f"claude.limits.{prefix}.resets_at"] = block.get("resets_at")
        remaining = resets_at - now
        values[f"claude.limits.{prefix}.resets_in"] = remaining
        values[f"claude.limits.{prefix}.resets_text"] = _humanise(remaining)


def _flatten(payload: dict[str, Any]) -> dict[str, Any]:
    now = time.time()
    values: dict[str, Any] = {}

    _window(values, "session", payload.get("five_hour"), now)
    _window(values, "week", payload.get("seven_day"), now)
    _window(values, "week_opus", payload.get("seven_day_opus"), now)
    _window(values, "week_sonnet", payload.get("seven_day_sonnet"), now)

    extra = payload.get("extra_usage")
    if isinstance(extra, dict):
        values["claude.limits.extra.enabled"] = bool(extra.get("is_enabled"))
        for key, metric in (
            ("used_credits", "claude.limits.extra.used_credits"),
            ("monthly_limit", "claude.limits.extra.monthly_limit"),
            ("utilization", "claude.limits.extra.pct"),
        ):
            value = extra.get(key)
            if isinstance(value, (int, float)):
                values[metric] = float(value)

    return values
