"""Tests for the cost/latency ledger and fail-closed tenant budgets."""
from sandbox.config import Settings
from sandbox.ledger import Ledger
from sandbox.models import ExecutionStatus, RiskTier


def test_default_budget_from_settings():
    ledger = Ledger(Settings(tenant_cost_cap_usd=5.0, tenant_call_cap=50))
    budget = ledger.budget_for("team-a")
    assert budget.cost_cap_usd == 5.0
    assert budget.call_cap == 50


def test_check_budget_allows_under_cap():
    ledger = Ledger(Settings())
    assert ledger.check_budget("team-a", RiskTier.LOW) == []


def test_call_cap_blocks():
    ledger = Ledger(Settings())
    ledger.set_budget("team-a", cost_cap_usd=100.0, call_cap=1)
    ledger.record("team-a", "sb-0001", "calculator",
                  ExecutionStatus.SUCCEEDED, 10, 20, 0.001)
    reasons = ledger.check_budget("team-a", RiskTier.LOW)
    assert any("call cap" in r for r in reasons)


def test_cost_cap_blocks():
    ledger = Ledger(Settings())
    ledger.set_budget("team-a", cost_cap_usd=0.001, call_cap=10_000)
    ledger.record("team-a", "sb-0001", "calculator",
                  ExecutionStatus.SUCCEEDED, 10, 20, 0.001)
    reasons = ledger.check_budget("team-a", RiskTier.LOW)
    assert any("cost cap" in r for r in reasons)


def test_cost_differs_by_tier():
    ledger = Ledger(Settings())
    assert ledger.cost_for(RiskTier.LOW, 0) < ledger.cost_for(RiskTier.MEDIUM, 0)
    assert ledger.cost_for(RiskTier.MEDIUM, 0) < ledger.cost_for(RiskTier.HIGH, 0)
    assert ledger.cost_for(RiskTier.LOW, 1000) > ledger.cost_for(RiskTier.LOW, 0)


def test_record_accumulates_totals():
    ledger = Ledger(Settings())
    ledger.record("team-a", "sb-0001", "calculator",
                  ExecutionStatus.SUCCEEDED, 10, 20, 0.001)
    ledger.record("team-a", "sb-0001", "http_fetch",
                  ExecutionStatus.DENIED, 0, 0, 0.0)
    totals = ledger.tenant_totals("team-a")
    assert totals.calls == 2
    assert totals.succeeded == 1
    assert totals.denied == 1
    assert totals.cost_usd == 0.001
    assert totals.avg_latency_ms == 10.0
    assert ledger.sandbox_cost("sb-0001") == 0.001
    tool = ledger.tool_totals("calculator")
    assert tool.calls == 1 and tool.cost_usd == 0.001


def test_unknown_tenant_totals_is_none():
    assert Ledger(Settings()).tenant_totals("nobody") is None
