"""System metrics for text, gauge and sparkline layers.

Providers are polled on one background thread at a fixed rate and publish an
immutable snapshot, so a slow sensor read (WMI in particular) can never stall a
frame. Anything a provider cannot supply is simply absent from the snapshot, and
layers render the placeholder rather than a wrong number.

Metric names are dotted and stable - they are what users type into scenes:

    cpu.load  cpu.freq  cpu.temp  cpu.cores
    mem.used_pct  mem.used_gb  mem.total_gb
    gpu.load  gpu.temp  gpu.vram_pct  gpu.vram_used_gb  gpu.power  gpu.name
    net.up  net.down  disk.used_pct
    time.now  time.date  time.uptime
"""

from __future__ import annotations

import logging
import threading
import time
from abc import ABC, abstractmethod
from typing import Any

log = logging.getLogger(__name__)

POLL_INTERVAL = 1.0


class MetricProvider(ABC):
    """A source of named metrics."""

    name = "provider"

    @property
    def available(self) -> bool:
        return True

    @abstractmethod
    def read(self) -> dict[str, Any]:
        """Return the current values. Must not raise."""

    def close(self) -> None:  # noqa: B027 - most providers hold nothing
        """Release any handles held by the provider."""


class CpuMemoryProvider(MetricProvider):
    """CPU, memory, disk and network, via psutil."""

    name = "cpu/memory"

    def __init__(self) -> None:
        import psutil

        self._psutil = psutil
        self._last_net = psutil.net_io_counters()
        self._last_time = time.monotonic()
        psutil.cpu_percent(interval=None)  # prime the delta-based reading

    def read(self) -> dict[str, Any]:
        ps = self._psutil
        values: dict[str, Any] = {}
        try:
            values["cpu.load"] = ps.cpu_percent(interval=None)
            values["cpu.cores"] = ps.cpu_count(logical=True)
            freq = ps.cpu_freq()
            if freq:
                values["cpu.freq"] = freq.current

            mem = ps.virtual_memory()
            values["mem.used_pct"] = mem.percent
            values["mem.used_gb"] = (mem.total - mem.available) / 1e9
            values["mem.total_gb"] = mem.total / 1e9

            disk = ps.disk_usage("/")
            values["disk.used_pct"] = disk.percent

            now = time.monotonic()
            net = ps.net_io_counters()
            span = max(1e-6, now - self._last_time)
            values["net.up"] = (net.bytes_sent - self._last_net.bytes_sent) / span / 1e6
            values["net.down"] = (net.bytes_recv - self._last_net.bytes_recv) / span / 1e6
            self._last_net, self._last_time = net, now
        except Exception as exc:  # noqa: BLE001 - a sensor hiccup is not fatal
            log.debug("psutil read failed: %s", exc)
        return values


class NvidiaProvider(MetricProvider):
    """NVIDIA GPU metrics via NVML."""

    name = "nvidia"

    def __init__(self, index: int = 0) -> None:
        import pynvml

        self._nvml = pynvml
        pynvml.nvmlInit()
        self._handle = pynvml.nvmlDeviceGetHandleByIndex(index)
        raw_name = pynvml.nvmlDeviceGetName(self._handle)
        self._name = raw_name.decode() if isinstance(raw_name, bytes) else raw_name

    def read(self) -> dict[str, Any]:
        nvml = self._nvml
        values: dict[str, Any] = {"gpu.name": self._name}
        try:
            util = nvml.nvmlDeviceGetUtilizationRates(self._handle)
            values["gpu.load"] = float(util.gpu)
            values["gpu.vram_load"] = float(util.memory)

            mem = nvml.nvmlDeviceGetMemoryInfo(self._handle)
            values["gpu.vram_pct"] = mem.used / mem.total * 100 if mem.total else None
            values["gpu.vram_used_gb"] = mem.used / 1e9
            values["gpu.vram_total_gb"] = mem.total / 1e9

            values["gpu.temp"] = float(
                nvml.nvmlDeviceGetTemperature(self._handle, nvml.NVML_TEMPERATURE_GPU)
            )
            values["gpu.power"] = nvml.nvmlDeviceGetPowerUsage(self._handle) / 1000.0
        except Exception as exc:  # noqa: BLE001 - driver hiccups happen
            log.debug("nvml read failed: %s", exc)
        return values

    def close(self) -> None:
        try:
            self._nvml.nvmlShutdown()
        except Exception as exc:  # noqa: BLE001 - shutdown must never raise
            log.debug("nvml shutdown failed: %s", exc)


class ClockProvider(MetricProvider):
    """Time and uptime. Always available, no dependencies."""

    name = "clock"

    def __init__(self) -> None:
        self._started = time.time()

    def read(self) -> dict[str, Any]:
        now = time.localtime()
        uptime = time.time() - self._started
        return {
            "time.now": time.strftime("%H:%M:%S", now),
            "time.date": time.strftime("%Y-%m-%d", now),
            "time.uptime": f"{int(uptime // 3600)}h{int(uptime % 3600 // 60):02d}m",
        }


class LibreHardwareMonitorProvider(MetricProvider):
    """CPU package temperature via LibreHardwareMonitorLib.

    Optional by design: psutil exposes no CPU temperature on Windows, and the
    only reliable alternatives need a kernel driver and administrator rights.
    When it is not available the metric is simply missing and layers show the
    placeholder - the app never demands elevation.
    """

    name = "librehardwaremonitor"

    def __init__(self, dll_path: str | None = None) -> None:
        import clr  # noqa: F401  (pythonnet)

        raise RuntimeError(
            "LibreHardwareMonitor support is not wired up yet; "
            "install pythonnet and LibreHardwareMonitorLib.dll to enable it"
        )

    def read(self) -> dict[str, Any]:  # pragma: no cover - not constructible yet
        return {}


class StatsRegistry:
    """Polls providers on a background thread and publishes a snapshot."""

    def __init__(self, interval: float = POLL_INTERVAL) -> None:
        self.interval = interval
        self._providers: list[MetricProvider] = []
        self._snapshot: dict[str, Any] = {}
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    @classmethod
    def with_defaults(cls, interval: float = POLL_INTERVAL) -> StatsRegistry:
        """Build a registry with every provider this machine can actually run."""
        registry = cls(interval)
        for factory in (ClockProvider, CpuMemoryProvider, NvidiaProvider):
            try:
                registry.add(factory())
            except Exception as exc:  # noqa: BLE001 - absent hardware is normal
                log.info("metric provider %s unavailable: %s", factory.__name__, exc)
        return registry

    def add(self, provider: MetricProvider) -> None:
        self._providers.append(provider)

    @property
    def provider_names(self) -> list[str]:
        return [p.name for p in self._providers]

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return dict(self._snapshot)

    def poll_once(self) -> dict[str, Any]:
        values: dict[str, Any] = {}
        for provider in self._providers:
            try:
                values.update(provider.read())
            except Exception as exc:  # noqa: BLE001 - one bad provider is not fatal
                log.debug("provider %s failed: %s", provider.name, exc)
        with self._lock:
            self._snapshot = values
        return values

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self.poll_once()  # publish immediately so the first frame has values
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="fitzlcd-stats", daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 2.0) -> None:
        self._stop.set()
        thread, self._thread = self._thread, None
        if thread:
            thread.join(timeout)
        for provider in self._providers:
            provider.close()

    def _run(self) -> None:
        while not self._stop.is_set():
            self.poll_once()
            self._stop.wait(self.interval)

    # Convenient as the engine's metrics_provider callable.
    def __call__(self) -> dict[str, Any]:
        return self.snapshot()
