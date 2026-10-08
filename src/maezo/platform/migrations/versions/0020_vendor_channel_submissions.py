"""Vendor channel submission store — the OP16 NO-DUPLICATE-INSTANCE business key (VW1-P4).

**Por que esta tabela existe.** A máquina de submissão do canal (OP16
`channel.submission.submit`) promete idempotência por business key NOMEADA —
`tenant + channel_submission + submission_ref + business_revision` — e "nunca uma segunda
instância = segunda trilha de resposta". A promessa é ESTRUTURAL: a chave de negócio é a
própria PRIMARY KEY, então um reenvio nunca cria segunda instância — no nível do storage, não
por convenção de aplicação.

**O que NAO esta aqui.** ZERO conteúdo de payload (a tabela guarda só a classe DECLARADA do
envelope — a perna de declaração de saúde é VW0-D16, RATIFY-LATER DPO, e ninguém inventa o
schema dela numa migration), ZERO cifra e ZERO prazo de retenção. Referências opacas, digest do
conteúdo de negócio, estágio fechado do ciclo próprio e o código de recusa quando houver.

**Ciclo.** `received → documents_pending → responded / refused` (eventos
`submission.received/documents_pending/responded/refused` do registry OP16). Recusa implica
código; estágio sem recusa implica código ausente — invariante gravada em CHECK, não em
aplicação. `case_ref`/`document_request_ref` só recebem objeto NATIVO (kinds declarados em
`external_cases`); a resposta nunca é aviso (PW1-C intocado).

**CAS/digest.** `command_digest` é o SHA-256 do conteúdo de NEGÓCIO do comando (sem
`command_id`, que é identidade de tentativa): reenvio da mesma chave com outro conteúdo é
`CONTRACT_MISMATCH` na máquina, nunca overwrite silencioso.

**Downgrade.** Restaura o estado anterior (tabela inexistente) e recusa-se sobre tabela
populada: instâncias de submissão vivas são do dono qualificado, não da migration. A migration
nunca é apagada do histórico.

Revision ID: 0020
Revises: 0019
Create Date: 2026-10-06
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0020"
down_revision: str | None = "0019"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE portal_vendor_submissions (
            tenant text NOT NULL CHECK (tenant <> ''),
            operation text NOT NULL DEFAULT 'channel_submission' CHECK (operation = 'channel_submission'),
            submission_ref text NOT NULL CHECK (submission_ref <> ''),
            business_revision text NOT NULL CHECK (business_revision ~ '^(0|[1-9][0-9]*)$'),
            submission_id text NOT NULL UNIQUE CHECK (submission_id <> ''),
            channel_ref text NOT NULL CHECK (channel_ref <> ''),
            contract_ref text NOT NULL CHECK (contract_ref <> ''),
            payload_class text NOT NULL CHECK (payload_class <> ''),
            command_digest text NOT NULL CHECK (command_digest ~ '^[0-9a-f]{64}$'),
            lifecycle text NOT NULL CHECK (
                lifecycle IN ('received', 'documents_pending', 'responded', 'refused')
            ),
            refusal_code text,
            case_ref text,
            document_request_ref text,
            created_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant, operation, submission_ref, business_revision),
            CHECK ((lifecycle = 'refused') = (refusal_code IS NOT NULL))
        )
    """)
    op.execute(
        "COMMENT ON TABLE portal_vendor_submissions IS 'Instancias de submissao do canal vendor "
        "(OP16, VW1-P4): business key nomeada tenant + channel_submission + submission_ref + "
        "business_revision como PRIMARY KEY (NO-DUPLICATE-INSTANCE); sem conteudo de payload "
        "(classe declarada apenas, VW0-D16 RATIFY-LATER DPO), sem cifra e sem retencao'"
    )
    op.execute("REVOKE ALL ON TABLE portal_vendor_submissions FROM PUBLIC")


def downgrade() -> None:
    # Reversão estrutural restaura o estado anterior e NUNCA apaga histórico de submissão
    # vivo: recusa sobre tabela populada é disposição do dono qualificado, não da migration.
    op.execute("""DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM portal_vendor_submissions) THEN
            RAISE EXCEPTION 'populated vendor submission store requires qualified lifecycle disposition';
        END IF;
    END $$""")
    op.execute("DROP TABLE portal_vendor_submissions")
