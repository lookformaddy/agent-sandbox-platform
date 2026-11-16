"""Tests for sandbox lifecycle management."""
import pytest

from sandbox.models import SandboxConfig, SandboxState
from sandbox.sandbox import SandboxError, SandboxManager


@pytest.fixture()
def manager():
    return SandboxManager()


def test_create_valid(manager):
    sandbox = manager.create(SandboxConfig(tenant="team-a"))
    assert sandbox.id == "sb-0001"
    assert sandbox.state is SandboxState.ACTIVE
    assert manager.get("sb-0001") is sandbox


def test_create_invalid_config_rejected(manager):
    with pytest.raises(SandboxError, match="cpu_ms_budget must be positive"):
        manager.create(SandboxConfig(cpu_ms_budget=0))
    with pytest.raises(SandboxError, match="allowlist mode requires"):
        manager.create(SandboxConfig(network_allowlist=()))
    with pytest.raises(SandboxError, match="filesystem root must be absolute"):
        manager.create(SandboxConfig(fs_roots=("relative/path",)))
    with pytest.raises(SandboxError, match="at least one filesystem root"):
        manager.create(SandboxConfig(fs_roots=()))


def test_ids_are_unique_and_sequential(manager):
    a = manager.create(SandboxConfig())
    b = manager.create(SandboxConfig())
    assert a.id != b.id
    assert a.id == "sb-0001" and b.id == "sb-0002"


def test_reuse_active_sandbox(manager):
    sandbox = manager.create(SandboxConfig())
    assert manager.reuse(sandbox.id) is sandbox


def test_reuse_unknown_raises(manager):
    with pytest.raises(SandboxError, match="unknown sandbox"):
        manager.reuse("sb-9999")


def test_reuse_terminated_raises(manager):
    sandbox = manager.create(SandboxConfig())
    manager.destroy(sandbox.id)
    with pytest.raises(SandboxError, match="terminated"):
        manager.reuse(sandbox.id)


def test_reuse_call_budget_exhausted(manager):
    sandbox = manager.create(SandboxConfig(max_calls=1))
    sandbox.calls_made = 1
    with pytest.raises(SandboxError, match="call budget"):
        manager.reuse(sandbox.id)


def test_suspend_resume_destroy(manager):
    sandbox = manager.create(SandboxConfig())
    manager.suspend(sandbox.id)
    assert sandbox.state is SandboxState.SUSPENDED
    with pytest.raises(SandboxError, match="suspended"):
        manager.reuse(sandbox.id)
    manager.resume(sandbox.id)
    assert sandbox.state is SandboxState.ACTIVE
    manager.destroy(sandbox.id, reason="test done")
    assert sandbox.state is SandboxState.TERMINATED
    assert sandbox.terminated_reason == "test done"


def test_list_filters(manager):
    a = manager.create(SandboxConfig(tenant="team-a"))
    manager.create(SandboxConfig(tenant="team-b"))
    manager.destroy(a.id)
    assert len(manager.list()) == 2
    assert len(manager.list(tenant="team-a")) == 1
    assert len(manager.list(state=SandboxState.TERMINATED)) == 1
    assert len(manager.list(state=SandboxState.ACTIVE)) == 1
