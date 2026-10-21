"""Limite de falhas de verificacao POR CPF, somando todas as conversas (D-04 do red team, decisao do dono).

**Por que existe.** O bloqueio de data de nascimento valia por conversa (`conversa_acesso_beneficiario`): nove
datas erradas contra o mesmo CPF, espalhadas por tres numeros, e o quarto numero verificou com a data certa.
A regra do dono (15/10/2026): 3 falhas por CPF em 6 horas, de qualquer conversa. Esta tabela e' o contador
compartilhado entre conversas.

**O que guarda.** Uma linha por `(tenant, chave)`: a contagem de falhas, o inicio da janela (fixa, aberta pela
primeira falha), o carimbo da ultima e se a janela ANTERIOR terminou bloqueada (o alarme de re-bloqueio do
mesmo CPF em duas janelas seguidas, revisao do #785). A `chave` e' o SHA-256 de um rotulo + o hash de
verificacao do CPF: uma derivacao que SO' serve a este limite. **Nao** e' o hash que a AMH recebe para
resolver um documento, entao quem lesse esta tabela nao teria uma chave de consulta. Nenhum CPF,
nascimento, nome, telefone ou texto; um CHECK recusa qualquer chave que nao seja 64 hexadecimais.

**Ciclo de vida.** A linha vale enquanto a janela de 6 h vale e mais uma janela (a seguinte le' se esta
bloqueou): o servico a apaga sozinho 12 h depois da primeira falha (`PostgresLimitePorCpf.expurgar`,
chamado pelo varredor de retencao do acesso). Nao ha' o que reter depois.

**Seguro com a versao anterior do codigo no ar.** Tabela nova; so' o servico de acesso novo a le' e escreve. O
`downgrade` apaga a tabela: ela e' descartavel (o pior efeito e' reabrir 3 tentativas por CPF).

Revision ID: 0028
Revises: 0027
Create Date: 2026-10-15
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0028"
down_revision: str | None = "0027"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(r"""
        CREATE TABLE acesso_falhas_por_cpf (
            tenant text NOT NULL CHECK (tenant <> ''),
            chave text NOT NULL CHECK (chave ~ '^[0-9a-f]{64}$'),
            falhas smallint NOT NULL CHECK (falhas >= 1),
            janela_inicio timestamptz NOT NULL,
            ultima_em timestamptz NOT NULL,
            janela_anterior_bloqueada boolean NOT NULL DEFAULT false,
            PRIMARY KEY (tenant, chave)
        )
    """)
    # O varredor apaga pela janela vencida.
    op.execute(
        "CREATE INDEX acesso_falhas_por_cpf_janela_idx ON acesso_falhas_por_cpf (tenant, janela_inicio)"
    )
    op.execute(
        "COMMENT ON TABLE acesso_falhas_por_cpf IS 'Falhas de verificacao por CPF (3 em 6 h, de qualquer "
        "conversa; D-04). Chave derivada do hash de verificacao, contagem e carimbos; nenhum CPF, "
        "nascimento, "
        "nome, telefone ou texto. Some sozinha uma janela depois de a sua passar'"
    )
    op.execute("REVOKE ALL ON TABLE acesso_falhas_por_cpf FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE acesso_falhas_por_cpf")
