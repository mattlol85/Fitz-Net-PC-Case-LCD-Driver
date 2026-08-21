"""Driver registry and autodetection.

Adding support for another panel is one module implementing :class:`Panel` plus
one :func:`register` call - nothing above this package changes.
"""

from __future__ import annotations

import logging

from fitzlcd.panels.base import Panel, PanelHandle
from fitzlcd.panels.ds916.driver import DS916Panel
from fitzlcd.panels.virtual import VirtualPanel

log = logging.getLogger(__name__)

#: Real hardware drivers, in preference order.
_DRIVERS: list[type[Panel]] = [DS916Panel]

#: Software-only drivers, never returned by a plain autodetect.
_VIRTUAL_DRIVERS: list[type[Panel]] = [VirtualPanel]


def register(driver: type[Panel], *, virtual: bool = False) -> None:
    target = _VIRTUAL_DRIVERS if virtual else _DRIVERS
    if driver not in target:
        target.append(driver)


def drivers(*, include_virtual: bool = False) -> list[type[Panel]]:
    return [*_DRIVERS, *(_VIRTUAL_DRIVERS if include_virtual else ())]


def autodetect(*, include_virtual: bool = False) -> list[PanelHandle]:
    """Enumerate every attached panel. Never raises: a broken driver is skipped."""
    handles: list[PanelHandle] = []
    for driver in drivers(include_virtual=include_virtual):
        try:
            found = driver.detect()
        except Exception as exc:  # noqa: BLE001 - one bad driver must not break detection
            log.warning("%s.detect() failed: %s", driver.__name__, exc)
            continue
        handles.extend(found)
    return handles


def find_first(*, include_virtual: bool = False) -> PanelHandle | None:
    handles = autodetect(include_virtual=include_virtual)
    return handles[0] if handles else None


def resolve(selector: str | None) -> PanelHandle | None:
    """Resolve a CLI/config selector to a handle.

    ``None`` or ``"auto"`` picks the first real panel; ``"virtual"`` forces the
    software panel; anything else is matched against a driver name or address
    (e.g. ``"COM5"``).
    """
    if selector in (None, "", "auto"):
        return find_first()
    assert selector is not None
    if selector.lower() == "virtual":
        return VirtualPanel.detect()[0]
    wanted = selector.lower()
    for handle in autodetect(include_virtual=True):
        if wanted in (handle.address.lower(), handle.label.lower()):
            return handle
    return None
