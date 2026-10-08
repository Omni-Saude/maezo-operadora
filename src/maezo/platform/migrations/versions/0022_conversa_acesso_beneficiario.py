"""Acesso do beneficiario por conversa: consentimento + verificacao de identidade (DL-0083, 08/10/2026).

**Por que esta tabela existe.** Decisao do dono (08/10/2026): ANTES de qualquer agente, o WhatsApp passa por
uma maquina de estados DETERMINISTICA (sem LLM) por conversa: consentimento (ACEITO) -> CPF [-> data de
nascimento] -> verificado. Entre uma mensagem e a seguinte alguem precisa lembrar em que passo a conversa
esta'
e quantas tentativas ja' foram gastas. Nao pode ser o checkpoint do LangGraph (os agentes nem rodam nesses
estados, e o CPF/nascimento NUNCA podem chegar ao checkpoint) nem a tabela do roteador
(`conversa_agente_ativo` e' de outro assunto). Fica aqui: uma linha por `(tenant, conversation_id)`.

**O que NAO esta aqui.** Nenhum telefone, nenhum CPF, nenhuma data de nascimento, nenhum nome, nenhum texto
digitado pela pessoa. So': o `conversation_id` keyed (`wa:{tenant}:hk1_{hmac}`, ADR-0035), o ESTADO (enum
fechado), o contador de tentativas, a versao e o sha256 do texto de consentimento mostrado, os carimbos de
tempo e o `portable_subject_ref` PSEUDONIMO da AMH (so' depois de conhecido). O hash de verificacao e o CPF
existem so' em memoria, pelo instante da chamada a AMH.

**Colunas de pendencia de gravacao.** `consentimento_pendente_gravacao` e `revogacao_pendente` marcam que a
decisao ja' vale AQUI mas ainda nao foi gravada no lago da AMH (`consent-record`); a gravacao e' repetida nos
turnos seguintes ate' dar certo (fail-safe: o acesso nao fica refem da gravacao, o registro fica pendente e
visivel). `consent_ref` e' a referencia opaca que a AMH devolve. `regravacoes_falhas` conta as gravacoes que
falharam (teto 5): no teto a regravacao automatica para, um log de alarme e um escalonamento humano pedem
a conclusao manual (runbook `acesso-beneficiario-revogacao-pendente`). `falhas_janela`/`janela_inicio`
contam as falhas de verificacao numa janela de 24 h que um sucesso NAO zera (acima de 6: bloqueio ate' o fim
da janela + atendente).

**Escrita.** Upsert simples por `(tenant, conversation_id)`: o canal serializa as mensagens de uma conversa
e o
processo guarda um lock por conversa; nao ha' CAS (diferente de `conversa_agente_ativo`).

**Isolamento de tenant.** O padrao vigente do repo: schema por tenant (`search_path` pinado pelo `env.py`),
coluna `tenant` na PK e em todo `WHERE`, e um CHECK amarrando o prefixo do `conversation_id` ao proprio
`tenant`. O repo nao usa RLS em migration nenhuma.

**Retencao (DL-0084, decisao do dono).** A linha e' apagada apos 90 DIAS sem atividade (`ultima_mensagem_em`,
indexada abaixo) pelo varredor idempotente `whatsapp/acesso_retencao.py`; quem volta depois consente e se
verifica de novo. Excecao: `revogacao_pendente` nunca e' apagada antes de gravada no lago. A ratificacao
formal segue com o DPO (`erasure-plan.template.yaml`, camada `acesso_beneficiario`).

**Seguro com a versao anterior do codigo no ar.** A tabela e' nova e so' o acesso do beneficiario (desligado
por
padrao, `MAEZO_ACESSO_BENEFICIARIO`) a le' e escreve. O `downgrade` RECUSA com a tabela populada (mesmo
padrao da 0021): ela guarda consentimentos e REVOGACOES pendentes de gravacao no lago
(`revogacao_pendente`), e apagar uma revogacao que a AMH ainda nao recebeu e' perder o pedido do titular.
Disposicao e' do dono, nao da migration.

Revision ID: 0022
Revises: 0021
Create Date: 2026-10-08
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0022"
down_revision: str | None = "0021"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # op.execute embrulha a string em sa.text(): ':hk1_' viraria bind parameter. '[:]' casa o
    # mesmo ':' literal sem ser lido como bind (mesma tecnica da 0016/0017).
    op.execute(r"""
        CREATE TABLE conversa_acesso_beneficiario (
            tenant text NOT NULL CHECK (tenant <> ''),
            conversation_id text NOT NULL CHECK (conversation_id ~ '^wa:[^:]+[:]hk1_[0-9a-f]+$'),
            estado text NOT NULL CHECK (estado IN (
                'sem_consentimento', 'aguardando_cpf', 'aguardando_nascimento', 'verificado',
                'bloqueado_humano', 'revogado'
            )),
            tentativas smallint NOT NULL DEFAULT 0 CHECK (tentativas BETWEEN 0 AND 3),
            texto_versao text NULL CHECK (texto_versao ~ '^[a-z0-9][a-z0-9._-]{0,63}$'),
            texto_sha256 text NULL CHECK (texto_sha256 ~ '^[0-9a-f]{64}$'),
            consentido_em timestamptz NULL,
            revogado_em timestamptz NULL,
            portable_subject_ref text NULL CHECK (portable_subject_ref ~ '^[A-Za-z0-9._:~-]{1,256}$'),
            consent_ref text NULL CHECK (consent_ref ~ '^[A-Za-z0-9._:~-]{1,256}$'),
            consentimento_pendente_gravacao boolean NOT NULL DEFAULT false,
            revogacao_pendente boolean NOT NULL DEFAULT false,
            verificado_em timestamptz NULL,
            expira_em timestamptz NULL,
            bloqueado_ate timestamptz NULL,
            ultima_mensagem_em timestamptz NOT NULL,
            regravacoes_falhas smallint NOT NULL DEFAULT 0 CHECK (regravacoes_falhas BETWEEN 0 AND 5),
            falhas_janela smallint NOT NULL DEFAULT 0 CHECK (falhas_janela BETWEEN 0 AND 7),
            janela_inicio timestamptz NULL,
            PRIMARY KEY (tenant, conversation_id),
            CONSTRAINT conversa_acesso_prefixo_do_tenant
                CHECK (starts_with(conversation_id, 'wa:' || tenant || ':')),
            CONSTRAINT conversa_acesso_verificado_completo
                CHECK (estado <> 'verificado'
                       OR (portable_subject_ref IS NOT NULL AND verificado_em IS NOT NULL
                           AND expira_em IS NOT NULL AND consentido_em IS NOT NULL)),
            CONSTRAINT conversa_acesso_bloqueio_com_prazo
                CHECK (estado <> 'bloqueado_humano' OR bloqueado_ate IS NOT NULL),
            CONSTRAINT conversa_acesso_pendencia_com_ref
                CHECK ((NOT consentimento_pendente_gravacao AND NOT revogacao_pendente)
                       OR portable_subject_ref IS NOT NULL),
            CONSTRAINT conversa_acesso_consentimento_com_texto
                CHECK (consentido_em IS NULL OR (texto_versao IS NOT NULL AND texto_sha256 IS NOT NULL))
        )
    """)
    op.execute(
        "CREATE INDEX conversa_acesso_beneficiario_ultima_mensagem "
        "ON conversa_acesso_beneficiario (ultima_mensagem_em)"
    )
    op.execute(
        "COMMENT ON TABLE conversa_acesso_beneficiario IS 'Acesso do beneficiario (DL-0083): estado do "
        "consentimento e da verificacao por conversa. So conversation_id keyed, estado, contadores, "
        "versao/sha256 do texto, carimbos e portable_subject_ref pseudonimo; nenhum telefone, CPF, "
        "nascimento, nome ou texto digitado'"
    )
    op.execute("REVOKE ALL ON TABLE conversa_acesso_beneficiario FROM PUBLIC")


def downgrade() -> None:
    # Reversao estrutural NUNCA apaga consentimento vivo nem revogacao pendente de gravacao no lago:
    # disposicao e' do dono qualificado, nao da migration (padrao da 0021).
    op.execute("""DO $$
    BEGIN
        IF EXISTS (SELECT 1 FROM conversa_acesso_beneficiario) THEN
            RAISE EXCEPTION 'populated conversa_acesso_beneficiario requires qualified lifecycle disposition';
        END IF;
    END $$""")
    op.execute("DROP TABLE conversa_acesso_beneficiario")
