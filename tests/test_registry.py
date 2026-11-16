"""Tests for the tool registry and JSON-schema validation."""
import pytest

from sandbox.demo_data import default_tools
from sandbox.models import RiskTier, ToolDefinition
from sandbox.registry import ToolRegistry, validate_args


@pytest.fixture()
def registry():
    registry = ToolRegistry()
    for tool in default_tools():
        registry.register(tool)
    return registry


def test_default_tools_registered(registry):
    names = {t.name for t in registry.list()}
    assert {"calculator", "kv_get", "kv_put", "file_read", "file_write",
            "http_fetch", "shell_run", "flaky_calc"} <= names


def test_duplicate_registration_rejected(registry):
    with pytest.raises(ValueError, match="already registered"):
        registry.register(ToolDefinition(
            name="calculator", description="dup", args_schema={"type": "object"}))


def test_missing_schema_rejected(registry):
    with pytest.raises(ValueError, match="must declare an args schema"):
        registry.register(ToolDefinition(
            name="no-schema", description="x", args_schema={}))


def test_list_by_risk_tier(registry):
    high = registry.list(risk_tier=RiskTier.HIGH)
    assert [t.name for t in high] == ["shell_run"]
    assert all(t.risk_tier is RiskTier.LOW for t in registry.list(risk_tier=RiskTier.LOW))


def test_valid_args_pass(registry):
    assert registry.validate_call(
        "calculator", {"op": "add", "a": 1, "b": 2}) == []


def test_unknown_tool_is_error(registry):
    errors = registry.validate_call("nope", {})
    assert errors and "unknown tool" in errors[0]


def test_missing_required_property(registry):
    errors = registry.validate_call("calculator", {"op": "add", "a": 1})
    assert any("missing required" in e for e in errors)


def test_wrong_type_rejected(registry):
    errors = registry.validate_call(
        "calculator", {"op": "add", "a": "one", "b": 2})
    assert any("expected number" in e for e in errors)


def test_bool_is_not_integer():
    errors = validate_args({"n": True}, {
        "type": "object",
        "properties": {"n": {"type": "integer"}},
        "required": ["n"]})
    assert any("boolean" in e for e in errors)


def test_enum_rejected(registry):
    errors = registry.validate_call(
        "calculator", {"op": "pow", "a": 2, "b": 3})
    assert any("not one of" in e for e in errors)


def test_additional_properties_rejected(registry):
    errors = registry.validate_call(
        "calculator", {"op": "add", "a": 1, "b": 2, "extra": 9})
    assert any("additional property" in e for e in errors)


def test_url_pattern_enforced(registry):
    errors = registry.validate_call(
        "http_fetch", {"url": "not-a-url"})
    assert any("pattern" in e for e in errors)
    assert registry.validate_call(
        "http_fetch", {"url": "https://api.example.com/x"}) == []


def test_min_max_length_enforced():
    schema = {"type": "object",
              "properties": {"s": {"type": "string", "minLength": 2, "maxLength": 3}}}
    assert validate_args({"s": "a"}, schema)
    assert validate_args({"s": "abcd"}, schema)
    assert validate_args({"s": "ab"}, schema) == []
