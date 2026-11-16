"""SHA-256 hash-chained audit log.

Every execution, approval and policy decision is appended as an entry
whose hash covers the previous entry's hash, so any later edit, deletion
or reordering is detectable by :meth:`AuditLog.verify`. The log is
append-only in memory (and serialisable to JSONL for persistence).
"""
from __future__ import annotations

import json
from typing import Any

from sandbox.models import AuditEntry

GENESIS_HASH = "0" * 64


class AuditLog:
    """Append-only, hash-chained audit log."""

    def __init__(self) -> None:
        self._entries: list[AuditEntry] = []
        self._prev_hash = GENESIS_HASH

    def append(self, event: str, payload: dict[str, Any]) -> AuditEntry:
        seq = len(self._entries) + 1
        entry_hash = AuditEntry.compute_hash(seq, event, payload, self._prev_hash)
        entry = AuditEntry(seq=seq, event=event, payload=payload,
                           prev_hash=self._prev_hash, entry_hash=entry_hash)
        self._entries.append(entry)
        self._prev_hash = entry_hash
        return entry

    def __len__(self) -> int:
        return len(self._entries)

    def entries(self) -> list[AuditEntry]:
        return list(self._entries)

    def verify(self) -> tuple[bool, list[int]]:
        """Recompute the chain; return (valid, [seq numbers of bad entries])."""
        prev = GENESIS_HASH
        bad: list[int] = []
        for entry in self._entries:
            expected = AuditEntry.compute_hash(
                entry.seq, entry.event, entry.payload, entry.prev_hash)
            if entry.prev_hash != prev or entry.entry_hash != expected:
                bad.append(entry.seq)
            prev = entry.entry_hash
        return (not bad, bad)

    def to_jsonl(self) -> str:
        return "\n".join(
            json.dumps({
                "seq": e.seq, "event": e.event, "payload": e.payload,
                "prev_hash": e.prev_hash, "entry_hash": e.entry_hash,
            }, sort_keys=True, default=str)
            for e in self._entries
        )

    @classmethod
    def from_jsonl(cls, text: str) -> "AuditLog":
        log = cls()
        for line in text.splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            log._entries.append(AuditEntry(
                seq=row["seq"], event=row["event"], payload=row["payload"],
                prev_hash=row["prev_hash"], entry_hash=row["entry_hash"]))
            log._prev_hash = row["entry_hash"]
        return log
