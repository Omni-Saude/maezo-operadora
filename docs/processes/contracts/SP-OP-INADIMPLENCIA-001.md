# Contrato — SP-OP-INADIMPLENCIA-001 (Suspensao / Rescisao por Inadimplencia)

**Status:** DRAFT (v0.2.0) — `DRAFT — requires human review (médico auditor / jurídico / DPO / regulatório / finanças / PO) before any deploy` (docs/review-queue.md)
**Fase:** 3 · **BPMN (shipped, DRAFT):** `src/maezo/processes/bpmn/SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn` · **Worker:** `src/maezo/tools/workers/inadimplencia.py` · **Testes:** `tests/integration/processes/test_sp_op_inadimplencia_001.py`, `docs/processes/test-specs/SP-OP-INADIMPLENCIA-001.md` (GAP-INAD-4)
**Gatilho regulatorio:** **RN 593 (DRAFT/verify — regulatório/jurídico sign-off pending)** (regras de suspensao/rescisao por inadimplencia: janela de purga/cure + notificacao previa ao beneficiario; **possivelmente consolida/supersede RN 412/2016 — OQ aberta**); Lei 9.656/1998 art. 13 par. unico II (rescisao/suspensao por nao-pagamento — periodo minimo de inadimplencia + comprovacao de notificacao ate o 50o dia — **DRAFT/verify**). Suspensao e rescisao por inadimplencia sao **`contract_termination` L0-HARD** (so humano; nao rebaixavel por tenant; CI rejeita rebaixamento — ADR-0008/0018). **TODAS as citacoes/prazos/escadas/candidate-groups sao DRAFT/verify.**
**Inverte o anti-padrao:** auto-suspender/auto-rescindir no estouro do prazo de purga (padrao classico de cobranca automatizada). Em Maezo a janela de purga (RN 593) e um **event-gateway** cujo timer NUNCA leva a um terminal adverso automatico — sempre a uma User Task humana. **Clona o esqueleto SP-OP-CANCEL-001** (cure-window/event-gateway + terminal de suspensao humano-gated + handoff neutro da rescisao).

> **HARMONIZACAO COM CANCEL-001 — RESOLVIDA (DRAFT-A adotada e SHIPPED; GAP-INAD-5).** A propriedade
> do terminal de rescisao (open question antes bloqueante) foi decidida e implementada:
> **CANCEL-001 detem o UNICO terminal de rescisao** (`End_ContratoRescindido`, ja em `main`,
> human-gated via `UT_AnaliseRescisao` + `ERR_CANCELLATION_NOT_HUMAN`, business key
> `CANCEL-{tenant}-{numero_contrato}`). **INADIMPLENCIA-001 detem o ciclo de cobranca/purga/
> notificacao previa e a *suspensao*** (`End_ContratoSuspenso_Inad`); quando o humano decide
> encaminhar a rescisao (`decisao_inadimplencia=ENCAMINHAR_RESCISAO`), o worker NEUTRO
> `operadora.inadimplencia.handoff_rescisao` inicia/correlaciona CANCEL-001 (idempotente por
> business key) e a instancia de INADIMPLENCIA termina em `End_RescisaoHandoffCancel` (NEUTRO —
> nenhum efeito adverso local). **INADIMPLENCIA-001 NAO declara nenhum terminal de rescisao proprio**
> e NAO wireia `register_contract_rescission`/`Error_ContractRescissionNotHuman` a nenhuma task
> (dropped por design — ver §"Harmonizacao com CANCEL-001" abaixo e a decisao completa em
> `docs/processes/harmonization-inadimplencia-cancel.md`). A anti-dupla-terminacao e provada por
> topologia (BPMN estatico, `test_inadimplencia_nao_tem_terminal_de_rescisao_proprio`/
> `test_rescisao_so_acontece_via_handoff_a_cancel`) + correlacao cross-process REAL em runtime
> (`ja_em_rescisao_cancel`, `test_suspensao_recusada_se_ja_em_rescisao_cancel`, GAP-INAD-1). **A
> ownership da rescisao NAO e mais uma open question** — o que permanece DRAFT/verify e SO o
> conteudo regulatorio (prazos/citacoes RN 593, dias uteis vs corridos, candidate groups),
> registrado em `docs/review-queue.md`.

## Invariante L0-HARD (no-adverse — nao negociavel; ADR-0018; covers EVERY adverse effect)

`contract_termination` e **L0-hard** (`_hard_frozen.yaml`, CI-enforced). NENHUM caminho automatizado suspende NEM rescinde um contrato por inadimplencia. Ambos os efeitos adversos SO nascem em User Task humana:
- Suspensao → `UT_AnaliseInadimplencia` (grupo `juridico-contratos` / `gestao-cobranca`), `decisao_inadimplencia == SUSPENDER`.
- Rescisao → `UT_AnaliseInadimplencia`, `decisao_inadimplencia == ENCAMINHAR_RESCISAO` — **NAO materializa a rescisao aqui** (DRAFT-A shipped, §Harmonizacao): dispara o worker NEUTRO `handoff_rescisao`, que inicia/correlaciona SP-OP-CANCEL-001 (unico detentor do terminal `End_ContratoRescindido`); a instancia de INADIMPLENCIA termina no terminal neutro `End_RescisaoHandoffCancel`.

Nenhuma DMN deste processo possui saida que suspenda/rescinda. As DMN apenas **classificam o estado de inadimplencia e roteiam** (sem variante adversa). Inadimplencia aparente, prazo de purga expirado, notificacao previa ausente, ambiguidade de valor/competencia, contrato coletivo e indisponibilidade da DMN **todos fail-safe para a User Task humana** (catch-all conservador → `ANALISE_HUMANA`, allowlist fechada `frozenset`). O UNICO efeito adverso local e materializado pelo worker `operadora.inadimplencia.register_contract_suspension`, guardado por `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` (carrega `responsavel_id`+tier — cadeia de auditoria ADR-0007; verifica tambem `ja_em_rescisao_cancel`, anti-dupla-terminacao cross-process, GAP-INAD-1). O terminal adverso so e alcancavel apos a User Task humana concluida na history. INADIMPLENCIA-001 **NAO** implementa `register_contract_rescission`/`Error_ContractRescissionNotHuman` — a rescisao e propriedade exclusiva de CANCEL-001 (ver §Harmonizacao).

## Terminais humano-gated (no-adverse — §4-bis-F / ADR-0018)

> **A propriedade do terminal de rescisao esta RESOLVIDA (DRAFT-A shipped — GAP-INAD-5, ver
> §Harmonizacao).** A tabela abaixo e a topologia REAL do BPMN
> (`SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn`): INADIMPLENCIA-001 detem SUSPENSAO; a
> RESCISAO e SEMPRE via handoff neutro a CANCEL-001 — nunca um terminal local. So o conteudo
> regulatorio (prazos/citacoes RN 593) permanece DRAFT/verify.

| End event | Natureza | So alcancavel via |
|---|---|---|
| `End_ContratoSuspenso_Inad` | **ADVERSO** — suspensao por inadimplencia (RN 593) | `UT_AnaliseInadimplencia` (ou `UT_CoordenacaoCobranca`) com `decisao_inadimplencia=SUSPENDER` humano (apos cure-window de purga RN 593) |
| `End_RescisaoHandoffCancel` | neutro (handoff) — **rescisao delegada a SP-OP-CANCEL-001** (DRAFT-A shipped; evita dupla-rescisao) | `UT_AnaliseInadimplencia` com `decisao_inadimplencia=ENCAMINHAR_RESCISAO` → worker `operadora.inadimplencia.handoff_rescisao` inicia/correlaciona CANCEL-001 que detem `End_ContratoRescindido` |
| `End_Purgado` | neutro — inadimplencia purgada (pagamento dentro da janela de cura) | `msg.inadimplencia.pagamento_recebido` dentro do cure-window |
| `End_ContratoMantido` | neutro — vinculo mantido (humano decidiu MANTER) | `UT_AnaliseInadimplencia` com `decisao_inadimplencia=MANTER` humano |
| `End_RiscoSlaNotificado` | neutro — alerta de risco de SLA notificado (analise segue aberta) | timer boundary nao-interruptivo `BT_AlertaSla` em `UT_AnaliseInadimplencia` → `operadora.inadimplencia.notify_sla_risk` |

INADIMPLENCIA-001 **NAO declara** `End_ContratoRescindido_Inad` nem qualquer outro terminal de
rescisao proprio (prova topologica estatica —
`test_inadimplencia_nao_tem_terminal_de_rescisao_proprio`,
`docs/processes/test-specs/SP-OP-INADIMPLENCIA-001.md`). Exatamente UM processo detem o terminal
de rescisao do `contract_termination`: **CANCEL-001**.

Os terminais ADVERSOS NUNCA aparecem na history do engine sem uma User Task humana concluida (teste de invariante). Nenhum fim ocorre sem o evento de dominio publicado antes (auditoria dupla engine+Kafka, ADR-0007).

## Business key (idempotencia) — coordenada com CANCEL-001

```
INAD-{tenant_id}-{numero_contrato}
```

> **Coordenacao com CANCEL-001 (RESOLVIDA — DRAFT-A shipped):** CANCEL-001 chaveia por `CANCEL-{tenant_id}-{numero_contrato}`; INADIMPLENCIA por `INAD-{tenant_id}-{numero_contrato}` — **prefixos distintos para o MESMO contrato**. Isto evita colisao de instancia mas **NAO evita por si dupla-rescisao** (duas instancias, dois terminais adversos sobre o mesmo `contract_termination`) — por isso a garantia anti-dupla-rescisao vem, em camadas (§Harmonizacao): (1) topologia — INADIMPLENCIA nao declara terminal de rescisao; (2) `mcp-cibseven.start_process`/`start_by_key` consulta a business key antes de iniciar (idempotencia de instancia); (3) o worker adverso `register_contract_suspension` CONSULTA em runtime se ja existe rescisao/suspensao ativa em CANCEL-001 para `{tenant}-{numero_contrato}` (`ja_em_rescisao_cancel`, resolvido por `resolve_facts` via `CibSevenTransport.find_active_instance`, FAIL-CLOSED se a consulta falhar — GAP-INAD-1) e recusa (`ERR_CONTRACT_SUSPENSION_NOT_HUMAN`) antes de materializar o efeito.

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `numero_contrato` | string | sim | Numero do contrato (chave de negocio; mesma identidade usada por CANCEL-001) |
| `matricula_beneficiario` | string | sim | Matricula/pseudo-id do titular (ADR-0006 — Zona Geral usa pseudonimo) |
| `tipo_plano` | string | sim | `individual` \| `familiar` \| `coletivo_empresarial` \| `coletivo_adesao` |
| `origem_solicitacao` | string | sim | `cobranca` \| `operadora` \| `agente_fernando` \| `juridico` |
| `competencias_em_aberto` | json | sim | Lista de competencias (`YYYY-MM`) em aberto (FATO; alimenta a contagem) |
| `meses_inadimplencia` | integer | sim* | Pre-resolvido por worker (`operadora.inadimplencia.resolve_facts`): contagem de meses em aberto (integer — **nunca `number`**) |
| `valor_total_devido_cents` | integer | sim* | Pre-resolvido por worker: debito total em centavos de BRL (inteiro — **nunca `number`**) |
| `dentro_periodo_minimo` | boolean | sim* | Pre-resolvido por worker: inadimplencia atingiu o periodo minimo legal (ex.: 60 dias — **DRAFT/verify** RN 593/Lei 9.656) |
| `notificacao_previa_feita` | boolean | sim* | Pre-resolvido por worker (`operadora.inadimplencia.check_prior_notice`): notificacao previa ao beneficiario comprovada (ate o 50o dia de inadimplencia — RN 593 — **DRAFT/verify**) |
| `dentro_janela_purga` | boolean | sim* | Pre-resolvido por worker: ainda dentro da janela de cura/purga (RN 593 — **DRAFT/verify**) |
| `data_solicitacao_iso` | string | sim | Data (`YYYY-MM-DD`) |
| `documentos_refs` | json | sim | Referencias de anexos/comprovantes (faturas, notificacoes; pode ser vazio) |
| `ja_em_rescisao_cancel` | boolean | nao | Pre-resolvido por worker (correlacao cross-process): existe instancia CANCEL-001 ativa rescindindo/suspendendo este contrato (anti-dupla-rescisao) |

\* Pre-resolvido por worker de fatos antes da `businessRuleTask` (aritmetica/conferencia/contagem; **sem decisao adversa**). Valores monetarios em **inteiro-centavos** (`integer`), nunca `number`.

## Variaveis de saida (preenchidas pelas User Tasks humanas)

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_inadimplencia` | string | `SUSPENDER` \| `ENCAMINHAR_RESCISAO` \| `MANTER` \| `SOLICITAR_INFO` (User Tasks humanas — unica origem das variantes adversas; `ENCAMINHAR_RESCISAO` NAO rescinde localmente — dispara handoff NEUTRO a CANCEL-001, ver §Harmonizacao) |
| `fundamentacao_contratual` | string | **Obrigatoria** se `SUSPENDER` ou `ENCAMINHAR_RESCISAO` |
| `referencia_regulatoria` | string | **Obrigatoria** se `SUSPENDER`/`ENCAMINHAR_RESCISAO` (ex.: RN 593 — DRAFT/verify) |
| `comprovacao_notificacao_previa` | string | **Obrigatoria** se `SUSPENDER`/`ENCAMINHAR_RESCISAO` — referencia ao comprovante de notificacao previa (RN 593) |
| `comprovacao_periodo_minimo` | string | **Obrigatoria** se `SUSPENDER`/`ENCAMINHAR_RESCISAO` — comprovacao do periodo minimo de inadimplencia e da janela de purga decorrida |
| `responsavel_id` | string | Aprovador humano (cadeia de auditoria ADR-0007) + tier; carregado nos workers adversos |
| `data_efeito_iso` | string | Data de efeito (suspensao/rescisao), respeitada a antecedencia regulatoria |
| `decisao_coordenacao` | string | `assumir_analise` \| `prorrogar_prazo` \| `seguir_analise` (estouro de SLA — humano `coordenacao-cobranca`) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}` (registro central em `config/topic_registry.yaml` — **W0.2/orquestrador e o unico editor**; ver §"Registro de topicos exigido"). Contexto = `inadimplencia`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.inadimplencia.received` | produz | apos start |
| Kafka | `agents.events.inadimplencia.notified` | produz | notificacao previa de inadimplencia disparada/registrada |
| Kafka | `agents.events.inadimplencia.sla_breached` | produz | SLA de analise estourado |
| Kafka | `agents.events.inadimplencia.completed` | produz | fim (payload.desfecho = `suspenso` \| `rescisao_handoff` \| `purgado` \| `mantido`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (reuso) |
| External task | `operadora.inadimplencia.resolve_facts` | consome (worker) | pre-resolve fatos (meses, valor, periodo minimo, janela de purga — FATOS, nunca decisao; **TASY write DROP**, consumimos CDC — MEMORY/ADR-0013) |
| External task | `operadora.inadimplencia.check_prior_notice` | consome (worker) | resolve `notificacao_previa_feita`; dispara/registra notificacao previa ao beneficiario (RN 593 — **DRAFT/verify**) |
| External task | `operadora.inadimplencia.prepare_dossier` | consome (worker→A2A) | convoca Fernando (general): monta dossie de analise; **instrui, nao decide** (principio Rafael) |
| External task | `operadora.inadimplencia.register_contract_suspension` | consome (worker) | **efeito adverso gated (UNICO efeito adverso local do processo)** — registra suspensao; recusa sem decisao humana (`ERR_CONTRACT_SUSPENSION_NOT_HUMAN`); carrega `responsavel_id`+tier; verifica `ja_em_rescisao_cancel` (anti-dupla) |
| External task | `operadora.inadimplencia.handoff_rescisao` | consome (worker) | **handoff NEUTRO** (nao gated — NAO e um efeito adverso) — inicia/correlaciona SP-OP-CANCEL-001 para a rescisao (CANCEL detem o UNICO terminal de rescisao, DRAFT-A shipped, §Harmonizacao); NAO rescinde aqui |
| External task | `operadora.inadimplencia.notify_sla_risk` | consome (worker) | alerta `coordenacao-cobranca` (timer nao-interruptivo) |
| Message BPMN | `msg.inadimplencia.pagamento_recebido` | recebe | correlacao por business key — pagamento dentro da janela de purga, destrava `End_Purgado` |
| Message BPMN | `msg.inadimplencia.notificacao_ack` | recebe | correlacao por business key — confirmacao da notificacao previa, destrava o gateway de prazo (cure-window) |
| Message BPMN | `msg.inadimplencia.info_received` | recebe | correlacao por business key — info/documentacao solicitada pelo humano chegou (`decisao_inadimplencia=SOLICITAR_INFO`); reabre `UT_AnaliseInadimplencia` |

## DMN referenciadas

Shape engine-deployavel (§4-bis-A): `typeRef ∈ {string, boolean, integer, long, double, date}` — **`number` e invalido**; dinheiro BRL → **inteiro-centavos (`integer`)** (consistente com `*_cents`); dias/SLA → string ISO 8601. Toda `decisionTable` com `hitPolicy`; toda tabela com row catch-all (`frozenset` fechado) → caminho humano conservador. `camunda:historyTimeToLive` namespaced (`P###D`). **Nenhuma DMN tem coluna de saida que suspenda/rescinda (sem variante adversa).**

### `inadimplencia_status` (hitPolicy FIRST — DRAFT; coracao adverso-like)
- **in:** `meses_inadimplencia: integer`, `dentro_periodo_minimo: boolean`, `notificacao_previa_feita: boolean`, `dentro_janela_purga: boolean`, `tipo_plano: string`
- **out:** `roteamento: string` (`AGUARDA_PURGA` \| `PENDENTE_NOTIFICACAO` \| `SEGUE_ANALISE` \| `ANALISE_HUMANA`), `motivo: string`
- **Sem saida `SUSPENDER`/`ENCAMINHAR_RESCISAO` por design.** `dentro_janela_purga=true` → `AGUARDA_PURGA` (cure-window; nunca adverso). `notificacao_previa_feita=false` → `PENDENTE_NOTIFICACAO`. `dentro_periodo_minimo=true` + notificacao feita + purga decorrida → `SEGUE_ANALISE` (humano decide SUSPENDER/ENCAMINHAR_RESCISAO/MANTER). `tipo_plano ∈ {coletivo_empresarial, coletivo_adesao}` → `ANALISE_HUMANA` (regras do estipulante; nunca auto-decide). Catch-all → `ANALISE_HUMANA`.

### `inadimplencia_purga` (hitPolicy FIRST — DRAFT; RN 593)
- **in:** `tipo_plano: string`
- **out:** `prazo_purga: string` (ISO 8601, ex.: `P10D` apos notificacao — **DRAFT/verify** RN 593), `prazo_notificacao_previa: string` (ISO 8601, ex.: ate 50o dia — **DRAFT/verify**), `periodo_minimo: string` (ISO 8601, ex.: `P60D` — **DRAFT/verify**), `fonte_regulatoria: string`
- **Apenas determina prazos regulatorios** (janela de purga/cura + notificacao previa + periodo minimo). **Nao decide suspender/rescindir.** O cure-window (event gateway) e dirigido por `prazo_purga`. Catch-all → prazos conservadores (maiores) + `fonte_regulatoria="DRAFT_DEFAULT"`.

### `inadimplencia_sla` (hitPolicy FIRST — DRAFT; todos os prazos DRAFT/verify)
- **in:** `tipo_plano: string`
- **out:** `sla_analise: string` (ISO 8601), `sla_alerta: string` (ISO 8601), `fonte_regulatoria: string`
- Prazos como string ISO; converter dias uteis→ISO conservadoramente no worker. **Dias uteis vs corridos e OQ aberta (RN 593) — ver Pendencias.**

## Papeis humanos (candidate groups)

> **PROPOSTO — DRAFT/verify contra a taxonomia organizacional da operadora (PO / IdP)** — ver Pendencias. Coordenar com os grupos de CANCEL-001 (`juridico-contratos`, `coordenacao-contratos`) para evitar divergencia de taxonomia entre os dois processos que tocam `contract_termination`.

| Grupo (candidateGroups) | Papel | Tarefa |
|---|---|---|
| `juridico-contratos` | Juridico/contratos (compartilhado com CANCEL-001) | `UT_AnaliseInadimplencia` (**suspensao/encaminhamento de rescisao SO aqui**: `decisao_inadimplencia ∈ {SUSPENDER, ENCAMINHAR_RESCISAO, MANTER, SOLICITAR_INFO}`) |
| `gestao-cobranca` | Gestao de cobranca/financeiro | `UT_AnaliseInadimplencia` (co-candidate para o merito de cobranca) |
| `coordenacao-cobranca` | Coordenacao de cobranca | `UT_CoordenacaoCobranca` (SLA de analise estourado — assume a decisao, que continua humana) |

A decisao adversa NUNCA muda de natureza por estouro de SLA: `UT_CoordenacaoCobranca` herda os mesmos campos obrigatorios.

Campos obrigatorios por decisao adversa (validacao de formulario/listener da User Task — sem eles a task NAO completa):
- `decisao_inadimplencia ∈ {SUSPENDER, ENCAMINHAR_RESCISAO}` → `fundamentacao_contratual` + `referencia_regulatoria` + `comprovacao_notificacao_previa` + `comprovacao_periodo_minimo`.

## SLAs

> Todos os prazos sao **DRAFT/verify** com regulatorio/juridico antes de qualquer timer em producao. Prazos legais sao em dias (uteis vs corridos e **OQ aberta** RN 593); ISO 8601 usa dias corridos — usar valores conservadores e resolver calendario util no worker.

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Janela de purga / cure-window (`${inadimplencia_purga.prazo_purga}`) | event gateway: `msg.inadimplencia.pagamento_recebido` **vs** timer → `UT_AnaliseInadimplencia` (humano decide; expiracao NUNCA auto-suspende/auto-rescinde) | RN 593 (cura/purga) — **DRAFT/verify** |
| Notificacao previa (`${inadimplencia_purga.prazo_notificacao_previa}`) | event gateway: `msg.inadimplencia.notificacao_ack` **vs** timer → reavaliacao | RN 593 (notificacao ate o 50o dia) — **DRAFT/verify** |
| Analise (`${inadimplencia_sla.sla_analise}`) | interruptivo → `agents.events.inadimplencia.sla_breached` + `UT_CoordenacaoCobranca` assume | politica interna / RN 593 — **DRAFT/verify** |
| Alerta de risco (`${inadimplencia_sla.sla_alerta}`) | ~50–70% do SLA | nao-interruptivo → `operadora.inadimplencia.notify_sla_risk` | politica interna — **DRAFT** |

> O event gateway de purga **substitui/INVERTE** qualquer padrao de "auto-suspender/auto-rescindir no estouro do prazo": a expiracao da janela de purga roteia para a User Task humana, nunca para um terminal adverso automatico. **Nunca** ha auto-suspensao/auto-rescisao por timeout.

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` | worker-guard em `operadora.inadimplencia.register_contract_suspension` | recusa executar (lanca BPMN error) sem `decisao_inadimplencia == SUSPENDER` setado por humano **e** sem `responsavel_id` (ADR-0007); tambem recusa se faltar `fundamentacao_contratual`/`referencia_regulatoria`/`comprovacao_notificacao_previa`/`comprovacao_periodo_minimo`, **ou** se `ja_em_rescisao_cancel=true` (anti-dupla-rescisao/suspensao). `tier` NAO e exigido (GAP-INAD-3 — nenhuma UT o coleta); repassado na trilha de auditoria quando presente. A instancia nao atinge `End_ContratoSuspenso_Inad`. Declarar `Error_ContractSuspensionNotHuman`. |
| `ERR_INAD_INVALID_CONTRATO` | declarado (`Error_InadContratoInvalido`) para uso dos workers | worker lanca BPMN error se contrato inconsistente na origem; tratamento a detalhar na promocao a FINAL |

> **`ERR_CONTRACT_RESCISSION_NOT_HUMAN` / `Error_ContractRescissionNotHuman` — DROPPED (GAP-INAD-5,
> DRAFT-A shipped).** O contrato ate a v0.1.0 declarava este erro para um worker
> `register_contract_rescission` condicionado a "se INADIMPLENCIA detiver o terminal (ver
> §Harmonizacao)". A harmonizacao resolveu que **CANCEL-001**, nao INADIMPLENCIA, detem o terminal
> de rescisao — logo este worker/erro NUNCA existiram no BPMN shipped (`inadimplencia.py` so
> declara `register_contract_suspension`; o BPMN so declara `Error_InadContratoInvalido` e
> `Error_ContractSuspensionNotHuman`). Mantido aqui apenas como nota historica; NAO implementar.

> **Guarda no-adverse (parte 4 de 5; §4-bis-F / ADR-0018):** `ERR_CONTRACT_SUSPENSION_NOT_HUMAN` e a ultima linha de defesa do UNICO efeito adverso local. O check `ja_em_rescisao_cancel` no worker e a **defesa em profundidade contra dupla-rescisao** complementar a §Harmonizacao (mesmo que a rescisao ja esteja em voo em CANCEL-001, a suspensao local e recusada).

## Teste de invariante (parte 5 de 5; engine REAL — ADR-0011)

- `test_suspensao_exige_user_task` — varre combinacoes de input das DMN (`inadimplencia_status`/`inadimplencia_purga`) e prova que nenhuma atinge `End_ContratoSuspenso_Inad` automaticamente; consulta `history/activity-instance` para provar que, se o terminal adverso esta no historico, **entao** `UT_AnaliseInadimplencia` (ou `UT_CoordenacaoCobranca`) humana tambem esta. **(implementado como `test_nenhum_caminho_automatizado_suspende_contrato` + `test_inadimplencia_aparente_roteia_para_humano_nao_suspende`, ver test-spec GAP-INAD-4.)**
- `test_expiracao_purga_nao_auto_suspende` — expiracao do cure-window roteia para User Task, nunca para terminal adverso.
- `test_inadimplencia_nao_dupla_rescisao` — **(harmonizacao, GAP-INAD-1)** prova que um mesmo `{tenant}-{numero_contrato}` nunca atinge terminal adverso em INADIMPLENCIA **e** em CANCEL-001 (correlacao cross-process REAL — `ja_em_rescisao_cancel` resolvido por consulta ao engine, honrado pelo guard de `register_contract_suspension`). **(implementado como `test_suspensao_recusada_se_ja_em_rescisao_cancel` + `test_suspensao_prossegue_sem_cancel_ativo` + a topologia estatica `test_inadimplencia_nao_tem_terminal_de_rescisao_proprio`/`test_rescisao_so_acontece_via_handoff_a_cancel`, ver test-spec GAP-INAD-4.)** NAO ha `test_rescisao_exige_user_task` local — a rescisao nao tem terminal proprio aqui; a User Task humana da rescisao e provada por `test_sp_op_cancel_001.py` (CANCEL-001 e o unico detentor).

A suite completa (engine REAL, ADR-0011) vive em `tests/integration/processes/test_sp_op_inadimplencia_001.py`
e e descrita ao nivel de teste em `docs/processes/test-specs/SP-OP-INADIMPLENCIA-001.md` (GAP-INAD-4 —
antes deste gap fechar, o processo violava o "mandatory quadruple" contrato+BPMN+DMN+test-spec).

## Harmonizacao com CANCEL-001 — RESOLVIDA (DRAFT-A shipped; GAP-INAD-5)

**O problema (risco L0 — dupla-rescisao/dupla-suspensao):** CANCEL-001 (Phase 2, em main) ja produz `End_ContratoSuspenso` / `End_ContratoRescindido` no ramo `tipo_solicitacao=inadimplencia`, via `UT_AnaliseRescisao` (grupo `juridico-contratos`). INADIMPLENCIA-001 cobre o mesmo efeito adverso (`contract_termination` por inadimplencia). Sem harmonizacao, o mesmo contrato poderia ser terminado por ambos.

**Decisao ADOTADA e SHIPPED (DRAFT-A — ver `docs/processes/harmonization-inadimplencia-cancel.md` para o
racional completo; a alternativa DRAFT-B, inversa, foi avaliada e descartada la):**
1. **Ownership do terminal de rescisao (RESOLVIDO):** exatamente UM processo detem `End_ContratoRescindido` para o `contract_termination` — **CANCEL-001** (ja em main, ja com terminal, ja human-gated, ja com o invariante no-denial testado). INADIMPLENCIA-001 detem o **ciclo de cobranca + notificacao + janela de purga + a suspensao**, e faz **handoff NEUTRO** (`operadora.inadimplencia.handoff_rescisao` → `End_RescisaoHandoffCancel`) quando o humano decide `ENCAMINHAR_RESCISAO`. INADIMPLENCIA-001 **NAO declara** `End_ContratoRescindido_Inad` nem qualquer worker/erro de rescisao gated local — confirmado no BPMN shipped (`Error_ContractSuspensionNotHuman`/`Error_InadContratoInvalido` sao os UNICOS `bpmn:error` declarados) e no worker (`inadimplencia.py` modulo docstring §"NAO HA worker de rescisao gated AQUI").
2. **Business-keys coordenadas (RESOLVIDO):** `INAD-{tenant}-{contrato}` (cobranca/purga) vs `CANCEL-{tenant}-{contrato}` (cancelamento/rescisao). Prefixos distintos, mesma identidade de contrato → correlacao cross-process por `{tenant}-{contrato}`. O worker adverso (`register_contract_suspension`) verifica `ja_em_rescisao_cancel` (resolvido por `resolve_facts` via consulta REAL ao engine, `CibSevenTransport.find_active_instance`, fail-closed) antes de materializar (defesa em profundidade, GAP-INAD-1).
3. **Sobreposicao regulatoria (AINDA DRAFT/verify — nao bloqueia a arquitetura):** se RN 593 **consolida/supersede RN 412/2016**, CANCEL-001 (que cita RN 412 + RN 593) e INADIMPLENCIA-001 (que cita RN 593) devem referenciar a **mesma** fonte vigente — evitar citacoes divergentes para o mesmo efeito. Esta e uma questao de CONTEUDO regulatorio (qual RN citar), nao de qual processo possui o terminal — ja resolvida.

**O que RESTA aberto (SO conteudo regulatorio/organizacional — registrado em `docs/review-queue.md`,
NAO bloqueia arquitetura nem deploy do engine):**
- **Dias uteis vs corridos** na janela de purga, notificacao previa e periodo minimo (RN 593)? — regulatório.
- **RN 593 supersede/consolida RN 412/2016?** (e quaisquer dispositivos da Lei 9.656 art. 13) — regulatório + jurídico.
- **Confirmacao dos candidate groups** `juridico-contratos`/`gestao-cobranca`/`coordenacao-cobranca` contra a taxonomia organizacional — PO/IdP.
- **Fernando** (agente de inadimplencia) precisa de SP-OP-INADIMPLENCIA-001 proprio, ou opera como AGJ navegador que so escala para CANCEL-001 (igual Lucas)? Ja RESOLVIDO no sentido operacional (GAP-INAD-6, PR #134: `prepare_dossier` delega `arrears.followup` a Fernando via A2A real); a framing de produto (processo dedicado vs navegador) permanece PO/produto.

## Notas de design / inversao do reference

- **Anti-padrao invertido:** auto-suspender/auto-rescindir no estouro do prazo de purga (cobranca automatizada). Em Maezo a janela de purga e event-gateway humano-gated — expiracao → User Task, nunca terminal adverso.
- **Clona o esqueleto CANCEL-001** (cure-window/event-gateway + terminal de suspensao humano-gated) — deliberadamente simetrico ao processo com que harmoniza; a rescisao NAO e clonada (fica exclusivamente em CANCEL-001, ver §Harmonizacao).
- **Fernando (general)** convocado via `operadora.inadimplencia.prepare_dossier`; **instrui, nao decide**. KPI alvo (phase3-plan): `false_denial_rate == 0`.
- **TASY write DROP** em todo worker (MEMORY/ADR-0013).
- Valores monetarios em **inteiro-centavos** (`integer`), nunca `number`.

## Pendencias para promocao a FINAL

- ~~RESOLVER A HARMONIZACAO CANCEL-001 (bloqueante)~~ — **RESOLVIDA e SHIPPED** (DRAFT-A: CANCEL-001 detem a rescisao; INADIMPLENCIA-001 detem cobranca/purga/suspensao + handoff neutro; GAP-INAD-5). O que resta e SO conteudo regulatorio (ver §Harmonizacao "O que RESTA aberto").
- DI (diagrama) e os bodies `.bpmn`/`.dmn`/`workers/inadimplencia.py` — SHIPPED (contra este contrato + a harmonizacao resolvida); DI ainda pendente (nao bloqueia teste/deploy do engine).
- Confirmacao de **todas** as citacoes RN com regulatorio/juridico: **RN 593** (janela de purga/cura; notificacao previa; periodo minimo; consolidacao de RN 412/2016) e Lei 9.656 art. 13 — **DRAFT/verify** contra texto vigente ANS.
- Confirmacao de TODOS os prazos (cure-window; notificacao; periodo minimo; SLA) e **dias uteis vs corridos** (OQ) → conversao ISO conservadora.
- **Confirmacao dos candidate groups** `juridico-contratos` / `gestao-cobranca` / `coordenacao-cobranca` contra a taxonomia (PO / IdP) — coordenar com CANCEL-001.
- Confirmar `contract_termination` por inadimplencia em `_hard_frozen.yaml` (L0) cobre ambos os processos.
- Matriz de hipoteses por `tipo_plano` (individual/familiar vs coletivo — coletivos seguem regras do estipulante).

## Registro de topicos exigido (REPORTE — nao editar `topic_registry.yaml` aqui)

**JA REGISTRADO** em `config/topic_registry.yaml` (dono: W0.2/orquestrador) — Kafka `agents.events.inadimplencia.*`: `received`, `notified`, `sla_breached`, `completed`. External-task `operadora.inadimplencia.*`: `resolve_facts`, `check_prior_notice`, `prepare_dossier`, `register_contract_suspension`, `handoff_rescisao`, `notify_sla_risk` (**NAO** `register_contract_rescission` — DROPPED, GAP-INAD-5, nunca wireado a nenhuma task no BPMN shipped). Message BPMN `msg.inadimplencia.pagamento_recebido`, `msg.inadimplencia.notificacao_ack`, `msg.inadimplencia.info_received`. (`operadora.events.publish` ja registrado.) Process key `SP-OP-INADIMPLENCIA-001` **JA REGISTRADO** em `KNOWN_PROCESS_KEYS` (ADR-0016, `src/maezo/tools/process_allowlist.py`) via PR humano CODEOWNERS.
