"""Custodia cifrada do telefone do beneficiario para a retomada pos-humano (GAP-XHITL-4, ADR-0061).

**Por que esta tabela existe.** A retomada (`platform/integrations/agent_resume.py`) precisa
mandar a instrucao do humano por WhatsApp, e o WhatsApp exige o NUMERO. A conversa so' carrega o
`phone_hash` keyed (ADR-0035), irreversivel por desenho. Decisao do dono (25/09/2026): cofre
cifrado na Zona PHI — ADR-0061, status Proposto, que exige ciencia do DPO antes de ligar em
qualquer ambiente.

**O que NAO esta aqui: o telefone em claro.** `ciphertext` e' AES-256-GCM do numero sob uma chave
de dados aleatoria por linha; a chave de dados so' existe embrulhada pelo KMS (`wrapped_key`,
`kms:Encrypt` no receptor, `kms:Decrypt` SO' no servico de retomada). O AAD do GCM e o
encryption context do KMS amarram cada linha a `(tenant, conversation_id)`: copiar o blob para
outra conversa nao decifra.

**Retencao.** `expires_at` e' gravado a cada upsert (padrao 30 dias, configuravel); a leitura
ignora linha vencida e `purge_expired` apaga as vencidas. O prazo e' PROPOSTA do ADR-0061, sujeita
ao DPO.

**Upsert.** Uma linha por `(tenant, conversation_id)`: a mensagem seguinte do mesmo beneficiario
re-cifra o numero e avanca `last_inbound_at`, que e' tambem o relogio da janela de 24h da Meta.

Revision ID: 0016
Revises: 0015
Create Date: 2026-09-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE beneficiario_contato_retomada (
            tenant text NOT NULL CHECK (tenant <> ''),
            conversation_id text NOT NULL CHECK (conversation_id ~ '^wa:[^:]+:hk1_[0-9a-f]+$'),
            key_ref text NOT NULL CHECK (key_ref <> ''),
            wrapped_key bytea NOT NULL,
            nonce bytea NOT NULL CHECK (length(nonce) = 12),
            ciphertext bytea NOT NULL,
            last_inbound_at timestamptz NOT NULL,
            expires_at timestamptz NOT NULL,
            updated_at timestamptz NOT NULL DEFAULT clock_timestamp(),
            PRIMARY KEY (tenant, conversation_id)
        )
    """)
    op.execute(
        "CREATE INDEX beneficiario_contato_retomada_expira ON beneficiario_contato_retomada (expires_at)"
    )
    op.execute(
        "COMMENT ON TABLE beneficiario_contato_retomada IS 'Zona PHI (ADR-0061): telefone do "
        "beneficiario CIFRADO (envelope KMS + AES-GCM) para a retomada pos-humano; nunca em claro'"
    )
    op.execute("REVOKE ALL ON TABLE beneficiario_contato_retomada FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE beneficiario_contato_retomada")
