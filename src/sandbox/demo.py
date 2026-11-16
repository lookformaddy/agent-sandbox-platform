"""Demo runner: the six scenarios from the README, end to end.

1. Safe tool run succeeds.
2. Blocked network call is denied (host not on the allow-list).
3. Filesystem escape attempt is denied (path outside the sandbox roots).
4. High-risk tool requires approval, then runs once approved.
5. Budget breach terminates the sandbox.
6. Audit hash chain verifies (and tamper detection works).
"""
from __future__ import annotations

from typing import Any

from sandbox.audit import AuditLog
from sandbox.config import Settings
from sandbox.demo_data import default_tools
from sandbox.executor import ExecutionEngine
from sandbox.ledger import Ledger
from sandbox.models import ExecutionStatus, SandboxConfig, ToolCall
from sandbox.registry import ToolRegistry
from sandbox.sandbox import SandboxManager


def build_engine(settings: Settings | None = None) -> ExecutionEngine:
    settings = settings or Settings()
    registry = ToolRegistry()
    for tool in default_tools():
        registry.register(tool)
    return ExecutionEngine(settings=settings, registry=registry,
                           manager=SandboxManager(), ledger=Ledger(settings),
                           audit=AuditLog())


def run_demo() -> dict[str, Any]:
    engine = build_engine()
    settings = engine.settings
    results: dict[str, Any] = {}

    # --- Scenario 1: safe tool run succeeds -------------------------------
    sb = engine.manager.create(SandboxConfig(tenant="team-a"))
    r1 = engine.execute(ToolCall(
        tool="calculator", args={"op": "add", "a": 40, "b": 2},
        sandbox_id=sb.id, tenant="team-a", call_id="demo-1"))
    results["safe_run"] = r1.status.value
    results["safe_value"] = (r1.result or {}).get("value")
    print(f"[1] safe calculator run: {r1.status.value} "
          f"(40 + 2 = {results['safe_value']}, {r1.cpu_ms} cpu-ms, "
          f"${r1.cost_usd:.6f})")

    # --- Scenario 2: blocked network call denied --------------------------
    r2 = engine.execute(ToolCall(
        tool="http_fetch", args={"url": "https://evil.example.net/steal"},
        sandbox_id=sb.id, tenant="team-a", call_id="demo-2"))
    results["blocked_network"] = r2.status.value
    print(f"[2] http_fetch to evil.example.net: {r2.status.value} "
          f"— {r2.reasons[0] if r2.reasons else ''}")

    # --- Scenario 3: filesystem escape denied ------------------------------
    r3 = engine.execute(ToolCall(
        tool="file_read", args={"path": "/workspace/../../etc/passwd"},
        sandbox_id=sb.id, tenant="team-a", call_id="demo-3"))
    results["fs_escape"] = r3.status.value
    print(f"[3] file_read /workspace/../../etc/passwd: {r3.status.value} "
          f"— {r3.reasons[0] if r3.reasons else ''}")

    # --- Scenario 4: high-risk tool needs approval, then runs --------------
    r4a = engine.execute(ToolCall(
        tool="shell_run", args={"command": "ls /workspace"},
        sandbox_id=sb.id, tenant="team-a", call_id="demo-4"))
    results["high_risk_before_approval"] = r4a.status.value
    print(f"[4a] shell_run without approval: {r4a.status.value}")
    engine.approvals.approve("demo-4", "shell_run", approver="human-reviewer")
    r4b = engine.execute(ToolCall(
        tool="shell_run", args={"command": "ls /workspace"},
        sandbox_id=sb.id, tenant="team-a", call_id="demo-4"))
    results["high_risk_after_approval"] = r4b.status.value
    results["high_risk_approved_by"] = r4b.approved_by
    print(f"[4b] shell_run after approval by {r4b.approved_by}: {r4b.status.value}")

    # --- Scenario 5: budget breach terminates the sandbox ------------------
    tiny = engine.manager.create(SandboxConfig(
        tenant="team-a", cpu_ms_budget=1, memory_mb_budget=512,
        wall_ms_per_call=5_000, max_calls=100))
    r5 = engine.execute(ToolCall(
        tool="calculator", args={"op": "mul", "a": 6, "b": 7},
        sandbox_id=tiny.id, tenant="team-a", call_id="demo-5"))
    results["budget_breach"] = r5.status.value
    results["breached_sandbox_state"] = engine.manager.get(tiny.id).state.value
    print(f"[5] calculator in 1 cpu-ms sandbox: {r5.status.value}; "
          f"sandbox state now {results['breached_sandbox_state']} "
          f"({r5.reasons[0] if r5.reasons else ''})")

    # --- Scenario 6: audit chain verifies, tamper detected -----------------
    valid, bad = engine.audit.verify()
    results["audit_entries"] = len(engine.audit)
    results["audit_valid"] = valid
    print(f"[6] audit chain: {len(engine.audit)} entries, valid={valid}")
    tampered = AuditLog.from_jsonl(engine.audit.to_jsonl())
    if tampered.entries():
        entry = tampered.entries()[0]
        entry.payload = dict(entry.payload, status="forged")
    tampered_valid, tampered_bad = tampered.verify()
    results["tamper_detected"] = (not tampered_valid) and bool(tampered_bad)
    print(f"    tamper check: forged entry detected at seq {tampered_bad} "
          f"(valid={tampered_valid})")

    # --- Ledger summary -----------------------------------------------------
    totals = engine.ledger.tenant_totals("team-a")
    results["ledger_calls"] = totals.calls if totals else 0
    results["ledger_succeeded"] = totals.succeeded if totals else 0
    results["ledger_denied"] = totals.denied if totals else 0
    results["ledger_cost_usd"] = round(totals.cost_usd, 6) if totals else 0.0
    print(f"Ledger team-a: {results['ledger_calls']} calls, "
          f"{results['ledger_succeeded']} succeeded, "
          f"{results['ledger_denied']} denied, "
          f"${results['ledger_cost_usd']:.6f} total")

    results["all_ok"] = (
        results["safe_run"] == ExecutionStatus.SUCCEEDED.value
        and results["safe_value"] == 42
        and results["blocked_network"] == ExecutionStatus.DENIED.value
        and results["fs_escape"] == ExecutionStatus.DENIED.value
        and results["high_risk_before_approval"] == ExecutionStatus.PENDING_APPROVAL.value
        and results["high_risk_after_approval"] == ExecutionStatus.SUCCEEDED.value
        and results["budget_breach"] == ExecutionStatus.TERMINATED.value
        and results["breached_sandbox_state"] == "terminated"
        and results["audit_valid"] is True
        and results["tamper_detected"] is True
    )
    print(f"\nDemo {'PASSED' if results['all_ok'] else 'FAILED'}")
    return results
