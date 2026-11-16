"""Execution engine: the fail-closed pipeline for one tool call.

Order of checks (fail-closed at every step — a refusal anywhere stops
the call before any side effect):

1. Idempotency: a repeated ``idempotency_key`` returns the stored result.
2. Sandbox exists, is active, and has call budget left.
3. Tool exists and arguments validate against its JSON schema.
4. Tenant budget pre-check (cost / call caps).
5. Policy decision (network allow-list, filesystem scope, secrets,
   command denylist). DENY ends the call; high-risk requires approval.
6. Approval gate for high-risk tools.
7. Resource check: the deterministic profile must fit the sandbox
   budget and the per-call wall limit, else the sandbox is terminated.
8. Simulated execution with bounded retries (transient failures only,
   exponential backoff, deterministic seeded outcomes).
9. Ledger recording + hash-chained audit entry for every outcome.

Execution itself is simulated deterministically: the result for a call
depends only on the tool, the arguments and the seed, never on wall-clock
time or network. The simulator is the documented seam where a real
container runtime (gVisor / Firecracker / Docker) plugs in.
"""
from __future__ import annotations

import hashlib
import itertools
import json
import random
import time
from typing import Any

from sandbox.approvals import ApprovalStore
from sandbox.audit import AuditLog
from sandbox.config import Settings
from sandbox.ledger import Ledger
from sandbox.models import (
    Decision, ExecutionResult, ExecutionStatus, Sandbox, ToolCall,
    deterministic_seed,
)
from sandbox.policy import PolicyEngine
from sandbox.registry import ToolRegistry
from sandbox.resources import ResourceEnforcer
from sandbox.sandbox import SandboxManager


def _stable_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, default=str)


class ExecutionEngine:
    """Run tool calls inside sandboxes, fail-closed at every step."""

    def __init__(self, settings: Settings | None = None,
                 registry: ToolRegistry | None = None,
                 manager: SandboxManager | None = None,
                 policy: PolicyEngine | None = None,
                 enforcer: ResourceEnforcer | None = None,
                 approvals: ApprovalStore | None = None,
                 ledger: Ledger | None = None,
                 audit: AuditLog | None = None) -> None:
        self.settings = settings or Settings()
        self.registry = registry or ToolRegistry()
        self.manager = manager or SandboxManager()
        self.policy = policy or PolicyEngine()
        self.enforcer = enforcer or ResourceEnforcer()
        self.approvals = approvals or ApprovalStore()
        self.ledger = ledger or Ledger(self.settings)
        self.audit = audit or AuditLog()
        self._call_counter = itertools.count(1)
        self._results: dict[str, ExecutionResult] = {}  # idempotency_key -> result

    # -- public API --------------------------------------------------------
    def execute(self, call: ToolCall) -> ExecutionResult:
        call_id = call.call_id or f"call-{next(self._call_counter):04d}"
        call.call_id = call_id

        # 1. Idempotency — same key returns the original result, no re-run.
        if call.idempotency_key:
            cached = self._results.get(call.idempotency_key)
            if cached is not None:
                dup = ExecutionResult(
                    call_id=cached.call_id, tool=cached.tool,
                    sandbox_id=cached.sandbox_id, tenant=cached.tenant,
                    status=ExecutionStatus.DUPLICATE, result=cached.result,
                    reasons=["idempotency key already seen; original result returned"],
                    cpu_ms=0, memory_mb=0, latency_ms=0, cost_usd=0.0,
                    attempts=0)
                self.audit.append("execution.duplicate", {
                    "call_id": call_id, "tool": call.tool,
                    "sandbox_id": call.sandbox_id, "tenant": call.tenant,
                    "idempotency_key": call.idempotency_key,
                    "original_call_id": cached.call_id})
                return dup

        result = self._run(call, call_id)

        if call.idempotency_key and result.status in (
                ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED):
            self._results[call.idempotency_key] = result
        return result

    # -- pipeline ----------------------------------------------------------
    def _run(self, call: ToolCall, call_id: str) -> ExecutionResult:
        def finish(status: ExecutionStatus, *, result=None, error=None,
                   reasons=None, cpu_ms=0, memory_mb=0, latency_ms=0,
                   cost_usd=0.0, attempts=1, approved_by=None,
                   terminate_sandbox: str | None = None) -> ExecutionResult:
            outcome = ExecutionResult(
                call_id=call_id, tool=call.tool, sandbox_id=call.sandbox_id,
                tenant=call.tenant, status=status, result=result, error=error,
                reasons=reasons or [], cpu_ms=cpu_ms, memory_mb=memory_mb,
                latency_ms=latency_ms, cost_usd=cost_usd, attempts=attempts,
                approved_by=approved_by)
            if status is ExecutionStatus.SUCCEEDED and terminate_sandbox is None:
                # Only charge successful executions to the sandbox budget.
                sandbox = self.manager.get(call.sandbox_id)
                if sandbox is not None:
                    self.enforcer.account(
                        sandbox, _profile(cpu_ms, memory_mb, latency_ms))
            self.ledger.record(call.tenant, call.sandbox_id, call.tool, status,
                               cpu_ms, latency_ms, cost_usd)
            self.audit.append("execution." + status.value, {
                "call_id": call_id, "tool": call.tool,
                "sandbox_id": call.sandbox_id, "tenant": call.tenant,
                "status": status.value, "reasons": outcome.reasons,
                "cpu_ms": cpu_ms, "latency_ms": latency_ms,
                "cost_usd": round(cost_usd, 6), "attempts": attempts,
                "error": error})
            if terminate_sandbox:
                sandbox = self.manager.get(call.sandbox_id)
                if sandbox is not None and sandbox.state.value != "terminated":
                    self.manager.destroy(call.sandbox_id, terminate_sandbox)
                    self.audit.append("sandbox.terminated", {
                        "sandbox_id": call.sandbox_id, "reason": terminate_sandbox,
                        "call_id": call_id})
            return outcome

        # 2. Sandbox must exist and be usable.
        try:
            sandbox = self.manager.reuse(call.sandbox_id)
        except ValueError as exc:
            return finish(ExecutionStatus.FAILED, error=str(exc), reasons=[str(exc)])

        # 3. Tool + schema validation.
        tool = self.registry.get(call.tool)
        if tool is None:
            return finish(ExecutionStatus.FAILED,
                          error=f"unknown tool {call.tool!r}",
                          reasons=[f"unknown tool {call.tool!r}"])
        schema_errors = self.registry.validate_call(call.tool, call.args)
        if schema_errors:
            return finish(ExecutionStatus.DENIED, error="argument validation failed",
                          reasons=schema_errors)

        kind = tool.behaviour.get("kind", "default")

        # 4. Tenant budget pre-check (fail-closed).
        budget_reasons = self.ledger.check_budget(call.tenant, tool.risk_tier)
        if budget_reasons:
            return finish(ExecutionStatus.DENIED,
                          error="tenant budget exceeded", reasons=budget_reasons)

        # 5. Policy decision.
        decision = self.policy.decide(call, tool, sandbox.config)
        if decision.decision is Decision.DENY:
            return finish(ExecutionStatus.DENIED,
                          error="policy denied the call", reasons=decision.reasons)

        # 6. Approval gate for high-risk tools.
        approved_by = None
        if decision.decision is Decision.REQUIRE_APPROVAL:
            approval = self.approvals.consume(call_id, call.tool)
            if approval is None:
                return finish(ExecutionStatus.PENDING_APPROVAL,
                              error="high-risk tool requires approval",
                              reasons=decision.reasons)
            approved_by = approval.approver
            self.audit.append("approval.consumed", {
                "call_id": call_id, "tool": call.tool, "approver": approved_by})

        # 7. Resource check — breach terminates the sandbox.
        profile, breaches = self.enforcer.check(sandbox, call, kind, self.settings.seed)
        if breaches:
            details = "; ".join(b.detail for b in breaches)
            return finish(
                ExecutionStatus.TERMINATED,
                error=f"resource budget breached: {details}",
                reasons=[b.detail for b in breaches],
                cpu_ms=profile.cpu_ms, memory_mb=profile.memory_mb,
                latency_ms=profile.latency_ms,
                cost_usd=self.ledger.cost_for(tool.risk_tier, profile.cpu_ms),
                terminate_sandbox=f"resource breach: {details}")

        # 8. Simulated execution with bounded retries on transient failures.
        max_attempts = 1 + self.settings.default_max_retries
        attempts = 0
        last_error: str | None = None
        while attempts < max_attempts:
            attempts += 1
            outcome, error, transient = self._simulate(call, tool, kind, attempts)
            if error is None:
                cost = self.ledger.cost_for(tool.risk_tier, profile.cpu_ms)
                return finish(ExecutionStatus.SUCCEEDED, result=outcome,
                              cpu_ms=profile.cpu_ms, memory_mb=profile.memory_mb,
                              latency_ms=profile.latency_ms, cost_usd=cost,
                              attempts=attempts, approved_by=approved_by)
            last_error = error
            if not transient or attempts >= max_attempts:
                break
            backoff_ms = self.settings.retry_backoff_base_ms * (2 ** (attempts - 1))
            time.sleep(min(backoff_ms, 50) / 1000.0)  # capped; simulation only
        return finish(ExecutionStatus.FAILED, error=last_error or "execution failed",
                      reasons=[last_error or "execution failed"],
                      cpu_ms=profile.cpu_ms, memory_mb=profile.memory_mb,
                      latency_ms=profile.latency_ms,
                      cost_usd=self.ledger.cost_for(tool.risk_tier, profile.cpu_ms),
                      attempts=attempts, approved_by=approved_by)

    # -- deterministic simulator -------------------------------------------
    def _simulate(self, call: ToolCall, tool, kind: str,
                  attempt: int) -> tuple[dict[str, Any] | None, str | None, bool]:
        """Simulate one tool execution. Returns (result, error, transient).

        Deterministic: the same tool + args + seed always yields the same
        result. A tool may declare ``transient_failure_rate`` in its
        behaviour; the seeded draw decides whether an attempt fails
        transiently (retryable) so retries are testable.
        """
        seed = deterministic_seed(call.tool, call.canonical(), str(attempt),
                                  base_seed=self.settings.seed)
        rng = random.Random(seed)
        failure_rate = float(tool.behaviour.get("transient_failure_rate", 0.0))
        if failure_rate > 0 and rng.random() < failure_rate:
            return None, "transient simulated failure (seeded)", True

        args = call.args
        if kind == "calc":
            op = args.get("op", "add")
            a, b = args["a"], args["b"]
            value = {"add": a + b, "sub": a - b, "mul": a * b,
                     "div": (a / b if b != 0 else None)}[op]
            if value is None:
                return None, "division by zero", False
            return {"value": value, "op": op}, None, False
        if kind == "kv":
            op = tool.behaviour.get("op", args.get("op", "get"))
            if op == "get":
                return {"key": args["key"], "value": f"synthetic-value-for-{args['key']}"}, None, False
            return {"key": args["key"], "stored": True}, None, False
        if kind == "file_read":
            digest = hashlib.sha256(
                f"synthetic-content:{args['path']}".encode()).hexdigest()[:16]
            return {"path": args["path"], "bytes": 128 + rng.randint(0, 512),
                    "content_sha256": digest}, None, False
        if kind == "file_write":
            return {"path": args["path"], "bytes_written": len(args.get("content", "")),
                    "written": True}, None, False
        if kind == "http":
            return {"url": args["url"], "status": 200,
                    "body_sha256": hashlib.sha256(args["url"].encode()).hexdigest()[:16],
                    "bytes": 256 + rng.randint(0, 1024)}, None, False
        if kind == "shell":
            return {"command": args["command"], "exit_code": 0,
                    "stdout": f"simulated output for: {args['command']}"}, None, False
        return {"tool": call.tool, "args": args, "ok": True}, None, False


def _profile(cpu_ms: int, memory_mb: int, latency_ms: int):
    from sandbox.resources import ResourceProfile
    return ResourceProfile(cpu_ms=cpu_ms, memory_mb=memory_mb, latency_ms=latency_ms)
