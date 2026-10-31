"""Motion helpers shared by animated layers.

Animation here is a pure function of ``ctx.time`` wherever it can be. Only the
things that must remember the previous frame -- a value easing toward a new
reading, a spin whose speed changes with activity -- keep state, and that state
lives on the layer instance. Scenes are data, so nothing here touches JSON: the
state is never written out and a scene reloads with a clean slate.

Two rules every helper follows:

* A step never covers more than :data:`MAX_STEP` seconds. The engine can pause
  or stall, and the first frame back should ease, not leap.
* Time that goes backwards means a new run of the scene (the engine resets its
  clock on every scene switch). State then snaps to where it should be instead
  of easing from wherever the last run left it.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

#: Longest simulated step, in seconds.
MAX_STEP = 0.25

#: Values closer than this are treated as unchanged, so float noise in a
#: reading cannot light up a "rise" that never happened.
EPSILON = 1e-6


def ease_out_cubic(t: float) -> float:
    """Fast start, soft landing. ``t`` is clamped to 0..1."""
    t = min(1.0, max(0.0, t))
    return 1.0 - (1.0 - t) ** 3


def pulse_wave(time: float, hz: float) -> float:
    """Smooth 0..1 oscillation at ``hz`` cycles per second, starting at its peak."""
    return 0.5 + 0.5 * math.cos(2.0 * math.pi * hz * time)


def approach(current: float, target: float, dt: float, tau: float) -> float:
    """Move ``current`` toward ``target`` exponentially.

    ``tau`` is the time constant in seconds: after ``tau`` seconds about 63% of
    the distance is covered, after ``3 * tau`` about 95%. ``tau <= 0`` snaps.
    """
    if tau <= 0:
        return target
    if dt <= 0:
        return current
    return target + (current - target) * math.exp(-dt / tau)


def as_finite_float(value: object) -> float | None:
    """A reading as a float, or ``None`` if it is missing, text, NaN or infinite.

    ``None`` is the signal to draw nothing for that reading. Coercing it to zero
    would show a confident 0% for a value that is simply unknown.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError, OverflowError):
        return None
    return number if math.isfinite(number) else None


#: Glide duration is the time to settle within ~5% of a new value.
GLIDE_SETTLE_TAU_RATIO = 3.0


@dataclass
class ValueGlide:
    """A reading that eases toward its target, and remembers the last rise.

    The rise memory is what makes an increase visible: the span between where
    the value was and where it is now is the "added" slice, which the ring can
    highlight for a moment before it cools back to its normal colour.
    """

    shown: float | None = None
    target: float | None = None
    last_time: float | None = None
    #: Where the most recent (still-highlighted) rise started, or ``None``.
    gain_from: float | None = None
    #: When that rise happened, on the same clock as ``ctx.time``.
    gain_at: float | None = None

    def reset(self) -> None:
        self.shown = self.target = self.last_time = None
        self.gain_from = self.gain_at = None

    def step(self, target: float | None, now: float, glide: float) -> float | None:
        """Advance to ``now`` and return the value to draw, or ``None``."""
        if target is None:
            self.reset()
            return None

        if self.shown is None or self.last_time is None or now < self.last_time:
            self.reset()
            self.shown = self.target = target
            self.last_time = now
            return target

        dt = min(max(now - self.last_time, 0.0), MAX_STEP)
        self.last_time = now
        prior = self.shown

        if target > self.target + EPSILON:
            if self.gain_from is None:
                self.gain_from = prior
            self.gain_at = now
        elif target < self.target - EPSILON:
            self.gain_from = self.gain_at = None
        self.target = target

        if glide <= 0:
            self.shown = target
        else:
            self.shown = approach(prior, target, dt, glide / GLIDE_SETTLE_TAU_RATIO)
        return self.shown

    def gain_progress(self, now: float, gain_flash: float) -> float | None:
        """0..1 through the highlight of the latest rise, or ``None`` when none is live."""
        if self.gain_from is None or self.gain_at is None or gain_flash <= 0:
            self.gain_from = self.gain_at = None
            return None
        progress = (now - self.gain_at) / gain_flash
        if progress >= 1.0:
            self.gain_from = self.gain_at = None
            return None
        return max(0.0, progress)
