"""Approval gates for high-risk tools.

A high-risk tool call cannot run until a human approves it. Approvals are
single-use by default (one approval covers one call), are recorded in the
audit log by the caller, and expire when consumed. There is no auto-approve
path: the only way a high-risk call executes is an explicit
:meth:`ApprovalStore.approve` for that exact call id.
"""
from __future__ import annotations

from sandbox.models import Approval


class ApprovalStore:
    """Single-use approvals keyed by call id."""

    def __init__(self) -> None:
        self._approvals: dict[str, Approval] = {}
        self._seq = 0

    def approve(self, call_id: str, tool: str, approver: str,
                expires_after_calls: int = 1) -> Approval:
        self._seq += 1
        approval = Approval(call_id=call_id, tool=tool, approver=approver,
                            granted_at_seq=self._seq,
                            expires_after_calls=expires_after_calls)
        self._approvals[call_id] = approval
        return approval

    def consume(self, call_id: str, tool: str) -> Approval | None:
        """Consume one use of an approval; returns None if not approved."""
        approval = self._approvals.get(call_id)
        if approval is None or approval.tool != tool:
            return None
        approval.expires_after_calls -= 1
        if approval.expires_after_calls <= 0:
            del self._approvals[call_id]
        return approval

    def has(self, call_id: str, tool: str) -> bool:
        approval = self._approvals.get(call_id)
        return approval is not None and approval.tool == tool

    def revoke(self, call_id: str) -> bool:
        return self._approvals.pop(call_id, None) is not None
