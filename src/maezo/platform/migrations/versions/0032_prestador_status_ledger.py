"""Ledger append-only do status do prestador (MPTT P3, camada A de fatos duros) — escuro.

**Por que existe.** A analise "Provider Trust Tiers" (§0, §2; decisao do dono D-T1(A): "Status
do prestador: sistema proprio do Maezo — ledger (baseline AMH assinada + deltas de
CRED/FRAUDE)") exige que o status legal do prestador (credenciado / descredenciado, e o
desfecho de investigacao de fraude que o referencia) seja PROJETADO numa tabela duravel: o
historico do motor expira em 1825 dias (`historyTimeToLive` de CRED/FRAUDE), entao o status de
longa vida nao pode depender dele.

**Quem escreve.** So dois escritores, ambos em `maezo.platform.prestador_status`:
(i) a importacao UNICA da baseline atestada pela equipe de rede da AMH (recusa entrada sem
atestacao — a assinatura e' humana); (ii) o reconciliador deterministico que le os TERMINAIS de
SP-OP-CRED-001 e SP-OP-FRAUDE-001 pela costura de historico do PR #1167. Os dois ficam atras da
bandeira `MAEZO_PRESTADOR_STATUS_LEDGER` (desligada por padrao): nenhum caminho de runtime
escreve aqui enquanto ela estiver desligada, e NENHUM consumidor le esta tabela neste PR (o
worker `resolve_prestador` e' o MPTT P6).

**Append-only.** Nenhuma linha muda depois de gravada: trigger `BEFORE UPDATE OR DELETE` e
`BEFORE TRUNCATE` levantam excecao (molde 0015), e `REVOKE ALL ... FROM PUBLIC` (molde de toda a
cadeia; esta cadeia nao cria papeis de aplicacao, entao nao ha GRANT de UPDATE/DELETE a revogar
de ninguem alem de PUBLIC). Correcao de status = nova linha (novo evento), nunca reescrita.

**Idempotencia.** `UNIQUE (tenant, chave_evento)`: a chave e' a do evento de origem —
`CRED:{instance_id}:{endActivityId}` / `FRAUDE:{instance_id}:{endActivityId}` para o motor
(idempotente em (instancia, evento final), analise §2) e `BASELINE_AMH:{sha256}:{prestador_id}`
para a baseline. Reprocessar o mesmo terminal (varredura noturna + evento) cai na mesma linha.

**Tenant.** Coluna `tenant` com o CHECK de OpaqueRef da casa (molde 0030) dentro do schema do
tenant (DL-0017, `search_path`); nenhuma linha cruza tenant (CADE, analise §5).

**PHI.** So identificadores e atributos CADASTRAIS do prestador (Zona Geral, ADR-0006 — o
contrato CRED declara `prestador_id` dado cadastral, nao PHI de beneficiario): nenhuma coluna de
beneficiario, nenhum texto livre, nenhum valor clinico. `valor` e `dimensao` sao enums fechados
por CHECK; nenhum estado fora deles e' persistivel. `DESCONHECIDO`/`INDISPONIVEL` NAO sao
gravaveis: sao estados de LEITURA (ausencia de linha / leitura falhou), nunca fatos.

**Cadeia alembic.** `down_revision = "0030"` (head da main em 01/11/2026). O PR #1074 (ainda nao
mergeado) adiciona a 0031; se ele entrar antes, o orquestrador re-aponta `down_revision` desta
migracao para "0031" (e os pins de head ja dizem 0032).

Revision ID: 0032
Revises: 0030
Create Date: 2026-11-01
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0032"
down_revision: str | None = "0030"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_CREATE = r"""
CREATE TABLE prestador_status_ledger (
    id bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    -- Espelho de OpaqueRef (molde 0030): sem whitespace, sem controle, sem /?#; 1..512.
    tenant text NOT NULL CHECK (
        tenant <> '' AND char_length(tenant) <= 512 AND tenant !~ '[[:space:][:cntrl:]/?#]'
    ),
    -- Identificador do prestador (pseudonimo/cadastral, Zona Geral). Charset fechado.
    prestador_id text NOT NULL CHECK (prestador_id ~ '^[A-Za-z0-9_.:-]{1,128}$'),
    origem text NOT NULL CHECK (origem IN ('BASELINE_AMH', 'CRED', 'FRAUDE')),
    dimensao text NOT NULL CHECK (dimensao IN ('situacao', 'fraude')),
    -- Enum fechado POR dimensao, e a origem tem de poder produzir a dimensao: a baseline e o CRED
    -- so' falam de situacao; o FRAUDE so' de fraude.
    valor text NOT NULL,
    CONSTRAINT prestador_status_ledger_valor_por_dimensao CHECK (
        (dimensao = 'situacao' AND origem IN ('BASELINE_AMH', 'CRED')
            AND valor IN ('CREDENCIADO', 'DESCREDENCIADO'))
        OR (dimensao = 'fraude' AND origem = 'FRAUDE'
            AND valor IN ('CONFIRMADA', 'MONITORAR', 'ARQUIVADA', 'ENCERRADA_SEM_DECISAO'))
    ),
    -- Data em que o status passou a valer (inicio do credenciamento / efeito do
    -- descredenciamento). Nula quando a fonte nao a informa — nunca inventada.
    data_efeito date,
    -- Chave de idempotencia do EVENTO de origem (ver docstring). Teto por char_length: o motor
    -- de regex do PostgreSQL recusa contagem de repeticao acima de 255 (molde 0023/0030).
    chave_evento text NOT NULL CHECK (
        char_length(chave_evento) BETWEEN 1 AND 400 AND chave_evento ~ '^[A-Za-z0-9_.:-]+$'
    ),
    -- De onde o fato veio: id da instancia do motor, ou sha256 do conteudo da baseline.
    fonte_ref text NOT NULL CHECK (fonte_ref ~ '^[A-Za-z0-9_.:-]{1,128}$'),
    -- Evidencia legivel: business key da instancia, ou a referencia ao documento assinado da
    -- baseline (o documento fica com a AMH; aqui so' a referencia).
    evidencia_ref text NOT NULL CHECK (
        evidencia_ref <> '' AND char_length(evidencia_ref) <= 512
        AND evidencia_ref !~ '[[:space:][:cntrl:]]'
    ),
    -- Atributos cadastrais do prestador (CRED-001 linhas 54/66/67), opcionais e limitados.
    tipo_prestador text CHECK (tipo_prestador ~ '^[a-z_]{1,32}$'),
    especialidade text CHECK (
        char_length(especialidade) BETWEEN 1 AND 128 AND especialidade !~ '[[:cntrl:]]'
    ),
    regiao_saude text CHECK (
        char_length(regiao_saude) BETWEEN 1 AND 128 AND regiao_saude !~ '[[:cntrl:]]'
    ),
    -- Versao do contrato de projecao que produziu a linha (reprodutibilidade).
    versao_projecao text NOT NULL CHECK (versao_projecao ~ '^[a-z0-9./-]{1,64}$'),
    registrado_em timestamptz NOT NULL DEFAULT clock_timestamp(),
    CONSTRAINT prestador_status_ledger_evento_unico UNIQUE (tenant, chave_evento)
)
"""


def upgrade() -> None:
    op.execute(_CREATE)
    # Eixo de leitura do consumidor (P6): historico de UM prestador, em ordem de registro.
    op.execute(
        "CREATE INDEX prestador_status_ledger_prestador_idx "
        "ON prestador_status_ledger (tenant, prestador_id, dimensao, id)"
    )
    op.execute("""
        CREATE FUNCTION prestador_status_ledger_imutavel() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'prestador_status_ledger e append-only';
        END $$
    """)
    op.execute(
        "CREATE TRIGGER prestador_status_ledger_imutavel BEFORE UPDATE OR DELETE ON "
        "prestador_status_ledger FOR EACH ROW EXECUTE FUNCTION prestador_status_ledger_imutavel()"
    )
    op.execute(
        "CREATE TRIGGER prestador_status_ledger_sem_truncate BEFORE TRUNCATE ON "
        "prestador_status_ledger FOR EACH STATEMENT EXECUTE FUNCTION "
        "prestador_status_ledger_imutavel()"
    )
    op.execute(
        "COMMENT ON TABLE prestador_status_ledger IS 'Ledger append-only do status do prestador "
        "(MPTT P3): baseline AMH atestada + deltas dos terminais de SP-OP-CRED-001/SP-OP-FRAUDE-001. "
        "Idempotente por (tenant, chave_evento). Sem dado de beneficiario. Escuro atras de "
        "MAEZO_PRESTADOR_STATUS_LEDGER'"
    )
    op.execute("REVOKE ALL ON TABLE prestador_status_ledger FROM PUBLIC")
    op.execute("REVOKE ALL ON FUNCTION prestador_status_ledger_imutavel() FROM PUBLIC")


def downgrade() -> None:
    # Descartavel enquanto escura (nenhum escritor roda com a bandeira desligada). Downgrade e'
    # ato explicito de operador, nunca expurgo de runtime.
    op.execute("DROP TABLE prestador_status_ledger")
    op.execute("DROP FUNCTION prestador_status_ledger_imutavel()")
