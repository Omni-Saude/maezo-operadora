"""Agente ativo da conversa no numero unico (plano `docs/plans/lucas-numero-unico.md` §2.1/§5, ADR-0062).

**Por que esta tabela existe.** Com o numero unico, a Helena atende a entrada e o Lucas atende
cobranca. Alguem precisa lembrar, entre uma mensagem e a seguinte, QUEM esta com a conversa. Nao
pode ser o checkpoint do LangGraph: medido em 01/10/2026 (langgraph 1.2.9), o `checkpoint_ns` no
grafo raiz e' ignorado, e dois grafos no mesmo `thread_id` se sobrescrevem. Nao pode ser variavel
de processo, porque conversa comum nao abre processo. Fica aqui: uma linha por
`(tenant, conversation_id)`, fora do estado dos dois grafos.

**O que NAO esta aqui.** Nenhum telefone e nenhum texto do beneficiario. So' o `conversation_id`
keyed (`wa:{tenant}:hk1_{hmac}`, ADR-0035), dois enums, um subtipo de cobranca fechado e uma
competencia `YYYY-MM`.

**Escrita por compare-and-set.** `revisao` e' a versao da linha: o roteador grava com
`UPDATE ... WHERE revisao = :esperada` (ou `INSERT ... ON CONFLICT DO NOTHING` na primeira vez).
Quem perde a corrida rele' e recalcula; o roteador nao chama LLM, entao recalcular e' barato.

**Isolamento de tenant.** O padrao vigente do repo: schema por tenant (`search_path` pinado pelo
`env.py`), coluna `tenant` na PK e em todo `WHERE`, e um CHECK amarrando o prefixo do
`conversation_id` ao proprio `tenant`. O repo nao usa RLS em migration nenhuma (plano §5).

**Retencao.** O roteador apaga ate' 100 linhas com `ultimo_turno_em` de mais de 30 dias, no maximo
uma vez a cada 10 minutos por processo (`roteamento.py`). O prazo pertence ao DPO
(`erasure-plan.template.yaml`, camada `roteamento_conversa`).

**Seguro com a versao anterior do codigo no ar.** A tabela e' nova e so' o roteador (desligado por
padrao) a le' e escreve; o `downgrade` e' `DROP TABLE` porque o vencimento ja' leva toda conversa
de volta para a Helena.

Revision ID: 0017
Revises: 0016
Create Date: 2026-10-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # op.execute embrulha a string em sa.text(): ':hk1_' viraria bind parameter. '[:]' casa o
    # mesmo ':' literal sem ser lido como bind (mesma tecnica da 0016).
    op.execute(r"""
        CREATE TABLE conversa_agente_ativo (
            tenant text NOT NULL CHECK (tenant <> ''),
            conversation_id text NOT NULL CHECK (conversation_id ~ '^wa:[^:]+[:]hk1_[0-9a-f]+$'),
            agente_ativo text NOT NULL CHECK (agente_ativo IN ('helena', 'lucas')),
            revisao bigint NOT NULL DEFAULT 0 CHECK (revisao >= 0),
            transicao_motivo text NOT NULL CHECK (transicao_motivo IN (
                'inicio', 'retorno_inatividade', 'retorno_saude', 'retorno_pedido_humano',
                'retorno_falha', 'handoff_cobranca', 'continua_lucas', 'retorno_fora_do_canal',
                'lucas_encerrou'
            )),
            lucas_cobranca_subtipo text NULL CHECK (lucas_cobranca_subtipo IN (
                'boleto_2via', 'vencimento', 'confirmacao_pagamento', 'contestacao',
                'cobranca_recebida', 'cancelamento', 'outro'
            )),
            lucas_competencia text NULL CHECK (lucas_competencia ~ '^\d{4}-(0[1-9]|1[0-2])$'),
            ativo_desde timestamptz NOT NULL,
            ultimo_turno_em timestamptz NOT NULL,
            expira_em timestamptz NOT NULL,
            PRIMARY KEY (tenant, conversation_id),
            CONSTRAINT conversa_agente_ativo_prefixo_do_tenant
                CHECK (starts_with(conversation_id, 'wa:' || tenant || ':')),
            CONSTRAINT conversa_agente_ativo_expira_depois
                CHECK (expira_em > ultimo_turno_em),
            CONSTRAINT conversa_agente_ativo_colunas_do_lucas
                CHECK (agente_ativo = 'lucas'
                       OR (lucas_cobranca_subtipo IS NULL AND lucas_competencia IS NULL))
        )
    """)
    op.execute("CREATE INDEX conversa_agente_ativo_ultimo_turno ON conversa_agente_ativo (ultimo_turno_em)")
    op.execute(
        "COMMENT ON TABLE conversa_agente_ativo IS 'Numero unico (ADR-0062): agente ativo por "
        "conversa, gravado por CAS (revisao). So conversation_id keyed, enums e YYYY-MM; nenhum "
        "telefone, nenhum texto'"
    )
    op.execute("REVOKE ALL ON TABLE conversa_agente_ativo FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE conversa_agente_ativo")
