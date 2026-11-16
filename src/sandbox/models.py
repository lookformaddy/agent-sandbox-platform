"""Domain models for the Agent Sandbox Platform."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


class SandboxState(str, Enum):
    CREATED = "created"
    ACTIVE = "active"
    SUSPENDED = "suspended"
    TERMINATED = "terminated"


class NetworkMode(str, Enum):
    NONE = "none"          # no network access at all
    ALLOWLIST = "allowlist"  # only allow-listed hosts
    OPEN = "open"          # unrestricted (discouraged; audit-flagged)


class RiskTier(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"


class ExecutionStatus(str, Enum):
    SUCCEEDED = "succeeded"
    DENIED = "denied"
    PENDING_APPROVAL = "pending_approval"
    FAILED = "failed"
    TERMINATED = "terminated"
    DUPLICATE = "duplicate"


class Decision(str, Enum):
    ALLOW = "allow"
    DENY = "deny"
    REQUIRE_APPROVAL = "require_approval"


@dataclass
class SandboxConfig:
    """Validated configuration for one sandbox."""

    tenant: str = "default"
    cpu_ms_budget: int = 60_000
    memory_mb_budget: int = 512
    wall_ms_per_call: int = 5_000
    max_calls: int = 100
    network_mode: NetworkMode = NetworkMode.ALLOWLIST
    network_allowlist: tuple[str, ...] = ("api.example.com", "objects.example.com")
    fs_roots: tuple[str, ...] = ("/workspace",)
    seed: int = 42

    def validate(self) -> list[str]:
        """Return a list of validation errors (empty = valid)."""
        errors: list[str] = []
        if not self.tenant:
            errors.append("tenant must not be empty")
        if self.cpu_ms_budget <= 0:
            errors.append("cpu_ms_budget must be positive")
        if self.memory_mb_budget <= 0:
            errors.append("memory_mb_budget must be positive")
        if self.wall_ms_per_call <= 0:
            errors.append("wall_ms_per_call must be positive")
        if self.max_calls <= 0:
            errors.append("max_calls must be positive")
        if not isinstance(self.network_mode, NetworkMode):
            errors.append("network_mode must be a NetworkMode")
        if self.network_mode is NetworkMode.ALLOWLIST and not self.network_allowlist:
            errors.append("allowlist mode requires at least one allow-listed host")
        if not self.fs_roots:
            errors.append("at least one filesystem root is required")
        for root in self.fs_roots:
            if not root.startswith("/"):
                errors.append(f"filesystem root must be absolute: {root!r}")
        return errors


@dataclass
class Sandbox:
    """A live (simulated) isolated execution sandbox."""

    id: str
    config: SandboxConfig
    state: SandboxState = SandboxState.ACTIVE
    calls_made: int = 0
    cpu_ms_used: int = 0
    memory_mb_peak: int = 0
    terminated_reason: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "tenant": self.config.tenant,
            "state": self.state.value,
            "calls_made": self.calls_made,
            "cpu_ms_used": self.cpu_ms_used,
            "memory_mb_peak": self.memory_mb_peak,
            "terminated_reason": self.terminated_reason,
        }


@dataclass
class ToolDefinition:
    """A registered tool: name, JSON-schema args, risk tier, cost."""

    name: str
    description: str
    args_schema: dict[str, Any]
    risk_tier: RiskTier = RiskTier.LOW
    timeout_ms: int = 5_000
    # Optional simulated behaviour knobs (deterministic, seeded).
    # e.g. {"kind": "http", "kind": "file_read", "kind": "shell",
    #       "kind": "kv", "kind": "calc", "transient_failure_rate": 0.0}
    behaviour: dict[str, Any] = field(default_factory=dict)

    def key(self) -> str:
        return self.name


@dataclass
class ToolCall:
    """One requested tool invocation."""

    tool: str
    args: dict[str, Any]
    sandbox_id: str
    tenant: str = "default"
    idempotency_key: str | None = None
    call_id: str | None = None

    def canonical(self) -> str:
        return json.dumps(
            {"tool": self.tool, "args": self.args,
             "sandbox_id": self.sandbox_id, "tenant": self.tenant},
            sort_keys=True, default=str)


@dataclass
class PolicyDecision:
    """Outcome of the fail-closed policy check for one call."""

    decision: Decision
    reasons: list[str] = field(default_factory=list)

    @property
    def allowed(self) -> bool:
        return self.decision is Decision.ALLOW


@dataclass
class ExecutionResult:
    """Outcome of one executed (or refused) tool call."""

    call_id: str
    tool: str
    sandbox_id: str
    tenant: str
    status: ExecutionStatus
    result: dict[str, Any] | None = None
    error: str | None = None
    reasons: list[str] = field(default_factory=list)
    cpu_ms: int = 0
    memory_mb: int = 0
    latency_ms: int = 0
    cost_usd: float = 0.0
    attempts: int = 1
    approved_by: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "call_id": self.call_id,
            "tool": self.tool,
            "sandbox_id": self.sandbox_id,
            "tenant": self.tenant,
            "status": self.status.value,
            "result": self.result,
            "error": self.error,
            "reasons": self.reasons,
            "cpu_ms": self.cpu_ms,
            "memory_mb": self.memory_mb,
            "latency_ms": self.latency_ms,
            "cost_usd": round(self.cost_usd, 6),
            "attempts": self.attempts,
            "approved_by": self.approved_by,
        }


@dataclass
class Approval:
    """A granted approval for a high-risk tool call."""

    call_id: str
    tool: str
    approver: str
    granted_at_seq: int
    expires_after_calls: int = 1


@dataclass
class AuditEntry:
    """One hash-chained audit log entry."""

    seq: int
    event: str
    payload: dict[str, Any]
    prev_hash: str
    entry_hash: str

    @staticmethod
    def compute_hash(seq: int, event: str, payload: dict[str, Any],
                     prev_hash: str) -> str:
        canonical = json.dumps(
            {"seq": seq, "event": event, "payload": payload,
             "prev_hash": prev_hash},
            sort_keys=True, default=str)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def deterministic_seed(*parts: str, base_seed: int = 42) -> int:
    """Derive a stable integer seed from text parts (no PYTHONHASHSEED dependence)."""
    digest = hashlib.sha256(
        "|".join([str(base_seed), *parts]).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)
