"""Acesso do beneficiario: quando a apresentacao do canal foi mostrada (14/10/2026).

**Por que existe.** A mensagem `apresentacao-canal-v1` sai uma vez na primeira verificacao e de novo so' na
volta depois de um silencio longo. `apresentado_em` guarda quando ela saiu; `NULL` = devida (nunca mostrada,
ou a pessoa ficou em silencio alem do prazo e a conversa a deve de novo). O "ultimo recebido" ja' existe:
`ultima_mensagem_em`.

**O que NAO esta aqui.** Nenhum texto, telefone ou dado de pessoa: um carimbo.

**Seguro com a versao anterior do codigo no ar.** Coluna nova, `NULL` por padrao, e a versao anterior grava
com lista de colunas explicita (nao a toca). Roda ANTES do deploy do codigo que a le'.

**Downgrade.** `DROP COLUMN`: a apresentacao volta a ser devida a todos.

Revision ID: 0026
Revises: 0025
Create Date: 2026-10-14
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0026"
down_revision: str | None = "0025"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE conversa_acesso_beneficiario ADD COLUMN IF NOT EXISTS apresentado_em timestamptz NULL"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE conversa_acesso_beneficiario DROP COLUMN IF EXISTS apresentado_em")
