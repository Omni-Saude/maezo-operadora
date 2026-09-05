"""Remove a camada semantica pgvector — `agent_memory.embedding` + extensao `vector` (DU-01-b).

DECISAO DO DONO (OWNER-DECISIONS-REGISTER R-005, 2026-09-04, opcao B):
"remover via migration `0009` (coluna `embedding`, extensao, parameter group e imagem de compose)
e levar no mesmo PR o rascunho de emenda de ADR-0002 §3."

--------------------------------------------------------------------------------------------
O que se remove, e por que remover e' mais honesto do que manter
--------------------------------------------------------------------------------------------
`0001_schema_agents.py` criou a extensao `vector` e a coluna `agent_memory.embedding
vector(1536)`. Nos dois casos, ZERO consumidores: `grep -rn 'ivfflat|hnsw' src/maezo` nao retorna
indice vetorial nenhum, nenhum writer jamais populou a coluna, e o unico modulo que sabe da
camada semantica — `src/maezo/tools/mcp_memory/server.py` — RECUSA fail-closed exatamente porque
nao ha provedor de embedding nem query pgvector (`REASON_SEMANTIC_SEARCH_NOT_WIRED`). O relatorio
de dominio D8 chamou o estado intermediario de "o pior dos dois mundos": paga-se extensao,
parametro de cluster e imagem de compose por uma capacidade que ninguem pede e, pior, por uma
superficie que passaria a tocar PHI (embeddings de memoria de agente) no dia em que fosse ligada.

O que NAO se remove: a tabela `agent_memory` inteira. A camada EPISODICA continua declarada por
ADR-0002 §2 e continua enumerada em `PERSISTENCE_LAYERS` (ordem 7) com uma `count_statement`
honesta. Esta migration remove UMA COLUNA, nao a memoria de agente.

ADR-0002 §3 (`docs/adr/0002-agent-state-three-layers.md:12`) ratifica a camada semantica com
embeddings pgvector, e `docs/adr/` e CODEOWNED. Este PR carrega a emenda como RASCUNHO em
`docs/adr/0047-emenda-adr0002-secao3-camada-semantica-suspensa.md` (Proposto — DRAFT/verify): §3
fica SUSPENSO ate existir consumidor, nao negado. Nenhum agente ratifica ADR — a ratificacao e ato
exclusivo do dono (R-006).

--------------------------------------------------------------------------------------------
A extensao e DB-GLOBAL e esta migration roda POR TENANT — a razao da guarda
--------------------------------------------------------------------------------------------
Esta e a assimetria que uma leitura rapida erra. `env.py` roda a cadeia uma vez por SCHEMA de
tenant (`alembic -x tenant=<id> upgrade head`, search_path `"<tenant>", public`), mas DL-0017
colocou a extensao em `public` DE PROPOSITO: tipo e operador `vector` sao objetos do BANCO, nao do
schema. Logo o DROP da coluna e por-tenant e o DROP da extensao e uma vez so, para o banco inteiro.

As duas formas ingenuas estao ambas erradas, e por motivos opostos:

  * `DROP EXTENSION IF EXISTS vector` puro, aplicado no PRIMEIRO tenant: falha com
    `dependent objects still exist` enquanto QUALQUER outro tenant ainda tiver a coluna — isto e,
    a migration do tenant A quebra por causa do schema do tenant B, que ainda nao rodou a sua.
  * `DROP EXTENSION ... CASCADE`: nao falha — e' pior. Ele DERRUBA silenciosamente a coluna
    `embedding` de todos os outros tenants antes de a migration DELES rodar, marcando o schema
    deles como migrado ate 0008 quando ja esta em 0009. Uma migration que altera o schema de um
    tenant que nao a executou nao e' uma migration; e' um efeito colateral cross-tenant.

Por isso o DROP da extensao e' condicionado a NAO RESTAR NENHUMA coluna de tipo `vector` no banco.
O ultimo tenant a migrar e' quem de fato remove a extensao; os anteriores removem so a sua coluna
e deixam a extensao de pe. A ordem de execucao entre tenants deixa de importar, que e' a unica
propriedade que torna esta migration segura num deployment multi-tenant.

--------------------------------------------------------------------------------------------
`downgrade()` e' CONVERGENTE com a guarda da 0001 — a mesma pergunta, a mesma resposta
--------------------------------------------------------------------------------------------
O inverso real de `upgrade()` e' recriar a extensao e a coluna, e `downgrade()` faz exatamente
isso — sob a MESMA guarda `pg_available_extensions` que a 0001 passou a usar.

Uma versao anterior desta migration deixou o `downgrade()` deliberadamente sem guarda, alegando
que assim ele "falharia alto em vez de devolver um 0008 sem a coluna que a 0008 tem". Essa premissa
e' FALSA neste repositorio, e falsa por causa da guarda que este mesmo PR poe na 0001: num servidor
sem pgvector a 0001 nao cria extensao nem coluna, logo a revisao 0008 daquele servidor NAO TEM
`agent_memory.embedding` (medido: `alembic -x tenant=d1 upgrade 0008` em `postgres:16` produz
`agent_memory` com 8 colunas, nenhuma delas `embedding`). Um `downgrade()` sem guarda, ali, nao
restaura "a coluna que a 0008 tem" — ele tenta construir algo que a 0008 nunca teve naquele
servidor e morre com `extension "vector" is not available`, quebrando a unica prova de rollback
real do repositorio (`tests/unit/platform/integrations/test_amh_inbox_live_pg.py::
test_upgrade_downgrade_upgrade_roundtrip`, que faz `upgrade head -> downgrade 0006 -> upgrade head`
e passa por 0009 -> 0008 no caminho) contra a imagem `postgres:16` que este mesmo PR poe no
compose.

Com a guarda, a propriedade que a 0001 declara para o `upgrade` (os dois caminhos chegam ao MESMO
head) vale tambem para o `downgrade`: onde a 0008 teve extensao e coluna, as duas voltam; onde a
0008 nunca as teve, nada e' inventado e o schema resultante e' exatamente a 0008 daquele servidor.
Nao ha caminho em que a guarda esconda uma reversibilidade nao entregue — para esconder algo seria
preciso um servidor onde a 0008 tem a coluna E a extensao nao esta disponivel, e esse servidor nao
existe: a coluna so pode ter sido criada por uma extensao que estava disponivel.

O que `downgrade()` NAO restaura, e nao tem como restaurar: os VALORES da coluna. Eram todos NULL
— a coluna nunca teve writer — entao a perda e nominal, mas a afirmacao correta continua sendo
"a coluna volta vazia", nao "o downgrade e' sem perdas".

Revision ID: 0009
Revises: 0008
Create Date: 2026-09-04
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. A coluna — por tenant. DDL nao qualificada; env.py poe o schema do tenant primeiro no
    #    search_path (DL-0017), mesma convencao de 0001..0008.
    # ------------------------------------------------------------------
    op.execute("ALTER TABLE IF EXISTS agent_memory DROP COLUMN IF EXISTS embedding")

    # ------------------------------------------------------------------
    # 2. A extensao — uma vez para o banco, e SO quando ninguem mais depende dela.
    #    Ver o docstring do modulo: nem o DROP puro nem o CASCADE servem aqui.
    #    O predicado varre pg_attribute inteiro (todos os schemas), nao so o do tenant corrente,
    #    porque a pergunta que ele responde tambem e' do banco inteiro.
    # ------------------------------------------------------------------
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_extension WHERE extname = 'vector')
               AND NOT EXISTS (
                   SELECT 1
                   FROM pg_attribute a
                   JOIN pg_class c ON c.oid = a.attrelid
                   JOIN pg_type t ON t.oid = a.atttypid
                   WHERE t.typname = 'vector'
                     AND a.attnum > 0
                     AND NOT a.attisdropped
                     AND c.relkind IN ('r', 'p', 'm', 'f')
               )
            THEN
                DROP EXTENSION vector;
            END IF;
        END
        $$
    """)


def downgrade() -> None:
    # Inverso CONVERGENTE, sob a MESMA guarda `pg_available_extensions` da 0001 — ver o docstring
    # do modulo. A extensao volta para 'public' (DB-global, DL-0017); a coluna volta por tenant,
    # VAZIA. Onde a extensao nao esta disponivel, a 0001 nunca criou nem uma nem outra: nao ha o
    # que restaurar, e o schema resultante e' a 0008 honesta daquele servidor.
    #
    # O `ALTER TABLE` vai dentro de `EXECUTE` pelo mesmo motivo que na 0001: o corpo de um bloco
    # `DO` e' parseado inteiro antes de rodar, e `public.vector(1536)` nao resolve num servidor
    # onde a extensao nao existe — a guarda seria decorativa se o tipo fosse parseado de qualquer
    # jeito.
    op.execute("""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name = 'vector') THEN
                CREATE EXTENSION IF NOT EXISTS vector WITH SCHEMA public;
                EXECUTE 'ALTER TABLE IF EXISTS agent_memory
                         ADD COLUMN IF NOT EXISTS embedding public.vector(1536)';
            END IF;
        END
        $$
    """)
