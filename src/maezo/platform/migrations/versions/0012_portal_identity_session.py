"""ADR-0049 D4: dedicated human sessions and reviewed membership, empty by default.

Revision 0012 follows the unshipped A2A admission marker 0011, retaining a single
0010 -> 0011 -> 0012 chain. Identity tables and A2A facts remain separate stores.
No author invents a missing predecessor. No seeded identities or automatic human ratification.
These tables contain identity/authorization data and short-lived OIDC verifier secrets: dedicated
BFF/admin grants, TLS, encryption at rest and Zone PHI deployment are mandatory. Application BFF
role reads membership only; an independently controlled administration role manages reviews.
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0012"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE portal_login_transactions (
            tenant text NOT NULL, state_hash text NOT NULL, browser_hash text NOT NULL,
            expires_at timestamptz NOT NULL, payload text NOT NULL,
            PRIMARY KEY (tenant, state_hash)
        )
    """)
    op.execute("""
        CREATE TABLE portal_code_claims (
            tenant text NOT NULL, code_hash text NOT NULL, expires_at timestamptz NOT NULL,
            PRIMARY KEY (tenant, code_hash)
        )
    """)
    op.execute("""
        CREATE TABLE portal_sessions (
            tenant text NOT NULL, secret_hash text NOT NULL, expires_at timestamptz NOT NULL,
            payload text NOT NULL, PRIMARY KEY (tenant, secret_hash)
        )
    """)
    op.execute("""
        CREATE TABLE portal_memberships (
            tenant text NOT NULL, issuer text NOT NULL, subject text NOT NULL,
            principal_ref text NOT NULL, payload text NOT NULL,
            PRIMARY KEY (tenant, issuer, subject), UNIQUE (tenant, principal_ref)
        )
    """)
    for table in ("portal_login_transactions", "portal_code_claims", "portal_sessions"):
        op.execute(f"CREATE INDEX ix_{table}_expiry ON {table} (tenant, expires_at)")
    for table in ("portal_login_transactions", "portal_code_claims", "portal_sessions", "portal_memberships"):
        op.execute(f"REVOKE ALL ON TABLE {table} FROM PUBLIC")


def downgrade() -> None:
    for table in ("portal_sessions", "portal_code_claims", "portal_login_transactions", "portal_memberships"):
        op.execute(f"DROP TABLE {table}")
