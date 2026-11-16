"""Tool registry with JSON-schema argument validation.

The validator is a small, dependency-free subset of JSON Schema covering
the constructs tool authors actually use: ``type``, ``properties``,
``required``, ``enum``, ``minimum``/``maximum``, ``minLength``/``maxLength``,
``pattern`` and ``additionalProperties``. Anything outside the subset is
ignored rather than silently accepted — unknown keywords never make an
invalid payload valid.
"""
from __future__ import annotations

import re
from typing import Any

from sandbox.models import RiskTier, ToolDefinition

_TYPE_CHECKS: dict[str, tuple[type, ...]] = {
    "string": (str,),
    "integer": (int,),
    "number": (int, float),
    "boolean": (bool,),
    "array": (list,),
    "object": (dict,),
    "null": (type(None),),
}


class SchemaValidationError(ValueError):
    """Raised when tool arguments fail JSON-schema validation."""


def validate_args(args: dict[str, Any], schema: dict[str, Any],
                  path: str = "$") -> list[str]:
    """Validate ``args`` against ``schema``; return error strings (empty = valid)."""
    errors: list[str] = []
    if not isinstance(args, dict):
        return [f"{path}: arguments must be an object"]
    schema_type = schema.get("type")
    if schema_type == "object" or "properties" in schema or "required" in schema:
        if not isinstance(args, dict):
            return [f"{path}: expected object"]
    for name in schema.get("required", []):
        if name not in args:
            errors.append(f"{path}.{name}: missing required property")
    properties = schema.get("properties", {})
    for name, value in args.items():
        if name not in properties:
            if schema.get("additionalProperties") is False:
                errors.append(f"{path}.{name}: additional property not allowed")
            continue
        errors.extend(_validate_value(value, properties[name], f"{path}.{name}"))
    return errors


def _validate_value(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    if expected:
        # bool is a subclass of int in Python — exclude it from integer/number.
        if expected == "integer" and isinstance(value, bool):
            errors.append(f"{path}: expected integer, got boolean")
            return errors
        if expected == "number" and isinstance(value, bool):
            errors.append(f"{path}: expected number, got boolean")
            return errors
        allowed = _TYPE_CHECKS.get(expected)
        if allowed is not None and not isinstance(value, allowed):
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return errors
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} is not one of {schema['enum']}")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: {value} is below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: {value} is above maximum {schema['maximum']}")
    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema and not re.search(schema["pattern"], value):
            errors.append(f"{path}: does not match pattern {schema['pattern']!r}")
    if isinstance(value, dict):
        errors.extend(validate_args(value, schema, path))
    if isinstance(value, list) and "items" in schema:
        for i, item in enumerate(value):
            errors.extend(_validate_value(item, schema["items"], f"{path}[{i}]"))
    return errors


class ToolRegistry:
    """Versioned registry of tools available inside sandboxes."""

    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if tool.name in self._tools:
            raise ValueError(f"tool {tool.name!r} is already registered")
        if not tool.args_schema:
            raise ValueError(f"tool {tool.name!r} must declare an args schema")
        self._tools[tool.name] = tool

    def get(self, name: str) -> ToolDefinition | None:
        return self._tools.get(name)

    def list(self, risk_tier: RiskTier | None = None) -> list[ToolDefinition]:
        tools = sorted(self._tools.values(), key=lambda t: t.name)
        if risk_tier is not None:
            tools = [t for t in tools if t.risk_tier is risk_tier]
        return tools

    def validate_call(self, tool_name: str, args: dict[str, Any]) -> list[str]:
        """Schema-validate args for a tool; unknown tool is an error."""
        tool = self.get(tool_name)
        if tool is None:
            return [f"unknown tool {tool_name!r}"]
        return validate_args(args, tool.args_schema)
