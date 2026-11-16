"""Settings for the Agent Sandbox Platform.

Every setting is overridable from the environment (``ASP_`` prefix).
No secrets are needed: execution is simulated and deterministic, so the
platform runs fully offline.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _env_str(name: str, default: str) -> str:
    return os.environ.get(name, default)


@dataclass
class Settings:
    """Runtime configuration for the sandbox platform."""

    # Sandbox defaults
    default_cpu_ms_budget: int = field(
        default_factory=lambda: _env_int("ASP_DEFAULT_CPU_MS_BUDGET", 60_000))
    default_memory_mb_budget: int = field(
        default_factory=lambda: _env_int("ASP_DEFAULT_MEMORY_MB_BUDGET", 512))
    default_wall_ms_per_call: int = field(
        default_factory=lambda: _env_int("ASP_DEFAULT_WALL_MS_PER_CALL", 5_000))
    default_max_calls: int = field(
        default_factory=lambda: _env_int("ASP_DEFAULT_MAX_CALLS", 100))
    default_network_mode: str = field(
        default_factory=lambda: _env_str("ASP_DEFAULT_NETWORK_MODE", "allowlist"))
    default_fs_roots: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            p for p in _env_str("ASP_DEFAULT_FS_ROOTS", "/workspace").split(",") if p))

    # Policy defaults
    default_network_allowlist: tuple[str, ...] = field(
        default_factory=lambda: tuple(
            h for h in _env_str(
                "ASP_DEFAULT_NETWORK_ALLOWLIST",
                "api.example.com,objects.example.com").split(",") if h))

    # Execution defaults
    default_max_retries: int = field(
        default_factory=lambda: _env_int("ASP_DEFAULT_MAX_RETRIES", 2))
    retry_backoff_base_ms: int = field(
        default_factory=lambda: _env_int("ASP_RETRY_BACKOFF_BASE_MS", 100))
    seed: int = field(default_factory=lambda: _env_int("ASP_SEED", 42))

    # Cost defaults (USD per call, per tool tier)
    cost_per_call_low: float = field(
        default_factory=lambda: _env_float("ASP_COST_PER_CALL_LOW", 0.0001))
    cost_per_call_medium: float = field(
        default_factory=lambda: _env_float("ASP_COST_PER_CALL_MEDIUM", 0.0005))
    cost_per_call_high: float = field(
        default_factory=lambda: _env_float("ASP_COST_PER_CALL_HIGH", 0.002))
    cost_per_cpu_ms: float = field(
        default_factory=lambda: _env_float("ASP_COST_PER_CPU_MS", 0.0000001))

    # Budget defaults (fail-closed caps)
    tenant_cost_cap_usd: float = field(
        default_factory=lambda: _env_float("ASP_TENANT_COST_CAP_USD", 10.00))
    tenant_call_cap: int = field(
        default_factory=lambda: _env_int("ASP_TENANT_CALL_CAP", 10_000))

    host: str = field(default_factory=lambda: _env_str("ASP_HOST", "0.0.0.0"))
    port: int = field(default_factory=lambda: _env_int("ASP_PORT", 8000))
