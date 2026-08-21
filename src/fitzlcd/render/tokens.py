"""``{metric}`` substitution for text layers.

``str.format`` cannot be used directly: metric names contain dots
(``{cpu.load}``) which ``format`` interprets as attribute access. Templates are
therefore expanded by hand, and an unknown or not-yet-available metric renders
as a placeholder instead of raising - a missing sensor must never take down the
render loop.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

#: Shown in place of a metric that has no value (no sensor, not polled yet).
PLACEHOLDER = "—"  # em dash

_TOKEN = re.compile(r"\{([a-zA-Z_][\w.]*)(?::([^}]*))?\}")


def find_tokens(template: str) -> set[str]:
    """Return the metric names referenced by ``template``."""
    return {match.group(1) for match in _TOKEN.finditer(template)}


def _format_one(value: Any, spec: str | None) -> str:
    if value is None:
        return PLACEHOLDER
    if not spec:
        return str(value)
    try:
        return format(value, spec)
    except (ValueError, TypeError):
        # e.g. a numeric spec applied to a string metric -- show the raw value
        # rather than blowing up mid-frame.
        return str(value)


def expand(template: str, metrics: Mapping[str, Any] | None = None) -> str:
    """Substitute ``{metric}`` and ``{metric:spec}`` references in ``template``."""
    if not template:
        return ""
    values = metrics or {}

    def replace(match: re.Match[str]) -> str:
        return _format_one(values.get(match.group(1)), match.group(2))

    return _TOKEN.sub(replace, template)


def is_dynamic(template: str) -> bool:
    """True if the template depends on live values and must re-render each frame."""
    return bool(_TOKEN.search(template or ""))
