# ADR-0050: Um dominio de idempotencia por guia em SP-OP-AUTH-001

**Status:** Proposed — registra a decisao do dono #16 (TOMADA em 2026-09-12); a POSTURA-ALVO de
dedup (`EXCLUSIVE` hoje vs `PERMANENT`) permanece **a ratificar** por medico-auditor /
ANS-regulatorio
**Data:** 2026-09-12
**Area:** Orquestracao

## Contexto

`SP-OP-AUTH-001` tinha **duas** business keys para a **mesma** guia TISS:

| canal | chave | origem |
|---|---|---|
| agente (`POST /v1/autorizacoes`, grafo do rafael, delegacao A2A) | `AUTH-{tenant_id}-{numero_guia_tiss}` | contrato `docs/processes/contracts/SP-OP-AUTH-001.md` e cabecalho do BPMN |
| portal (despachante de intake nativo) | `AUTHI-{guide_identity_ref}` | `guide_identity_ref`, uma referencia OPACA do portal |

Nao era um erro de digitacao, e e' por isso que sobreviveu: a chave `AUTHI-` e' bem-formada e o
engine a aceita, entao ela abre uma instancia **perfeitamente funcional** — num **segundo
dominio de idempotencia**, invisivel ao outro canal.

Consequencias medidas na arvore, antes desta decisao:

1. `find_active_instance` de um canal nunca encontra a instancia do outro ⇒ a mesma guia pode
   ter **duas instancias vivas**, cada uma com sua `UT_AnaliseMedicoAuditor`. Duas analises
   medicas concorrentes da mesma autorizacao podem divergir.
2. `correlate_message` por business key (`tools/mcp_cibseven/transport.py`) e' inutilizavel para
   instancias abertas pelo portal.
3. Todo consumidor que correlaciona pelo `_business_key` publicado nos fatos de dominio
   (`tools/workers/events.py`) ve **duas formas** para o mesmo tipo de caso.
4. A guarda `absent_at_cutover` do plugin Java recusa uma guia ja' existente, mas o canal de
   agente nao tem a checagem reversa — os dois canais podiam negar-se mutuamente.

Havia ainda um defeito **latente** no proprio canal de agente: a chave era montada por f-string
em dois lugares independentes (`agents/rafael/graph.py`, `runtime/agent_runtime/ingress.py`),
ambos com `state.get(..., "")`. Com tenant e guia ausentes a chave **colapsava** para `AUTH--`
— uma unica chave compartilhada por toda solicitacao malformada.

A pergunta que bloqueava a correcao — *pode uma guia TISS reenviada/reaberta reutilizar seu
numero?* — e' semantica MEDICA/ANS e estava explicitamente marcada "Human call" no mapa
`_START_DEDUP_POLICY`. O dono respondeu em 2026-09-12 (decisao #16) a parte que era dele:
**opcao (a) — um dominio de idempotencia por guia, na forma contratual.**

## Decisao

1. **Uma unica business key para `SP-OP-AUTH-001`:** `AUTH-{tenant_id}-{numero_guia_tiss}`, nos
   dois canais. A forma nao muda um byte (o contrato proibe renomear chaves implantadas
   enquanto o ADR-0038 estiver `Proposed`).
2. **Um unico compositor:** `src/maezo/tools/process_business_keys.py::auth_business_key`. Todo
   caminho que inicia ou correlaciona AUTH passa por ele; nenhum modulo de `src/` pode montar a
   chave por conta propria (cercado por teste que le a AST de `src/maezo/**`).
3. **Componente ausente/em branco e' RECUSADO na montagem**, nunca concatenado. `AUTH--` deixa
   de ser produzivel.
4. **A chave legada `AUTHI-` nao e' convertida — e' RECUSADA por nome**
   (`LegacyAuthIntakeBusinessKeyError`). Nao existe funcao total de `guide_identity_ref`
   (opaco) para `numero_guia_tiss`; "migrar" seria fabricar identidade clinica. Nenhuma
   instancia de producao carrega a chave legada: o caminho que a montava nunca teve chamador em
   `src/` (`dispatch_prepared_start`, 0 call sites), entao a recusa nao quebra nada existente.
5. **`SP-OP-AUTH-001` passa de `NON_STRICT` para `EXCLUSIVE`** em `_START_DEDUP_POLICY`.
6. **O canal do portal fica INERTE POR RECUSA TIPADA** enquanto a fonte `guide` publicada nao
   trouxer `numero_guia_tiss` (`AuthIntakeGuideNumberUnavailableError`), em vez de voltar a
   abrir um segundo dominio.

### Por que `EXCLUSIVE` e nao `PERMANENT`

Duas razoes independentes, qualquer uma suficiente:

1. **A pergunta de dominio continua aberta.** O dono decidiu o DOMINIO (uma instancia por
   guia), nao o reuso CROSS-TIME. `PERMANENT` sobre uma chave cujo segundo caso e' legitimo
   recusaria esse caso com `ALREADY_COMPLETED`, **sem humano no circuito** — um desfecho
   adverso contra um beneficiario fabricado por um portao de idempotencia, exatamente o que a
   classificacao de `SP-OP-CANCEL-001` ja recusa.
2. **`PERMANENT` nao se sustentaria.** `start_process_idempotent` desvia uma proveniencia
   `HumanIntakeProvenance` para `start_human` **antes** de ler a postura: o canal do portal nao
   escreve reivindicacao duravel nem consulta nenhuma. `EXCLUSIVE` sobrevive a essa assimetria
   — a chave compartilhada colapsa os dois canais sobre UM `ACT_HI_PROCINST.BUSINESS_KEY_` e
   uma geracao ENCERRADA e' reiniciavel dos dois lados, entao as duas portas concordam sobre o
   que garantem. `PERMANENT` nao: seria um portao permanente por uma porta e ausente pela
   outra. Uma postura que um dos canais ignora em silencio e' pior que nenhuma, porque se le
   como garantia.

> **postura-alvo a ratificar.** Se medico-auditor / ANS-regulatorio decidirem que o numero de
> guia TISS e' one-shot, a postura-alvo e' `PERMANENT`. A promocao exige, no MESMO pacote:
> (i) rotear o canal do portal pelo portao (razao 2); (ii) excluir as reivindicacoes AUTH da
> varredura por idade de `audit_emit_dedup` (co-requisito 1 do mapa, que vale para toda chave
> `PERMANENT`); (iii) revisao humana da recusa de segundo caso.

## Consequencias

**Positivas:**
- Uma guia, uma instancia, um dominio — independente do canal por onde o pedido entra.
- `correlate_message` por business key volta a valer para instancias do portal.
- Starts concorrentes da mesma guia deixam de depender da janela TOCTOU de
  `find_active_instance` e passam a ser mutuamente excluidos pela reivindicacao duravel.
- A chave colapsada `AUTH--` deixa de existir — e sob `EXCLUSIVE` ela teria deixado de ser
  latente: a segunda solicitacao malformada receberia a instancia (o caso clinico) da primeira.
- Um compositor unico, cercado: a duplicacao nao pode renascer em silencio.

**Negativas (aceitas):**
- **Guia travada em vez de segundo start.** Sob `EXCLUSIVE`, uma falha do engine depois da
  reivindicacao duravel e antes de a instancia existir trava aquela guia ALTO
  (`StartClaimWithoutInstanceError`) ate um operador resolver. E' a mesma troca ja aceita para
  `SP-OP-CANCEL-001`, e a direcao de falha e' ruidosa, nunca silenciosa.
- **Um turno de agente sem `numero_guia_tiss` passa a falhar alto** em vez de seguir com uma
  chave colapsada. No ingresso HTTP isso vira `422` (era um start com chave invalida).
- **O canal do portal permanece inerte**, agora com a razao dita em voz alta. Habilita-lo exige
  a fonte publicar o numero da guia — um campo novo num DTO FECHADO, com dono de fonte e
  analise de minimizacao (o numero liga ao beneficiario) — e o daemon de WP-J1-01.
- **Uma familia GATED passa a exigir autorizacao de `READ_HISTORY` no preflight D7-A.**
  `gateway/engine_start.py::authorize_start` autoriza `READ_ACTIVE` e, **so' quando
  `is_strict_start_dedup(process_key)`**, tambem `READ_HISTORY` — o portao precisa poder
  perguntar ao engine se a geracao reivindicada terminou. Promover AUTH portanto acrescenta esse
  requisito ao start de AUTH pelo caminho com preflight.
  **O que o repositorio prova:** `platform/engine_bootstrap/secured_plan.py::_registered`
  REGISTRA o companion `READ_HISTORY` para TODO schema de START, incondicionalmente, e
  `gateway/engine_schemas.py::registered_schema` o aceita. **O que o repositorio NAO prova:**
  que o plano EFETIVAMENTE INSTALADO por um dono num ambiente concreto contenha o binding
  correspondente — registro de schema nao e' binding instalado. Antes de ativar AUTH em um
  ambiente com preflight D7-A, confirmar o binding `READ_HISTORY` de `SP-OP-AUTH-001` no plano
  instalado; sem ele o start e' NEGADO (`engine_operation_denied`) — fail-closed, ruidoso,
  nunca um start silencioso.
- A perna (c) de DL-0043 (ID cru de fonte em business key, proibicao 5 do ADR-0037) **nao e'
  fechada aqui** e nao e' atenuada: esta ADR implanta a forma que o contrato ja exige, e o
  proprio contrato mantem a chave como interna ("nunca exibi-la em URL/erro/recibo externo nem
  derivar acesso de sua posse").
- A forma `{PREFIXO}-{tenant}-{guia}` continua ambigua se um `tenant_id` contiver `-`; nenhum
  tenant implantado tem, e a desambiguacao pertence a ratificacao do ADR-0038.

## Supersedes

—. Registra a decisao do dono #16 e **emenda** a classificacao de `SP-OP-AUTH-001` no mapa
`_START_DEDUP_POLICY` introduzido por B-3/GAP-D3-02; nao edita nenhuma ADR aceita. Relacionada
a ADR-0037 (proibicoes 5 e 6 sobre forma/conteudo de chaves), ADR-0038 (`Proposed`, forma das
chaves) e ADR-0049 (portal humano, D4 — o browser nunca fornece business key).
