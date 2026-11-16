import pytest

fastapi = pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from sandbox.demo import build_engine
from sandbox.service import create_app


@pytest.fixture()
def client():
    return TestClient(create_app(build_engine()))


def test_health(client):
    response = client.get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert response.json()["tools"] == 8


def test_create_and_get_sandbox(client):
    response = client.post("/sandboxes", json={"tenant": "team-a"})
    assert response.status_code == 201
    sandbox_id = response.json()["id"]
    fetched = client.get(f"/sandboxes/{sandbox_id}")
    assert fetched.status_code == 200
    assert fetched.json()["tenant"] == "team-a"


def test_create_invalid_sandbox_is_422(client):
    response = client.post("/sandboxes", json={"tenant": "team-a", "cpu_ms_budget": 0})
    assert response.status_code == 422


def test_list_tools(client):
    tools = client.get("/tools").json()["tools"]
    names = {t["name"] for t in tools}
    assert "shell_run" in names
    assert any(t["risk_tier"] == "high" for t in tools)


def test_execute_safe_tool(client):
    sandbox_id = client.post("/sandboxes", json={}).json()["id"]
    response = client.post(f"/sandboxes/{sandbox_id}/execute", json={
        "tool": "calculator", "args": {"op": "add", "a": 40, "b": 2}})
    assert response.status_code == 201
    assert response.json()["result"]["value"] == 42


def test_execute_blocked_network_is_403(client):
    sandbox_id = client.post("/sandboxes", json={}).json()["id"]
    response = client.post(f"/sandboxes/{sandbox_id}/execute", json={
        "tool": "http_fetch", "args": {"url": "https://evil.example.net/"}})
    assert response.status_code == 403


def test_execute_high_risk_needs_approval_then_succeeds(client):
    sandbox_id = client.post("/sandboxes", json={}).json()["id"]
    payload = {"tool": "shell_run", "args": {"command": "ls /workspace"},
               "call_id": "api-1"}
    first = client.post(f"/sandboxes/{sandbox_id}/execute", json=payload)
    assert first.status_code == 202
    approval = client.post("/approvals/api-1", json={
        "tool": "shell_run", "approver": "alice"})
    assert approval.status_code == 201
    second = client.post(f"/sandboxes/{sandbox_id}/execute", json=payload)
    assert second.status_code == 201
    assert second.json()["approved_by"] == "alice"


def test_execute_unknown_sandbox_is_404(client):
    response = client.post("/sandboxes/sb-9999/execute", json={
        "tool": "calculator", "args": {"op": "add", "a": 1, "b": 2}})
    assert response.status_code == 404


def test_budget_endpoints(client):
    response = client.post("/budgets", json={
        "tenant": "team-a", "cost_cap_usd": 5.0, "call_cap": 100})
    assert response.status_code == 201
    budget = client.get("/budgets/team-a").json()
    assert budget["cost_cap_usd"] == 5.0
    assert budget["calls"] == 0


def test_audit_verify_endpoint(client):
    sandbox_id = client.post("/sandboxes", json={}).json()["id"]
    client.post(f"/sandboxes/{sandbox_id}/execute", json={
        "tool": "calculator", "args": {"op": "add", "a": 1, "b": 2}})
    body = client.get("/audit/verify").json()
    assert body["chain_valid"] is True
    assert body["entries"] >= 1


def test_destroy_sandbox(client):
    sandbox_id = client.post("/sandboxes", json={}).json()["id"]
    response = client.delete(f"/sandboxes/{sandbox_id}")
    assert response.status_code == 200
    assert response.json()["state"] == "terminated"
    assert client.get(f"/sandboxes/{sandbox_id}").json()["state"] == "terminated"
