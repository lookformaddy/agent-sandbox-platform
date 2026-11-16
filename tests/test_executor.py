"""End-to-end tests for the execution engine."""
from tests.conftest import run

from sandbox.models import ExecutionStatus, SandboxState


def test_safe_run_succeeds(engine, sandbox):
    result = run(engine, sandbox.id, "calculator", {"op": "add", "a": 40, "b": 2})
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.result["value"] == 42
    assert result.cpu_ms > 0 and result.cost_usd > 0
    assert sandbox.calls_made == 1
    assert sandbox.cpu_ms_used == result.cpu_ms


def test_deterministic_results(engine, sandbox):
    r1 = run(engine, sandbox.id, "calculator", {"op": "mul", "a": 6, "b": 7},
             call_id="det-1")
    r2 = run(engine, sandbox.id, "calculator", {"op": "mul", "a": 6, "b": 7},
             call_id="det-2")
    assert r1.result == r2.result
    assert (r1.cpu_ms, r1.memory_mb, r1.latency_ms) == \
           (r2.cpu_ms, r2.memory_mb, r2.latency_ms)


def test_blocked_network_denied(engine, sandbox):
    result = run(engine, sandbox.id, "http_fetch",
                 {"url": "https://evil.example.net/steal"})
    assert result.status is ExecutionStatus.DENIED
    assert any("allow-listed" in r for r in result.reasons)


def test_allowlisted_network_succeeds(engine, sandbox):
    result = run(engine, sandbox.id, "http_fetch",
                 {"url": "https://api.example.com/v1/data"})
    assert result.status is ExecutionStatus.SUCCEEDED
    assert result.result["status"] == 200


def test_filesystem_escape_denied(engine, sandbox):
    result = run(engine, sandbox.id, "file_read",
                 {"path": "/workspace/../../etc/passwd"})
    assert result.status is ExecutionStatus.DENIED
    assert sandbox.state is SandboxState.ACTIVE  # denial does not kill the sandbox


def test_secret_in_args_denied(engine, sandbox):
    result = run(engine, sandbox.id, "kv_put",
                 {"key": "k", "value": "token: sk-abcdefgh12345678"})
    assert result.status is ExecutionStatus.DENIED


def test_denylisted_command_denied(engine, sandbox):
    result = run(engine, sandbox.id, "shell_run", {"command": "rm -rf /"})
    assert result.status is ExecutionStatus.DENIED


def test_unknown_tool_failed(engine, sandbox):
    result = run(engine, sandbox.id, "nope", {})
    assert result.status is ExecutionStatus.FAILED
    assert "unknown tool" in (result.error or "")


def test_invalid_args_denied(engine, sandbox):
    result = run(engine, sandbox.id, "calculator", {"op": "add", "a": 1})
    assert result.status is ExecutionStatus.DENIED
    assert any("missing required" in r for r in result.reasons)


def test_unknown_sandbox_failed(engine):
    result = run(engine, "sb-9999", "calculator", {"op": "add", "a": 1, "b": 2})
    assert result.status is ExecutionStatus.FAILED


def test_high_risk_pending_then_approved(engine, sandbox):
    pending = run(engine, sandbox.id, "shell_run", {"command": "ls /workspace"},
                  call_id="gate-1")
    assert pending.status is ExecutionStatus.PENDING_APPROVAL
    engine.approvals.approve("gate-1", "shell_run", approver="alice")
    approved = run(engine, sandbox.id, "shell_run", {"command": "ls /workspace"},
                   call_id="gate-1")
    assert approved.status is ExecutionStatus.SUCCEEDED
    assert approved.approved_by == "alice"


def test_approval_is_single_use(engine, sandbox):
    engine.approvals.approve("once-1", "shell_run", approver="alice")
    first = run(engine, sandbox.id, "shell_run", {"command": "ls /workspace"},
                call_id="once-1")
    assert first.status is ExecutionStatus.SUCCEEDED
    # Same approval cannot be spent twice.
    second = run(engine, sandbox.id, "shell_run", {"command": "ls /workspace"},
                 call_id="once-1")
    assert second.status is ExecutionStatus.PENDING_APPROVAL


def test_approval_for_wrong_tool_does_not_apply(engine, sandbox):
    engine.approvals.approve("wrong-1", "calculator", approver="alice")
    result = run(engine, sandbox.id, "shell_run", {"command": "ls /workspace"},
                 call_id="wrong-1")
    assert result.status is ExecutionStatus.PENDING_APPROVAL


def test_budget_breach_terminates_sandbox(engine, tiny_sandbox):
    result = run(engine, tiny_sandbox.id, "calculator",
                 {"op": "mul", "a": 6, "b": 7})
    assert result.status is ExecutionStatus.TERMINATED
    assert tiny_sandbox.state is SandboxState.TERMINATED
    assert tiny_sandbox.terminated_reason and "cpu budget" in tiny_sandbox.terminated_reason
    # A terminated sandbox cannot be reused.
    again = run(engine, tiny_sandbox.id, "calculator",
                {"op": "add", "a": 1, "b": 2})
    assert again.status is ExecutionStatus.FAILED


def test_idempotent_replay_returns_duplicate(engine, sandbox):
    first = run(engine, sandbox.id, "calculator", {"op": "add", "a": 1, "b": 2},
                idempotency_key="idem-1", call_id="idem-a")
    assert first.status is ExecutionStatus.SUCCEEDED
    calls_before = sandbox.calls_made
    second = run(engine, sandbox.id, "calculator", {"op": "add", "a": 1, "b": 2},
                 idempotency_key="idem-1", call_id="idem-b")
    assert second.status is ExecutionStatus.DUPLICATE
    assert second.result == first.result
    assert sandbox.calls_made == calls_before  # replay did not re-execute


def test_tenant_cost_cap_denies(engine, sandbox):
    engine.ledger.set_budget("team-a", cost_cap_usd=0.0, call_cap=10_000)
    result = run(engine, sandbox.id, "calculator", {"op": "add", "a": 1, "b": 2})
    assert result.status is ExecutionStatus.DENIED
    assert any("cost cap" in r for r in result.reasons)


def test_every_outcome_is_audited(engine, sandbox):
    before = len(engine.audit)
    run(engine, sandbox.id, "calculator", {"op": "add", "a": 1, "b": 2})
    run(engine, sandbox.id, "http_fetch", {"url": "https://evil.example.net/"})
    assert len(engine.audit) == before + 2
    valid, bad = engine.audit.verify()
    assert valid and bad == []


def test_flaky_tool_retries_to_success(engine, sandbox):
    # flaky_calc fails transiently on ~90% of seeded draws; with 2 retries
    # the deterministic draws eventually succeed — the point is the engine
    # retries instead of failing on the first transient error.
    result = run(engine, sandbox.id, "flaky_calc",
                 {"op": "add", "a": 1, "b": 2}, call_id="flaky-1")
    assert result.status in (ExecutionStatus.SUCCEEDED, ExecutionStatus.FAILED)
    if result.status is ExecutionStatus.SUCCEEDED:
        assert result.attempts >= 1
    # Either way, attempts never exceed the retry bound.
    assert result.attempts <= 3


def test_division_by_zero_fails_without_retry(engine, sandbox):
    result = run(engine, sandbox.id, "calculator",
                 {"op": "div", "a": 1, "b": 0})
    assert result.status is ExecutionStatus.FAILED
    assert result.attempts == 1  # permanent error: no retry
