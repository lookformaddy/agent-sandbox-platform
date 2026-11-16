"""Default tool catalog and demo scenarios (all synthetic)."""
from __future__ import annotations

from sandbox.models import RiskTier, ToolDefinition


def default_tools() -> list[ToolDefinition]:
    """The seven tools shipped with the platform."""
    return [
        ToolDefinition(
            name="calculator",
            description="Deterministic arithmetic (add/sub/mul/div).",
            args_schema={
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "sub", "mul", "div"]},
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["op", "a", "b"],
                "additionalProperties": False,
            },
            risk_tier=RiskTier.LOW,
            behaviour={"kind": "calc"},
        ),
        ToolDefinition(
            name="kv_get",
            description="Read a synthetic key-value entry.",
            args_schema={
                "type": "object",
                "properties": {"key": {"type": "string", "minLength": 1, "maxLength": 128}},
                "required": ["key"],
                "additionalProperties": False,
            },
            risk_tier=RiskTier.LOW,
            behaviour={"kind": "kv", "op": "get"},
        ),
        ToolDefinition(
            name="kv_put",
            description="Write a synthetic key-value entry.",
            args_schema={
                "type": "object",
                "properties": {
                    "key": {"type": "string", "minLength": 1, "maxLength": 128},
                    "value": {"type": "string", "maxLength": 4096},
                },
                "required": ["key", "value"],
                "additionalProperties": False,
            },
            risk_tier=RiskTier.LOW,
            behaviour={"kind": "kv", "op": "put"},
        ),
        ToolDefinition(
            name="file_read",
            description="Read a file inside the sandbox filesystem roots.",
            args_schema={
                "type": "object",
                "properties": {"path": {"type": "string", "minLength": 1}},
                "required": ["path"],
                "additionalProperties": False,
            },
            risk_tier=RiskTier.MEDIUM,
            behaviour={"kind": "file_read"},
        ),
        ToolDefinition(
            name="file_write",
            description="Write a file inside the sandbox filesystem roots.",
            args_schema={
                "type": "object",
                "properties": {
                    "path": {"type": "string", "minLength": 1},
                    "content": {"type": "string", "maxLength": 65536},
                },
                "required": ["path", "content"],
                "additionalProperties": False,
            },
            risk_tier=RiskTier.MEDIUM,
            behaviour={"kind": "file_write"},
        ),
        ToolDefinition(
            name="http_fetch",
            description="Fetch a URL whose host is on the network allow-list.",
            args_schema={
                "type": "object",
                "properties": {
                    "url": {"type": "string",
                            "pattern": r"^https?://[A-Za-z0-9.-]+(/.*)?$"},
                    "method": {"type": "string", "enum": ["GET", "POST"]},
                },
                "required": ["url"],
                "additionalProperties": False,
            },
            risk_tier=RiskTier.MEDIUM,
            behaviour={"kind": "http"},
        ),
        ToolDefinition(
            name="shell_run",
            description="Run a shell command inside the sandbox (high-risk: "
                        "requires human approval; denylist enforced).",
            args_schema={
                "type": "object",
                "properties": {
                    "command": {"type": "string", "minLength": 1, "maxLength": 2048},
                },
                "required": ["command"],
                "additionalProperties": False,
            },
            risk_tier=RiskTier.HIGH,
            timeout_ms=5_000,
            behaviour={"kind": "shell"},
        ),
        ToolDefinition(
            name="flaky_calc",
            description="Calculator that fails transiently on a seeded draw, "
                        "to demonstrate bounded retries with backoff.",
            args_schema={
                "type": "object",
                "properties": {
                    "op": {"type": "string", "enum": ["add", "sub", "mul", "div"]},
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["op", "a", "b"],
                "additionalProperties": False,
            },
            risk_tier=RiskTier.LOW,
            behaviour={"kind": "calc", "transient_failure_rate": 0.9},
        ),
    ]
