"""Tests for resource enforcement and budget breaches."""
from sandbox.models import SandboxConfig, ToolCall
from sandbox.resources import ResourceEnforcer
from sandbox.sandbox import SandboxManager


def _sandbox(**overrides):
    config = SandboxConfig(tenant="team-a")
    for key, value in overrides.items():
        setattr(config, key, value)
    return SandboxManager().create(config)


def _call(tool="calculator", **args):
    return ToolCall(tool=tool, args={"op": "add", "a": 1, "b": 2, **args},
                    sandbox_id="sb-0001")


def test_profile_is_deterministic():
    enforcer = ResourceEnforcer()
    sandbox = _sandbox()
    call = _call()
    p1, _ = enforcer.check(sandbox, call, "calc", base_seed=42)
    p2, _ = enforcer.check(sandbox, call, "calc", base_seed=42)
    assert (p1.cpu_ms, p1.memory_mb, p1.latency_ms) == \
           (p2.cpu_ms, p2.memory_mb, p2.latency_ms)


def test_profile_differs_by_tool_kind():
    enforcer = ResourceEnforcer()
    sandbox = _sandbox()
    calc, _ = enforcer.check(sandbox, _call(), "calc", base_seed=42)
    shell, _ = enforcer.check(sandbox, _call(tool="shell_run"), "shell", base_seed=42)
    assert shell.cpu_ms > calc.cpu_ms


def test_no_breach_for_generous_budget():
    enforcer = ResourceEnforcer()
    sandbox = _sandbox()
    _, breaches = enforcer.check(sandbox, _call(), "calc", base_seed=42)
    assert breaches == []


def test_cpu_budget_breach():
    enforcer = ResourceEnforcer()
    sandbox = _sandbox(cpu_ms_budget=1)
    _, breaches = enforcer.check(sandbox, _call(), "calc", base_seed=42)
    assert any(b.kind == "cpu_budget" for b in breaches)


def test_memory_budget_breach():
    enforcer = ResourceEnforcer()
    sandbox = _sandbox(memory_mb_budget=1)
    _, breaches = enforcer.check(sandbox, _call(), "calc", base_seed=42)
    assert any(b.kind == "memory_budget" for b in breaches)


def test_wall_time_breach():
    enforcer = ResourceEnforcer()
    sandbox = _sandbox(wall_ms_per_call=1)
    _, breaches = enforcer.check(sandbox, _call(), "calc", base_seed=42)
    assert any(b.kind == "wall_time" for b in breaches)


def test_call_budget_breach():
    enforcer = ResourceEnforcer()
    sandbox = _sandbox(max_calls=1)
    sandbox.calls_made = 1
    _, breaches = enforcer.check(sandbox, _call(), "calc", base_seed=42)
    assert any(b.kind == "call_budget" for b in breaches)


def test_account_charges_sandbox():
    enforcer = ResourceEnforcer()
    sandbox = _sandbox()
    profile, _ = enforcer.check(sandbox, _call(), "calc", base_seed=42)
    enforcer.account(sandbox, profile)
    assert sandbox.calls_made == 1
    assert sandbox.cpu_ms_used == profile.cpu_ms
    assert sandbox.memory_mb_peak == profile.memory_mb
