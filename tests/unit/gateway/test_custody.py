"""Unit tests for maezo.gateway.custody — Chain of Custody (ADR-0020).

TDD London School: tests written BEFORE implementation.
"""

from maezo.gateway.custody import CustodyBundle


def test_custody_seal() -> None:
    """CustodyBundle.seal_bundle must return a Merkle root hash."""
    evidence_refs = [
        "hash-record-001",
        "hash-record-002",
        "hash-record-003",
    ]

    bundle_root = CustodyBundle.seal_bundle(evidence_refs)

    assert bundle_root is not None
    assert len(bundle_root) == 64  # SHA-256 hex digest
    assert all(c in "0123456789abcdef" for c in bundle_root)


def test_custody_verify_valid() -> None:
    """verify_bundle with the original evidence_refs must return True."""
    evidence_refs = [
        "hash-record-001",
        "hash-record-002",
        "hash-record-003",
    ]

    bundle_root = CustodyBundle.seal_bundle(evidence_refs)
    assert CustodyBundle.verify_bundle(bundle_root, evidence_refs)


def test_custody_tamper_detection_addition() -> None:
    """Adding a reference after sealing must invalidate the bundle."""
    evidence_refs = [
        "hash-record-001",
        "hash-record-002",
    ]

    bundle_root = CustodyBundle.seal_bundle(evidence_refs)

    # Tamper: add a reference
    tampered = evidence_refs + ["hash-record-003"]
    assert not CustodyBundle.verify_bundle(bundle_root, tampered)


def test_custody_tamper_detection_removal() -> None:
    """Removing a reference after sealing must invalidate the bundle."""
    evidence_refs = [
        "hash-record-001",
        "hash-record-002",
        "hash-record-003",
    ]

    bundle_root = CustodyBundle.seal_bundle(evidence_refs)

    # Tamper: remove a reference
    tampered = evidence_refs[:2]
    assert not CustodyBundle.verify_bundle(bundle_root, tampered)


def test_custody_tamper_detection_modification() -> None:
    """Modifying a reference after sealing must invalidate the bundle."""
    evidence_refs = [
        "hash-record-001",
        "hash-record-002",
    ]

    bundle_root = CustodyBundle.seal_bundle(evidence_refs)

    # Tamper: modify a reference
    tampered = ["hash-record-001", "hash-record-MODIFIED"]
    assert not CustodyBundle.verify_bundle(bundle_root, tampered)


def test_custody_tamper_detection_reorder() -> None:
    """Reordering references after sealing must invalidate the bundle
    (Merkle tree is order-sensitive)."""
    evidence_refs = [
        "hash-record-001",
        "hash-record-002",
        "hash-record-003",
    ]

    bundle_root = CustodyBundle.seal_bundle(evidence_refs)

    # Tamper: reorder
    tampered = ["hash-record-003", "hash-record-001", "hash-record-002"]
    assert not CustodyBundle.verify_bundle(bundle_root, tampered)


def test_custody_different_evidence_different_root() -> None:
    """Different evidence sets must produce different Merkle roots."""
    refs_a = ["hash-a-1", "hash-a-2"]
    refs_b = ["hash-b-1", "hash-b-2"]

    root_a = CustodyBundle.seal_bundle(refs_a)
    root_b = CustodyBundle.seal_bundle(refs_b)

    assert root_a != root_b


def test_custody_single_evidence() -> None:
    """Single evidence reference must work (single-leaf Merkle tree)."""
    refs = ["hash-single"]

    root = CustodyBundle.seal_bundle(refs)
    assert root is not None
    assert CustodyBundle.verify_bundle(root, refs)


def test_custody_empty_evidence() -> None:
    """Empty evidence list must produce a deterministic root."""
    root = CustodyBundle.seal_bundle([])
    assert root is not None
    assert len(root) == 64
    assert CustodyBundle.verify_bundle(root, [])
