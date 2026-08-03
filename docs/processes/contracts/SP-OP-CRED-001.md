# Contrato — SP-OP-CRED-001 ((Des)credenciamento de Prestador / Rede)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review (médico-auditor/jurídico/DPO/regulatório/finanças/PO) before any deploy` (docs/review-queue.md)
**Fase:** 3 (ONE-PHASE-AHEAD — autorado contra contrato; BPMN/DMN/worker autorados na wave de BUILD W-B) · **BPMN (alvo):** `spec/processes/bpmn/SP-OP-CRED-001_Descredenciamento.bpmn`
**Gatilho regulatorio:** **RN 567 (DRAFT/verify — regulatório/jurídico sign-off pending)** (descredenciamento de prestador hospitalar / notificação prévia ao beneficiário e à ANS / substituição equivalente); **RN 566 (DRAFT/verify — regulatório/jurídico sign-off pending)** (garantias de rede / dimensionamento); Lei 9.656/1998 art. 17 (alteração/redimensionamento da rede assistencial — **DRAFT/verify**). Descredenciar prestador (`provider_decredentialing`) **E** negar pedido de credenciamento sao **DUAS direcoes adversas distintas** → matriz **L1** (so humano; nao auto-rebaixavel por tenant; CI rejeita rebaixamento — ADR-0008). **TODAS as citacoes/prazos/escadas de alcada/candidate-groups sao DRAFT/verify.**
**Inverte o anti-padrao:** `../maezo-reference/healthcare_platform/platform_services/bpmn/SP-PS-002_Credentialing.bpmn` (READ-ONLY, ADR-0011) — que tem um `Gateway_Approval` **bare** com `${credential_approved == true/false}` roteando direto para `Task_ActivateCredentials` (auto-credencia) OU `Task_SendRejection` (**auto-rejeita, sem humano**), e um `Boundary_LicenseTimeout` (P30D) → `Task_ExpireRequest` (**auto-expira por timeout**). Em Maezo as duas direcoes adversas (negar credenciamento; descredenciar) nascem **so** em User Task humana; a DMN apenas **sinaliza candidata e roteia**; nenhum caminho automatizado nega ou descredencia, e nenhum timeout auto-expira/auto-descredencia. Ver §"Notas de design / inversao do reference". **Clona o esqueleto SP-OP-CANCEL-001** (cure-window/event-gateway de notificacao previa + terminais adversos humano-gated).

> Este contrato e o ponto de sincronizacao (§4-bis-F): o BPMN/DMN/agente (Carolina — PHI zone, `credentialing.analyze`) derivam dele. Toda a mecanica no-adverse de cinco partes (ADR-0018) esta materializada abaixo, **para as duas direcoes adversas**.

## Invariante L1 (no-adverse — nao negociavel; ADR-0018; covers EVERY adverse effect)

`provider_decredentialing` e **L1** (`_hard_frozen.yaml`, CI-enforced — promocao a `_hard_frozen` pendente de sign-off arquitetura+compliance). NENHUM caminho automatizado:
- **descredencia** um prestador ja credenciado (efeito adverso direcao A); NEM
- **nega** um pedido de credenciamento de um prestador candidato (efeito adverso direcao B).

Ambas as direcoes adversas SO nascem em User Tasks humanas:
- Descredenciamento → `UT_AnaliseDescredenciamento` (grupo `gestao-rede` / `juridico-rede`), `decisao_cred == DESCREDENCIAR`.
- Negativa de credenciamento → `UT_AnaliseCredenciamento` (grupo `gestao-rede`), `decisao_cred == NEGAR_CREDENCIAMENTO`.

Nenhuma DMN deste processo possui saida que negue/descredencie. As DMN so produzem dominios de roteamento **sem variante adversa** (ver §DMN). Documentacao incompleta, licenca aparentemente irregular, indicio de irregularidade, queixa de qualidade, vencimento de credencial, ambiguidade e indisponibilidade da DMN **todos fail-safe para uma User Task humana** (catch-all conservador → `ANALISE_HUMANA`, allowlist fechada `frozenset`). Os efeitos adversos sao materializados apenas pelos workers `operadora.cred.register_descredenciamento` e `operadora.cred.register_cred_denial`, guardados respectivamente por `ERR_DECRED_NOT_HUMAN` e `ERR_CRED_DENIAL_NOT_HUMAN` (carregam `responsavel_id` + tier na cadeia de auditoria ADR-0007). Os terminais adversos so sao alcancaveis apos a User Task humana concluida na history.

Indicio de irregularidade/fraude de credencial (`fraud_accusation`, **L0 hard**) NUNCA e auto-flagueado: roteia para `UT_AnaliseDescredenciamento`, que decide encaminhar a SP-OP-FRAUDE-001 (`encaminhar_fraude=true`). Nenhum branch auto-acusa.

## Terminais humano-gated (no-adverse — §4-bis-F / ADR-0018)

| End event | Natureza | So alcancavel via |
|---|---|---|
| `End_PrestadorDescredenciado` | **ADVERSO** (direcao A) — descredenciamento pela operadora | `UT_AnaliseDescredenciamento` com `decisao_cred=DESCREDENCIAR` humano (apos cure-window de notificacao previa RN 567) |
| `End_CredenciamentoNegado` | **ADVERSO** (direcao B) — negativa do pedido de credenciamento | `UT_AnaliseCredenciamento` com `decisao_cred=NEGAR_CREDENCIAMENTO` humano |
| `End_PrestadorCredenciado` | neutro — credenciamento aprovado | caminho clerical (DMN admissibilidade favoravel + dentro de criterios) OU `UT_AnaliseCredenciamento` com `APROVAR_CREDENCIAMENTO` |
| `End_VinculoMantido` | neutro — descredenciamento nao prosseguiu / prestador mantido | `UT_AnaliseDescredenciamento` com `MANTER` humano OU caminho neutro |
| `End_SubstituicaoRegistrada` | neutro — substituicao/redimensionamento equivalente registrado (RN 567 — substituicao por prestador equivalente nao e, por si, efeito adverso contra o substituto; mas a *saida* do descredenciado E adverso e exige a UT) | `UT_AnaliseDescredenciamento` com decisao humana que inclui plano de substituicao |

Os dois terminais ADVERSOS (`End_PrestadorDescredenciado`, `End_CredenciamentoNegado`) NUNCA aparecem na history do engine sem uma User Task humana concluida (teste de invariante — ver §Teste de invariante). Nenhum fim ocorre sem o evento de dominio correspondente publicado antes (auditoria dupla engine+Kafka, ADR-0007).

## Business key (idempotencia)

```
CRED-{tenant_id}-{prestador_id}
```

Variante por pedido (quando ha multiplos ciclos de credenciamento/descredenciamento para o mesmo prestador): `CRED-{tenant_id}-{prestador_id}-{protocolo_cred}`. Uma instancia ativa por prestador (ou por protocolo); reenvio da mesma solicitacao retorna a instancia ativa. `mcp-cibseven.start_process` DEVE consultar a business key antes de iniciar (start idempotente — sem duplicar descredenciamento/negativa).

> **Coordenacao de chave (OQ — ver Pendencias):** `CRED-{tenant}-{prestador}` e disjunta de `CONTAS-`/`AUTH-` (que chaveiam por lote/guia). Sem colisao com INADIMPLENCIA/CANCEL (que chaveiam por contrato de beneficiario) — confirmar que `prestador_id` e estavel e pseudonimo-compativel (ADR-0006 — Zona Geral; prestador PJ/PF e dado cadastral, nao PHI do beneficiario).

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `prestador_id` | string | sim | Prestador objeto do (des)credenciamento (chave de negocio) |
| `protocolo_cred` | string | nao | Protocolo do ciclo de credenciamento/descredenciamento |
| `direcao` | string | sim | `credenciamento` \| `descredenciamento` (orienta o ramo; **nao decide** o desfecho adverso) |
| `tipo_prestador` | string | sim | `pessoa_fisica` \| `clinica` \| `hospital` \| `laboratorio` \| `sadt` \| `opme` |
| `origem_solicitacao` | string | sim | `prestador` \| `operadora` \| `agente_carolina` \| `auditoria_qualidade` \| `juridico` |
| `motivo_informado` | string | nao | Texto livre (fundamento da solicitacao / motivo do descredenciamento) — sem PHI de beneficiario |
| `data_solicitacao_iso` | string | sim | Data (`YYYY-MM-DD`) da solicitacao |
| `documentos_refs` | json | sim | Referencias de anexos/comprovantes (licenca, CRM/CNES, contrato; pode ser vazio) |
| `licenca_valida` | boolean | sim* | Pre-resolvido por worker (`operadora.cred.verify_credentials`): registro profissional/CNES vigente (FATO — nunca decide negar) |
| `documentacao_completa` | boolean | sim* | Pre-resolvido por worker: documentos minimos presentes |
| `dentro_criterios_rede` | boolean | sim* | Pre-resolvido por worker: prestador atende criterios objetivos de rede (especialidade/regiao demandadas — RN 566) |
| `notificacao_previa_feita` | boolean | sim* | Pre-resolvido por worker (`operadora.cred.check_prior_notice`): notificacao previa ao beneficiario/ANS comprovada (RN 567 — **so descredenciamento**) |
| `substituto_equivalente_identificado` | boolean | nao | Pre-resolvido por worker: ha prestador equivalente para substituicao (RN 567 — **so descredenciamento**) |
| `tem_beneficiarios_vinculados` | boolean | sim* | Seeded pelo agente Carolina (delegacao `credentialing.analyze`): ha beneficiarios vinculados ao prestador — consumida pela DMN `cred_prior_notice` (RN 567 — **so descredenciamento**) |
| `indicio_irregularidade_sinalizado` | boolean | nao | Sinal **informativo** de worker (NUNCA decide; so roteia a humano / encaminha FRAUDE) |
| `regiao_saude` | string | nao† | Regiao de saude / area de atuacao do prestador — dado **cadastral/geografico** (Zona Geral, ADR-0006). Compoe o fato `network_changed` que a `network_change_bridge` correlaciona com SP-OP-ADEQUACAO-001 (`ADEQ-{tenant}-{regiao_saude}-{especialidade}-{ciclo}`). GAP-XPROC-2. |
| `especialidade` | string | nao† | Especialidade/servico do prestador (taxonomia TUSS/CBO — DRAFT). Dado **cadastral**; compoe o fato `network_changed` consumido por SP-OP-ADEQUACAO-001. GAP-XPROC-2. |

\* Pre-resolvido por worker de fatos antes das `businessRuleTask` (verificacao/conferencia; **sem decisao adversa**).
† **Obrigatoria para a choreografia CRED→ADEQUACAO** (GAP-XPROC-2): sem `regiao_saude`+`especialidade` no fato `network_changed`, a `network_change_bridge` nao consegue formar a business key de ADEQUACAO e o fato cai em NO-OP (nao inicia adequacao). Dado cadastral do prestador (nunca PHI de beneficiario, ADR-0006). Semeada no start (agente Carolina / origem da solicitacao).

## Variaveis de saida (preenchidas pelas User Tasks humanas)

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_cred` | string | `APROVAR_CREDENCIAMENTO` \| `NEGAR_CREDENCIAMENTO` \| `DESCREDENCIAR` \| `MANTER` \| `SOLICITAR_INFO` (User Tasks humanas — unica origem das duas variantes adversas) |
| `fundamentacao` | string | **Obrigatoria** se `NEGAR_CREDENCIAMENTO` ou `DESCREDENCIAR` — fundamentacao da decisao adversa |
| `referencia_regulatoria` | string | **Obrigatoria** se `DESCREDENCIAR` (RN 567 — DRAFT/verify) ou `NEGAR_CREDENCIAMENTO` (RN 566 — DRAFT/verify) |
| `comprovacao_notificacao_previa` | string | **Obrigatoria** se `DESCREDENCIAR` — referencia ao comprovante de notificacao previa (beneficiario/ANS, RN 567) |
| `plano_substituicao` | string | **Obrigatoria** se `DESCREDENCIAR` de hospital/prestador com beneficiarios vinculados — substituto equivalente / redimensionamento (RN 567) |
| `responsavel_id` | string | Aprovador humano (cadeia de auditoria ADR-0007) + tier; carregado nos workers adversos |
| `data_efeito_iso` | string | Data de efeito do desfecho (descredenciamento/credenciamento), respeitada a antecedencia regulatoria |
| `decisao_coordenacao` | string | `assumir_analise` \| `prorrogar_prazo` \| `seguir_analise` (estouro de SLA — humano `coordenacao-rede`) |
| `encaminhar_fraude` | boolean | Preenchida por `UT_AnaliseDescredenciamento`: encaminhar a SP-OP-FRAUDE-001 (indicio nunca auto-flagueia) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}` (registro central em `config/topic_registry.yaml` — **W0.2/orquestrador e o unico editor**; este contrato so declara o que precisa ser registrado — ver §"Registro de topicos exigido"). Contexto = `cred`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.cred.received` | produz | apos start (solicitacao de (des)credenciamento recebida) |
| Kafka | `agents.events.cred.pended` | produz | pendencia de documentacao / notificacao previa aberta |
| Kafka | `agents.events.cred.sla_breached` | produz | SLA de analise estourado |
| Kafka | `agents.events.cred.network_changed` | **produz (fato de mudanca de rede)** | desfecho que altera a rede: `prestador_descredenciado` \| `prestador_credenciado` \| `substituicao_registrada` — **consumido pela `network_change_bridge` → INICIA SP-OP-ADEQUACAO-001** (GAP-XPROC-2; payload: `tenant_id`, `prestador_id`, `tipo_prestador`, `regiao_saude`, `especialidade`, `tipo_mudanca`, `data_efeito_iso`). A ponte correlaciona por `tenant_id`+`regiao_saude`+`especialidade` e deriva `ciclo_avaliacao` do `data_efeito_iso` (trimestre) para a bk `ADEQ-{tenant}-{regiao}-{especialidade}-{ciclo}` |
| Kafka | `agents.events.cred.completed` | produz | fim (payload.desfecho = `credenciado` \| `credenciamento_negado` \| `descredenciado` \| `vinculo_mantido` \| `substituicao_registrada`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (reuso, todos os SP-OP) |
| External task | `operadora.cred.verify_credentials` | consome (worker) | resolve `licenca_valida`/`documentacao_completa` (registro profissional/CNES — FATO; **TASY write DROP**, consumimos CDC, nunca escrevemos — MEMORY/ADR-0013) |
| External task | `operadora.cred.check_network_criteria` | consome (worker) | resolve `dentro_criterios_rede` (criterios objetivos RN 566 — FATO, nunca decide negar) |
| External task | `operadora.cred.check_prior_notice` | consome (worker) | resolve `notificacao_previa_feita`; dispara/registra notificacao previa ao beneficiario/ANS (RN 567 — **DRAFT/verify**) |
| External task | `operadora.cred.prepare_dossier` | consome (worker→A2A) | convoca Carolina (PHI zone): monta dossie de analise (delegacao `credentialing.analyze`); **instrui, nao decide** (principio Rafael) |
| External task | `operadora.cred.register_cred_denial` | consome (worker) | **efeito adverso gated (direcao B)** — registra negativa de credenciamento; recusa sem decisao humana (`ERR_CRED_DENIAL_NOT_HUMAN`); carrega `responsavel_id`+tier |
| External task | `operadora.cred.register_descredenciamento` | consome (worker) | **efeito adverso gated (direcao A)** — registra descredenciamento; recusa sem decisao humana (`ERR_DECRED_NOT_HUMAN`); carrega `responsavel_id`+tier; emite `network_changed` |
| External task | `operadora.cred.register_credenciamento` | consome (worker) | registra credenciamento aprovado (neutro); emite `network_changed` |
| External task | `operadora.cred.notify_sla_risk` | consome (worker) | alerta `coordenacao-rede` (timer nao-interruptivo) |
| Message BPMN | `msg.cred.notification_ack` | recebe | correlacao por business key — confirmacao da notificacao previa, destrava o gateway de prazo (cure-window) |
| Message BPMN | `msg.cred.info_received` | recebe | correlacao por business key — documentacao solicitada pelo humano chegou, destrava reavaliacao. **Payload de correlacao:** para o ramo `PENDENTE_DOCUMENTACAO` (`ICE_AguardarInfoDoc` → `ST_VerifyCredentials`) a mensagem DEVE carregar o boolean `documentacao_completa=true` — `verify_credentials` RESPEITA um boolean ja resolvido e nao o re-deriva de `documentos_refs`, entao enviar so os documentos deixa o `documentacao_completa=false` anterior de pe, `cred_admissibility` (FIRST, `r_cred_doc_pendente`) re-roteia a `PENDENTE_DOCUMENTACAO` e a instancia volta a mesma espera (loop de pendencia neutro — sem negativa nem descredenciamento automatico) |

## DMN referenciadas

Shape engine-deployavel (§4-bis-A): `typeRef ∈ {string, boolean, integer, long, double, date}` — **`number` e invalido**; dias/SLA → string ISO 8601. Toda `decisionTable` com `hitPolicy`; toda tabela com row catch-all (`frozenset` fechado) → caminho humano conservador. `camunda:historyTimeToLive` namespaced (`P###D`) em cada `<decision>`. **Nenhuma DMN tem coluna de saida que negue credenciamento ou descredencie (sem variante adversa nas duas direcoes).**

### `cred_admissibility` (hitPolicy FIRST — DRAFT)
- **in:** `direcao: string`, `tipo_prestador: string`, `documentacao_completa: boolean`, `licenca_valida: boolean`, `dentro_criterios_rede: boolean`
- **out:** `roteamento: string` (`SEGUE_ANALISE` \| `PENDENTE_DOCUMENTACAO` \| `ANALISE_HUMANA`), `motivo: string`
- **Sem saida `NEGAR`/`DESCREDENCIAR` por design.** `documentacao_completa=false` → `PENDENTE_DOCUMENTACAO` (nunca negativa). Licenca aparentemente invalida / fora de criterios de rede NAO produzem negativa: roteiam para `ANALISE_HUMANA` (so o humano nega/descredencia). Catch-all (row final) → `ANALISE_HUMANA`.

### `cred_route` (hitPolicy FIRST — DRAFT; coracao adverso-like)
- **in:** `direcao: string`, `tipo_prestador: string`, `origem_solicitacao: string`, `indicio_irregularidade_sinalizado: boolean`
- **out:** `roteamento: string` (`ANALISE_CREDENCIAMENTO` \| `ANALISE_DESCREDENCIAMENTO` \| `ANALISE_HUMANA`), `motivo: string`
- **Sem variante `NEGAR`/`DESCREDENCIAR`/`AUTO_APROVAR_ADVERSO`** por design. `direcao=credenciamento` → `ANALISE_CREDENCIAMENTO` (User Task humana decide aprovar/negar). `direcao=descredenciamento` → `ANALISE_DESCREDENCIAMENTO` (User Task humana decide descredenciar/manter, apos cure-window). `indicio_irregularidade_sinalizado=true` → `ANALISE_DESCREDENCIAMENTO` (humano decide encaminhar FRAUDE; nunca auto-acusa). Catch-all → `ANALISE_HUMANA`.

> **Nota (inversao do reference):** este e o lugar onde o `Gateway_Approval` bare do `SP-PS-002_Credentialing` (`${credential_approved}`) e substituido. A DMN **nao** emite `credential_approved`; ela roteia para a User Task humana, que e a unica origem das variantes adversas.

### `cred_prior_notice` (hitPolicy UNIQUE — DRAFT; **so descredenciamento** — RN 567)
- **in:** `tipo_prestador: string`, `tem_beneficiarios_vinculados: boolean`
- **out:** `exige_notificacao_previa: boolean`, `exige_substituto_equivalente: boolean`, `prazo_notificacao: string` (ISO 8601, ex.: `P30D` — **DRAFT/verify** RN 567), `fonte_regulatoria: string`
- **Apenas determina obrigacoes regulatorias de notificacao/substituicao** (RN 567 — antecedencia minima e substituicao por equivalente em descredenciamento hospitalar). **Nao decide descredenciar.** O cure-window (event gateway) e dirigido por `prazo_notificacao`. Catch-all → `exige_notificacao_previa=true` (conservador: na duvida, exige notificacao).

### `cred_sla` (hitPolicy UNIQUE — DRAFT; todos os prazos DRAFT/verify)
- **in:** `direcao: string`, `tipo_prestador: string`
- **out:** `sla_analise: string` (ISO 8601), `sla_alerta: string` (ISO 8601), `fonte_regulatoria: string`
- Prazos como string ISO; converter dias uteis→ISO conservadoramente no worker.

## Papeis humanos (candidate groups)

> **PROPOSTO — DRAFT/verify contra a taxonomia organizacional da operadora (PO / IdP)** — ver Pendencias. Os nomes `gestao-rede`, `juridico-rede` e `coordenacao-rede` sao candidatos e podem nao corresponder aos grupos reais do IdP/console de User Tasks.

| Grupo (candidateGroups) | Papel | Tarefa |
|---|---|---|
| `gestao-rede` | Gestao de rede credenciada | `UT_AnaliseCredenciamento` (**negativa de credenciamento SO aqui**: `decisao_cred ∈ {APROVAR_CREDENCIAMENTO, NEGAR_CREDENCIAMENTO, SOLICITAR_INFO}`); `UT_AnaliseDescredenciamento` (co-candidate) |
| `juridico-rede` | Juridico de rede/contratos de prestador | `UT_AnaliseDescredenciamento` (**descredenciamento SO aqui**: `decisao_cred ∈ {DESCREDENCIAR, MANTER, SOLICITAR_INFO}`; tambem decide `encaminhar_fraude`) |
| `coordenacao-rede` | Coordenacao de rede | `UT_CoordenacaoRede` (SLA de analise estourado — assume a decisao, que continua humana) |

Toda `<bpmn:userTask>` traz `camunda:candidateGroups` (gate D1). A decisao adversa NUNCA muda de natureza por estouro de SLA: `UT_CoordenacaoRede` herda os mesmos campos obrigatorios.

Campos obrigatorios por decisao adversa (validacao de formulario/listener da User Task — sem eles a task NAO completa):
- `decisao_cred == NEGAR_CREDENCIAMENTO` → `fundamentacao` + `referencia_regulatoria`.
- `decisao_cred == DESCREDENCIAR` → `fundamentacao` + `referencia_regulatoria` + `comprovacao_notificacao_previa` + (se ha beneficiarios vinculados) `plano_substituicao`.

## SLAs

> Todos os prazos sao **DRAFT/verify** com regulatorio/juridico antes de qualquer timer em producao. Prazos legais sao em dias uteis (e por vezes corridos); ISO 8601 usa dias corridos — usar valores conservadores e resolver calendario util no worker.

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Analise (`${cred_sla.sla_analise}`) | tipico **P30D** (DRAFT/verify) | interruptivo → `agents.events.cred.sla_breached` + `UT_CoordenacaoRede` assume | politica interna / RN 566 — **DRAFT/verify** |
| Alerta de risco (`${cred_sla.sla_alerta}`) | ~50–70% do SLA | nao-interruptivo → `operadora.cred.notify_sla_risk` | politica interna — **DRAFT** |
| Notificacao previa / cure-window (`${cred_prior_notice.prazo_notificacao}`, ex. P30D) | event gateway: `msg.cred.notification_ack` **vs** timer → `UT_AnaliseDescredenciamento` (humano decide; expiracao NUNCA auto-descredencia) | RN 567 (antecedencia minima de notificacao em descredenciamento) — **DRAFT/verify** |

> O event gateway de notificacao previa **substitui/INVERTE** o `Boundary_LicenseTimeout`(P30D)→`Task_ExpireRequest` do reference: a expiracao do prazo roteia para a User Task humana, nunca para um terminal adverso automatico. **Nunca** ha auto-descredenciamento/auto-expiracao por timeout.

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_DECRED_NOT_HUMAN` | worker-guard em `operadora.cred.register_descredenciamento` | recusa executar (lanca BPMN error / falha de tarefa) sem `decisao_cred == DESCREDENCIAR` setado por humano numa User Task **e** sem `responsavel_id`+tier (ADR-0007); tambem recusa se faltar `fundamentacao`/`referencia_regulatoria`/`comprovacao_notificacao_previa` (ou `plano_substituicao` quando exigido). A instancia nao atinge `End_PrestadorDescredenciado`. Declarar `Error_DecredNotHuman`. |
| `ERR_CRED_DENIAL_NOT_HUMAN` | worker-guard em `operadora.cred.register_cred_denial` | recusa executar sem `decisao_cred == NEGAR_CREDENCIAMENTO` setado por humano **e** sem `responsavel_id`+tier; tambem recusa se faltar `fundamentacao`/`referencia_regulatoria`. A instancia nao atinge `End_CredenciamentoNegado`. Declarar `Error_CredDenialNotHuman`. |
| `ERR_CRED_INVALID_PRESTADOR` | declarado (`Error_CredPrestadorInvalido`) para uso dos workers | worker lanca BPMN error se o prestador for inconsistente na origem; tratamento a detalhar na promocao a FINAL |

> **Guarda no-adverse (parte 4 de 5 do padrao §4-bis-F / ADR-0018):** os dois `ERR_*_NOT_HUMAN` sao a ultima linha de defesa **em cada direcao adversa** — mesmo que um bug de modelagem alcance um worker adverso sem User Task humana, o worker recusa. O teste de invariante (parte 5) verifica que isto nunca acontece consultando a history do engine.

## Teste de invariante (parte 5 de 5; engine REAL — ADR-0011)

- `test_descredenciamento_exige_user_task` — varre as combinacoes de input das DMN (`cred_admissibility`/`cred_route`) e prova que nenhuma combinacao atinge `End_PrestadorDescredenciado` automaticamente; consulta `history/activity-instance` para provar que, se `End_PrestadorDescredenciado` esta no historico, **entao** `UT_AnaliseDescredenciamento` (ou `UT_CoordenacaoRede`) concluida por humano tambem esta.
- `test_negativa_credenciamento_exige_user_task` — analogo para `End_CredenciamentoNegado` ↔ `UT_AnaliseCredenciamento`.
- `test_indicio_irregularidade_roteia_para_humano` — `indicio_irregularidade_sinalizado=true` nunca auto-acusa (sem `fraud_accusation` automatico).
- `test_expiracao_notificacao_nao_auto_descredencia` — expiracao do cure-window roteia para User Task, nunca para terminal adverso.

## Notas de design / inversao do reference

- **Anti-padrao invertido** (`SP-PS-002_Credentialing.bpmn`): no reference o `Gateway_Approval` (`${credential_approved == true/false}`) auto-credencia (`Task_ActivateCredentials`) OU **auto-rejeita** (`Task_SendRejection`), e o `Boundary_LicenseTimeout`(P30D) **auto-expira** (`Task_ExpireRequest`) — tres efeitos sem humano. Em Maezo: (1) `credential_approved` deixa de existir como variavel decisoria automatizada; as DMN so roteiam (`cred_route`); (2) negativa de credenciamento e descredenciamento sao **duas** User Tasks humanas; (3) o cure-window (event-gateway de notificacao previa RN 567) substitui o boundary timeout — expiracao → User Task, nunca auto-expira/auto-descredencia.
- **Duas direcoes adversas, um processo:** ao contrario dos SP-OP single-direction (AUTH nega; CONTAS aceita glosa), CRED carrega **dois** terminais adversos e **dois** workers gated. O padrao de cinco partes e aplicado integralmente a cada direcao.
- **Emite fato de mudanca de rede:** `agents.events.cred.network_changed` e consumido por **SP-OP-ADEQUACAO-001** (adequacao geografica/dimensionamento — RN 259/566). Se ADEQUACAO for autorado antes do BUILD de CRED, deve **stubar** este fato (ver phase3-plan §W-C: "consome fato de mudanca de rede do CRED-001 — autorar CRED antes ou stub o fato").
- **Carolina (PHI zone)** e convocada via `operadora.cred.prepare_dossier` (`credentialing.analyze`); **instrui, nao decide** — a gestao de rede / juridico humano decide na User Task (principio Rafael). KPI alvo (phase3-plan): `false_decredentialing_rate == 0`.
- **TASY write DROP** em todo worker: consumimos o CDC do amh-data-platform, nunca escrevemos no Tasy (MEMORY/ADR-0013).
- **Reference e READ-ONLY / portado, nunca importado** (ADR-0011): o anti-padrao de auto-decisao e **INVERTIDO**, nao herdado.

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) e os bodies `.bpmn`/`.dmn`/`workers/cred.py` (autorados na wave de BUILD W-B, contra este contrato).
- Confirmacao de **todas** as citacoes RN com regulatorio/juridico: **RN 567** (descredenciamento — antecedencia de notificacao previa ao beneficiario/ANS; obrigacao de substituicao por equivalente; quando aplica a hospital vs demais) e **RN 566** (garantias/dimensionamento de rede); Lei 9.656 art. 17 — **DRAFT/verify** contra texto vigente ANS (podem ter sido consolidadas/substituidas).
- Confirmacao de TODOS os prazos (SLA de analise; cure-window de notificacao previa; conversao dias uteis→ISO).
- **Confirmacao dos candidate groups** `gestao-rede` / `juridico-rede` / `coordenacao-rede` contra a taxonomia organizacional (PO / IdP).
- Promocao de `provider_decredentialing` a `_hard_frozen.yaml` (L1) — sign-off arquitetura+compliance.
- Matriz de hipoteses de descredenciamento por `tipo_prestador` (hospital com beneficiarios vinculados vs prestador isolado — obrigacoes RN 567 distintas).
- Linkage com SP-OP-FRAUDE-001 (encaminhamento de indicio de irregularidade de credencial — `fraud_accusation` L0; nenhum branch auto-acusa) — jurídico + compliance.
- Contrato de payload do fato `network_changed` **harmonizado** com SP-OP-ADEQUACAO-001 (GAP-XPROC-2): o fato carrega `regiao_saude`+`especialidade`+`tipo_mudanca`+`data_efeito_iso`, consumido pela `network_change_bridge` que INICIA SP-OP-ADEQUACAO-001. **Pendente de review**: a fonte cadastral de `regiao_saude`/`especialidade` do prestador (base de rede) e a periodicidade de `ciclo_avaliacao` (trimestre vs mes — RN 259/566) — ver docs/review-queue.md.

## Registro de topicos exigido (REPORTE — nao editar `topic_registry.yaml` aqui)

Adicionar a `config/topic_registry.yaml` (dono: W0.2/orquestrador) — Kafka `agents.events.cred.*`: `received`, `pended`, `sla_breached`, `network_changed`, `completed`. External-task `operadora.cred.*`: `verify_credentials`, `check_network_criteria`, `check_prior_notice`, `prepare_dossier`, `register_cred_denial`, `register_descredenciamento`, `register_credenciamento`, `notify_sla_risk`. Message BPMN `msg.cred.notification_ack`, `msg.cred.info_received`. (`operadora.events.publish` ja registrado.) Process key `SP-OP-CRED-001` a adicionar a `KNOWN_PROCESS_KEYS` (ADR-0016) via **PR humano CODEOWNERS** (fail-closed; nao habilitado em main ate o merge).
