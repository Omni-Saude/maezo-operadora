"""Acesso do beneficiario: CHECKs de `portable_subject_ref`/`consent_ref` aceitos pelo Postgres (08/10/2026).

**O defeito.** A 0022 escreveu `~ '^[A-Za-z0-9._:~-]{1,256}$'`. O motor de regex do Postgres limita a
repeticao a 255 (`DUPMAX`): o CHECK so' e' avaliado quando a coluna NAO e' nula, entao a tabela nasceu, a
primeira mensagem (sem ref) gravou, e a gravacao do estado `verificado` (que traz a ref) falhou com
`invalid regular expression: invalid repetition count(s)`. Medido no dev em 08/10/2026 23:15 UTC: o
beneficiario era identificado, o estado nao persistia e a mensagem seguinte voltava a ser lida como CPF.

**A correcao.** Mesmo vocabulario, mesmo teto de 256 caracteres, expresso como `~ '^[...]+$' AND
char_length(...) <= 256`. Nada de dado muda (as linhas com ref nunca chegaram a ser gravadas).

**Idempotente** (`DROP CONSTRAINT IF EXISTS` + `ADD`): no dev a correcao foi aplicada antes do merge, por uma
task avulsa que rodou ESTE arquivo pelo mesmo `alembic upgrade head`; rodar de novo nao muda nada.

Revision ID: 0023
Revises: 0022
Create Date: 2026-10-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0023"
down_revision: str | None = "0022"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TABELA = "conversa_acesso_beneficiario"
_COLUNAS = ("portable_subject_ref", "consent_ref")


def upgrade() -> None:
    for coluna in _COLUNAS:
        nome = f"{_TABELA}_{coluna}_check"
        op.execute(f"ALTER TABLE {_TABELA} DROP CONSTRAINT IF EXISTS {nome}")
        op.execute(
            f"ALTER TABLE {_TABELA} ADD CONSTRAINT {nome} CHECK "
            f"({coluna} ~ '^[A-Za-z0-9._:~-]+$' AND char_length({coluna}) <= 256)"
        )


def downgrade() -> None:
    # Voltar ao `{1,256}` da 0022 recolocaria um CHECK que o Postgres nao consegue avaliar: a reversao
    # mantem a forma valida (o teto e o vocabulario sao os mesmos).
    pass
