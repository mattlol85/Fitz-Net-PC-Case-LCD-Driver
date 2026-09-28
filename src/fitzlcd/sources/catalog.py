"""Human names for the dotted metric namespace.

Scenes store raw keys like ``cpu.load`` - they are stable and hand-editable -
but a person choosing what a gauge shows should see "Processor usage". This is
the one place that maps the two. It is descriptive only: a key missing from
here still works everywhere, it is just shown as itself.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

#: Group order in pickers, most commonly wanted first.
GROUPS = (
    "Processor",
    "Graphics",
    "Memory & storage",
    "Network",
    "Time",
    "Claude",
    "Claude usage limits",
    "Counter-Strike 2",
    "Other",
)

_PREFIX_GROUPS = {
    "cpu": "Processor",
    "gpu": "Graphics",
    "mem": "Memory & storage",
    "disk": "Memory & storage",
    "net": "Network",
    "time": "Time",
    "claude.limits": "Claude usage limits",
    "claude": "Claude",
    "cs2": "Counter-Strike 2",
}


@dataclass(frozen=True)
class MetricInfo:
    key: str
    label: str
    group: str
    #: Python format spec used when the reading is inserted into text.
    fmt: str = ""
    #: Suffix shown after the value, e.g. "%" or " °C".
    unit: str = ""
    #: Whether a bar/ring can show it (a number with a natural 0-100 range).
    percent: bool = False

    @property
    def token(self) -> str:
        """What "Insert live value" puts into a text field."""
        spec = f":{self.fmt}" if self.fmt else ""
        return f"{{{self.key}{spec}}}{self.unit}"

    def format(self, value: Any) -> str:
        if value is None:
            return "—"
        try:
            return f"{value:{self.fmt}}{self.unit}" if self.fmt else f"{value}{self.unit}"
        except (TypeError, ValueError):
            return str(value)


def _m(key, label, fmt="", unit="", percent=False) -> MetricInfo:
    return MetricInfo(key, label, group_for(key), fmt, unit, percent)


def group_for(key: str) -> str:
    for prefix in sorted(_PREFIX_GROUPS, key=len, reverse=True):
        if key == prefix or key.startswith(prefix + "."):
            return _PREFIX_GROUPS[prefix]
    return "Other"


KNOWN: dict[str, MetricInfo] = {
    m.key: m
    for m in (
        _m("cpu.load", "Processor usage", ".0f", "%", True),
        _m("cpu.temp", "Processor temperature", ".0f", "°C"),
        _m("cpu.freq", "Processor speed", ".0f", " MHz"),
        _m("cpu.cores", "Processor threads"),
        _m("gpu.load", "Graphics usage", ".0f", "%", True),
        _m("gpu.temp", "Graphics temperature", ".0f", "°C"),
        _m("gpu.power", "Graphics power draw", ".0f", " W"),
        _m("gpu.vram_pct", "Graphics memory used", ".0f", "%", True),
        _m("gpu.vram_load", "Graphics memory activity", ".0f", "%", True),
        _m("gpu.vram_used_gb", "Graphics memory used (GB)", ".1f", " GB"),
        _m("gpu.vram_total_gb", "Graphics memory total (GB)", ".0f", " GB"),
        _m("gpu.name", "Graphics card name"),
        _m("mem.used_pct", "Memory used", ".0f", "%", True),
        _m("mem.used_gb", "Memory used (GB)", ".1f", " GB"),
        _m("mem.total_gb", "Memory total (GB)", ".0f", " GB"),
        _m("disk.used_pct", "Disk space used", ".0f", "%", True),
        _m("net.down", "Download speed", ".1f", " MB/s"),
        _m("net.up", "Upload speed", ".1f", " MB/s"),
        _m("time.now", "Time"),
        _m("time.date", "Date"),
        _m("time.uptime", "Time since startup"),
        _m("claude.today.tokens", "Tokens today", ",.0f"),
        _m("claude.today.messages", "Messages today"),
        _m("claude.today.cost_usd", "Estimated cost today", ",.2f"),
        _m("claude.week.tokens", "Tokens this week", ",.0f"),
        _m("claude.week.cost_usd", "Estimated cost this week", ",.2f"),
        _m("claude.total.tokens", "Tokens all time", ",.0f"),
        _m("claude.model", "Current model"),
        _m("claude.limits.session.pct", "Session limit used", ".0f", "%", True),
        _m("claude.limits.session.resets_text", "Session limit resets in"),
        _m("claude.limits.week.pct", "Weekly limit used", ".0f", "%", True),
        _m("claude.limits.week.resets_text", "Weekly limit resets in"),
        _m("cs2.player.state.health", "Health", ".0f", "", True),
        _m("cs2.player.state.armor", "Armor", ".0f", "", True),
        _m("cs2.player.match_stats.kills", "Kills"),
        _m("cs2.player.match_stats.deaths", "Deaths"),
        _m("cs2.player.active_weapon.name", "Weapon"),
        _m("cs2.map.name", "Map"),
        _m("cs2.map.round", "Round"),
    )
}


def describe(key: str) -> MetricInfo:
    """Friendly info for any key; unknown keys get a label derived from the key."""
    if key in KNOWN:
        return KNOWN[key]
    return MetricInfo(key, key, group_for(key))


def catalog(live_keys: Iterable[str] = ()) -> list[MetricInfo]:
    """Every metric worth offering: the known ones plus whatever is live now.

    Sorted by group then label, so a picker can insert a header per group.
    """
    infos = dict(KNOWN)
    for key in live_keys:
        infos.setdefault(key, describe(key))
    order = {g: i for i, g in enumerate(GROUPS)}
    return sorted(
        infos.values(),
        key=lambda m: (order.get(m.group, len(GROUPS)), m.key not in KNOWN, m.label.lower()),
    )
