import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

import pytest

from sandbox.audit import AuditLog
from sandbox.config import Settings
from sandbox.demo import build_engine
from sandbox.demo_data import default_tools
from sandbox.executor import ExecutionEngine
from sandbox.ledger import Ledger
from sandbox.models import SandboxConfig, ToolCall
from sandbox.registry import ToolRegistry
from sandbox.sandbox import SandboxManager


@pytest.fixture()
def settings():
    return Settings()


@pytest.fixture()
def engine(settings):
    return build_engine(settings)


@pytest.fixture()
def sandbox(engine):
    return engine.manager.create(SandboxConfig(tenant="team-a"))


@pytest.fixture()
def tiny_sandbox(engine):
    return engine.manager.create(SandboxConfig(
        tenant="team-a", cpu_ms_budget=1, memory_mb_budget=512,
        wall_ms_per_call=5_000, max_calls=100))


def run(engine, sandbox_id, tool, args, tenant="team-a", **kwargs):
    return engine.execute(ToolCall(
        tool=tool, args=args, sandbox_id=sandbox_id, tenant=tenant, **kwargs))
