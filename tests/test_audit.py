"""Tests for the hash-chained audit log."""
from sandbox.audit import AuditLog


def test_empty_log_verifies():
    log = AuditLog()
    valid, bad = log.verify()
    assert valid and bad == []


def test_append_and_verify():
    log = AuditLog()
    log.append("execution.succeeded", {"call_id": "c1"})
    log.append("execution.denied", {"call_id": "c2"})
    valid, bad = log.verify()
    assert valid and bad == []
    assert len(log) == 2


def test_entries_are_hash_chained():
    log = AuditLog()
    first = log.append("a", {"x": 1})
    second = log.append("b", {"x": 2})
    assert second.prev_hash == first.entry_hash
    assert first.prev_hash == "0" * 64
    assert first.entry_hash != second.entry_hash


def test_tampered_payload_detected():
    log = AuditLog()
    log.append("execution.succeeded", {"call_id": "c1"})
    log.append("execution.denied", {"call_id": "c2"})
    log.entries()[0].payload = {"call_id": "forged"}
    valid, bad = log.verify()
    assert not valid
    assert bad == [1]


def test_deleted_entry_detected():
    log = AuditLog()
    log.append("a", {})
    log.append("b", {})
    log.append("c", {})
    del log._entries[1]
    valid, bad = log.verify()
    assert not valid
    assert bad  # the entry after the gap no longer chains


def test_jsonl_round_trip_verifies():
    log = AuditLog()
    log.append("execution.succeeded", {"call_id": "c1"})
    log.append("approval.granted", {"call_id": "c1", "approver": "alice"})
    restored = AuditLog.from_jsonl(log.to_jsonl())
    valid, bad = restored.verify()
    assert valid and bad == []
    assert len(restored) == 2


def test_jsonl_round_trip_tamper_detected():
    log = AuditLog()
    log.append("execution.succeeded", {"call_id": "c1"})
    text = log.to_jsonl().replace("c1", "c9")
    restored = AuditLog.from_jsonl(text)
    valid, bad = restored.verify()
    assert not valid
