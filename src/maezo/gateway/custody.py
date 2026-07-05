"""Chain of Custody — Merkle-root tamper-evident bundle (ADR-0020).

CustodyBundle is a PROJECTION over the audit chain (ADR-0007), NOT a fork.
It creates a Merkle tree over ordered evidence references (record_hashes from
the audit chain), producing a bundle_root that is sealed back into the chain
BEFORE the human decision.

Any tampering (addition, removal, modification, reordering) changes the root,
providing tamper-evidence by construction.

No PHI in custody: only pseudonymized pointers, never raw PHI (ADR-0006).
"""

from __future__ import annotations

import hashlib

import structlog

logger = structlog.get_logger(__name__)


class CustodyBundle:
    """Merkle-root tamper-evident bundle for chain of custody.

    Seals a set of evidence references (record_hashes from the audit chain)
    into a single Merkle root. The root is then sealed back into the audit
    chain before the human decision — anchoring the decision to a fixed
    set of evidence.

    All methods are static/class-level because the bundle is a pure function
    over evidence references — no mutable state.
    """

    @staticmethod
    def seal_bundle(evidence_refs: list[str]) -> str | None:
        """Seal a list of evidence references into a Merkle root.

        Args:
            evidence_refs: Ordered list of evidence record hashes.

        Returns:
            Merkle root as a 64-char SHA-256 hex digest.
        """
        if not evidence_refs:
            # Empty bundle: hash of empty string (deterministic)
            root = hashlib.sha256(b"").hexdigest()
            logger.info("custody_bundle_sealed_empty", root=root)
            return root

        # Hash each individual evidence reference
        leaves = [hashlib.sha256(ref.encode("utf-8")).digest() for ref in evidence_refs]

        # Build Merkle tree bottom-up
        while len(leaves) > 1:
            next_level: list[bytes] = []
            for i in range(0, len(leaves), 2):
                left = leaves[i]
                right = leaves[i + 1] if i + 1 < len(leaves) else left
                combined = left + right
                next_level.append(hashlib.sha256(combined).digest())
            leaves = next_level

        root = leaves[0].hex()
        logger.info(
            "custody_bundle_sealed",
            evidence_count=len(evidence_refs),
            root=root,
        )
        return root

    @staticmethod
    def verify_bundle(bundle_root: str | None, evidence_refs: list[str]) -> bool:
        """Verify that a set of evidence references matches a sealed bundle root.

        Args:
            bundle_root: The previously computed Merkle root.
            evidence_refs: The evidence references to verify.

        Returns:
            True if the recomputed root matches the bundle_root.
        """
        if bundle_root is None:
            return False
        recomputed = CustodyBundle.seal_bundle(evidence_refs)
        return recomputed == bundle_root
