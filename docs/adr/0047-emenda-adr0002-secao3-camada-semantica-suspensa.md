# ADR-0042: Emenda a ADR-0002 §3 — a camada semantica fica SUSPENSA ate existir consumidor (GAP DU-01-b)

**Status:** **Proposto — DRAFT/verify. NADA AQUI ESTA RATIFICADO.** Redigido por AGENTE
(`A1-PGVECTOR`, R1) sob a decisao do dono R-005/R-006; um agente nao ratifica ADR. Enquanto este
documento carregar `DRAFT/verify`, ADR-0002 §3 continua sendo o texto vigente. Assinatura humana
pendente — `/docs/adr/` e dono-gated (`.github/CODEOWNERS`). · **Data:** 2026-09-04 · **Area:**
Dados / Memoria de agente

> **Emenda DRAFT 2026-09-04 (pendente de ratificação pelo dono — CODEOWNERS docs/adr/)**
>
> **Enquadramento.** Este ADR **nao edita** `docs/adr/0002-agent-state-three-layers.md`. Ele e' a
> correcao de registro, na forma que este repositorio ja litigou: `docs/adr/README.md:8` ("ADR
> aceito so muda por novo ADR com `Supersedes`"), o precedente ADR-0027 (emenda ADR-0007 sem tocar
> no arquivo dela) e a `## Convencao seguida` de ADR-0032 (`0032:119-128`), reafirmada por ADR-0041
> depois que blocos `## Emenda` in-loco em sete ADRs `Accepted` foram classificados como desvio de
> governanca MAJOR e revertidos. O arquivo de ADR-0002 permanece byte-identico.
>
> **O que esta emenda faz:** SUSPENDE §3 ate existir consumidor. **Nao** o nega, **nao** o
> supersede, e **nao** rejeita o desenho.

## Contexto

ADR-0002 §3 (`docs/adr/0002-agent-state-three-layers.md:12`) decidiu, e ratificou em 2026-07-05,
uma terceira camada de estado de agente: *"anotacoes derivadas em texto estruturado + embeddings
pgvector. Conteudo canonico NUNCA mora aqui — sempre referencia a recurso FHIR. Embeddings sao
descartaveis/re-indexaveis."*

A camada foi **construida no schema** e **nunca foi consumida**. Os fatos, todos re-derivaveis da
arvore:

| Fato | Como verificar |
|---|---|
| A extensao e a coluna existiam | `0001_schema_agents.py` — `CREATE EXTENSION ... vector` e `embedding vector(1536)` em `agent_memory` |
| Nenhum indice vetorial jamais existiu | `grep -rn 'ivfflat\|hnsw' src/maezo` -> 0 hits |
| A coluna nunca teve writer | nenhum `INSERT`/`UPDATE` de `agent_memory` em `src/` |
| O unico modulo que conhece a camada RECUSA | `mcp_memory/server.py` levanta `SemanticMemoryUnavailableError` com `reason=semantic_search_not_wired` — nao ha provedor de embedding nem query vetorial |
| O custo era real | extensao no banco, `,pgvector` em `shared_preload_libraries` do Aurora (onde era ate incorreto — GAP DU-03) e a imagem `pgvector/pgvector:pg16` no compose |

O relatorio de dominio D8 chamou o estado intermediario de **"o pior dos dois mundos"**: paga-se
infraestrutura por uma capacidade que ninguem pede e, no dia em que fosse ligada, abre-se uma
superficie NOVA de PHI (embeddings de memoria de agente) com custo de inferencia recorrente e sem
pedinte.

## Decisao

**Decisao do dono (OWNER-DECISIONS-REGISTER R-005, 2026-09-04, opcao B), citada literalmente:**

> "B — remover via migration `0009` (coluna `embedding`, extensão, parameter group e imagem de
> compose) e levar no mesmo PR o rascunho de emenda de ADR-0002 §3."

**Decisao do dono sobre a FORMA (R-006), citada literalmente:**

> "SIM — o PR carrega o rascunho de emenda marcado DRAFT, e a ratificação continua sendo ato
> exclusivo do dono."

Em consequencia, e **condicionado a ratificacao humana**:

1. **ADR-0002 §3 fica SUSPENSO** — sem consumidor, a camada semantica nao e' materializada no
   schema. `0009_drop_pgvector` remove `agent_memory.embedding` e a extensao `vector`; o parameter
   group do Aurora perde o token `pgvector`; o compose passa a `postgres:16`.
2. **§3 nao e' negado nem rejeitado.** O desenho continua sendo o desenho: se um consumidor
   aparecer — um caminho de recall semantico com provedor de embedding real, indice vetorial e
   decisao de PHI tomada —, §3 volta a valer e a materializacao volta com ele, por uma migration
   nova (`downgrade()` da 0009 e' o inverso honesto e recria coluna e extensao).
3. **§1 (working) e §2 (episodica) ficam intocados.** A tabela `agent_memory` continua existindo e
   continua enumerada no plano de eliminacao LGPD (`PERSISTENCE_LAYERS`, ordem 7) com uma sonda
   `SELECT count(*)` honesta. O que saiu foi UMA COLUNA, nao a memoria de agente.
4. **A negativa aceita em `docs/adr/0002-agent-state-three-layers.md:18` permanece valida como
   registro**: *"pgvector tem teto de escala — revisitar acima de ~5M embeddings/instancia"*. Ela
   nao e' apagada nem contradita; ela fica **sem sujeito** enquanto §3 estiver suspenso, e volta a
   ser vinculante junto com §3. O runbook `docs/runbooks/devops-stack.md` §"pgvector scale" foi
   marcado `**REMOVED**` e preserva o procedimento de escala exatamente para esse dia.

## Consequencias

**Positivas**
- O repositorio deixa de pagar extensao, parametro de cluster e imagem de compose por uma camada
  sem consumidor.
- Uma superficie de PHI *futura* (embeddings de memoria de agente) deixa de existir por acidente:
  se voltar, volta por decisao explicita, com a analise de PHI feita na hora certa.
- A recusa de `mcp_memory.recall_semantic` deixa de ser "ainda nao ligado" e passa a ser um fato
  com registro: nao ha coluna, e des-suspender §3 e' ato do dono.
- `deploy/aws-ecs/.../task-bootstrap-db.tf` para de criar uma extensao que a migration seguinte
  removeria.

**Negativas (aceitas)**
- Um consumidor futuro paga a migration de volta (a `downgrade()` da 0009 e' exatamente esse
  caminho, e ela EXIGE um servidor com pgvector — em `postgres:16` ela falha alto, de proposito).
- `docs/adr/0002-agent-state-three-layers.md` lido **isoladamente** ainda descreve a camada como
  vigente. Mitigado pela linha de ADR-0042 no indice `docs/adr/README.md` (a convencao deste repo
  marca a ADR EMENDANTE, nao a emendada) e pelas citacoes a este arquivo em
  `mcp_memory/server.py`, `platform/erasure.py`, `docs/architecture/overview.md`,
  `spec/policies/retention/erasure-plan.template.yaml` e no runbook.

**Divida registrada, nao fechada aqui**
- `0001_schema_agents.py` foi tornada TOLERANTE a ausencia da extensao (guarda
  `pg_available_extensions`) porque, sem isso, o primeiro passo de `alembic upgrade head` num
  servidor sem pgvector morre DENTRO da 0001, onde migration nenhuma alcanca. Essa e' a segunda
  excecao ao forward-only do ADR-0011 neste repositorio (a primeira esta em DL-0017) e esta
  disclosed no proprio arquivo. Um squash de baseline das migrations tornaria a guarda
  desnecessaria e continua sendo uma decisao em aberto, do dono/DBA.
- O `search_path` `"{tenant}", public` das migrations e dos pools asyncpg foi mantido inalterado,
  embora seu motivo original (o tipo `vector` em `public`) tenha caducado. Estreita-lo e' decisao
  separada, com prova viva — nao um efeito colateral desta remocao.

## Supersedes

**Amends** ADR-0002 (§3 apenas, e apenas quanto a MATERIALIZACAO da camada), **sem editar o
arquivo de ADR-0002** e **condicionado a ratificacao humana**. **Nao supersede** ADR-0002: §1, §2 e
§4 continuam integralmente vigentes, a negativa de `0002:18` continua registrada, e o desenho de §3
nao e' rejeitado — apenas suspenso ate existir consumidor. Nenhuma outra ADR e' afetada.
