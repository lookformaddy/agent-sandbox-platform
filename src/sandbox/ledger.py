"""Cost / latency ledger with fail-closed tenant budgets.

Every execution (including denied ones, at zero cost) is recorded per
tenant, sandbox and tool. Tenant caps are checked *before* execution:
if the next call would push the tenant over its cost or call cap, the
call is refused and the refusal is recorded — the cap can never be
breached by spending.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from sandbox.config import Settings
from sandbox.models import ExecutionStatus, RiskTier


@dataclass
class TenantBudget:
    tenant: str
    cost_cap_usd: float
    call_cap: int


@dataclass
class TenantTotals:
    tenant: str
    calls: int = 0
    succeeded: int = 0
    denied: int = 0
    terminated: int = 0
    cost_usd: float = 0.0
    total_latency_ms: int = 0
    total_cpu_ms: int = 0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.calls if self.calls else 0.0


@dataclass
class ToolTotals:
    tool: str
    calls: int = 0
    cost_usd: float = 0.0
    total_latency_ms: int = 0

    @property
    def avg_latency_ms(self) -> float:
        return self.total_latency_ms / self.calls if self.calls else 0.0


_COST_BY_TIER = {
    RiskTier.LOW: lambda s: s.cost_per_call_low,
    RiskTier.MEDIUM: lambda s: s.cost_per_call_medium,
    RiskTier.HIGH: lambda s: s.cost_per_call_high,
}


class Ledger:
    """Per-tenant / per-tool cost and latency accounting with hard caps."""

    def __init__(self, settings: Settings | None = None) -> None:
        self.settings = settings or Settings()
        self._budgets: dict[str, TenantBudget] = {}
        self._tenants: dict[str, TenantTotals] = {}
        self._tools: dict[str, ToolTotals] = {}
        self._sandbox_cost: dict[str, float] = {}

    def set_budget(self, tenant: str, cost_cap_usd: float, call_cap: int) -> TenantBudget:
        budget = TenantBudget(tenant=tenant, cost_cap_usd=cost_cap_usd, call_cap=call_cap)
        self._budgets[tenant] = budget
        return budget

    def budget_for(self, tenant: str) -> TenantBudget:
        return self._budgets.get(tenant) or TenantBudget(
            tenant=tenant,
            cost_cap_usd=self.settings.tenant_cost_cap_usd,
            call_cap=self.settings.tenant_call_cap)

    def cost_for(self, tier: RiskTier, cpu_ms: int) -> float:
        base = _COST_BY_TIER[tier](self.settings)
        return base + cpu_ms * self.settings.cost_per_cpu_ms

    def check_budget(self, tenant: str, tier: RiskTier,
                     cpu_ms: int = 0) -> list[str]:
        """Fail-closed pre-check: reasons the next call would breach a cap."""
        budget = self.budget_for(tenant)
        totals = self._tenants.get(tenant)
        reasons: list[str] = []
        calls = totals.calls if totals is not None else 0
        spent = totals.cost_usd if totals is not None else 0.0
        if calls + 1 > budget.call_cap:
            reasons.append(
                f"tenant {tenant!r} call cap exceeded "
                f"({calls + 1} > {budget.call_cap})")
        if spent + self.cost_for(tier, cpu_ms) > budget.cost_cap_usd:
            reasons.append(
                f"tenant {tenant!r} cost cap exceeded "
                f"(${spent + self.cost_for(tier, cpu_ms):.6f} "
                f"> ${budget.cost_cap_usd:.2f})")
        return reasons

    def record(self, tenant: str, sandbox_id: str, tool: str,
               status: ExecutionStatus, cpu_ms: int, latency_ms: int,
               cost_usd: float) -> None:
        totals = self._tenants.setdefault(tenant, TenantTotals(tenant=tenant))
        totals.calls += 1
        totals.cost_usd += cost_usd
        totals.total_latency_ms += latency_ms
        totals.total_cpu_ms += cpu_ms
        if status is ExecutionStatus.SUCCEEDED:
            totals.succeeded += 1
        elif status is ExecutionStatus.DENIED:
            totals.denied += 1
        elif status is ExecutionStatus.TERMINATED:
            totals.terminated += 1
        tool_totals = self._tools.setdefault(tool, ToolTotals(tool=tool))
        tool_totals.calls += 1
        tool_totals.cost_usd += cost_usd
        tool_totals.total_latency_ms += latency_ms
        self._sandbox_cost[sandbox_id] = self._sandbox_cost.get(sandbox_id, 0.0) + cost_usd

    def tenant_totals(self, tenant: str) -> TenantTotals | None:
        return self._tenants.get(tenant)

    def tool_totals(self, tool: str) -> ToolTotals | None:
        return self._tools.get(tool)

    def sandbox_cost(self, sandbox_id: str) -> float:
        return self._sandbox_cost.get(sandbox_id, 0.0)

    def all_tenants(self) -> list[TenantTotals]:
        return sorted(self._tenants.values(), key=lambda t: t.tenant)

    def all_tools(self) -> list[ToolTotals]:
        return sorted(self._tools.values(), key=lambda t: t.tool)
