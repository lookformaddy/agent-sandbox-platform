"""Optional FastAPI service surface.

Requires fastapi + httpx (installed via requirements.txt). The test suite
auto-skips this module when FastAPI is not installed; it runs in CI.
"""
from __future__ import annotations

import json
from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from sandbox.config import Settings
from sandbox.demo import build_engine
from sandbox.executor import ExecutionEngine
from sandbox.models import ExecutionStatus, NetworkMode, SandboxConfig, ToolCall


class CreateSandboxRequest(BaseModel):
    tenant: str = "default"
    cpu_ms_budget: int = 60_000
    memory_mb_budget: int = 512
    wall_ms_per_call: int = 5_000
    max_calls: int = 100
    network_mode: str = "allowlist"
    network_allowlist: list[str] = Field(
        default_factory=lambda: ["api.example.com", "objects.example.com"])
    fs_roots: list[str] = Field(default_factory=lambda: ["/workspace"])


class ToolCallRequest(BaseModel):
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)
    tenant: str = "default"
    idempotency_key: str | None = None
    call_id: str | None = None


class ApproveRequest(BaseModel):
    tool: str
    approver: str


class BudgetRequest(BaseModel):
    tenant: str
    cost_cap_usd: float
    call_cap: int


def create_app(engine: ExecutionEngine | None = None) -> FastAPI:
    engine = engine or build_engine()
    app = FastAPI(title="Agent Sandbox Platform",
                  description="Sandboxed tool execution for AI agents")

    @app.get("/health")
    def health() -> dict[str, Any]:
        return {"status": "ok",
                "sandboxes": len(engine.manager.list()),
                "tools": len(engine.registry.list()),
                "audit_entries": len(engine.audit)}

    @app.post("/sandboxes", status_code=201)
    def create_sandbox(req: CreateSandboxRequest) -> dict[str, Any]:
        try:
            mode = NetworkMode(req.network_mode)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=f"invalid network_mode: {req.network_mode!r}") from exc
        try:
            config = SandboxConfig(
                tenant=req.tenant, cpu_ms_budget=req.cpu_ms_budget,
                memory_mb_budget=req.memory_mb_budget,
                wall_ms_per_call=req.wall_ms_per_call,
                max_calls=req.max_calls, network_mode=mode,
                network_allowlist=tuple(req.network_allowlist),
                fs_roots=tuple(req.fs_roots))
            sandbox = engine.manager.create(config)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        return sandbox.to_dict()

    @app.get("/sandboxes")
    def list_sandboxes(tenant: str | None = None) -> dict[str, Any]:
        return {"sandboxes": [s.to_dict() for s in engine.manager.list(tenant=tenant)]}

    @app.get("/sandboxes/{sandbox_id}")
    def get_sandbox(sandbox_id: str) -> dict[str, Any]:
        sandbox = engine.manager.get(sandbox_id)
        if sandbox is None:
            raise HTTPException(status_code=404, detail="unknown sandbox")
        return sandbox.to_dict()

    @app.delete("/sandboxes/{sandbox_id}")
    def destroy_sandbox(sandbox_id: str) -> dict[str, Any]:
        try:
            sandbox = engine.manager.destroy(sandbox_id)
        except ValueError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        return sandbox.to_dict()

    @app.get("/tools")
    def list_tools() -> dict[str, Any]:
        return {"tools": [{
            "name": t.name, "description": t.description,
            "risk_tier": t.risk_tier.value, "args_schema": t.args_schema,
        } for t in engine.registry.list()]}

    @app.post("/sandboxes/{sandbox_id}/execute", status_code=201)
    def execute(sandbox_id: str, req: ToolCallRequest) -> dict[str, Any]:
        if engine.manager.get(sandbox_id) is None:
            raise HTTPException(status_code=404, detail="unknown sandbox")
        result = engine.execute(ToolCall(
            tool=req.tool, args=req.args, sandbox_id=sandbox_id,
            tenant=req.tenant, idempotency_key=req.idempotency_key,
            call_id=req.call_id))
        body = result.to_dict()
        if result.status is ExecutionStatus.DENIED:
            raise HTTPException(status_code=403, detail=json.dumps(body))
        if result.status is ExecutionStatus.PENDING_APPROVAL:
            raise HTTPException(status_code=202, detail=json.dumps(body))
        if result.status is ExecutionStatus.TERMINATED:
            raise HTTPException(status_code=410, detail=json.dumps(body))
        if result.status is ExecutionStatus.FAILED:
            raise HTTPException(status_code=422, detail=json.dumps(body))
        return body

    @app.post("/approvals/{call_id}", status_code=201)
    def approve(call_id: str, req: ApproveRequest) -> dict[str, Any]:
        approval = engine.approvals.approve(call_id, req.tool, req.approver)
        engine.audit.append("approval.granted", {
            "call_id": call_id, "tool": req.tool, "approver": req.approver})
        return {"call_id": approval.call_id, "tool": approval.tool,
                "approver": approval.approver}

    @app.post("/budgets", status_code=201)
    def set_budget(req: BudgetRequest) -> dict[str, Any]:
        budget = engine.ledger.set_budget(req.tenant, req.cost_cap_usd, req.call_cap)
        return {"tenant": budget.tenant, "cost_cap_usd": budget.cost_cap_usd,
                "call_cap": budget.call_cap}

    @app.get("/budgets/{tenant}")
    def get_budget(tenant: str) -> dict[str, Any]:
        totals = engine.ledger.tenant_totals(tenant)
        budget = engine.ledger.budget_for(tenant)
        return {"tenant": tenant, "cost_cap_usd": budget.cost_cap_usd,
                "call_cap": budget.call_cap,
                "calls": totals.calls if totals else 0,
                "cost_usd": round(totals.cost_usd, 6) if totals else 0.0}

    @app.get("/audit/verify")
    def audit_verify() -> dict[str, Any]:
        valid, bad = engine.audit.verify()
        return {"chain_valid": valid, "entries": len(engine.audit),
                "bad_entries": bad}

    return app


app = create_app()
