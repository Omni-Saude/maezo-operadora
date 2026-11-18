"""Linha de grupo dos avisos `rco.aviso_auditoria` e o codigo `fila_sem_grupo` do outbox (DL-0186) — escuro.

**Por que existe.** O relay da 0036 publica cada aviso de `sinalAuditoria` do K1 do rco em
`operadora.notifications.internal`; o consumidor (`notifications_inbox`, unidade 2b) confere a mensagem e a
endereca ao `grupo_principal` que o K6 PINADO declara para a fila (`rco_pin.RcoRulesPin.grupos_principais`).
Um offset commitado nao prova que alguem foi avisado (ADR-0037 XRD-10): so uma linha commitada prova. Esta
tabela e' essa linha. Ela NAO reaproveita `escalation_team_notice` (0015): aquela tabela nao tem coluna para
o tipo do aviso, o sinal, a fila nem o parametro do prazo, e alterar o recibo de outro processo para caber
este misturaria dois contratos numa relacao imutavel ja lida por humanos.

**Uma linha por (aviso, grupo).** Uma mensagem do relay = uma fila = um grupo = uma linha. A chave primaria
e' `(tenant, notice_ref)`, com `notice_ref` = sha256 canonico de
`(type, tenant, chave_idempotencia, grupo, audience)` (`notifications_inbox.AvisoRcoGrupo.notice_ref`): a
re-entrega (do relay, at-least-once, ou do broker) cai em `ON CONFLICT DO NOTHING` na mesma linha. Se um pin
novo mudar o grupo da fila, a re-entrega vira linha do grupo novo (direcao nao adversa), nunca reescreve a
antiga.

**Imutavel.** Trigger `BEFORE UPDATE OR DELETE` e `BEFORE TRUNCATE` levantam excecao e
`REVOKE ALL ... FROM PUBLIC` (molde 0015/0035; esta cadeia nao cria papeis de aplicacao): a linha e' o
registro do que foi entregue a um grupo; corrigir e' gravar outra, nunca reescrever a que um humano pode ja
ter lido.

**`fila_sem_grupo` no outbox.** O relay passa a marcar, ANTES de publicar, a linha do outbox cuja fila nao tem
`grupo_principal` no pin (`falhou` com `ultimo_erro = 'fila_sem_grupo'`, sem contar tentativa). Por isso esta
migracao troca o CHECK de `rco_aviso_auditoria.ultimo_erro` da 0036 (nome gerado pelo Postgres para o CHECK
de coluna: `rco_aviso_auditoria_ultimo_erro_check`) por `IN ('publicacao_falhou', 'fila_sem_grupo')`. A 0036
nao e' editada. Linhas antigas com `publicacao_falhou` continuam validas.

**PHI.** Nenhuma coluna de beneficiario, guia, CPF, carteirinha, chave de negocio nem texto livre. A linha
guarda so o que a mensagem do relay ja leva (`tenant`, `task_id` da tarefa externa, nome da composicao, sinal
e fila do vocabulario fechado do pacote, NOME do parametro do prazo do K6, `chave_idempotencia`), o grupo do
pin e a audiencia fixa `staff`. Todas com CHECK de forma de token, as mesmas da 0036 e de
`rco_avisos.linha_da_mensagem`.

**Tenant.** Coluna `tenant` com o CHECK de OpaqueRef da casa (molde 0030/0035/0036) dentro do schema do
tenant (DL-0017, `search_path`).

**Retencao.** A tabela entra na matriz de eliminacao (`lifecycle/erasure_plan.py`, camada
`aviso_grupo_regras_k1`) como `SEM_COLUNA_DE_TITULAR`; prazo, base legal e eliminacao pendem do DPO.

**Downgrade.** Apaga a tabela e a funcao e devolve o CHECK original (`= 'publicacao_falhou'`). Se houver
linha do outbox com `ultimo_erro = 'fila_sem_grupo'`, o downgrade FALHA com excecao explicita antes de mudar
qualquer coisa: reescrever o codigo para `publicacao_falhou` falsificaria o registro (nada foi publicado) e
apagar a linha perderia o aviso. O operador decide antes (`rco_avisos_relay --reprocessar` devolve as `falhou`
a `pendente` com `ultimo_erro` nulo) e repete o downgrade.

**Cadeia alembic.** `down_revision = "0036"` (head da `main` com o outbox dos avisos, DL-0185).

Revision ID: 0037
Revises: 0036
Create Date: 2026-11-18
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0037"
down_revision: str | None = "0036"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOKEN = "'^[A-Za-z0-9._-]{1,64}$'"

#: Nome que o Postgres deu ao CHECK de coluna `ultimo_erro` da 0036 (`<tabela>_<coluna>_check`).
_CHECK_ULTIMO_ERRO = "rco_aviso_auditoria_ultimo_erro_check"

_CREATE = rf"""
CREATE TABLE rco_aviso_grupo (
    -- Espelho de OpaqueRef (molde 0030/0035/0036): sem whitespace, sem controle, sem /?#; 1..512.
    tenant text NOT NULL CHECK (
        tenant <> '' AND char_length(tenant) <= 512 AND tenant !~ '[[:space:][:cntrl:]/?#]'
    ),
    -- sha256 canonico de (type, tenant, chave_idempotencia, grupo, audience) (ver docstring).
    notice_ref text NOT NULL CHECK (notice_ref ~ '^[0-9a-f]{{64}}$'),
    -- A chave da linha do outbox (0036): sha256 de `tenant:task_id:composicao:sinal:fila`.
    chave_idempotencia text NOT NULL CHECK (chave_idempotencia ~ '^[0-9a-f]{{64}}$'),
    -- Id da tarefa externa do motor (o mesmo da `dedup_key` da auditoria); nunca a chave de negocio.
    task_id text NOT NULL CHECK (task_id ~ '^[A-Za-z0-9._:-]{{1,128}}$'),
    composicao text NOT NULL CHECK (composicao ~ '^[A-Za-z0-9_-]{{1,120}}$'),
    sinal text NOT NULL CHECK (sinal ~ {_TOKEN}),
    fila text NOT NULL CHECK (fila ~ {_TOKEN}),
    prazo_parametro text NOT NULL CHECK (prazo_parametro ~ '^[A-Za-z0-9._:-]{{1,128}}$'),
    -- `grupo_principal` da fila no K6 pinado (dado do pin; `rco_manifesto`, forma de token).
    grupo text NOT NULL CHECK (grupo ~ {_TOKEN}),
    audience text NOT NULL DEFAULT 'staff' CHECK (audience = 'staff'),
    registrado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
    PRIMARY KEY (tenant, notice_ref)
)
"""

_ULTIMO_ERRO_0037 = "ultimo_erro IN ('publicacao_falhou', 'fila_sem_grupo')"
_ULTIMO_ERRO_0036 = "ultimo_erro IN ('publicacao_falhou')"

#: Recusa explicita do downgrade com linha que o CHECK original nao aceita (ver docstring).
_RECUSAR_DOWNGRADE_COM_SEM_GRUPO = """
DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM rco_aviso_auditoria WHERE ultimo_erro = 'fila_sem_grupo') THEN
        RAISE EXCEPTION 'downgrade 0037 recusado: ha linhas de rco_aviso_auditoria com ultimo_erro = '
            'fila_sem_grupo; o CHECK da 0036 nao as aceita e reescrever o codigo falsificaria o registro. '
            'Rode rco_avisos_relay --reprocessar (ou decida as linhas) e repita o downgrade';
    END IF;
END $$
"""


def upgrade() -> None:
    op.execute(_CREATE)
    # A leitura de uma fila de equipe: "o que espera o MEU grupo, mais novo primeiro" (molde 0015).
    op.execute("CREATE INDEX rco_aviso_grupo_fila_idx ON rco_aviso_grupo (tenant, grupo, registrado_em DESC)")
    op.execute("""
        CREATE FUNCTION rco_aviso_grupo_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'rco_aviso_grupo e imutavel';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER rco_aviso_grupo_imutavel BEFORE UPDATE OR DELETE ON rco_aviso_grupo "
        "FOR EACH ROW EXECUTE FUNCTION rco_aviso_grupo_imutavel()"
    )
    op.execute(
        "CREATE TRIGGER rco_aviso_grupo_sem_truncate BEFORE TRUNCATE ON rco_aviso_grupo "
        "FOR EACH STATEMENT EXECUTE FUNCTION rco_aviso_grupo_imutavel()"
    )
    op.execute("REVOKE ALL ON TABLE rco_aviso_grupo FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION rco_aviso_grupo_imutavel() FROM PUBLIC")
    op.execute(
        "COMMENT ON TABLE rco_aviso_grupo IS 'Linha imutavel de um aviso rco.aviso_auditoria (DL-0186) "
        "enderecado ao grupo_principal da fila no K6 pinado: gravada pelo notifications_inbox antes de o "
        "offset avancar. Idempotente por (tenant, notice_ref). Sem dado de beneficiario nem texto livre'"
    )
    # O outbox da 0036 ganha o codigo fechado `fila_sem_grupo` (o relay marca antes de publicar).
    op.execute(
        f"ALTER TABLE rco_aviso_auditoria DROP CONSTRAINT {_CHECK_ULTIMO_ERRO}, "
        f"ADD CONSTRAINT {_CHECK_ULTIMO_ERRO} CHECK ({_ULTIMO_ERRO_0037})"
    )


def downgrade() -> None:
    # Ato explicito de operador, nunca expurgo de runtime. Primeiro a recusa (nada muda se houver
    # `fila_sem_grupo`); depois o CHECK original, a tabela e a funcao.
    op.execute(_RECUSAR_DOWNGRADE_COM_SEM_GRUPO)
    op.execute(
        f"ALTER TABLE rco_aviso_auditoria DROP CONSTRAINT {_CHECK_ULTIMO_ERRO}, "
        f"ADD CONSTRAINT {_CHECK_ULTIMO_ERRO} CHECK ({_ULTIMO_ERRO_0036})"
    )
    op.execute("DROP TABLE rco_aviso_grupo")
    op.execute("DROP FUNCTION rco_aviso_grupo_imutavel()")
