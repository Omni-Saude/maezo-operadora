"""Outbox dos avisos de `sinalAuditoria` do K1 do rco (DL-0185, unidade 2a) — escuro.

**Por que existe.** O pacote K1 2.3.0 (DL-0183) pode devolver, numa composicao, um `sinalAuditoria` nao
vazio (ex.: `aviso_urgencia_carencia`), e o K6 pinado declara para cada sinal as filas que precisam ser
avisadas e o parametro do prazo da analise (`rco_manifesto.AvisoK6`). O aviso nao tem tarefa BPMN propria
(nao ha `notify_sla_risk` para ele, nem retry do motor): sem uma fila duravel, um broker fora do ar no
instante da decisao perderia o aviso em silencio. Esta tabela e' essa fila: o harness grava as linhas
DEPOIS de completar a tarefa (fail-open, nunca muda a decisao) e o relay `maezo.platform.rco_avisos_relay`
publica cada uma em `operadora.notifications.internal`. O consumidor (unidade 2b) nao e' desta unidade.

**Uma linha por (aviso, fila).** O sinal e' expandido pelas filas do K6 (`rco_avisos.avisos_para`): um
sinal com duas filas sao duas linhas, entregues e falhadas independentemente.

**Idempotencia.** `UNIQUE (tenant, chave_idempotencia)`, com `chave_idempotencia` = sha256 de
`tenant:task_id:composicao:sinal:fila` (`rco_avisos.chave_idempotencia`): a re-entrega da tarefa externa
(mesmo `task_id`, estavel no motor) cai em `ON CONFLICT DO NOTHING` e nao duplica o aviso.

**Ciclo de vida.** `pendente -> reivindicada (lease) -> entregue | falhou`, no molde da 0008:
    pendente      gravada, nunca reivindicada (ou devolvida por um lote interrompido, ou reprocessada).
    reivindicada  em posse de um relay ate `reivindicacao_expira_em`; vencido o prazo, qualquer relay a
                  reivindica de novo (queda entre publicar e marcar = republicacao, at-least-once).
    entregue      terminal; `entregue_em` preenchido.
    falhou        a publicacao falhou; `ultimo_erro` guarda um CODIGO FECHADO (nunca texto do broker) e
                  `tentativas` conta as publicacoes tentadas. So volta a `pendente` por ato de operador
                  (`--reprocessar`), para que um broker fora do ar nao vire um laco de republicacao.
Os CHECKs sao bicondicionais onde a 0008 explica por que (entregue <=> `entregue_em`, reivindicada <=>
lease) e `falhou` exige o codigo.

**PHI.** Nenhuma coluna de beneficiario, guia, CPF, carteirinha, chave de negocio nem texto livre. A linha
guarda so ids que a auditoria ja usa (`tenant`, `task_id`, que compoe a `dedup_key` da linha de auditoria
`tenant:task_id`), o nome da composicao do manifesto, o sinal e a fila do vocabulario fechado do pacote e o
NOME do parametro do prazo publicado no K6. Todas com CHECK de forma de token.

**Retencao.** A tabela entra na matriz de eliminacao (`lifecycle/erasure_plan.py`, camada
`aviso_auditoria_regras_k1`) como `SEM_COLUNA_DE_TITULAR`; prazo, base legal e eliminacao pendem do DPO.

**Cadeia alembic.** `down_revision = "0035"` (head da `main` com o registro de decisoes K1). A 0031 ficou
vaga (reservada ao PR #1074, que entrou como 0033): nao e' usada aqui.

Revision ID: 0036
Revises: 0035
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0036"
down_revision: str | None = "0035"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOKEN = "'^[A-Za-z0-9._-]{1,64}$'"

_CREATE = rf"""
CREATE TABLE rco_aviso_auditoria (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    criado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- Espelho de OpaqueRef (molde 0030/0035): sem whitespace, sem controle, sem /?#; 1..512.
    tenant text NOT NULL CHECK (
        tenant <> '' AND char_length(tenant) <= 512 AND tenant !~ '[[:space:][:cntrl:]/?#]'
    ),
    -- sha256 de `tenant:task_id:composicao:sinal:fila` (ver docstring).
    chave_idempotencia text NOT NULL CHECK (chave_idempotencia ~ '^[0-9a-f]{{64}}$'),
    -- Id da tarefa externa do motor (o mesmo da `dedup_key` da auditoria); nunca a chave de negocio.
    task_id text NOT NULL CHECK (task_id ~ '^[A-Za-z0-9._:-]{{1,128}}$'),
    composicao text NOT NULL CHECK (composicao ~ '^[A-Za-z0-9_-]{{1,120}}$'),
    sinal text NOT NULL CHECK (sinal ~ {_TOKEN}),
    fila text NOT NULL CHECK (fila ~ {_TOKEN}),
    prazo_parametro text NOT NULL CHECK (prazo_parametro ~ '^[A-Za-z0-9._:-]{{1,128}}$'),
    status text NOT NULL DEFAULT 'pendente' CHECK (
        status IN ('pendente', 'reivindicada', 'entregue', 'falhou')
    ),
    tentativas integer NOT NULL DEFAULT 0 CHECK (tentativas >= 0),
    -- Codigo FECHADO da ultima falha (`rco_avisos.ERROS`); nunca a mensagem do broker.
    ultimo_erro text CHECK (ultimo_erro IN ('publicacao_falhou')),
    reivindicada_por text CHECK (reivindicada_por ~ '^[A-Za-z0-9._:-]{{1,255}}$'),
    reivindicacao_expira_em timestamptz,
    entregue_em timestamptz,
    atualizado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT rco_aviso_auditoria_entregue CHECK ((status = 'entregue') = (entregue_em IS NOT NULL)),
    CONSTRAINT rco_aviso_auditoria_lease CHECK (
        (status = 'reivindicada') = (reivindicacao_expira_em IS NOT NULL)
    ),
    CONSTRAINT rco_aviso_auditoria_falha_com_codigo CHECK (status <> 'falhou' OR ultimo_erro IS NOT NULL),
    CONSTRAINT rco_aviso_auditoria_idempotencia UNIQUE (tenant, chave_idempotencia)
)
"""


def upgrade() -> None:
    op.execute(_CREATE)
    # Varredura do relay: o que ainda nao saiu, mais antigo primeiro. PARCIAL (molde 0008): o indice
    # acompanha o trabalho em voo, nao o historico.
    op.execute(
        "CREATE INDEX rco_aviso_auditoria_pendentes_idx ON rco_aviso_auditoria (criado_em, id) "
        "WHERE status IN ('pendente', 'reivindicada')"
    )
    # `--reprocessar` e a triagem do operador: as que falharam.
    op.execute(
        "CREATE INDEX rco_aviso_auditoria_falhas_idx ON rco_aviso_auditoria (id) WHERE status = 'falhou'"
    )
    op.execute("REVOKE ALL ON TABLE rco_aviso_auditoria FROM PUBLIC")
    op.execute(
        "COMMENT ON TABLE rco_aviso_auditoria IS 'Outbox dos avisos de sinalAuditoria do K1 do rco "
        "(DL-0185): uma linha por (aviso, fila) do K6 pinado, gravada pelo harness depois de completar a "
        "tarefa (fail-open) e publicada pelo relay em operadora.notifications.internal. Idempotente por "
        "(tenant, chave_idempotencia). Sem dado de beneficiario nem texto livre'"
    )


def downgrade() -> None:
    # Inverso honesto: uma tabela e seus dois indices. Ato explicito de operador, nunca expurgo de runtime.
    op.execute("DROP TABLE rco_aviso_auditoria")
