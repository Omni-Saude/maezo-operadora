# ADR-0070: Convenção de boundary para códigos de guarda — do `*_NOT_HUMAN` à tarefa humana nomeada, predicado T-E e regras de allowlist

**Status:** Proposed — redigido por AGENTE por ordem do dono (2026-10-31, R-098 / WP-ADR-0030-COMPLETION); **ratificação
humana PENDENTE** (CODEOWNERS de `/docs/adr/`). Nenhum BPMN, worker ou allowlist muda por esta ADR.
**Data:** 2026-10-31
**Área:** Orquestração (runtime spine) | Auditoria (ADR-0007) | HITL (ADR-0005/0008/0018)
**Emenda (não substitui):** ADR-0030 §5 — só para boundaries de guarda **novos** (forma H abaixo). Os boundaries de guarda
já modelados sob a ADR-0030 (forma N) ficam como estão.
**Apoia-se em:** ADR-0030 §2 (gate de prova de boundary, cláusula (b)), §4 (T-E como co-requisito), §5 (guarda sem
boundary modelado = incidente) e Tier 3 do plano de migração; DL-0047 (R-097/R-098: endurecimento é decisão de programa
única, por convenção ratificada antes de qualquer correção); habilitação AUTH de 2026-08-25 (commit `b2493914f`).

## Contexto

**O que a ordem do dono pede.** Em 2026-09-04 o dono registrou (R-098, ver `docs/decisions-log.md` DL-0047) que
qualquer endurecimento do tratamento de códigos de guarda seria **uma** decisão de programa — uma ADR de convenção
ratificada antes das correções, nunca correções avulsas. Em 2026-10-31, ao responder aos issues de papel, o dono
mandou abrir essa ADR para os dois códigos de guarda que hoje não têm boundary (FRAUDE e ADEQUACAO) e para os códigos de
custódia, seguindo a decisão em fases sobre falha de custódia (registro em
`docs/prompts/fcp-protocol-2026-10-30/OWNER-DECISIONS-2026-10-31.md`; análise em
`docs/prompts/fcp-protocol-2026-10-30/analyses/FRAUDE-custody-and-inconsistency.md`, ambos locais e fora do Git).

**Como as coisas estão hoje (medido em `main` 2be4515f).**

- O harness só converte um `WorkerBpmnError(code)` num `bpmnError` real quando `code` está na allowlist de produção.
  Qualquer outro código vira incidente (`failure(retries=0)`). A allowlist é montada em
  `src/maezo/runtime/worker_runtime/service.py::PRODUCTION_BPMN_ERROR_ALLOWLIST`, a partir das constantes por família
  (`*_BPMN_ERROR_ALLOWLIST`) que o gate `scripts/ci/check_bpmn_error_allowlist.py` prova cobertas pelo BPMN (cláusula
  (b)). Depois aplica o filtro T-E.
- Há **dois predicados**, e a relação entre eles é de **contenção**, não de igualdade (lição do commit `b2493914f`):
  - `scripts/ci/check_bpmn_error_allowlist.py::is_te_gated`, com espelho em
    `src/maezo/runtime/worker_runtime/service.py::_is_te_gated`, diz quais códigos **ainda não podem** entrar na
    allowlist. São todos os `*_NOT_HUMAN` mais os de bloqueio de negativa, menos os já habilitados
    (`scripts/ci/check_bpmn_error_allowlist.py::TE_ENABLED_CODES` = `src/maezo/runtime/worker_runtime/service.py::_TE_ENABLED_CODES`).
  - `src/maezo/tools/workers/harness.py::is_guard_refusal_code` diz quais recusas **precisam** de linha de auditoria
    `decision=REFUSED` (o T-E, no ar desde 2026-07-24).
  - A invariante é {ainda gated} ⊆ {auditado}; `tests/unit/tools/workers/test_harness_audited_refusal.py::test_detector_contem_o_gate`
    a trava. Um código habilitado sai do primeiro predicado e **continua** no segundo.
- **Os boundaries de guarda modelados hoje terminam todos num fim NEUTRO, não numa tarefa humana.** Essa é a forma que a
  ADR-0030 prescreveu (§5 e Tier 3: "routed to the modeled neutral terminal"):

  | Código | Tarefa que lança | Boundary → destino | Estado |
  |---|---|---|---|
  | `ERR_AUTH_DENIAL_INCOMPLETE` | `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn::ST_EnviarNegativaFormal` | `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn::BE_NegativaIncompleta` → `spec/processes/bpmn/SP-OP-AUTH-001_Autorizacao_Previa.bpmn::End_FundamentacaoIncompletaBloqueada` | habilitado em 2026-08-25 |
  | `ERR_CANCEL_MANTER_NOT_HUMAN` | `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn::ST_ConfirmMaintainedDecision` | `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn::BE_ManterNaoConfirmado` → `spec/processes/bpmn/SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn::End_ManterNaoConfirmado` | gated em T-E |
  | `ERR_DECRED_NOT_HUMAN` | `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn::ST_RegisterDescredenciamento` | `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn::BE_DecredNaoHumano` → `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn::End_DecredBloqueadoNaoHumano` | gated em T-E |
  | `ERR_CRED_DENIAL_NOT_HUMAN` | `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn::ST_RegisterCredDenial` | `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn::BE_CredDenialNaoHumano` → `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn::End_CredGuardBloqueadoNaoHumano` | gated em T-E |
  | `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` | `spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn::ST_RegisterSuspension` | `spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn::BE_SuspensaoNaoHumano` → `spec/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn::End_SuspensaoBloqueadaNaoHumano` | gated em T-E |

- **FRAUDE e ADEQUACAO não têm boundary algum** nas tarefas de efeito adverso
  (`spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn::ST_RegisterFraudAccusation`,
  `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn::ST_SealCustodyBundle`,
  `spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn::ST_RegisterFallbackCommitment`). Pela ADR-0030 §5, um
  código de guarda sem boundary modelado é **incidente** — e é o que acontece hoje com
  `ERR_FRAUD_ACCUSATION_NOT_HUMAN`, `ERR_FALLBACK_COMMITMENT_NOT_HUMAN`, `ERR_CUSTODY_NOT_SEALED` e
  `ERR_PHI_IN_CUSTODY`. O incidente é visível e seguro, mas não tem dono, prazo nem caminho de reparo auditado: o único
  conserto disponível no cockpit é editar variável à mão, que é justamente a mudança não auditada que a custódia existe
  para impedir.
- Os dois códigos de custódia **não** terminam em `_NOT_HUMAN`, então hoje nenhum dos dois predicados os enxerga. Se um
  deles ganhasse boundary sem ajuste do predicado, a recusa sairia da allowlist sem linha `REFUSED`.

## Decisao

### 1. Duas formas de boundary de guarda, e só duas

- **Forma N — fim neutro (a da ADR-0030, mantida).** O boundary preso à tarefa que lança leva a um fim neutro que
  documenta que **nada** foi feito. Serve quando o caso não pode ser refeito dentro da mesma instância sem refazer a
  decisão humana inteira. Os cinco boundaries da tabela acima são forma N e **continuam válidos por esta ADR**; ela não
  manda migrá-los.
- **Forma H — tarefa humana nomeada (nova, para os boundaries novos).** O boundary preso à tarefa que lança leva a uma
  `userTask` **nomeada**, com `camunda:candidateGroups` da taxonomia canônica (DEC-21), que é o único papel capaz de
  reparar a causa. Regras da forma H:
  1. **Formulário sem dado:** a tarefa mostra só o código e um token de motivo limitado (ex.: `bundle_vazio`,
     `ref_index` + `marcador`). Nunca o valor que causou a recusa, nunca PHI.
  2. **Saídas fechadas:** (a) *reparar* — volta a um ponto **anterior** à guarda, que roda de novo (nunca pula a guarda,
     nunca escreve direto a variável guardada); (b) *encerrar* — fim neutro com desfecho próprio. Encerrar **nunca**
     publica um desfecho de negócio (ex.: `arquivado_sem_indicio`, `vinculo_mantido`). Defeito técnico não vira achado.
  3. **Prazo e limite fora do Python:** o SLA da tarefa e o número máximo de tentativas saem de saída de DMN cujo valor é
     fixado pelo papel dono (juridico/compliance, DPO), nunca de constante no código (AGENTS.md regra 5). Ao atingir o
     limite, o caminho vai a um fim neutro (forma N), nunca a um loop.
  4. **Reparo só por canal auditado:** a correção entra por mensagem ou formulário do processo, nunca por edição de
     variável no cockpit.

### 2. Quando o código continua incidente (ADR-0030 §5, reafirmado)

- **Sem boundary modelado, é incidente.** Habilitar um código na allowlist sem o boundary correspondente é proibido; o
  gate (cláusula (b)) já recusa.
- **Classe adulteração: incidente permanente.** Um código lançado numa **verificação depois da decisão humana**, em que
  rotear (mesmo para re-selar) encobriria uma possível adulteração e anularia uma decisão que o engine não pode refazer,
  fica incidente para sempre, com runbook (investigação de segurança/jurídico e nova decisão humana sobre corpus selado
  de novo). Primeiro caso nomeado: `ERR_CUSTODY_NOT_SEALED` lançado em
  `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn::ST_RegisterFraudAccusation`.
- **Precedência na mesma tarefa:** quando uma tarefa pode lançar um código roteável e um código da classe adulteração, a
  falha de adulteração vence. O worker não pode esconder uma falha de custódia sob o código roteável (hoje
  `register_fraud_accusation` junta as duas em `ERR_FRAUD_ACCUSATION_NOT_HUMAN` quando há erro misto — a onda de BPMN da
  FRAUDE precisa separar).
- As declarações-raiz sem boundary (ex.: `ERR_CONTAS_GLOSA_NOT_HUMAN`, DL-0047) **não mudam**. Esta ADR não endurece a
  cláusula (c) do gate para declarações-raiz.

### 3. Predicado T-E (recusa auditada)

Um código é **co-requisito de T-E** — precisa de linha `decision=REFUSED` antes de entrar na allowlist e continua auditado
depois — quando cumpre **qualquer** destas:

- (a) termina em `_NOT_HUMAN`;
- (b) está no conjunto fechado de bloqueio de negativa (hoje só `ERR_AUTH_DENIAL_INCOMPLETE`);
- (c) **(novo)** é código de recusa de custódia com boundary modelado — entram, na fase 1, `ERR_CUSTODY_NOT_SEALED` e
  `ERR_PHI_IN_CUSTODY`. A forma H termina em fim neutro no limite, e esse fim tem o mesmo problema de visibilidade que a
  ADR-0030 §4 descreve para os guardas.

O item (c) entra como **conjunto nomeado e fechado**, declarado nos dois lugares que já carregam o (b) — o detector do
harness e o gate — e preso por teste de igualdade entre as duas listas. Nunca por padrão de nome amplo. A relação entre
"gated" e "auditado" continua sendo contenção (`test_detector_contem_o_gate`).

### 4. Regras de allowlist

1. **Origem única.** Um código entra em `PRODUCTION_BPMN_ERROR_ALLOWLIST` só pela constante `*_BPMN_ERROR_ALLOWLIST` da
   família do worker, unida em `_GATE_PROVEN_BPMN_ERROR_CODES` e provada pela cláusula (b). Nunca por lista à mão em
   `service.py`.
2. **Habilitação T-E explícita.** Um código co-requisito de T-E só sai do filtro quando está em `TE_ENABLED_CODES` **e**
   em `_TE_ENABLED_CODES`, com os dois conjuntos idênticos. O arquivo do gate é CODEOWNERS (`/scripts/ci/`), então toda
   habilitação passa por revisão do dono e do security-team.
3. **Prova antes de habilitar.** Cada código habilitado tem teste de integração no engine local (CIB Seven), rodado duas
   vezes no mesmo engine, que prova: o boundary dispara; o token chega ao destino (fim neutro na forma N; tarefa humana
   nomeada com o grupo certo na forma H); existe linha de auditoria `REFUSED` ligada na cadeia; e a varredura de PHI da
   linha dá zero. Os testes negativos da guarda (sem decisão humana, campo faltando) vêm no mesmo pacote.
4. **Habilitar é reversível.** Tirar o código das duas listas devolve o caminho a incidente.
5. **Lote por decisão, não por código.** Habilitações saem em lote atado a uma decisão registrada do dono (ex.: R-075,
   lote único CANCEL + CRED + INADIMPLENCIA). Nunca código avulso sem linha nesta ADR ou na decisão.

### 5. Códigos cobertos por esta convenção

| Código | Tarefa | Forma | Destino | Quando |
|---|---|---|---|---|
| `ERR_FRAUD_ACCUSATION_NOT_HUMAN` | `ST_RegisterFraudAccusation` (FRAUDE) | H | volta a `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn::UT_DecisaoInvestigador` (`investigacao-fraude`), que é a única tarefa que decide a acusação, para recompor decisão e fundamentação; fim neutro no limite | onda de BPMN posterior |
| `ERR_FALLBACK_COMMITMENT_NOT_HUMAN` | `ST_RegisterFallbackCommitment` (ADEQUACAO) | H | volta a `spec/processes/bpmn/SP-OP-ADEQUACAO-001_Adequacao_Rede.bpmn::UT_DecisaoFallback` (grupos conforme DEC-21) para recompor o compromisso; fim neutro no limite | onda de BPMN posterior |
| `ERR_CUSTODY_NOT_SEALED` na selagem | `ST_SealCustodyBundle` (FRAUDE) | H | nova tarefa "Reconstruir e re-selar a custódia" (grupo a confirmar em DEC-21); reparo volta a `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn::ST_AssembleDossier` e re-sela; no limite, novo fim neutro "custódia não constituída" — nunca coordenação, nunca `ARQUIVAR` | fase 1 da decisão de custódia |
| `ERR_PHI_IN_CUSTODY` | `ST_SealCustodyBundle` (FRAUDE) | H | nova tarefa de triagem do DPO (grupo de privacidade a criar em DEC-21); saídas: falso positivo com exceção auditada por ref, troca por ponteiro pseudonimizado e re-selagem, ou incidente de privacidade; fim neutro no limite | fase 1 da decisão de custódia |
| `ERR_CUSTODY_NOT_SEALED` após a decisão | `ST_RegisterFraudAccusation` (FRAUDE) | — | **incidente permanente** + runbook (§2) | já é assim; fica |
| `ERR_CANCEL_MANTER_NOT_HUMAN`, `ERR_DECRED_NOT_HUMAN`, `ERR_CRED_DENIAL_NOT_HUMAN`, `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` | ver tabela do Contexto | N | o fim neutro já modelado (ADR-0030 Tier 3) | lote T-E R-075 |

A fase 0 da decisão de custódia (recusar bundle vazio, selar refs + indicadores, raiz na cadeia de auditoria, re-selar no
retorno da diligência, corrigir o fim silencioso de `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn::GW_CustodiaSelada`)
não depende desta ADR e não é decidida aqui. Ela só não pode regredir a visibilidade enquanto a fase 1 não chega.

### 6. Questões que a ratificação precisa responder

- **Q1 — forma N basta para o lote R-075?** Esta ADR mantém a forma N para CANCEL/CRED/INADIMPLENCIA, como a ADR-0030
  Tier 3 prescreve. Se o dono quiser que esses quatro também caiam numa tarefa humana (forma H), é mudança de BPMN em
  quatro processos e emenda explícita à ADR-0030 Tier 3, fora desta ADR.
- **Q2 — grupos.** Os grupos da tarefa de custódia e da triagem do DPO ainda não existem na taxonomia; entram pela
  análise de DEC-21.
- **Q3 — valores.** O SLA e o limite de tentativas das tarefas da forma H são valores do papel dono (juridico/compliance
  para custódia e fraude; DPO para PHI; gestão de rede para ADEQUACAO). Até lá, as DMNs nascem com a marca DRAFT/verify.

## Consequencias

**Positivas:**
- Uma regra única para todo código de guarda. Ou ele tem boundary modelado numa das duas formas e é auditado como recusa,
  ou é incidente. Fecha o R-098 como decisão de programa, como o DL-0047 exigiu.
- Os códigos de FRAUDE e ADEQUACAO ganham destino e dono definidos antes de qualquer BPMN, sem correções avulsas.
- Os códigos de custódia passam a contar para o T-E antes de ganharem boundary. Fecha o buraco "fora dos dois
  predicados".
- A classe adulteração fica nomeada: ninguém "conserta" um incidente de custódia pós-decisão roteando-o.

**Negativas (aceitas):**
- Duas formas em vez de uma. A forma N fica para os boundaries que já existem; uniformizar exigiria editar quatro
  processos regulados sem ganho de segurança (nenhum dos dois caminhos executa o efeito adverso).
- O predicado T-E ganha um conjunto nomeado a mais, mantido em dois arquivos. É o mesmo custo que o conjunto de bloqueio
  de negativa já tem, e o teste de igualdade o cobre.
- A forma H acrescenta tarefas, timers e DMNs a FRAUDE e ADEQUACAO nas ondas seguintes.

## Supersedes

— Emenda a **ADR-0030 §5** só para boundaries de guarda **novos** (forma H); mantém a ADR-0030 §4, §5 e Tier 3 para os
boundaries que já existem. Complementa a **ADR-0007** (linha `REFUSED`), a **ADR-0018** (nenhum caminho executa o efeito
adverso) e a **ADR-0020** (custódia selada antes da decisão).
