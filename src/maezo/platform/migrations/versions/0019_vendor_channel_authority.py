"""Vendor channel authority store — the ONLY authorized source for vendor provisioning (VW1-P0).

**Por que esta tabela existe.** A audiência `vendor` entra no portal por provisionamento
administrativo (ADR-0063): quem provisiona é a sessão humana administrativa da operadora —
auto-provisionamento vedado. O objeto de credenciamento do canal vive AQUI: ausente ou vazio, a
fonte autorizada não existe e todo ato vendor responde `SOURCE_UNAVAILABLE` (inércia honesta,
nunca um valor fabricado).

**O que NAO esta aqui.** ZERO cifras (o canal não carrega segredo neste store) e ZERO prazo de
retenção — ambos são RATIFY-LATER do DPO e ninguém os inventa numa migration. A tabela guarda
somente o estado de credenciamento: referências opacas, revisão CAS e status fechado.

**CAS.** `revision` é o token compare-and-swap do ato administrativo: um grant/revoke de
membership vendor só é válido com `expected_revision` igual à revisão corrente da linha do canal
e `record.revision == expected_revision + 1`; desacordo é `CONTRACT_MISMATCH`, nunca overwrite.

**Status.** Texto fechado espelhando a semântica `active`/`revoked` do plano provider
(`read_profile.MembershipProjection.state`). Revogar é gravar o novo estado; a linha nunca é
apagada pelo application plane.

**Downgrade.** Restaura o estado anterior (tabela inexistente) e recusa-se sobre tabela
populada: disposição de credenciamento vivo é do dono qualificado, não da migration. A migration
nunca é apagada do histórico.

Revision ID: 0019
Revises: 0018
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0019"
down_revision: str | None = "0018"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE portal_vendor_channels (
            tenant text NOT NULL CHECK (tenant <> ''),
            channel_ref text NOT NULL CHECK (channel_ref <> ''),
            revision bigint NOT NULL CHECK (revision >= 0),
            status text NOT NULL CHECK (status IN ('active', 'revoked')),
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant, channel_ref)
        )
    """)
    op.execute(
        "COMMENT ON TABLE portal_vendor_channels IS 'Fonte autorizada do credenciamento de canal "
        "vendor (ADR-0063, VW1-P0): revisao CAS por canal, status ativo/revogado; sem cifra e sem "
        "retencao (RATIFY-LATER DPO)'"
    )
    op.execute("REVOKE ALL ON TABLE portal_vendor_channels FROM PUBLIC")


def downgrade() -> None:
    op.execute("""DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM portal_vendor_channels) THEN
            RAISE EXCEPTION 'populated vendor channel authority requires qualified lifecycle disposition';
        END IF;
    END $$""")
    op.execute("DROP TABLE portal_vendor_channels")
