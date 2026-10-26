"""Estruturas de ajuste de comissao em RASCUNHO (D-36, decisao do dono 2026-10-25) — dark.

**Por que existe.** A decisao D-36 pede uma INTERFACE interna de gestao para CADASTRAR
ESTRUTURAS de ajuste de comissao nas tres dimensoes (produto, canal, corretor) — multiplos
ajustes simultaneos, vigencias e precedencia — SEM calcular, publicar ou liquidar nada: os
dados entram no FUTURO. A dimensao comissao e' fail-closed pela A8
(`gateway/commercial_commission_admission.py`): a decisao `admitted_out_of_scope` registra
que nenhuma superficie cresca fato de comissao, e a Forma 1 (politica ratificada) so' vale
por sucessor versionado. Esta tabela e' a estrutura de RASCUNHO que essa fronteira permite:
estrutura de cadastro, nunca valor corrente, nunca settlement, nunca base de pagamento.

**O que guarda.** Uma linha por rascunho `(tenant, policy_ref)`: a dimensao, o id simbolico
do alvo (`dimensao_ref` — identificador opaco, nunca nome/taxonomia de terceiro), o TIPO de
ajuste (`percentual`/`valor`/`faixa`) com os espacos de valor TODOS anulaveis (a estrutura
pode — e no cadastro dark costuma — nascer sem valor algum), a moeda opcional das
modalidades monetarias, a janela de vigencia, a precedencia estrutural declarada e a
auditoria (quem criou/atualizou e quando — colunas; NENHUM fluxo de aprovacao).

**O pin estrutural.** `status` tem CHECK `= 'DRAFT'` — o unico estado que o banco aceita.
Ativacao NAO e' um UPDATE: exige migracao sucessora (o mesmo regimes de sucessor versionado
da Forma 1 da A8) aPOS ratificacao A8 Forma 1 pelos tres papeis (`comercial` + `legal` +
`DPO`). Enquanto isso, nenhum endpoint de ativacao/calculo/liquidacao existe por desenho e
nenhum estado diferente de `DRAFT` e' persistivel nem por bug.

**Seguro com a versao anterior do codigo no ar.** Tabela nova; nenhuma rota a escreve com a
flag OFF (`staff_commission_policies`, default False). O `downgrade` apaga a tabela: ela e'
descartavel — rascunhos nao carregam direito adquirido (o pior efeito e' redigitar
estruturas de teste).

Revision ID: 0029
Revises: 0028
Create Date: 2026-10-25
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0029"
down_revision: str | None = "0028"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE = r"""
CREATE TABLE commission_adjustment_policies (
    tenant text NOT NULL CHECK (tenant <> ''),
    policy_ref text NOT NULL CHECK (policy_ref ~ '^[A-Za-z0-9_-]{16,128}$'),
    dimensao text NOT NULL CHECK (dimensao IN ('produto', 'canal', 'corretor')),
    -- Identificador SIMBOLICO do alvo (id opaco do produto/canal/corretor). Nunca nome,
    -- nunca documento, nunca taxonomia de terceiro: o cadastro dark nao carrega dado pessoal.
    -- Charset fechado, espelhado byte a byte com o contrato (`DimensaoRef`).
    dimensao_ref text NOT NULL CHECK (dimensao_ref ~ '^[A-Za-z0-9_.:-]{1,128}$'),
    ajuste_tipo text NOT NULL CHECK (ajuste_tipo IN ('percentual', 'valor', 'faixa')),
    -- Espacos de valor TODOS anulaveis (D-36: estrutura agora, valor no futuro). Cada tipo
    -- de ajuste so' aceita valor nos proprios espacos — CHECK de consistencia estrutural.
    percentual numeric(12, 4),
    valor numeric(16, 2),
    faixa_min numeric(16, 2),
    faixa_max numeric(16, 2),
    -- Moeda estrutural das modalidades monetarias; percentual nao a tem.
    moeda text CHECK (moeda IS NULL OR moeda ~ '^[A-Z]{3}$'),
    vigencia_inicio timestamptz,
    vigencia_fim timestamptz,
    -- Precedencia ESTRUTURAL declarada no cadastro (rank entre dimensoes). Nenhuma semantica
    -- de resolucao vive aqui: a regra de combinacao e' ato futuro pos-ratificacao.
    precedencia smallint NOT NULL CHECK (precedencia >= 1),
    revision integer NOT NULL CHECK (revision >= 1),
    -- O PIN: 'DRAFT' e' o unico estado persistivel (migracao sucessora e' o unico caminho
    -- para qualquer outro estado — nunca UPDATE).
    status text NOT NULL DEFAULT 'DRAFT' CHECK (status = 'DRAFT'),
    criado_por text NOT NULL CHECK (criado_por <> ''),
    criado_em timestamptz NOT NULL,
    atualizado_por text NOT NULL CHECK (atualizado_por <> ''),
    atualizado_em timestamptz NOT NULL,
    PRIMARY KEY (tenant, policy_ref),
    CHECK (
        (ajuste_tipo = 'percentual' AND valor IS NULL AND faixa_min IS NULL AND faixa_max IS NULL)
        OR (ajuste_tipo = 'valor' AND percentual IS NULL AND faixa_min IS NULL AND faixa_max IS NULL)
        OR (ajuste_tipo = 'faixa' AND percentual IS NULL AND valor IS NULL)
    ),
    CHECK (moeda IS NULL OR ajuste_tipo IN ('valor', 'faixa')),
    CHECK (vigencia_inicio IS NULL OR vigencia_fim IS NULL OR vigencia_inicio < vigencia_fim),
    CHECK (faixa_min IS NULL OR faixa_max IS NULL OR faixa_min <= faixa_max)
)
"""


def upgrade() -> None:
    op.execute(_CREATE)
    # Ordem de leitura do painel de rascunhos (precedencia declarada primeiro).
    op.execute(
        "CREATE INDEX commission_adjustment_policies_leitura_idx "
        "ON commission_adjustment_policies (tenant, precedencia, policy_ref)"
    )
    op.execute(
        "COMMENT ON TABLE commission_adjustment_policies IS 'Estruturas de ajuste de comissao "
        "em RASCUNHO (D-36, decisao do dono 2026-10-25): dimensao produto/canal/corretor, id "
        "simbolico do alvo, tipo percentual/valor/faixa com valores anulaveis, vigencia, "
        "precedencia e auditoria. Sem valor corrente, sem settlement, sem aprovacao; "
        "status fixado em DRAFT por CHECK — ativacao exige migracao sucessora aPOS ratificacao "
        "A8 Forma 1 (comercial+legal+DPO)'"
    )
    op.execute("REVOKE ALL ON TABLE commission_adjustment_policies FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE commission_adjustment_policies")
