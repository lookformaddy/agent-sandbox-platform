"""Resource enforcement: simulated CPU / memory / time budgets.

Execution is simulated (no real process is spawned — this is the control
plane, and the deterministic simulator is the documented seam where a real
container runtime plugs in). Each call draws a deterministic resource
profile from its seeded inputs, so the same call always costs the same.
Budgets are enforced fail-closed: a call whose profile would breach the
sandbox budget, or whose wall time exceeds the per-call limit, is
terminated *before* it runs, the breach is accounted, and the sandbox is
terminated so the overrun cannot continue.
"""
from __future__ import annotations

import random
from dataclasses import dataclass

from sandbox.models import Sandbox, SandboxConfig, ToolCall, deterministic_seed

# Per-kind base resource profiles (simulated milliseconds / megabytes).
_BASE_PROFILES: dict[str, tuple[int, int, int]] = {
    # (cpu_ms, memory_mb, latency_ms)
    "calc": (8, 4, 12),
    "kv": (15, 8, 25),
    "file_read": (40, 16, 60),
    "file_write": (60, 24, 90),
    "http": (120, 32, 250),
    "shell": (200, 64, 400),
    "default": (50, 16, 80),
}


@dataclass
class ResourceProfile:
    cpu_ms: int
    memory_mb: int
    latency_ms: int


@dataclass
class Breach:
    kind: str  # "cpu_budget" | "memory_budget" | "wall_time" | "call_budget"
    detail: str


class ResourceEnforcer:
    """Simulate and enforce resource budgets for a sandbox."""

    def profile_for(self, call: ToolCall, kind: str,
                    base_seed: int) -> ResourceProfile:
        """Deterministic resource profile for one call (seeded, reproducible)."""
        base_cpu, base_mem, base_lat = _BASE_PROFILES.get(kind, _BASE_PROFILES["default"])
        rng = random.Random(deterministic_seed(call.tool, call.canonical(), kind, base_seed=base_seed))
        jitter = 0.5 + rng.random()  # 0.5x–1.5x
        return ResourceProfile(
            cpu_ms=max(1, int(base_cpu * jitter)),
            memory_mb=max(1, int(base_mem * jitter)),
            latency_ms=max(1, int(base_lat * jitter)),
        )

    def check(self, sandbox: Sandbox, call: ToolCall, kind: str,
              base_seed: int) -> tuple[ResourceProfile, list[Breach]]:
        """Return the call's profile plus any budget breaches (empty = fits)."""
        config: SandboxConfig = sandbox.config
        profile = self.profile_for(call, kind, base_seed)
        breaches: list[Breach] = []
        if sandbox.calls_made >= config.max_calls:
            breaches.append(Breach(
                "call_budget",
                f"sandbox {sandbox.id} exhausted its call budget "
                f"({config.max_calls} calls)"))
        if sandbox.cpu_ms_used + profile.cpu_ms > config.cpu_ms_budget:
            breaches.append(Breach(
                "cpu_budget",
                f"cpu budget exceeded: {sandbox.cpu_ms_used + profile.cpu_ms}ms "
                f"> {config.cpu_ms_budget}ms"))
        if profile.memory_mb > config.memory_mb_budget:
            breaches.append(Breach(
                "memory_budget",
                f"call needs {profile.memory_mb}MB > {config.memory_mb_budget}MB budget"))
        if profile.latency_ms > config.wall_ms_per_call:
            breaches.append(Breach(
                "wall_time",
                f"call latency {profile.latency_ms}ms exceeds per-call wall limit "
                f"{config.wall_ms_per_call}ms"))
        return profile, breaches

    def account(self, sandbox: Sandbox, profile: ResourceProfile) -> None:
        """Charge a successful call's resources to the sandbox."""
        sandbox.calls_made += 1
        sandbox.cpu_ms_used += profile.cpu_ms
        sandbox.memory_mb_peak = max(sandbox.memory_mb_peak, profile.memory_mb)
