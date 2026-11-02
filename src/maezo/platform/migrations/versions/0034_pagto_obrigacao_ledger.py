"""Razao append-only das obrigacoes de pagamento (pacote PAGTO PR-E, detector de duplicidade) — escuro.

**Por que existe.** A analise PAGTO (§4, `operadora.pagto.detect_duplicidade`; §8 D6(A), aceita
pelo dono como "razao append-only de pagamentos") pede um registro DURAVEL de cada ordem de
pagamento que tocou uma obrigacao: a deduplicacao de start PERMANENT de SP-OP-PAGTO-001 so' pega
a MESMA business key, nunca a mesma obrigacao chegando sob outra chave (conta paga por CONTAS e
de novo por RECURSO, lote reapresentado com outro numero). O historico do motor expira; esta
tabela nao depende dele.

**Quem escreve, e quando** (analise §4 D6(A): "written at start and at release"). So dois
escritores, ambos atras da bandeira `MAEZO_PAGTO_DETECT_DUPLICIDADE` (desligada por padrao):
(i) `REGISTRADA` — o detector, no topico `operadora.pagto.validate_payment_data` (o primeiro
worker da ordem em PAGTO), DEPOIS da validacao de sempre, na mesma transacao em que le os
indicios; (ii) `LIBERADA` — o envoltorio dos DOIS workers de liberacao, so' DEPOIS que o guard
de sempre devolveu a liberacao (recusa do guard = nada gravado). Nenhum dos dois libera, cancela
ou roteia: a linha e' registro, nao efeito.

**Append-only.** Trigger `BEFORE UPDATE OR DELETE` e `BEFORE TRUNCATE` levantam excecao (molde
0015/0032) e `REVOKE ALL ... FROM PUBLIC` (esta cadeia nao cria papeis de aplicacao). Uma ordem
que muda de estado ganha NOVA linha (novo `evento`), nunca reescrita.

**Chave natural.** `UNIQUE (tenant, chave_obrigacao, ordem_pagamento_id, evento)`: idempotente por
(ordem, evento) — a re-entrega da mesma tarefa externa cai na mesma linha. A unicidade NAO e'
por obrigacao sozinha DE PROPOSITO: uma UNIQUE em `(tenant, chave_obrigacao)` faria o banco
recusar a segunda ordem e APAGAR a prova da duplicidade que o detector existe para mostrar. O
nivel 1 (mesma obrigacao, outra ordem) e' a leitura dentro do mesmo lock transacional
(`pg_advisory_xact_lock`) em que a linha e' gravada — duas ordens concorrentes da mesma
obrigacao se serializam e a segunda ve a primeira.

`chave_obrigacao` = `OBRIG-{origem}-{sha256}` sobre `[tenant, origem, componentes...]`
(`maezo.platform.pagto_obrigacao.ledger.chave_obrigacao`): componentes por origem conforme a
analise §4 nivel 1 — CONTAS: lote + prestador + guia + conta (`numero_conta`, semeado pelo
handoff de CONTAS a partir do PR #1180; ausente = "-") (a identidade de que o protocolo do
demonstrativo deriva; o protocolo nao chega a PAGTO); RECURSO: `glosa_id`; outro:
`ordem_pagamento_id`. Digest em vez de concatenacao: sem ambiguidade de separador.

**Tenant.** Coluna `tenant` com o CHECK de OpaqueRef da casa (molde 0030/0032) dentro do schema
do tenant (DL-0017, `search_path`).

**PHI.** Nenhuma coluna de beneficiario. So' o que a ordem de PAGTO ja carrega como Zona Geral
(contrato SP-OP-PAGTO-001, variaveis de entrada): id da ordem, `prestador_id`, lote TISS,
competencia (`YYYY-MM`), valor em centavos, a origem (enum fechado) e o id da instancia de
origem resolvida pelo PR-D. Guia e glosa so' entram DENTRO do digest da chave. Nenhum texto
livre.

**Cadeia alembic.** `down_revision = "0032"` (head da main em 01/11/2026, ledger de status do
prestador do PR #1175). O PR #1074 (aberto) passa a trazer a 0033 revisando a 0032; se ele entrar
antes, o orquestrador re-aponta `down_revision` desta migracao para "0033" (e os pins de head ja dizem
0034).

Revision ID: 0034
Revises: 0032
Create Date: 2026-11-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0034"
down_revision: str | None = "0032"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE = r"""
CREATE TABLE pagto_obrigacao_ledger (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Espelho de OpaqueRef (molde 0030): sem whitespace, sem controle, sem /?#; 1..512.
    tenant text NOT NULL CHECK (
        tenant <> '' AND char_length(tenant) <= 512 AND tenant !~ '[[:space:][:cntrl:]/?#]'
    ),
    -- REGISTRADA (detector, na validacao) | LIBERADA (depois do guard de liberacao).
    evento text NOT NULL CHECK (evento IN ('REGISTRADA', 'LIBERADA')),
    origem text NOT NULL CHECK (origem IN ('contas', 'recurso', 'outro')),
    -- Digest da obrigacao (ver docstring); a origem esta' no prefixo e tem de bater com a coluna.
    chave_obrigacao text NOT NULL CHECK (chave_obrigacao ~ '^OBRIG-(contas|recurso|outro)-[0-9a-f]{64}$'),
    CONSTRAINT pagto_obrigacao_ledger_chave_da_origem CHECK (
        chave_obrigacao LIKE 'OBRIG-' || origem || '-%'
    ),
    ordem_pagamento_id text NOT NULL CHECK (
        ordem_pagamento_id <> '' AND char_length(ordem_pagamento_id) <= 256
        AND ordem_pagamento_id !~ '[[:space:][:cntrl:]]'
    ),
    -- Identificador do prestador (Zona Geral). Charset fechado (molde 0032).
    prestador_id text NOT NULL CHECK (prestador_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
    numero_lote_tiss text CHECK (
        numero_lote_tiss <> '' AND char_length(numero_lote_tiss) <= 128
        AND numero_lote_tiss !~ '[[:space:][:cntrl:]]'
    ),
    -- Contrato SP-OP-PAGTO-001: `competencia` YYYY-MM. Nula quando a ordem nao a traz.
    competencia text CHECK (competencia ~ '^[0-9]{4}-(0[1-9]|1[0-2])$'),
    -- Mesmo dominio de `valor_pagamento_cents` (inteiro > 0, cabe em Java long).
    valor_cents bigint NOT NULL CHECK (valor_cents > 0),
    -- Instancia de origem resolvida no historico do motor (PR-D, `lastro_origem_instancia_id`).
    origem_instancia_id text CHECK (origem_instancia_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
    -- Versao do detector que produziu a linha (reprodutibilidade).
    versao_detector text NOT NULL CHECK (versao_detector ~ '^[a-z0-9./-]{1,64}$'),
    registrado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT pagto_obrigacao_ledger_evento_unico
        UNIQUE (tenant, chave_obrigacao, ordem_pagamento_id, evento)
)
"""


def upgrade() -> None:
    op.execute(_CREATE)
    # Nivel 2 (soma por instancia de origem) e nivel 3 (similaridade) — eixos de leitura do detector.
    op.execute(
        "CREATE INDEX pagto_obrigacao_ledger_origem_idx "
        "ON pagto_obrigacao_ledger (tenant, origem_instancia_id) WHERE origem_instancia_id IS NOT NULL"
    )
    op.execute(
        "CREATE INDEX pagto_obrigacao_ledger_similar_idx "
        "ON pagto_obrigacao_ledger (tenant, prestador_id, competencia, valor_cents)"
    )
    op.execute("""
        CREATE FUNCTION pagto_obrigacao_ledger_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'pagto_obrigacao_ledger e append-only';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER pagto_obrigacao_ledger_imutavel BEFORE UPDATE OR DELETE ON "
        "pagto_obrigacao_ledger FOR EACH ROW EXECUTE FUNCTION pagto_obrigacao_ledger_imutavel()"
    )
    op.execute(
        "CREATE TRIGGER pagto_obrigacao_ledger_sem_truncate BEFORE TRUNCATE ON "
        "pagto_obrigacao_ledger FOR EACH STATEMENT EXECUTE FUNCTION "
        "pagto_obrigacao_ledger_imutavel()"
    )
    op.execute(
        "COMMENT ON TABLE pagto_obrigacao_ledger IS 'Razao append-only das obrigacoes de pagamento "
        "(pacote PAGTO PR-E): REGISTRADA na validacao, LIBERADA depois do guard. Idempotente por "
        "(tenant, chave_obrigacao, ordem_pagamento_id, evento). Sem dado de beneficiario. Escuro atras "
        "de MAEZO_PAGTO_DETECT_DUPLICIDADE'"
    )
    op.execute("REVOKE ALL ON TABLE pagto_obrigacao_ledger FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION pagto_obrigacao_ledger_imutavel() FROM PUBLIC")


def downgrade() -> None:
    # Descartavel enquanto escura (nenhum escritor roda com a bandeira desligada). Downgrade e'
    # ato explicito de operador, nunca expurgo de runtime.
    op.execute("DROP TABLE pagto_obrigacao_ledger")
    op.execute("DROP FUNCTION pagto_obrigacao_ledger_imutavel()")
