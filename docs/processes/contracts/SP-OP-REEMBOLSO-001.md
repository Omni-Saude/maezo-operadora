# Contrato — SP-OP-REEMBOLSO-001 (Reembolso ao Beneficiario)

**Status:** DRAFT (v0.2.0) — `DRAFT — requires human review before any deploy` (docs/review-queue.md; DL-0157)
**Fase:** 2 · **Wave:** WC.2 · **BPMN:** `spec/processes/bpmn/SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn`
**Gatilho regulatorio:** Lei 9.656/1998 art. 12 (reembolso na livre escolha / fora da rede / urgencia-emergencia: **DRAFT/verify**), prazo de reembolso (~30 dias — **DRAFT/verify**). A RN 259/2011 foi substituida pela RN 566/2022 (prazos em dias uteis); a norma que fixa o prazo de reembolso segue **nao identificada** (`docs/compliance/rn-currency-review.md`, R3). **Negar/reduzir reembolso = `authorization_denial`-class, L0 hard (`_hard_frozen.yaml`).**

> Este contrato e o ponto de sincronizacao (§4-bis-F): o BPMN/DMN/worker de REEMBOLSO derivam dele.

## O que a v0.2.0 muda (DL-0157)

O sistema so **organiza** o pedido, os documentos e a resposta. **Uma pessoa da analise decide direito e valor**, como hoje. O financeiro **paga fora do sistema** e registra `data_pagamento`. O Lucas recebe o pedido pelo WhatsApp e avisa a beneficiaria.

**Removido** (nao voltam sem um DL novo): a conferencia de cobertura/admissibilidade por maquina (`reembolso_admissibility`), o calculo do valor (`reembolso_calculo`, `calculate_amount`), a auto-aprovacao L2 (`reembolso_auto_approval`, teto D-07, bloqueio R-058), o pagamento emitido pelo sistema (`issue_payment`, CNAB) e a conferencia de prazo por worker (`check_prazo`). As pendencias D-07, R-058, R-059, R-060, R-157 e F-1 deixam de existir por perda de objeto (a tabela de reembolso da atuaria fica para uma versao futura e **nao bloqueia** esta).

**Consequencia:** `dentro_tabela`, `dentro_teto_l2`, `valor_calculado_tabela_cents`, `multiplo_tabela_aplicado`, `fonte_tabela`, `requer_avaliacao_clinica`, `cobertura_prevista`, `beneficiario_ativo`, `carencia_cumprida` e `dentro_prazo` **nao sao mais variaveis deste processo**.

## Invariante L0 hard (nao negociavel)

A negativa (ou reducao) de reembolso ao beneficiario SO nasce nas User Tasks humanas
(`UT_AnaliseReembolso`, `UT_RevisaoAuditorMedico`, `UT_CoordenacaoReembolso`). **Todo pedido passa por uma delas**: nao ha caminho de Start ate um terminal de decisao sem User Task humana (cercado por teste que le o BPMN). Nenhuma DMN deste processo possui saida de negativa/reducao.

**Terminais de decisao (todos exigem User Task humana na trilha):**
- `End_ReembolsoNegado` — `decisao_reembolso == NEGAR`.
- `End_ReembolsoParcial` — `decisao_reembolso == APROVAR_PARCIAL` (reduz o pedido: adverso), depois do registro do pagamento.
- `End_ReembolsoAprovadoAnalista` — `decisao_reembolso == APROVAR`, depois do registro do pagamento.

Terminais neutros: `End_ReembolsoCancelado` (pendencia expirada, decisao humana), `End_ReembolsoProtocoloInvalido` (guard tecnico de origem — `ERR_REEMBOLSO_INVALID_PROTOCOLO`; NAO e decisao de pagar/negar) e `End_RiscoSlaNotificado` (alerta informativo; a analise segue).

**`GW_DecisaoReembolso` nao tem flow default** (v0.1.0 tinha `APROVAR` como default): uma `decisao_reembolso` ausente ou desconhecida nao tem caminho — o motor recusa completar a tarefa e ela segue aberta. Nada vira aprovacao por omissao.

**Worker-guard:** `operadora.reembolso.send_reembolso_denial` recusa (`ERR_REEMBOLSO_DENIAL_NOT_HUMAN`) qualquer execucao sem `decisao_reembolso ∈ {NEGAR, APROVAR_PARCIAL}` setado por um humano, sem `justificativa`, `fundamentacao_contratual`, `texto_ao_beneficiario` e `analista_id` (ou `auditor_id`), e, em `APROVAR_PARCIAL`, sem valor aprovado positivo e **menor** que o solicitado (ADR-0007). O worker so **valida**: nao comunica nada.

## Business key (idempotencia)

```
REEMB-{tenant_id}-{protocolo_reembolso}
```

Uma instancia por solicitacao; reenvio da mesma solicitacao retorna a instancia ativa (`start_process_idempotent` consulta a business key antes de iniciar). O protocolo mostrado a beneficiaria e `REE-XXXX-XXXX`, derivado do id da instancia (sem PII), no molde de `CAN-XXXX-XXXX` do cancelamento.

## Variaveis de entrada

O Lucas coleta uma pergunta por vez e abre o processo **so depois** de a beneficiaria declarar que tem os documentos, enviar os arquivos e confirmar o resumo. Nao ha OCR: **`documentacao_completa` significa "declarada e enviada pela beneficiaria", nao "conferida"**; quem confere e a analise humana, que ve os anexos.

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `protocolo_reembolso` | string | sim | Protocolo da solicitacao (chave de negocio) |
| `numero_guia_tiss` | string | nao | Guia TISS de livre escolha, quando aplicavel |
| `beneficiario_pseudo_id` | string | sim | Pseudonimo do beneficiario (ADR-0006 — NUNCA CPF/nome) |
| `matricula_beneficiario` | string | sim | Matricula no plano (chave cadastral pseudonimizada) |
| `relacao_com_titular` | string | sim | `titular` \| `dependente` — para quem foi o atendimento. Nunca o nome (ADR-0006) |
| `tipo_reembolso` | string | sim | `livre_escolha` \| `fora_rede` \| `urgencia_emergencia` \| `indisponibilidade_rede` |
| `motivo_nao_rede` | string | sim | Motivo informado para nao usar a rede, em conjunto fechado (ex.: `urgencia`, `sem_prestador_no_prazo`, `preferencia_fora_rede`). Nunca texto livre nem diagnostico |
| `categoria_procedimento` | string | sim | `consulta` \| `exame` \| `terapia` (a DMN de SLA ainda reconhece `internacao`/`opme`/`alta_complexidade`, hoje nao coletadas) |
| `codigo_procedimento_tuss` | string | nao | Opcional: o beneficiario nao conhece o TUSS (era obrigatorio na v0.1.0) |
| `cid10` | string | nao | Opcional; o Lucas **nunca pergunta diagnostico** |
| `valor_solicitado_cents` | integer | sim | Valor pago pela beneficiaria, em centavos de BRL (inteiro — nunca `number`) |
| `data_atendimento` | date | sim | Data do atendimento/despesa |
| `data_solicitacao` | date | sim | Data da solicitacao (base de contagem de prazo) |
| `documentos_refs` | json | sim | Lista de `{tipo: pedido_medico \| nota_fiscal, document_ref, sha256}`. Aceita **mais de um arquivo por tipo**. `document_ref` (`rdoc_<32hex>`) aponta para a custodia S3 da Zona PHI (DL-0159, ADR-0006); o engine nunca recebe o arquivo. `sha256` e' o do conteudo (principio do `input_hash`, ADR-0020): o portal recusa entregar um objeto que nao confere |
| `aviso_beneficiario_whatsapp` | boolean | nao | `true` so quando o Lucas abriu o pedido com a flag `MAEZO_REEMBOLSO_V1` ligada. Sem ela (outra origem) o processo segue calado |
| `agent_id` | string | nao | `lucas` quando ha aviso |
| `conversation_id` | string | nao | Conversa do WhatsApp para o aviso |

**Resolvida por worker:** `dias_desde_atendimento` (integer; `data_solicitacao − data_atendimento`, `-1` se nao calculavel) por `operadora.reembolso.check_coverage`. **Resolvida por DMN:** `situacao_prazo` (`prazo.situacao_prazo`). Nenhuma delas decide.

## Variaveis de saida

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_reembolso` | string | `APROVAR` \| `NEGAR` \| `APROVAR_PARCIAL` \| `SOLICITAR_INFO` \| `SOLICITAR_AUDITOR` (User Tasks humanas). `SOLICITAR_AUDITOR` e o botao "escalar" do analista (merito clinico) |
| `valor_reembolso_aprovado_cents` | integer | Valor aprovado pelo humano. **Obrigatorio e positivo** em `APROVAR` e `APROVAR_PARCIAL` (o formulario do portal ja exige); em `APROVAR_PARCIAL` deve ser menor que o solicitado |
| `justificativa` | string | **Interna.** Obrigatoria em `NEGAR` e `APROVAR_PARCIAL` |
| `fundamentacao_contratual` | string | **Interna.** Obrigatoria em `NEGAR` e `APROVAR_PARCIAL` |
| `texto_ao_beneficiario` | string | **O que o Lucas leva a beneficiaria.** Obrigatorio em `NEGAR` e `APROVAR_PARCIAL`, escrito pelo analista, **distinto** de `justificativa`/`fundamentacao_contratual` (que podem citar clausula e dado clinico) |
| `info_solicitada` | string | A pergunta do analista a beneficiaria. Obrigatoria em `SOLICITAR_INFO` |
| `cid10_referencia` | string | Obrigatoria se NEGAR/APROVAR_PARCIAL por merito clinico |
| `parecer_auditor` | string | Obrigatoria se a negativa/reducao decorre de `UT_RevisaoAuditorMedico` |
| `decisao_pendencia` | string | `cancelar_solicitacao` \| `conceder_prazo_extra` \| `seguir_analise` (pendencia expirada — humano decide) |
| `data_pagamento` | date | Registrada pelo financeiro em `UT_RegistrarPagamento` ("pago em DD/MM"), ISO `AAAA-MM-DD` |
| `analista_id` / `auditor_id` | string | Referencia PSEUDONIMA de quem concluiu a decisao no portal, preenchida pelo SERVIDOR (o navegador nunca escolhe). Exigida pela guarda L0 na negativa/reducao (DL-0159) |
| `aguardando_resposta` | boolean | `true` ao entrar na espera da resposta da beneficiaria (`ST_PublishReembolsoPended`), `false` ao (re)preparar o dossie e na correlacao. E' a UNICA variavel que o Lucas le' para saber se uma mensagem dela e' a resposta (DL-0159) |
| `resposta_beneficiario` | string | A resposta da beneficiaria a um pedido de informacao (texto ja' pelo scrub de identificadores, ate' 1000 caracteres), entregue pela correlacao de `msg.reembolso.docs_received`. PHI: so' exibida ao analista |
| `documentos_adicionais_refs` | json | Os arquivos da resposta (`{tipo: complemento, document_ref, sha256}`), na mesma correlacao. Uma nova rodada de pedido de informacao substitui a lista anterior (o analista ve' a ultima) |

`texto_ao_beneficiario` e `info_solicitada` sao **PHI** e **nunca** viajam no evento de aviso: o consumidor os le do historico do motor (`PHI_PROCESS_VARS`).

## Topicos

> Convencao `{dominio}.{contexto}.{acao}`. Kafka = fatos de dominio. External-task = `operadora.reembolso.*`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.reembolso.received` | produz | apos start |
| Kafka | `agents.events.reembolso.pended` | produz | pendencia aberta (`SOLICITAR_INFO`) |
| Kafka | `agents.events.reembolso.sla_breached` | produz | SLA de analise estourado |
| Kafka | `agents.events.reembolso.completed` | produz | fim (`payload.desfecho` = `aprovado_analista` \| `aprovado_parcial` \| `negado_analista` \| `cancelado_pendencia`) |
| Kafka | `agents.events.process_completed` | produz | **aviso ao beneficiario** (so com `aviso_beneficiario_whatsapp=true`): `tenant_id`, `agent_id`, `conversation_id`, `resultado` ∈ `{reembolso_aprovado, reembolso_parcial, reembolso_negado, reembolso_info_solicitada, reembolso_pago}` |
| External task | `operadora.events.publish` | consome | publicador generico de eventos de dominio e avisos |
| External task | `operadora.reembolso.check_coverage` | consome | `ST_ValidarSolicitacao`: guard de protocolo + resolve `dias_desde_atendimento` |
| External task | `operadora.reembolso.analyze_request` | consome | `ST_PrepararDossie`: convoca o agente analista (Marina) para montar o dossie. Recebe `situacao_prazo` por `camunda:inputParameter` (`${prazo.situacao_prazo}`) |
| External task | `operadora.reembolso.request_documents` | consome | registra que a etapa de pendencia rodou e retorna `{}` — **NAO afirma `notified`** (o aviso sai pelo Lucas) |
| External task | `operadora.reembolso.send_reembolso_denial` | consome | **efeito adverso validado** — `ST_EnviarNegativa` e `ST_ComunicarReducao` (guard `ERR_REEMBOLSO_DENIAL_NOT_HUMAN`) |
| External task | `operadora.reembolso.notify_sla_risk` | consome | registra a etapa de alerta de risco de SLA e retorna `{}` — nenhum canal e contatado (FAB-SLA-RISK-NOTIFIED-SLICE4) |
| Message BPMN | `msg.reembolso.docs_received` | recebe | correlacao por business key; **a resposta volta direto para `ST_PrepararDossie`/analise** (nao ha mais admissibilidade). Primeiro produtor real: o Lucas |

## DMN referenciadas

> Shape engine-deployavel (§4-bis-A): typeRef ∈ {string, boolean, integer, long, double, date}; dias/SLA → string ISO 8601. Todo decisionTable tem `hitPolicy`; toda tabela tem linha catch-all. **Nenhuma DMN tem coluna de saida de negativa/reducao.**

### `reembolso_prazo` (hitPolicy FIRST — DRAFT, informativa) — arquivo `spec/processes/dmn/reembolso_prazo.dmn`
- **in:** `dias_desde_atendimento: integer`
- **out:** `situacao_prazo: string` (`DENTRO` | `FORA` | `NAO_AVALIADO`)
- **So informativa**: alimenta o dossie do analista e **nao roteia** (todo pedido vai para a analise humana). Hoje so tem o catch-all `NAO_AVALIADO`, porque o prazo contratual/regulatorio **nao esta confirmado**. Quando o juridico definir, entram as linhas `DENTRO`/`FORA` (o prazo vive na DMN, nunca em Python). A coluna e hoje "dead input" no baseline de `test_dmn_dead_inputs_fence`; sai de la quando as linhas entrarem.

### `reembolso_sla` (hitPolicy FIRST — DRAFT, todos os prazos DRAFT/verify) — arquivo `spec/processes/dmn/reembolso_sla.dmn`
- **in:** `tipo_reembolso: string`, `categoria_procedimento: string`
- **out:** `sla_analise: string` (ISO 8601), `sla_alerta: string` (ISO 8601), `fonte_regulatoria: string`
- Inalterada na v0.2.0. A DMN ainda cita a RN 259/2011 (substituida pela RN 566/2022): revisao pendente com o regulatorio.

## Papeis humanos (candidate groups)

> Nomes de grupo **PROPOSTOS** — confirmar contra a taxonomia organizacional do operador. Todo `userTask` carrega `camunda:candidateGroups`.

| Grupo (candidateGroups) | Papel | Tarefa |
|---|---|---|
| `analise-reembolso` | Analista de reembolso | `UT_AnaliseReembolso` (todo pedido passa aqui), `UT_DecidirPendenciaExpirada` |
| `medico-auditor` | Medico auditor | `UT_RevisaoAuditorMedico` (merito clinico — `SOLICITAR_AUDITOR`) |
| `coordenacao-reembolso` | Coordenacao de reembolso | `UT_CoordenacaoReembolso` (SLA de analise estourado — assume e decide) |
| `financeiro-reembolso` | Financeiro | `UT_RegistrarPagamento` (paga fora do sistema e registra `data_pagamento`) — **grupo novo na v0.2.0** |

Campos obrigatorios por decisao (validacao do formulario do portal e do guard do worker — sem eles a task nao completa):
- `APROVAR` → `valor_reembolso_aprovado_cents` (> 0).
- `APROVAR_PARCIAL` → `valor_reembolso_aprovado_cents` (< `valor_solicitado_cents`) + `justificativa` + `fundamentacao_contratual` + `texto_ao_beneficiario`.
- `NEGAR` → `justificativa` + `fundamentacao_contratual` + `texto_ao_beneficiario` (+ `cid10_referencia`/`parecer_auditor` no merito clinico).
- `SOLICITAR_INFO` → `info_solicitada`.

## Bindings de agente (arbitragem `PERSP-REEMBOLSO-BINDING`, revista por DL-0157/DL-0158)

**Lucas** declara `SP-OP-REEMBOLSO-001` em `process_keys:` (`spec/agents/lucas/agent.yaml`) e abre o pedido (flag `MAEZO_REEMBOLSO_V1`, desligada por padrao), no molde do cancelamento.

**Marina NAO declara a chave — por desenho, nao por omissao** (menor privilegio): ela e convocada por **delegacao A2A** (`operadora.reembolso.analyze_request`) de dentro de uma instancia ja rodando e nunca inicia a propria. Fato estrutural (parse de YAML, nao grep de substring): a chave esta em `process_keys:` do Lucas e **so dele**. Cercado por `tests/unit/gateway/test_reembolso_sem_binding_de_agente.py`.

## SLAs

> Todos os prazos sao **DRAFT/verify** com regulatorio/juridico antes de qualquer timer em producao. Prazos legais sao em dias uteis; ISO 8601 usa dias corridos — valores conservadores.

| Timer | Valor (DRAFT) | Tipo | Fonte |
|---|---|---|---|
| Analise (padrao) | P30D | interruptivo → `UT_CoordenacaoReembolso` assume | prazo de reembolso ~30 dias — **DRAFT/verify** (RN 566/2022 / Lei 9.656 art. 12) |
| Analise (urgencia/emergencia) | P5D | idem | **DRAFT/verify** |
| Alerta de risco | DMN `sla_alerta` | nao-interruptivo → `notify_sla_risk` | politica interna |
| Pendencia de informacao/documentos | P5D | `GW_AguardarDocs` (mensagem OU timer) → `UT_DecidirPendenciaExpirada` (humano decide destino) | **DRAFT/verify** (suspensao de prazo durante pendencia) |

`conceder_prazo_extra` volta a `GW_AguardarDocs` (rearma o timer); `seguir_analise` volta a `ST_PrepararDossie`; `cancelar_solicitacao` encerra com desfecho neutro. **A expiracao nunca auto-nega.**

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_REEMBOLSO_DENIAL_NOT_HUMAN` | guard de `send_reembolso_denial` | recusa sem decisao humana completa (ver "Invariante L0"). Nao e BPMN error modelado: vira incidente (`failure(retries=0)`) |
| `ERR_REEMBOLSO_INVALID_PROTOCOLO` | `check_coverage` (`WorkerBpmnError`) | capturado por `BE_ReembolsoProtocoloInvalido` em `ST_ValidarSolicitacao` → `End_ReembolsoProtocoloInvalido` (GAP-REEMBOLSO-2; fail-safe tecnico, NAO decisao de pagar/negar) |
| `ERR_EVENT_PUBLISH_FAILED` | boundary de cada `ST_Avisar*` | a falha do aviso nao trava o processo: segue para o proximo passo |

Removidos na v0.2.0: `ERR_REEMBOLSO_CALCULO_DMN_INDISPONIVEL`, `ERR_REEMBOLSO_VALOR_PAGAMENTO_INVALIDO`, `ERR_REEMBOLSO_AUTO_PAGAMENTO_BLOQUEADO`.

## Avisos ao beneficiario (pelo Lucas)

Cada aviso e um fato sem PHI publicado por `ST_Avisar*` (gateway `aviso_beneficiario_whatsapp == true`). O consumidor (`agent_resume`) confere o historico do motor antes de avisar, respeita a **janela de 24h da Meta** (fora dela ninguem e avisado e a equipe e alertada) e envia:

| `resultado` | Quando | Texto |
|---|---|---|
| `reembolso_aprovado` | decisao `APROVAR` | fixo: aprovado, valor e previsao de pagamento |
| `reembolso_parcial` | decisao `APROVAR_PARCIAL` | `texto_ao_beneficiario` do analista, com as cercas de saida |
| `reembolso_negado` | decisao `NEGAR` | `texto_ao_beneficiario` do analista, com as cercas de saida, e como pedir revisao |
| `reembolso_info_solicitada` | decisao `SOLICITAR_INFO` | `info_solicitada` do analista, com as cercas de saida |
| `reembolso_pago` | apos `UT_RegistrarPagamento` | fixo: pagamento feito |

Texto livre do analista numa comunicacao adversa exige **revisao juridica antes de producao**.

## Pendencias para promocao a FINAL

- Revisao do juridico dos textos de "nao aprovado" e "parcial" e das condicoes dos casos legais (urgencia fora da rede, falta de prestador no prazo).
- Definicao do prazo de reembolso (preencher `reembolso_prazo.dmn`) e confirmacao dos prazos de `reembolso_sla.dmn`; base RN 566/2022.
- Template Meta para aviso fora da janela de 24h.
- Pessoas reais nos grupos `analise-reembolso`, `medico-auditor`, `coordenacao-reembolso` e `financeiro-reembolso`; confirmacao dos nomes contra a taxonomia organizacional.
- Zona PHI para os comprovantes (foto/PDF): custodia em S3 com SSE-KMS implementada para dev (DL-0159; JPG/PNG/PDF ate' 10 MiB); a retencao de 5 anos (decisao do dono — DL-0158) e a disposicao dos cinco textos humanos (`phi_completeness.py::DISPOSITIONS`) sao do DPO.
- Politica de suspensao de prazo em pendencia.

## Tópicos a registrar (REPORTE)

Kafka `agents.events.reembolso.{received,pended,sla_breached,completed}`; external-task `operadora.reembolso.{check_coverage,analyze_request,request_documents,send_reembolso_denial,notify_sla_risk}`; message BPMN `msg.reembolso.docs_received`. Removidos: `operadora.reembolso.{check_prazo,calculate_amount,issue_payment}`.

## Anti-pattern invertido (ADR-0011, reference READ-ONLY)

O `../maezo-reference` so possui um `doctor_reimbursement_summary_worker.py` (extrato ao prestador — **nao** e adjudicacao ao beneficiario). Este processo e **greenfield**. O padrao a evitar (auto-aplicar efeito adverso por timeout ou por regra) esta **invertido** aqui: nenhuma regra decide, nenhum timer nega; toda negativa/reducao e User Task humana.
