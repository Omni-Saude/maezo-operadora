"""Coleta do pedido de reembolso pelo WhatsApp (DL-0158/DL-0159, `MAEZO_REEMBOLSO_V1`).

**Por que esta tabela existe.** O Lucas e' de um turno so' (sem checkpoint, ADR-0062) e a coleta do
reembolso dura varias mensagens (perguntas fechadas, laco de documentos, confirmacao). Alguem precisa
lembrar, entre uma mensagem e a seguinte, em que etapa a pessoa esta e o que ja' respondeu. Nao pode ser
variavel de processo (o processo so' nasce na confirmacao) nem o checkpoint do grafo. Fica aqui: uma linha
por `(tenant, conversation_id)`, fora do estado dos agentes.

**O que NAO esta aqui.** Nenhum telefone e nenhum texto livre da COLETA. A unica excecao e' a RESPOSTA a um
pedido de informacao da equipe (`resposta_texto`, DL-0160): ja' passada pelo scrub de identificadores,
ate' 1000
caracteres, so' na etapa `resposta` (CHECK) e apagada no "terminei" que a entrega ao processo (ou quando
a linha
vence). Fora isso, so' o `conversation_id` keyed
(`wa:{tenant}:hk1_{hmac}`, ADR-0035), enums FECHADOS (etapa, relacao com o titular, categoria, tipo,
motivo), a data do atendimento, o valor pedido em centavos e a lista de `{tipo, document_ref}` — a
referencia e' OPACA: o arquivo vive na custodia da Zona PHI (ADR-0006), nunca aqui. Depois da abertura do
processo a linha guarda tambem o protocolo (`REE-XXXX-XXXX`, sem PII), para responder "qual o meu
protocolo?".

**Escrita por compare-and-set.** `revisao` e' a versao da linha: `UPDATE ... WHERE revisao = :esperada` (ou
`INSERT ... ON CONFLICT DO NOTHING` na primeira vez). Quem perde a corrida rele' e recalcula; a maquina de
estados nao chama modelo, entao recalcular e' barato.

**Isolamento de tenant.** Mesmo padrao da 0017: schema por tenant (`search_path` pinado), coluna `tenant`
na PK e em todo `WHERE`, e um CHECK amarrando o prefixo do `conversation_id` ao proprio `tenant`.

**Retencao.** A coleta nao concluida vence em `expira_em` (a coleta e' descartavel: a pessoa recomeca) e e'
purgada pelo codigo que a le'. O prazo final pertence ao DPO (`erasure-plan.template.yaml`, camada
`coleta_reembolso`).

**Seguro com a versao anterior do codigo no ar.** A tabela e' nova e so' o turno de reembolso (desligado por
padrao) a le' e escreve; o `downgrade` e' `DROP TABLE`.

Revision ID: 0031
Revises: 0030
Create Date: 2026-10-29
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0031"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # op.execute embrulha a string em sa.text(): ':hk1_' viraria bind parameter. '[:]' casa o
    # mesmo ':' literal sem ser lido como bind (mesma tecnica da 0016/0017).
    op.execute(r"""
        CREATE TABLE reembolso_coleta (
            tenant text NOT NULL CHECK (tenant <> ''),
            conversation_id text NOT NULL CHECK (conversation_id ~ '^wa:[^:]+[:]hk1_[0-9a-f]+$'),
            etapa text NOT NULL CHECK (etapa IN (
                'tem_nota', 'tem_pedido', 'relacao', 'categoria', 'tipo', 'data', 'valor', 'motivo',
                'doc_pedido', 'doc_nota', 'confirma', 'aberto', 'resposta', 'encerrado'
            )),
            relacao_com_titular text NOT NULL DEFAULT '' CHECK (relacao_com_titular IN
                ('', 'titular', 'dependente')),
            categoria_procedimento text NOT NULL DEFAULT '' CHECK (categoria_procedimento IN
                ('', 'consulta', 'exame', 'terapia')),
            tipo_reembolso text NOT NULL DEFAULT '' CHECK (tipo_reembolso IN
                ('', 'livre_escolha', 'fora_rede', 'urgencia_emergencia', 'indisponibilidade_rede')),
            motivo_nao_rede text NOT NULL DEFAULT '' CHECK (motivo_nao_rede IN
                ('', 'urgencia', 'sem_prestador_no_prazo', 'preferencia_fora_rede')),
            data_atendimento date NULL,
            valor_solicitado_cents bigint NOT NULL DEFAULT 0 CHECK (valor_solicitado_cents >= 0),
            perguntou_terminou boolean NOT NULL DEFAULT false,
            documentos jsonb NOT NULL DEFAULT '[]'::jsonb CHECK (jsonb_typeof(documentos) = 'array'),
            protocolo text NULL CHECK (protocolo ~ '^REE-[0-9A-F]{4}-[0-9A-F]{4}$'),
            resposta_texto text NOT NULL DEFAULT '' CHECK (char_length(resposta_texto) <= 1000),
            revisao bigint NOT NULL DEFAULT 0 CHECK (revisao >= 0),
            criado_em timestamptz NOT NULL,
            atualizado_em timestamptz NOT NULL,
            expira_em timestamptz NOT NULL,
            PRIMARY KEY (tenant, conversation_id),
            CONSTRAINT reembolso_coleta_prefixo_do_tenant
                CHECK (starts_with(conversation_id, 'wa:' || tenant || ':')),
            CONSTRAINT reembolso_coleta_expira_depois CHECK (expira_em > atualizado_em),
            CONSTRAINT reembolso_coleta_protocolo_so_aberto
                CHECK (protocolo IS NULL OR etapa IN ('aberto', 'resposta')),
            CONSTRAINT reembolso_coleta_resposta_so_na_etapa
                CHECK (resposta_texto = '' OR etapa = 'resposta')
        )
    """)
    op.execute("CREATE INDEX reembolso_coleta_expira_em ON reembolso_coleta (expira_em)")
    op.execute(
        "COMMENT ON TABLE reembolso_coleta IS 'Coleta do pedido de reembolso pelo WhatsApp (DL-0158): "
        "etapa e respostas FECHADAS por conversa, gravada por CAS (revisao). Nenhum telefone, nenhum texto "
        "livre; document_ref e opaco (o arquivo vive na Zona PHI)'"
    )
    op.execute("REVOKE ALL ON TABLE reembolso_coleta FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP TABLE reembolso_coleta")
