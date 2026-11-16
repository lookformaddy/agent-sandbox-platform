"""Command-line interface for the Agent Sandbox Platform.

Usage:
    agent-sandbox demo
    agent-sandbox tools
    agent-sandbox create --tenant team-a
    agent-sandbox run --sandbox sb-0001 --tool calculator \\
        --args '{"op": "add", "a": 40, "b": 2}'
    agent-sandbox approve --call-id call-0004 --tool shell_run --approver alice
    agent-sandbox audit
    agent-sandbox ledger --tenant team-a
"""
from __future__ import annotations

import argparse
import json
import sys

from sandbox.config import Settings
from sandbox.demo import build_engine, run_demo
from sandbox.models import SandboxConfig, ToolCall


def _engine():
    return build_engine(Settings())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="agent-sandbox",
                                     description="Sandboxed tool execution for AI agents")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("demo", help="Run the six-scenario demo")
    sub.add_parser("tools", help="List registered tools")
    sub.add_parser("audit", help="Verify the audit hash chain")

    p_create = sub.add_parser("create", help="Create a sandbox")
    p_create.add_argument("--tenant", default="default")
    p_create.add_argument("--cpu-ms-budget", type=int, default=60_000)
    p_create.add_argument("--memory-mb-budget", type=int, default=512)
    p_create.add_argument("--wall-ms-per-call", type=int, default=5_000)
    p_create.add_argument("--max-calls", type=int, default=100)

    p_run = sub.add_parser("run", help="Execute a tool call in a sandbox")
    p_run.add_argument("--sandbox", required=True)
    p_run.add_argument("--tool", required=True)
    p_run.add_argument("--args", default="{}", help="JSON object of tool arguments")
    p_run.add_argument("--tenant", default="default")
    p_run.add_argument("--idempotency-key", default=None)
    p_run.add_argument("--call-id", default=None)

    p_approve = sub.add_parser("approve", help="Approve a high-risk tool call")
    p_approve.add_argument("--call-id", required=True)
    p_approve.add_argument("--tool", required=True)
    p_approve.add_argument("--approver", required=True)

    p_ledger = sub.add_parser("ledger", help="Show tenant ledger totals")
    p_ledger.add_argument("--tenant", default="default")

    args = parser.parse_args(argv)

    if args.command == "demo":
        results = run_demo()
        return 0 if results["all_ok"] else 1

    engine = _engine()
    if args.command == "tools":
        for tool in engine.registry.list():
            print(f"{tool.name:<12} {tool.risk_tier.value:<7} {tool.description}")
        return 0
    if args.command == "audit":
        valid, bad = engine.audit.verify()
        print(f"audit chain valid={valid}, entries={len(engine.audit)}, bad={bad}")
        return 0 if valid else 1
    if args.command == "create":
        sandbox = engine.manager.create(SandboxConfig(
            tenant=args.tenant, cpu_ms_budget=args.cpu_ms_budget,
            memory_mb_budget=args.memory_mb_budget,
            wall_ms_per_call=args.wall_ms_per_call, max_calls=args.max_calls))
        print(json.dumps(sandbox.to_dict(), indent=2))
        return 0
    if args.command == "run":
        try:
            call_args = json.loads(args.args)
        except json.JSONDecodeError as exc:
            print(f"invalid --args JSON: {exc}", file=sys.stderr)
            return 2
        result = engine.execute(ToolCall(
            tool=args.tool, args=call_args, sandbox_id=args.sandbox,
            tenant=args.tenant, idempotency_key=args.idempotency_key,
            call_id=args.call_id))
        print(json.dumps(result.to_dict(), indent=2))
        return 0 if result.status.value == "succeeded" else 1
    if args.command == "approve":
        approval = engine.approvals.approve(args.call_id, args.tool, args.approver)
        print(f"approved {approval.tool} call {approval.call_id} by {approval.approver}")
        return 0
    if args.command == "ledger":
        totals = engine.ledger.tenant_totals(args.tenant)
        if totals is None:
            print(f"no ledger activity for tenant {args.tenant!r}")
            return 0
        print(json.dumps({
            "tenant": totals.tenant, "calls": totals.calls,
            "succeeded": totals.succeeded, "denied": totals.denied,
            "terminated": totals.terminated,
            "cost_usd": round(totals.cost_usd, 6),
            "avg_latency_ms": round(totals.avg_latency_ms, 2),
        }, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
