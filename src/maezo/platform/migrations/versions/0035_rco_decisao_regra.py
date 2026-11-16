"""Registro append-only das decisoes das regras K1 do rco (ADR-0077 §4, unidade 5c-2) — escuro.

**Por que existe.** O ADR-0077 §4 pede uma linha por avaliacao que casou uma regra do K1, em qualquer
estado manual, para medir a concordancia por regra ja em `sombra` (K5, painel de concordancia, monitor
de reversao da 5e) e para fixar a amostragem de revisao. `dmn_versions` na cadeia de auditoria prova
QUAL tabela decidiu; esta tabela guarda o que o monitor precisa contar (ordinal por regra, amostra,
desfecho) sem reler a cadeia.

**Quem escreve.** Um unico escritor: `WorkerHarness._registrar_decisoes_rco` (5c-2), depois da emissao da
auditoria, FAIL-OPEN (a linha e observacao; a auditoria e a fonte da verdade). Em 5c-2 toda regra esta
em `sombra` e o indicador `efeito_k1_habilitado` e falso: nenhum caso muda por causa desta tabela.

**Append-only.** Trigger `BEFORE UPDATE OR DELETE` e `BEFORE TRUNCATE` levantam excecao e
`REVOKE ALL ... FROM PUBLIC` (molde 0015/0032/0034; esta cadeia nao cria papeis de aplicacao). Por isso o
desfecho humano NAO e coluna da linha da decisao: e a tabela filha `rco_decisao_regra_desfecho`,
tambem append-only, com uma linha por desfecho registrado (a 5d/5e liga; vale o ultimo por `id`).

**Ordinal.** `ordinal` e o 1-based por (tenant, id_regra, versao_regra) das decisoes adversas coerentes
com a classe que a revisao inicial mira (ADR-0077 §3: `glosar` contas/administrativo, `negar`
administrativo); nulo nas demais. Linha perdida no caminho fail-open desloca os ordinais (5d/5e revisam).
Calculado por quem grava sob
`pg_advisory_xact_lock`; o indice unico parcial e a segunda barreira (um escritor que furasse o lock
levaria erro, nunca dois ordinais iguais).

**Idempotencia.** `UNIQUE (tenant, chave_idempotencia)`, onde `chave_idempotencia` e o sha256 de
`tenant:task_id:decision_key`: a re-entrega da tarefa externa nao duplica nem consome ordinal.

**PHI.** Nenhuma coluna de beneficiario, guia, CPF, carteirinha nem texto livre. A chave de negocio NAO
e gravada (minimizacao, LGPD; so vive em memoria para o HMAC da amostragem). O vinculo com o caso e
indireto: `task_id` entra so em hash em `chave_idempotencia` (sha256 de tenant:task_id:decision_key) e
aponta para a linha de auditoria; eliminada a auditoria daquele `task_id`, esta linha nao tem vinculo.
As saidas da DMN so entram com forma de token curto.

**Tenant.** Coluna `tenant` com o CHECK de OpaqueRef da casa (molde 0030/0032/0034) dentro do schema do
tenant (DL-0017, `search_path`).

**Cadeia alembic.** `down_revision = "0034"` (head da `main` com a 0034 do PAGTO PR-E).

Revision ID: 0035
Revises: 0034
Create Date: 2026-11-16
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0035"
down_revision: str | None = "0034"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_TOKEN = "'^[A-Za-z0-9._-]{1,64}$'"

_CREATE = rf"""
CREATE TABLE rco_decisao_regra (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    criado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
    -- Espelho de OpaqueRef (molde 0030): sem whitespace, sem controle, sem /?#; 1..512.
    tenant text NOT NULL CHECK (
        tenant <> '' AND char_length(tenant) <= 512 AND tenant !~ '[[:space:][:cntrl:]/?#]'
    ),
    -- sha256 de `tenant:task_id:decision_key` (ver docstring); a tarefa bruta nao e gravada.
    chave_idempotencia text NOT NULL CHECK (chave_idempotencia ~ '^[0-9a-f]{{64}}$'),
    id_regra text NOT NULL CHECK (id_regra ~ {_TOKEN}),
    versao_regra text NOT NULL CHECK (
        versao_regra ~ '^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
    ),
    sha_dmn text NOT NULL CHECK (sha_dmn ~ '^sha256:[0-9a-f]{{64}}$'),
    decision_key text NOT NULL CHECK (decision_key ~ '^rco_[A-Za-z0-9_-]{{1,120}}$'),
    -- Saidas da DMN: token curto ou nulo (nunca texto livre; ver docstring).
    decisao text CHECK (decisao ~ {_TOKEN}),
    classe_adverso text CHECK (classe_adverso ~ {_TOKEN}),
    motivo_tab38 text CHECK (motivo_tab38 ~ {_TOKEN}),
    fila text CHECK (fila ~ {_TOKEN}),
    estado_manual text NOT NULL CHECK (estado_manual IN ('sombra', 'ativa_l2', 'desligada')),
    ativacao_pacote text NOT NULL CHECK (ativacao_pacote IN ('plena', 'conferida', 'em_correcao')),
    ativacao_efetiva text NOT NULL CHECK (
        ativacao_efetiva IN ('plena', 'conferida', 'conferencia_integral', 'em_correcao')
    ),
    modo text NOT NULL CHECK (modo IN ('sombra', 'desligada', 'ativa')),
    -- Vocabulario de `rco_ativacao.ACOES` (saida de `filtrar_saida`).
    acao text NOT NULL CHECK (
        acao IN ('registrar_apenas', 'revisao_humana', 'conferencia', 'efeito_com_amostragem', 'efeito')
    ),
    amostrada boolean NOT NULL,
    motivo_amostra text CHECK (motivo_amostra IN ('inicial', 'aleatoria', 'chave_ausente')),
    CONSTRAINT rco_decisao_regra_amostra_coerente CHECK (amostrada = (motivo_amostra IS NOT NULL)),
    -- Qual chave HMAC decidiu a linha (a auditoria reproduz a amostra); nulo em inicial/chave_ausente.
    id_chave_amostra text CHECK (id_chave_amostra ~ '^[A-Za-z0-9._:-]{{1,64}}$'),
    ordinal bigint CHECK (ordinal > 0),
    CONSTRAINT rco_decisao_regra_idempotencia UNIQUE (tenant, chave_idempotencia)
)
"""

_CREATE_DESFECHO = r"""
CREATE TABLE rco_decisao_regra_desfecho (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    rco_decisao_regra_id bigint NOT NULL REFERENCES rco_decisao_regra (id),
    concordou text NOT NULL CHECK (concordou IN ('sim', 'nao', 'sem_desfecho')),
    -- Quando o humano decidiu (nulo em `sem_desfecho`); `registrado_em` e quando esta linha entrou.
    desfecho_em timestamptz,
    registrado_em timestamptz NOT NULL DEFAULT clock_timestamp()
)
"""


def _append_only(tabela: str) -> None:
    op.execute(f"""
        CREATE FUNCTION {tabela}_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '{tabela} e append-only';
        END $$
    """)
    op.execute(
        f"CREATE TRIGGER {tabela}_imutavel BEFORE UPDATE OR DELETE ON {tabela} "
        f"FOR EACH ROW EXECUTE FUNCTION {tabela}_imutavel()"
    )
    op.execute(
        f"CREATE TRIGGER {tabela}_sem_truncate BEFORE TRUNCATE ON {tabela} "
        f"FOR EACH STATEMENT EXECUTE FUNCTION {tabela}_imutavel()"
    )
    op.execute(f"REVOKE ALL ON TABLE {tabela} FROM PUBLIC")
    op.execute(f"REVOKE ALL ON FUNCTION {tabela}_imutavel() FROM PUBLIC")


def upgrade() -> None:
    op.execute(_CREATE)
    # Segunda barreira do ordinal: dois escritores nunca gravam o mesmo ordinal da mesma regra.
    op.execute(
        "CREATE UNIQUE INDEX rco_decisao_regra_ordinal_uidx "
        "ON rco_decisao_regra (tenant, id_regra, versao_regra, ordinal) WHERE ordinal IS NOT NULL"
    )
    # Leitura do monitor (5e): decisoes de uma regra, em ordem.
    op.execute(
        "CREATE INDEX rco_decisao_regra_regra_idx ON rco_decisao_regra (tenant, id_regra, versao_regra, id)"
    )
    _append_only("rco_decisao_regra")
    op.execute(
        "COMMENT ON TABLE rco_decisao_regra IS 'Registro append-only das decisoes das regras K1 do rco "
        "(ADR-0077 §4, 5c-2): uma linha por avaliacao que casou regra, em qualquer estado manual. "
        "Idempotente por (tenant, chave_idempotencia). Sem dado de beneficiario; chave de negocio nao "
        "gravada. Escuro: toda regra em sombra, efeito_k1_habilitado falso'"
    )

    op.execute(_CREATE_DESFECHO)
    op.execute(
        "CREATE INDEX rco_decisao_regra_desfecho_fk_idx "
        "ON rco_decisao_regra_desfecho (rco_decisao_regra_id, id)"
    )
    _append_only("rco_decisao_regra_desfecho")
    op.execute(
        "COMMENT ON TABLE rco_decisao_regra_desfecho IS 'Desfecho humano de uma decisao K1, ligado depois "
        "(5d/5e) como NOVA linha (a decisao e append-only); vale o ultimo desfecho por decisao'"
    )


def downgrade() -> None:
    # Descartavel enquanto escura (so' o harness escreve, fail-open). Downgrade e' ato explicito de
    # operador, nunca expurgo de runtime. A tabela filha primeiro (FK); depois as funcoes.
    op.execute("DROP TABLE rco_decisao_regra_desfecho")
    op.execute("DROP TABLE rco_decisao_regra")
    op.execute("DROP FUNCTION rco_decisao_regra_desfecho_imutavel()")
    op.execute("DROP FUNCTION rco_decisao_regra_imutavel()")
