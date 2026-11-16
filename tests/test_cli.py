"""Tests for approval gates and the demo/CLI surface."""
from sandbox.approvals import ApprovalStore
from sandbox.demo import run_demo


def test_approval_store_single_use():
    store = ApprovalStore()
    assert not store.has("c1", "shell_run")
    store.approve("c1", "shell_run", approver="alice")
    assert store.has("c1", "shell_run")
    approval = store.consume("c1", "shell_run")
    assert approval is not None and approval.approver == "alice"
    assert not store.has("c1", "shell_run")


def test_approval_wrong_tool_not_consumed():
    store = ApprovalStore()
    store.approve("c1", "calculator", approver="alice")
    assert store.consume("c1", "shell_run") is None
    assert store.has("c1", "calculator")


def test_revoke():
    store = ApprovalStore()
    store.approve("c1", "shell_run", approver="alice")
    assert store.revoke("c1") is True
    assert store.revoke("c1") is False


def test_demo_passes():
    results = run_demo()
    assert results["all_ok"] is True
    assert results["safe_value"] == 42
    assert results["audit_valid"] is True
    assert results["tamper_detected"] is True
    assert results["ledger_succeeded"] >= 2
    assert results["ledger_denied"] >= 2


def test_cli_demo_and_tools(capsys):
    from sandbox.cli import main
    assert main(["tools"]) == 0
    assert "calculator" in capsys.readouterr().out
    assert main(["demo"]) == 0
    out = capsys.readouterr().out
    assert "Demo PASSED" in out


def test_cli_run_and_audit(capsys):
    # Each main() call builds a fresh in-process engine, so a sandbox
    # created in one invocation does not exist in the next: running
    # against it fails cleanly (exit 1) instead of crashing.
    from sandbox.cli import main
    assert main(["create", "--tenant", "team-a"]) == 0
    assert main(["run", "--sandbox", "sb-0001", "--tool", "calculator",
                 "--args", '{"op": "add", "a": 2, "b": 3}']) == 1
    assert main(["audit"]) == 0
