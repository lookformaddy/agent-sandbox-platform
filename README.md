# Agent Sandbox Platform

**Run agent tools like they might be hostile — because sometimes they are.**
A secure execution plane that runs AI agent tool calls inside isolated
sandboxes with resource limits, network allow-lists, filesystem scoping,
approval gates, and a tamper-evident audit trail. Every decision is
fail-closed: anything not explicitly allowed is denied.

## The enterprise problem

Agents that call tools are powerful and dangerous in equal measure. A
single hallucinated shell command, a prompt-injected exfiltration URL, or
a runaway loop can delete data, leak credentials, or burn a budget in
minutes. Most agent frameworks treat tool execution as a trusted
passthrough. This platform treats it as a security boundary:

- **Sandbox lifecycle** — create, reuse, suspend, and destroy sandboxes
  with validated configs (CPU / memory / time limits, network modes,
  filesystem scopes). A terminated sandbox can never be revived.
- **Tool registry** — versioned tools with JSON-schema argument
  validation, per-tool risk tiers, and approval gates for high-risk tools.
- **Fail-closed policy engine** — network allow-list (deny by default),
  filesystem path scoping (no `..` escape), secret / credential blocking
  in argument values, and a shell command denylist.
- **Resource enforcement** — simulated CPU / memory / wall-time budgets
  with breach accounting; a breach terminates the sandbox immediately.
- **Execution engine** — timeouts, retries with backoff on transient
  failures only, idempotency keys, and deterministic seeded results.
- **Audit** — SHA-256 hash-chained log of every execution, approval, and
  policy decision, with tamper detection.
- **Cost / latency ledger** — per-tenant, per-sandbox, per-tool
  accounting with fail-closed budget caps checked *before* execution.

## What it demonstrates

- A layered defence-in-depth pipeline: idempotency → sandbox state →
  schema validation → tenant budget → policy → approval → resources →
  execution, with an audit entry for every outcome.
- Deterministic, seeded simulation: the same tool + args + seed always
  yields the same result and the same resource profile, so behaviour is
  reproducible and testable without any real process or network.
- Fail-closed defaults everywhere: unknown tool, unknown sandbox,
  unlisted host, out-of-scope path, secret in an argument, missing
  approval, or exceeded budget all refuse the call *before* any side
  effect.

## Quickstart

```bash
make install   # create .venv and install dependencies
make test      # run the test suite
make demo      # six-scenario demo (see below)
make serve     # FastAPI service on :8000
```

Or without make:

```bash
python -m venv .venv && .venv/bin/pip install -r requirements.txt
PYTHONPATH=src python -m pytest -q
PYTHONPATH=src python scripts/demo.py
PYTHONPATH=src python -m uvicorn sandbox.service:app --port 8000
```

## The demo

`scripts/demo.py` runs six scenarios end to end:

1. **Safe tool run succeeds** — `calculator` 40 + 2 = 42 inside a sandbox.
2. **Blocked network call denied** — `http_fetch` to `evil.example.net`
   is refused (host not on the allow-list).
3. **Filesystem escape denied** — `file_read` of
   `/workspace/../../etc/passwd` is refused (outside the sandbox roots).
4. **High-risk tool requires approval, then runs** — `shell_run` returns
   `pending_approval` until a human approves it, then succeeds.
5. **Budget breach terminates execution** — a calculator call in a
   1 cpu-ms sandbox is terminated and the sandbox is destroyed.
6. **Audit chain verifies** — the hash chain is valid, and a forged
   entry is detected.

The demo also prints the per-tenant ledger (calls, succeeded, denied,
total cost).

## API

| Endpoint | Description |
|---|---|
| `GET /health` | Status, sandbox/tool/audit counts |
| `POST /sandboxes` | Create a sandbox (422 on invalid config) |
| `GET /sandboxes` | List sandboxes (optional `?tenant=`) |
| `GET /sandboxes/{id}` | Get one sandbox |
| `DELETE /sandboxes/{id}` | Destroy (terminate) a sandbox |
| `GET /tools` | List registered tools with risk tiers |
| `POST /sandboxes/{id}/execute` | Execute a tool call (201 / 403 denied / 202 pending approval / 410 terminated / 422 failed) |
| `POST /approvals/{call_id}` | Approve a high-risk call |
| `POST /budgets` / `GET /budgets/{tenant}` | Set / read tenant budgets and spend |
| `GET /audit/verify` | Verify the hash-chained audit log |

## CLI

```bash
agent-sandbox demo
agent-sandbox tools
agent-sandbox create --tenant team-a
agent-sandbox run --sandbox sb-0001 --tool calculator \
    --args '{"op": "add", "a": 40, "b": 2}'
agent-sandbox approve --call-id call-0004 --tool shell_run --approver alice
agent-sandbox audit
agent-sandbox ledger --tenant team-a
```

## Tools shipped

| Tool | Risk | Behaviour |
|---|---|---|
| `calculator` | low | Deterministic arithmetic |
| `kv_get` / `kv_put` | low | Synthetic key-value store |
| `file_read` | medium | Read inside sandbox filesystem roots |
| `file_write` | medium | Write inside sandbox filesystem roots |
| `http_fetch` | medium | Fetch only allow-listed hosts |
| `shell_run` | **high** | Shell command; approval + denylist enforced |
| `flaky_calc` | low | Calculator with seeded transient failures (retry demo) |

## Policy defaults

- Network mode `allowlist`; default hosts `api.example.com`,
  `objects.example.com`. Metadata (`169.254.169.254`) and loopback hosts
  are never reachable, even in `open` mode.
- Filesystem roots default to `/workspace`; paths are lexically
  normalised before the scope check, so `..` escapes are caught.
- Secrets are blocked by pattern (AWS keys, private keys, `sk-`/`ghp_`
  tokens, `password=`/`api_key=` assignments, bearer tokens).
- Shell denylist blocks `rm -rf`, `sudo`, `curl|sh`, `chmod 777`,
  netcat listeners, writes to `/dev/sda`, and similar.
- Approvals are single-use: one approval covers one call.
- Tenant caps default to $10.00 / 10,000 calls; checked before execution.

All defaults are overridable via `ASP_*` environment variables (see
`.env.example`).

## Architecture

See [`docs/architecture.md`](docs/architecture.md) for the component map,
the execution pipeline, and design decisions (simulation seam, why
fail-closed ordering matters, in-memory state, deterministic seeding).

## Project layout

```
src/sandbox/
  config.py    Settings (env-overridable)
  models.py    Domain dataclasses and enums
  policy.py    Fail-closed policy engine (network / fs / secrets / commands)
  registry.py  Tool registry + JSON-schema validation
  sandbox.py   Sandbox lifecycle manager
  resources.py Resource profiles + budget enforcement
  approvals.py Single-use approval store
  executor.py  Execution pipeline (fail-closed, idempotent, retried)
  audit.py     SHA-256 hash-chained audit log
  ledger.py    Cost/latency ledger + tenant budget caps
  demo_data.py Default tool catalog
  service.py   FastAPI service
  cli.py       Command-line interface
  demo.py      Six-scenario demo runner
tests/         pytest suite (policy, registry, sandbox, resources,
               audit, ledger, executor, CLI, API)
scripts/demo.py
docs/architecture.md  docs/github-actions-ci.yml
```

## Notes

- **Offline by design.** Execution is simulated deterministically; no
  real process is spawned and no network is touched. The simulator is
  the documented seam where a real container runtime (gVisor /
  Firecracker / Docker) plugs in — the policy, approval, resource, audit,
  and ledger layers are runtime-agnostic.
- **Clean-room.** All code is original and all data is synthetic. No
  employer code, data, or proprietary material is included.
- **CI** ships as `docs/github-actions-ci.yml` — move it to
  `.github/workflows/ci.yml` to activate (the push integration used for
  this repo cannot carry workflow files, so it lives in `docs/`).
