"""Custody + erasure tracking — chain-of-custody bundles and LGPD erasure log.

ADR-0020: CustodyBundle as a PROJECTION over the audit chain (ADR-0007),
NOT a fork. Custody bundles seal Merkle roots over ordered evidence references
and are recorded back into the audit chain before human decisions.

M12 erasure_log: Tracks every LGPD erasure operation (ADR-0002), recording
the cascade result per fhir_patient_id for compliance auditability.

Revision ID: 0004
Revises: 0003
Create Date: 2026-07-05
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # custody_bundles — Merkle-root sealed evidence bundles (ADR-0020)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS custody_bundles (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           text NOT NULL,
            bundle_root         text NOT NULL,
            evidence_count      integer NOT NULL DEFAULT 0,
            evidence_refs       jsonb NOT NULL DEFAULT '[]'::jsonb,
            process_instance_id text,
            feature_snapshot_id text,
            sealed_by           text NOT NULL,
            sealed_at           timestamptz NOT NULL DEFAULT now(),
            audit_record_hash   text NOT NULL,

            CONSTRAINT uq_custody_bundle_root UNIQUE (bundle_root)
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_custody_bundles_tenant
            ON custody_bundles (tenant_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_custody_bundles_sealed_at
            ON custody_bundles (sealed_at DESC)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_custody_bundles_audit_hash
            ON custody_bundles (audit_record_hash)
    """)

    # ------------------------------------------------------------------
    # erasure_log — LGPD erasure audit trail (M12, ADR-0002)
    # ------------------------------------------------------------------
    op.execute("""
        CREATE TABLE IF NOT EXISTS erasure_log (
            id                  uuid PRIMARY KEY DEFAULT gen_random_uuid(),
            tenant_id           text NOT NULL,
            fhir_patient_id     text NOT NULL,
            requested_by        text NOT NULL,
            status              text NOT NULL DEFAULT 'pending',
            working_erased      boolean NOT NULL DEFAULT false,
            episodic_erased     boolean NOT NULL DEFAULT false,
            semantic_erased     boolean NOT NULL DEFAULT false,
            errors              jsonb NOT NULL DEFAULT '[]'::jsonb,
            requested_at        timestamptz NOT NULL DEFAULT now(),
            completed_at        timestamptz,
            verified_at         timestamptz,
            verification_status text
        )
    """)

    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_erasure_log_tenant_patient
            ON erasure_log (tenant_id, fhir_patient_id)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_erasure_log_status
            ON erasure_log (status)
    """)
    op.execute("""
        CREATE INDEX IF NOT EXISTS ix_erasure_log_requested_at
            ON erasure_log (requested_at DESC)
    """)


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS erasure_log CASCADE")
    op.execute("DROP TABLE IF EXISTS custody_bundles CASCADE")
