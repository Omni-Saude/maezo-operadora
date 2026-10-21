"""Lembrete proativo de cobranca: idempotencia (um por pessoa e competencia) e opt-out (PARAR).

**Por que existe.** O lembrete (`platform/lembretes/`) nao pode mandar duas vezes a mesma competencia
a mesma conversa, e quem respondeu PARAR nunca mais recebe. Duas tabelas pequenas, por
`(tenant, conversation_id)`:

- `lembrete_cobranca_enviado`: PK `(tenant, conversation_id, competencia)`. A linha e' RESERVADA antes
  do envio e apagada se o envio falha; uma queda entre a reserva e o envio custa um lembrete a menos,
  nunca um a mais.
- `lembrete_cobranca_optout`: PK `(tenant, conversation_id)`. O PARAR do beneficiario.

**O que NAO esta aqui.** Nenhum telefone, CPF, valor, nome ou texto: so' o `conversation_id`
pseudonimo (keyed), a competencia `AAAA-MM`, carimbos e o canal de envio (`texto`/`template`). Um CHECK
amarra o prefixo do tenant.

**Seguro com a versao anterior do codigo no ar.** Tabelas novas; so' o job do lembrete (desligado por
padrao, `MAEZO_LEMBRETE_COBRANCA`) as le' e escreve. O `downgrade` RECUSA com
`lembrete_cobranca_optout` populada: apagar um PARAR e' voltar a mandar mensagem a quem pediu para nao
receber; disposicao e' do dono. A tabela de enviados e' descartavel (so' idempotencia) e cai com o
downgrade.

Revision ID: 0027
Revises: 0026
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0027"
down_revision: str | None = "0026"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # op.execute embrulha a string em sa.text(): ':hk1_' viraria bind parameter; '[:]' casa o ':' literal.
    op.execute(r"""
        CREATE TABLE lembrete_cobranca_enviado (
            tenant text NOT NULL CHECK (tenant <> ''),
            conversation_id text NOT NULL CHECK (conversation_id ~ '^wa:[^:]+[:]hk1_[0-9a-f]+$'),
            competencia text NOT NULL CHECK (competencia ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
            reservado_em timestamptz NOT NULL,
            canal_envio text NOT NULL CHECK (canal_envio IN ('texto', 'template')),
            PRIMARY KEY (tenant, conversation_id, competencia),
            CONSTRAINT lembrete_enviado_prefixo_do_tenant
                CHECK (starts_with(conversation_id, 'wa:' || tenant || ':'))
        )
    """)
    op.execute(r"""
        CREATE TABLE lembrete_cobranca_optout (
            tenant text NOT NULL CHECK (tenant <> ''),
            conversation_id text NOT NULL CHECK (conversation_id ~ '^wa:[^:]+[:]hk1_[0-9a-f]+$'),
            optout_em timestamptz NOT NULL,
            PRIMARY KEY (tenant, conversation_id),
            CONSTRAINT lembrete_optout_prefixo_do_tenant
                CHECK (starts_with(conversation_id, 'wa:' || tenant || ':'))
        )
    """)
    op.execute(
        "COMMENT ON TABLE lembrete_cobranca_enviado IS 'Idempotencia do lembrete de cobranca: um por "
        "conversa e competencia. So conversation_id keyed, competencia, carimbo e canal; nenhum telefone, "
        "valor ou texto'"
    )
    op.execute(
        "COMMENT ON TABLE lembrete_cobranca_optout IS 'PARAR do lembrete de cobranca: so conversation_id "
        "keyed e carimbo'"
    )
    op.execute("REVOKE ALL ON TABLE lembrete_cobranca_enviado FROM PUBLIC")
    op.execute("REVOKE ALL ON TABLE lembrete_cobranca_optout FROM PUBLIC")


def downgrade() -> None:
    op.execute("""DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM lembrete_cobranca_optout) THEN
            RAISE EXCEPTION 'populated lembrete_cobranca_optout requires qualified lifecycle disposition';
        END IF;
    END $$""")
    op.execute("DROP TABLE lembrete_cobranca_optout")
    op.execute("DROP TABLE lembrete_cobranca_enviado")
