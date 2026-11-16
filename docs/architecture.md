# Architecture — Agent Sandbox Platform

## The problem in one paragraph

An AI agent's tool call is an instruction from an untrusted author (the
model, the prompt, or an attacker who poisoned either) executed with the
agent's privileges. The platform inserts a control plane between the
agent and the tool: every call is validated, authorised, budgeted, and
audited before anything runs, and the sandbox that runs it is destroyed
the moment it misbehaves.

## Component map

```
                ┌─────────────────────────────┐
                │  CLI / FastAPI service      │
                └──────────────┬──────────────┘
                               ▼
                ┌─────────────────────────────┐
                │  ExecutionEngine (pipeline) │
                └──────┬──────────────────────┘
         ┌─────────────┼───────────────────────────────┐
         ▼             ▼             ▼                   ▼
   SandboxManager  ToolRegistry  PolicyEngine   ResourceEnforcer
   (lifecycle)     (schemas)     (fail-closed)  (budgets)
         │             │             │                   │
         └─────────────┴──────┬──────┴───────────────────┘
                              ▼
                 ┌────────────────────────┐
                 │ ApprovalStore (gates)  │
                 └────────────────────────┘
         ┌──────────────┐      ┌───────────────────┐
         ▼              ▼      ▼                   ▼
   Ledger (cost/    AuditLog (SHA-256    Deterministic
   latency, caps)   hash chain)         simulator
```

| Component | Responsibility |
|---|---|
| `SandboxManager` | Create / reuse / suspend / resume / destroy sandboxes; validates configs; a terminated sandbox can never be revived. |
| `ToolRegistry` | Versioned tools, JSON-schema argument validation (stdlib subset validator). |
| `PolicyEngine` | Network allow-list, filesystem scoping, secret blocking, command denylist. Returns ALLOW / DENY / REQUIRE_APPROVAL. |
| `ResourceEnforcer` | Deterministic per-call resource profile (seeded); checks CPU / memory / wall-time / call budgets; breach accounting. |
| `ApprovalStore` | Single-use approvals for high-risk tools; no auto-approve path. |
| `ExecutionEngine` | The pipeline: idempotency → sandbox → schema → tenant budget → policy → approval → resources → simulated execution with bounded retries. |
| `Ledger` | Per-tenant / sandbox / tool cost and latency; fail-closed caps checked before execution. |
| `AuditLog` | Append-only SHA-256 hash chain over every execution, approval, and policy outcome; tamper detection. |
| `Simulator` | Deterministic tool outcomes (seeded on tool + args + seed). The seam where a real container runtime plugs in. |

## Execution pipeline (fail-closed order)

The order is deliberate — each step is cheaper and safer than the next,
and a refusal anywhere stops the call before any side effect:

1. **Idempotency** — a repeated `idempotency_key` returns the stored
   result (`duplicate`) without re-executing.
2. **Sandbox state** — the sandbox must exist, be active, and have call
   budget left.
3. **Schema validation** — arguments must satisfy the tool's JSON schema
   (unknown tool, missing required field, wrong type, extra property).
4. **Tenant budget pre-check** — cost / call caps; the cap can never be
   breached by spending because the check happens before execution.
5. **Policy decision** — secrets, network allow-list, filesystem scope,
   command denylist. DENY ends the call; HIGH risk yields
   REQUIRE_APPROVAL.
6. **Approval gate** — a high-risk call consumes a single-use approval
   for that exact call id; without one it returns `pending_approval`.
7. **Resource check** — the deterministic profile must fit the sandbox
   budget and the per-call wall limit. A breach terminates the sandbox
   (not just the call) so the overrun cannot continue.
8. **Execution with retries** — transient failures are retried with
   exponential backoff (bounded, default 2 retries); permanent errors
   fail immediately without retry.
9. **Accounting** — the ledger records cost/latency and the audit log
   records the outcome, for every status including denials.

## Data flow

```
Agent ──ToolCall──▶ Engine ──▶ policy/resource checks
                       │            │
                       │ allow      │ deny / breach
                       ▼            ▼
                 Simulator     AuditLog + Ledger
                       │
                       ▼
                 ExecutionResult (status, result, cpu/memory/latency/cost)
```

## Design decisions and trade-offs

- **Simulation instead of real containers.** The platform ships a
  deterministic simulator so it runs anywhere (including CI) with no
  Docker daemon, no network, and no credentials. The trade-off is that
  it cannot catch real-world escape techniques (kernel exploits,
  container breakouts); the simulator is explicitly the seam where a
  real runtime (gVisor / Firecracker / Docker) plugs in, behind the
  same policy / approval / resource / audit interfaces.
- **Fail-closed everywhere.** Every check defaults to deny: unknown
  hosts, unknown tools, unknown sandboxes, unlisted paths, and missing
  approvals all refuse. The cost is a stricter authoring experience
  (tools must be registered, hosts allow-listed); the benefit is that a
  misconfiguration fails safe instead of failing open.
- **Deterministic seeding.** Resource profiles and simulated outcomes
  derive from `SHA-256(seed ‖ tool ‖ canonical args)`, never from
  wall-clock time or `PYTHONHASHSEED`, so tests and demos are
  reproducible across machines and runs.
- **Single-use approvals.** An approval covers exactly one call id.
  Re-approving is cheap; re-using an approval is a privilege-escalation
  hole, so it is not permitted.
- **Breach terminates the sandbox, not just the call.** A budget breach
  signals a runaway or hostile agent; killing only the call leaves the
  sandbox alive to try again. Termination is audited with the reason.
- **In-memory state.** Sandboxes, ledger, and audit log live in memory
  (the audit log serialises to JSONL for persistence). This keeps the
  platform dependency-free and testable; a production deployment would
  back the audit log with append-only storage and the ledger with a
  durable store.
- **No numpy / sklearn.** All statistics and validation are stdlib, so
  the core runs on a bare Python install; FastAPI is optional and the
  service tests auto-skip without it.
- **Secret scanning is pattern-based, not a DLP service.** It catches
  the common credential shapes (AWS keys, private keys, `sk-`/`ghp_`
  tokens, `password=` assignments). It is a backstop, not a substitute
  for not passing secrets to tools at all.

## Security notes

- The denylist is a second layer, not the primary control: even a
  command that passes the denylist still needs approval (high-risk
  tier) and still runs inside the sandbox's filesystem/network scope.
- Metadata and loopback hosts (`169.254.169.254`, `localhost`) are
  blocked even in `open` network mode.
- The audit chain detects tampering but does not prevent it in-process;
  in production, stream entries to append-only storage as they are
  written.
