"""Sandbox lifecycle management.

Sandboxes are isolated execution contexts with validated configs. The
manager owns create / destroy / reuse: a sandbox can be reused across
calls until its call budget is spent or it is terminated, and a terminated
sandbox can never be revived — the caller must create a fresh one.
"""
from __future__ import annotations

import itertools

from sandbox.models import Sandbox, SandboxConfig, SandboxState


class SandboxError(ValueError):
    """Raised for invalid sandbox operations (bad config, bad state)."""


class SandboxManager:
    """Create, track, reuse and destroy sandboxes."""

    def __init__(self) -> None:
        self._sandboxes: dict[str, Sandbox] = {}
        self._counter = itertools.count(1)

    def create(self, config: SandboxConfig) -> Sandbox:
        errors = config.validate()
        if errors:
            raise SandboxError("invalid sandbox config: " + "; ".join(errors))
        sandbox_id = f"sb-{next(self._counter):04d}"
        sandbox = Sandbox(id=sandbox_id, config=config)
        self._sandboxes[sandbox_id] = sandbox
        return sandbox

    def get(self, sandbox_id: str) -> Sandbox | None:
        return self._sandboxes.get(sandbox_id)

    def list(self, tenant: str | None = None,
             state: SandboxState | None = None) -> list[Sandbox]:
        sandboxes = list(self._sandboxes.values())
        if tenant is not None:
            sandboxes = [s for s in sandboxes if s.config.tenant == tenant]
        if state is not None:
            sandboxes = [s for s in sandboxes if s.state is state]
        return sandboxes

    def reuse(self, sandbox_id: str) -> Sandbox:
        """Return an existing sandbox for another call, if it is usable."""
        sandbox = self.get(sandbox_id)
        if sandbox is None:
            raise SandboxError(f"unknown sandbox {sandbox_id!r}")
        if sandbox.state is SandboxState.TERMINATED:
            raise SandboxError(f"sandbox {sandbox_id!r} is terminated and cannot be reused")
        if sandbox.state is SandboxState.SUSPENDED:
            raise SandboxError(f"sandbox {sandbox_id!r} is suspended")
        if sandbox.calls_made >= sandbox.config.max_calls:
            raise SandboxError(
                f"sandbox {sandbox_id!r} exhausted its call budget "
                f"({sandbox.config.max_calls} calls)")
        return sandbox

    def suspend(self, sandbox_id: str) -> Sandbox:
        sandbox = self._require(sandbox_id)
        if sandbox.state is SandboxState.TERMINATED:
            raise SandboxError(f"sandbox {sandbox_id!r} is already terminated")
        sandbox.state = SandboxState.SUSPENDED
        return sandbox

    def resume(self, sandbox_id: str) -> Sandbox:
        sandbox = self._require(sandbox_id)
        if sandbox.state is SandboxState.TERMINATED:
            raise SandboxError(f"sandbox {sandbox_id!r} is terminated")
        sandbox.state = SandboxState.ACTIVE
        return sandbox

    def destroy(self, sandbox_id: str, reason: str = "destroyed") -> Sandbox:
        sandbox = self._require(sandbox_id)
        sandbox.state = SandboxState.TERMINATED
        sandbox.terminated_reason = reason
        return sandbox

    def _require(self, sandbox_id: str) -> Sandbox:
        sandbox = self.get(sandbox_id)
        if sandbox is None:
            raise SandboxError(f"unknown sandbox {sandbox_id!r}")
        return sandbox
