"""What the caption computer itself is doing, for the preflight check.

The church product runs on the computer that already runs ProPresenter, OBS
or vMix (requirements N-01/N-02), so "is this machine coping?" is a real
question, and one the operator cannot answer from a spinning cursor. This
samples CPU and memory a few times a minute and names the busiest programs,
so the status page can say "ProPresenter is using 70% of the CPU" rather
than "captions are slow".

psutil does the reading. It is pinned in requirements.txt, but if it is ever
missing the monitor reports `available: false` and the preflight says "not
measured" instead of guessing - a missing number must never look like a
healthy one.
"""

from __future__ import annotations

import os
import time

try:
    import psutil
except Exception:                                   # pragma: no cover - environment
    psutil = None

SAMPLE_SECONDS = 2.0          # CPU and memory
PROCESS_SECONDS = 10.0        # the busiest-programs list; walking every process costs more
TOP_PROCESSES = 4
TOP_MIN_PERCENT = 5.0         # below this a program is not worth naming
# Not programs: Windows accounts idle time to a pseudo-process, which would
# otherwise top every list at 80%.
IDLE_NAMES = {"system idle process", "idle"}


class HostMonitor:
    def __init__(self):
        self.available = psutil is not None
        self.cpu_percent: float | None = None
        self.cpu_count: int | None = psutil.cpu_count(logical=True) if self.available else None
        self.mem_percent: float | None = None
        self.mem_available_mb: int | None = None
        self.own_cpu_percent: float | None = None
        self.busiest: list[dict] = []
        self.updated_at: float | None = None
        self._last_process_walk = 0.0
        self._process = psutil.Process(os.getpid()) if self.available else None
        if self.available:
            # cpu_percent() compares against the previous call; prime it so
            # the first real sample is not meaningless.
            try:
                psutil.cpu_percent(interval=None)
                self._process.cpu_percent(interval=None)
            except Exception:
                pass

    def sample(self) -> None:
        """Cheap; call every SAMPLE_SECONDS. Never raises."""
        if not self.available:
            return
        try:
            self.cpu_percent = float(psutil.cpu_percent(interval=None))
            vm = psutil.virtual_memory()
            self.mem_percent = float(vm.percent)
            self.mem_available_mb = int(vm.available // (1024 * 1024))
            # Per-process percent is of one core; report our own share of
            # the whole machine, same scale as cpu_percent.
            self.own_cpu_percent = round(
                float(self._process.cpu_percent(interval=None)) / (self.cpu_count or 1), 1)
            self.updated_at = time.time()
        except Exception:
            return
        if time.time() - self._last_process_walk >= PROCESS_SECONDS:
            self._last_process_walk = time.time()
            self._walk_processes()

    def _walk_processes(self) -> None:
        """The busiest programs by CPU. Each process's cpu_percent() is
        relative to the previous walk, so the first walk's numbers are
        zeros and the list fills in from the second one."""
        # One entry per program name, not per process: a browser or OBS is
        # a dozen processes, and "chrome.exe 3%" four times over says less
        # than "chrome.exe 12%" once.
        by_name: dict[str, float] = {}
        try:
            for proc in psutil.process_iter(["name"]):
                try:
                    pct = proc.cpu_percent(interval=None)
                except (psutil.NoSuchProcess, psutil.AccessDenied, psutil.ZombieProcess):
                    continue
                name = (proc.info.get("name") or "").strip() or f"pid {proc.pid}"
                if name.lower() in IDLE_NAMES or pct <= 0:
                    continue
                by_name[name[:48]] = by_name.get(name[:48], 0.0) + pct
        except Exception:
            return
        # Percent per process is of one core on most platforms; express it
        # as a share of the whole machine so it lines up with cpu_percent.
        cores = self.cpu_count or 1
        found = [{"name": name, "cpu_percent": round(total / cores, 1)}
                 for name, total in by_name.items() if total >= TOP_MIN_PERCENT]
        found.sort(key=lambda p: p["cpu_percent"], reverse=True)
        self.busiest = [p for p in found if p["cpu_percent"] >= 1.0][:TOP_PROCESSES]

    def snapshot(self) -> dict:
        return {
            "available": self.available,
            "cpu_percent": self.cpu_percent,
            "cpu_count": self.cpu_count,
            "mem_percent": self.mem_percent,
            "mem_available_mb": self.mem_available_mb,
            "own_cpu_percent": self.own_cpu_percent,
            "busiest": list(self.busiest),
            "age": None if self.updated_at is None else round(time.time() - self.updated_at, 1),
        }
