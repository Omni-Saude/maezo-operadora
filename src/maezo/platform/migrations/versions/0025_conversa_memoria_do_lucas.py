"""Memoria curta do Lucas na conversa: a ultima resposta e os avisos de caso aberto (simulador, 13/10/2026).

**Por que existe.** A bateria do simulador de beneficiario (rodadas 2 e 3) mostrou o Lucas mandando o MESMO
texto varias vezes seguidas ("Sua mensalidade de outubro de 2026 e' de R$ ...", o aviso de caso aberto) e
o aviso "sua duvida ja' esta' com a nossa equipe" a cada mensagem. O Lucas e' de um turno so' (sem
checkpoint, ADR-0062): sem memoria, ele nao sabe o que acabou de dizer. Duas colunas resolvem:

- `lucas_ultima_resposta`: os 16 primeiros hex do sha256 do ULTIMO texto que o Lucas enviou. So' para
  comparar ("este texto e' igual ao anterior?"); o texto nunca e' guardado.
- `lucas_avisos_caso_aberto`: quantos avisos de caso aberto o Lucas ja' deu no caso de cobranca aberto
  da conversa. Depois de dois, ele responde so' o fato.

**O que NAO esta aqui.** Nenhum texto, nenhum telefone, nenhum valor: um hash truncado e um contador.

**Seguro com a versao anterior do codigo no ar.** As colunas sao novas, com default (`NULL` e `0`), e a
versao anterior escreve com a lista de colunas explicita — ela continua gravando sem toca-las. Por isso
esta migration roda ANTES do deploy do codigo que as le'.

**Downgrade.** `DROP COLUMN` das duas: o Lucas volta a nao lembrar a ultima resposta.

Revision ID: 0025
Revises: 0024
Create Date: 2026-10-13
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0025"
down_revision: str | None = "0024"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
        ALTER TABLE conversa_agente_ativo
            ADD COLUMN IF NOT EXISTS lucas_ultima_resposta text NULL
                CHECK (lucas_ultima_resposta ~ '^[0-9a-f]{16}$'),
            ADD COLUMN IF NOT EXISTS lucas_avisos_caso_aberto smallint NOT NULL DEFAULT 0
                CHECK (lucas_avisos_caso_aberto BETWEEN 0 AND 100)
    """)


def downgrade() -> None:
    op.execute("""
        ALTER TABLE conversa_agente_ativo
            DROP COLUMN IF EXISTS lucas_avisos_caso_aberto,
            DROP COLUMN IF EXISTS lucas_ultima_resposta
    """)
