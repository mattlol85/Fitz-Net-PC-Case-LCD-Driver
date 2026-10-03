"""Counter-Strike 2 Game State Integration (GSI) source.

Unlike the polled providers in :mod:`fitzlcd.sources.stats`, CS2 pushes JSON
payloads to an HTTP endpoint of our choosing while a match is in progress -
there is nothing to poll. :class:`Cs2GsiProvider` runs its own tiny HTTP
server on a background thread, keeps the latest payload (flattened into the
usual dotted-key metric namespace) behind a lock, and hands it back
instantly from :meth:`read`, so it composes into :class:`StatsRegistry
<fitzlcd.sources.stats.StatsRegistry>` exactly like any other
:class:`~fitzlcd.sources.stats.MetricProvider`.

Metric names are dotted and stable, mirroring ``stats.py``'s convention:

    cs2.connected  cs2.last_seen_seconds_ago  cs2.status_text
    cs2.map.name  cs2.map.phase  cs2.map.round  cs2.map.team_ct.score  ...
    cs2.round.phase  cs2.round.win_team  cs2.round.bomb
    cs2.player.state.health  cs2.player.state.armor  cs2.player.state.money ...
    cs2.player.active_weapon.name  cs2.player.active_weapon.ammo_clip ...
    cs2.player.match_stats.kills  cs2.player.match_stats.deaths ...
    cs2.player.hit_events  cs2.player.last_hit_amount  cs2.player.last_hit_kind

See ``GSI_INTEGRATION.md`` (in the CounterStrikePoc repo) for the payload
shape this is built against.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import TYPE_CHECKING, Any

from fitzlcd.sources.stats import MetricProvider

if TYPE_CHECKING:
    from fitzlcd.config import AppConfig

log = logging.getLogger(__name__)

#: Fields whose loss (a positive delta) counts as a "hit" worth visualising.
_HIT_FIELDS = ("health", "armor")

#: GSI payloads are a few KB; anything far larger is not CS2 and is not read.
_MAX_BODY_BYTES = 1_000_000


def _as_float(value: object) -> float | None:
    try:
        return float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


class _Cs2GsiHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - BaseHTTPRequestHandler's naming
        provider: Cs2GsiProvider = self.server.provider  # type: ignore[attr-defined]
        try:
            length = int(self.headers.get("Content-Length", 0))
        except (TypeError, ValueError):
            length = -1
        if not 0 <= length <= _MAX_BODY_BYTES:
            self.send_response(413 if length > 0 else 400)
            self.end_headers()
            return
        try:
            body = self.rfile.read(length) if length else b""
            payload = json.loads(body) if body else {}
        except (OSError, ValueError):  # socket error, bad JSON or bad UTF-8
            self.send_response(400)
            self.end_headers()
            return
        if not isinstance(payload, dict):
            self.send_response(400)
            self.end_headers()
            return

        auth = payload.get("auth")
        token = auth.get("token") if isinstance(auth, dict) else None
        if provider._token and token != provider._token:
            self.send_response(401)
            self.end_headers()
            return

        provider._ingest(payload)
        self.send_response(200)
        self.end_headers()

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A002
        log.debug("cs2 gsi: " + format, *args)  # noqa: G003


class Cs2GsiProvider(MetricProvider):
    """Listens for CS2 GSI POSTs and publishes them as flat ``cs2.*`` metrics."""

    name = "cs2-gsi"

    def __init__(
        self,
        port: int = 13001,
        path: str = "/gsi",
        token: str = "",
        heartbeat: float = 8.0,
        hit_window: float = 12.0,
        max_hit_events: int = 64,
        host: str = "127.0.0.1",
    ) -> None:
        self._path = path
        self._token = token
        self._heartbeat = heartbeat
        self._hit_window = hit_window

        self._lock = threading.Lock()
        self._snapshot: dict[str, Any] = {}
        self._hit_events: deque[dict[str, Any]] = deque(maxlen=max_hit_events)
        self._last_post_monotonic: float | None = None
        self._last_hit_t: float | None = None
        self._round_phase_prev: str | None = None

        self._server = ThreadingHTTPServer((host, port), _Cs2GsiHandler)
        self._server.provider = self  # type: ignore[attr-defined]
        self._server.timeout = 0.5
        self._thread = threading.Thread(
            target=self._server.serve_forever, name="fitzlcd-cs2gsi", daemon=True
        )
        self._thread.start()

    # ------------------------------------------------------------- ingest

    def _ingest(self, payload: dict[str, Any]) -> None:
        try:
            now = time.monotonic()
            values = _flatten(payload)
            self._update_hit_events(payload, now)
            values["cs2.player.hit_events"] = list(self._hit_events)
            if self._hit_events:
                last = self._hit_events[-1]
                values["cs2.player.last_hit_amount"] = last["amount"]
                values["cs2.player.last_hit_kind"] = last["kind"]

            with self._lock:
                self._snapshot = values
                self._last_post_monotonic = now
        except Exception as exc:  # noqa: BLE001 - a malformed payload must never wedge the listener
            log.debug("cs2 gsi ingest failed: %s", exc)

    def _update_hit_events(self, payload: dict[str, Any], now: float) -> None:
        round_phase = payload.get("round", {}).get("phase")

        # Freezetime resets health/armor; treat that edge as the start of a
        # new round and drop anything still lingering from the last one.
        if round_phase == "freezetime" and self._round_phase_prev != "freezetime":
            self._hit_events.clear()
        self._round_phase_prev = round_phase

        if round_phase == "live":
            prev_state = payload.get("previously", {}).get("player", {}).get("state", {})
            curr_state = payload.get("player", {}).get("state", {})
            for field_name in _HIT_FIELDS:
                if field_name not in prev_state:
                    continue
                prev_value = _as_float(prev_state[field_name])
                curr_value = _as_float(curr_state.get(field_name))
                if prev_value is None or curr_value is None:
                    continue
                delta = prev_value - curr_value
                if delta > 0:  # a loss, never a heal/regen/buy
                    self._hit_events.append({"t": now, "amount": delta, "kind": field_name})

        while self._hit_events and now - self._hit_events[0]["t"] > self._hit_window:
            self._hit_events.popleft()

    # -------------------------------------------------------------- read

    def read(self) -> dict[str, Any]:
        with self._lock:
            snapshot = dict(self._snapshot)
            last_post = self._last_post_monotonic

        if last_post is None:
            return {"cs2.connected": False, "cs2.status_text": "WAITING FOR MATCH…"}

        now = time.monotonic()
        age = now - last_post
        connected = age <= self._heartbeat
        snapshot["cs2.last_seen_seconds_ago"] = age
        snapshot["cs2.connected"] = connected
        snapshot["cs2.status_text"] = "" if connected else "WAITING FOR MATCH…"

        hit_events = snapshot.get("cs2.player.hit_events") or []
        if hit_events:
            snapshot["cs2.player.last_hit_seconds_ago"] = now - hit_events[-1]["t"]

        return snapshot

    def close(self) -> None:
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception as exc:  # noqa: BLE001 - shutdown must never raise
            log.debug("cs2 gsi shutdown failed: %s", exc)
        if self._thread.is_alive():
            self._thread.join(timeout=2.0)


def _flatten(payload: dict[str, Any]) -> dict[str, Any]:
    """Project one GSI payload into the flat ``cs2.*`` metric namespace."""
    values: dict[str, Any] = {}

    provider = payload.get("provider", {})
    values["cs2.provider.steamid"] = provider.get("steamid")
    values["cs2.provider.timestamp"] = provider.get("timestamp")

    map_ = payload.get("map", {})
    values["cs2.map.name"] = map_.get("name")
    values["cs2.map.mode"] = map_.get("mode")
    values["cs2.map.phase"] = map_.get("phase")
    values["cs2.map.round"] = map_.get("round")
    team_ct = map_.get("team_ct", {})
    team_t = map_.get("team_t", {})
    values["cs2.map.team_ct.score"] = team_ct.get("score")
    values["cs2.map.team_ct.name"] = team_ct.get("name")
    values["cs2.map.team_t.score"] = team_t.get("score")
    values["cs2.map.team_t.name"] = team_t.get("name")

    round_ = payload.get("round", {})
    values["cs2.round.phase"] = round_.get("phase")
    values["cs2.round.win_team"] = round_.get("win_team")
    values["cs2.round.bomb"] = round_.get("bomb")

    player = payload.get("player", {})
    values["cs2.player.name"] = player.get("name")
    values["cs2.player.team"] = player.get("team")

    state = player.get("state", {})
    values["cs2.player.state.health"] = state.get("health")
    values["cs2.player.state.armor"] = state.get("armor")
    values["cs2.player.state.helmet"] = state.get("helmet", False)
    values["cs2.player.helmet_label"] = "HELMET" if state.get("helmet") else ""
    values["cs2.player.state.flashed"] = state.get("flashed", 0)
    values["cs2.player.flashed_label"] = "FLASHED" if state.get("flashed", 0) > 0 else ""
    values["cs2.player.state.burning"] = state.get("burning", 0)
    values["cs2.player.burning_label"] = "BURNING" if state.get("burning", 0) > 0 else ""
    values["cs2.player.state.money"] = state.get("money")
    values["cs2.player.state.equip_value"] = state.get("equip_value")
    values["cs2.player.state.round_kills"] = state.get("round_kills")
    values["cs2.player.state.round_killhs"] = state.get("round_killhs")

    weapons = player.get("weapons", {})
    values["cs2.player.weapons"] = weapons
    active = next((w for w in weapons.values() if w.get("state") == "active"), None)
    values["cs2.player.active_weapon.name"] = active.get("name") if active else None
    values["cs2.player.active_weapon.type"] = active.get("type") if active else None
    values["cs2.player.active_weapon.ammo_clip"] = active.get("ammo_clip") if active else None
    values["cs2.player.active_weapon.ammo_clip_max"] = (
        active.get("ammo_clip_max") if active else None
    )
    values["cs2.player.active_weapon.ammo_reserve"] = active.get("ammo_reserve") if active else None

    match_stats = player.get("match_stats", {})
    values["cs2.player.match_stats.kills"] = match_stats.get("kills")
    values["cs2.player.match_stats.deaths"] = match_stats.get("deaths")
    values["cs2.player.match_stats.assists"] = match_stats.get("assists")
    values["cs2.player.match_stats.mvps"] = match_stats.get("mvps")
    values["cs2.player.match_stats.score"] = match_stats.get("score")

    bomb = payload.get("bomb", {})
    values["cs2.bomb.state"] = bomb.get("state")
    values["cs2.bomb.position"] = bomb.get("position")

    countdowns = payload.get("phase_countdowns", {})
    values["cs2.phase.phase"] = countdowns.get("phase")
    seconds_left = _as_float(countdowns.get("phase_ends_in"))
    values["cs2.phase.seconds_left"] = seconds_left

    allplayers = payload.get("allplayers")
    if allplayers:
        values["cs2.allplayers.count"] = len(allplayers)
        values["cs2.allplayers.alive_ct"] = sum(
            1
            for p in allplayers.values()
            if p.get("team") == "CT"
            and _as_float(p.get("state", {}).get("health")) not in (None, 0)
        )
        values["cs2.allplayers.alive_t"] = sum(
            1
            for p in allplayers.values()
            if p.get("team") == "T" and _as_float(p.get("state", {}).get("health")) not in (None, 0)
        )

    return values


_CFG_TEMPLATE = """\
"FitzLCD GSI"
{{
    "uri"       "http://127.0.0.1:{port}{path}"
    "timeout"   "5.0"
    "buffer"    "0.1"
    "throttle"  "0.1"
    "heartbeat" "30.0"
    "auth"
    {{
        "token" "{token}"
    }}
    "data"
    {{
        "provider"               "1"
        "map"                    "1"
        "round"                  "1"
        "player_id"              "1"
        "player_state"           "1"
        "player_weapons"         "1"
        "player_match_stats"     "1"
        "bomb"                   "1"
        "phase_countdowns"       "1"
        "allplayers_id"          "1"
        "allplayers_state"       "1"
        "allplayers_match_stats" "1"
    }}
}}
"""


def write_cs2_gsi_cfg(config: AppConfig, path: str = "/gsi") -> Path:
    """Write the ready-made GSI ``.cfg`` file next to the app config as a staging copy.

    Call :func:`install_cs2_gsi_cfg` to write directly into CS2's cfg folder.
    """
    from fitzlcd.config import app_dir

    target = app_dir() / "gamestate_integration_fitzlcd.cfg"
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            _CFG_TEMPLATE.format(port=config.cs2_gsi_port, path=path, token=config.cs2_gsi_token),
            encoding="utf-8",
        )
    except OSError as exc:  # a convenience copy; startup must not depend on it
        log.warning("could not write the CS2 GSI staging cfg to %s: %s", target, exc)
    return target


def find_cs2_cfg_dir() -> Path | None:
    """Return CS2's ``game/csgo/cfg`` directory if it can be located, else ``None``."""
    cs2_relative = Path("steamapps/common/Counter-Strike Global Offensive/game/csgo/cfg")
    for lib in _steam_library_paths():
        candidate = lib / cs2_relative
        if candidate.is_dir():
            return candidate
    return None


def cs2_gsi_cfg_installed() -> bool:
    """Return ``True`` if ``gamestate_integration_fitzlcd.cfg`` exists in CS2's cfg folder."""
    cs2_cfg_dir = find_cs2_cfg_dir()
    if cs2_cfg_dir is None:
        return False
    return (cs2_cfg_dir / "gamestate_integration_fitzlcd.cfg").is_file()


def install_cs2_gsi_cfg(config: AppConfig, path: str = "/gsi") -> Path:
    """Write the GSI cfg directly into CS2's ``game/csgo/cfg`` folder.

    Raises :exc:`FileNotFoundError` if CS2's cfg folder cannot be found
    automatically — call :func:`find_cs2_cfg_dir` to check first.
    """
    cs2_cfg_dir = find_cs2_cfg_dir()
    if cs2_cfg_dir is None:
        raise FileNotFoundError(
            "Could not locate CS2's cfg folder. Install Steam and CS2, or copy "
            "gamestate_integration_fitzlcd.cfg manually."
        )
    target = cs2_cfg_dir / "gamestate_integration_fitzlcd.cfg"
    target.write_text(
        _CFG_TEMPLATE.format(port=config.cs2_gsi_port, path=path, token=config.cs2_gsi_token),
        encoding="utf-8",
    )
    return target


def _steam_library_paths() -> list[Path]:
    """Return all Steam library root paths to search for CS2."""
    roots: list[Path] = []

    reg_path = _steam_install_path_from_registry()
    if reg_path:
        roots.append(reg_path)

    for candidate in [
        Path(os.environ.get("PROGRAMFILES(X86)", r"C:\Program Files (x86)")) / "Steam",
        Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "Steam",
    ]:
        if candidate.is_dir() and candidate not in roots:
            roots.append(candidate)

    all_roots = list(roots)
    for root in roots:
        vdf = root / "steamapps" / "libraryfolders.vdf"
        if vdf.exists():
            for extra in _parse_library_paths(vdf):
                if extra not in all_roots:
                    all_roots.append(extra)

    return all_roots


def _steam_install_path_from_registry() -> Path | None:
    try:
        import winreg  # noqa: PLC0415 - Windows-only

        for hive, subkey in [
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Valve\Steam"),
            (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Valve\Steam"),
            (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Valve\Steam"),
        ]:
            try:
                key = winreg.OpenKey(hive, subkey)
                value, _ = winreg.QueryValueEx(key, "InstallPath")
                winreg.CloseKey(key)
                p = Path(value)
                if p.is_dir():
                    return p
            except OSError:
                continue
    except ImportError:
        pass
    return None


def _parse_library_paths(vdf_path: Path) -> list[Path]:
    """Extract Steam library root paths from ``libraryfolders.vdf``."""
    paths: list[Path] = []
    try:
        text = vdf_path.read_text(encoding="utf-8", errors="replace")
        for line in text.splitlines():
            parts = [p for p in line.strip().split('"') if p.strip()]
            if len(parts) >= 2 and parts[0].lower() == "path":
                p = Path(parts[1])
                if p.is_dir():
                    paths.append(p)
    except Exception:  # noqa: BLE001 - malformed VDF must never crash startup
        pass
    return paths
