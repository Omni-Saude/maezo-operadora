# Contrato — SP-OP-FRAUDE-001 (Investigacao de Fraude — Cadeia de Custodia)

**Status:** DRAFT (v0.1.0) — `DRAFT — requires human review (medico-auditor/juridico/DPO/regulatorio/financas/PO) before any deploy` (docs/review-queue.md)
**Fase:** 3 (PATHFINDER — classe nova: cadeia de custodia / selagem de evidencia) · **Wave:** W-A (quadrupla SOZINHA, igual CONTAS em Phase 2) · **BPMN (a autorar contra este contrato):** `spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn`
**Gatilho regulatorio:** investigacao de indicio de fraude/abuso com possivel referral a ANS, esfera civel e/ou penal. RN 593/2023 (cancelamento/rescisao quando ha fraude — interacao com SP-OP-CANCEL-001/INADIMPLENCIA-001: **DRAFT/verify**); RN 567/2018 (descredenciamento — quando a investigacao referencia prestador, interacao com SP-OP-CRED-001: **DRAFT/verify**); Lei 9.656/1998 art. 13 (fraude como hipotese de rescisao); LGPD (tratamento de dado em investigacao + retencao probatoria); Codigo Penal / Codigo de Processo Civil (cadeia de custodia probatoria — **DRAFT/verify** com juridico). **TODAS as citacoes de RN/Lei e prazos sao DRAFT/verify com regulatorio+juridico antes de qualquer timer/efeito em producao.**

> **Classe nova (pathfinder):** este e o primeiro SP-OP a materializar **selagem de evidencia tamper-evident ANTES da decisao humana** (cadeia de custodia). O `bundle_root` (Merkle sobre os hashes de registro ordenados do dossie) e gravado na hash-chain ADR-0007 ANTES de `UT_DecisaoInvestigador`, de modo que a decisao humana recai sobre um corpus selado e verificavel (ANS/judicialmente defensavel). Nao forka a hash-chain — projeta sobre ela (`src/maezo/gateway/custody.py`). Ver ADR-0020 (cadeia de custodia — PROPOSTO em W0) e ADR-0007 (auditoria/nao-repudio).

> **Sink convergente:** FRAUDE-001 e o destino unico de todos os handoffs `encaminhar_fraude` de Phase 2 (CONTAS/RECURSO/REEMBOLSO/CANCEL/NIP, onde um humano — nunca automacao — setou `encaminhar_fraude=true`). FRAUDE **nunca le de volta / bloqueia / pre-empta** uma decisao de glosa, recurso, reembolso ou cancelamento: e estritamente downstream. A costura de Phase 2 (`indicio_fraude_sinalizado` INFORMATIVO; humano seta `encaminhar_fraude`) e preservada — ver §"Costura com Phase 2 (sink convergente)".
>
> **Estado da costura (DRAFT/verify):** hoje apenas **SP-OP-CONTAS-001** (Phase 2, em main) declara explicitamente a variavel/seam `encaminhar_fraude` (+ `indicio_fraude_sinalizado`). Os equivalentes em RECURSO/REEMBOLSO/CANCEL/NIP sao **a ligar** na wave de BUILD (handoff humano simetrico ao de CONTAS) — **DRAFT/verify**; este contrato declara o sink convergente alvo, nao afirma que as 5 origens ja emitem o handoff. Ver Pendencias.

**Inverte o anti-padrao:** `../maezo-reference/healthcare_platform/revenue_cycle/coding/workers/detect_fraud_worker_v2.py` (topic `revenue_cycle.coding.detect_fraud`) e suas 7 DMNs `fraud_scoring/*` (`upcoding_complexity_ceiling`, `unbundling_partial_bundles`, `phantom_no_diagnosis`, `phantom_suspicious_prefix`, `frequency_zscore_threshold`, `provider_peer_deviation`, `risk_thresholds`) + `coding_operations/fraud_detection.dmn` + `coding_audit/finalization_gates/fraud_clearance.dmn`. No reference o worker **soma** scores das 7 DMNs e, quando o `recommendation` de `risk_thresholds` e `"flag"`, **lanca o BPMN error `FRAUD_DETECTED`** (veredito automatico por score) e `fraud_clearance.dmn` emite `BLOQUEAR`/`PROSSEGUIR`. Em Maezo isto e INVERTIDO: o score de fraude e **FATO DE ROTEAMENTO, NUNCA veredito**; nao existe `FRAUD_DETECTED` nem coluna de saida `ACUSAR`/`BLOQUEAR`/`CONFIRMAR`; o scoring de referencia entra **apenas como input de montagem de dossie** ("indicadores presentes"), e a acusacao so nasce na User Task humana `UT_DecisaoInvestigador`. Ver §"Notas de design / inversao do reference".

## Invariante L0 hard (nao negociavel) — `fraud_accusation`

`fraud_accusation` e **L0 hard** (`_hard_frozen.yaml`, CI-enforced; ADR-0008/0018; nao rebaixavel por overlay de tenant). NENHUM caminho automatizado acusa fraude, constitui dolo, descredencia prestador, rescinde contrato por fraude, refere a ANS/civel/penal nem produz qualquer efeito adverso decorrente de fraude. A **acusacao de fraude SO nasce na User Task humana `UT_DecisaoInvestigador`** (grupo `investigacao-fraude`), via `decisao_fraude == ACUSAR_FRAUDE`, e SO depois que o `bundle_root` do dossie ja foi **selado na hash-chain** (decisao sobre corpus verificavel).

O **score de fraude e um FATO DE ROTEAMENTO, NUNCA um veredito.** Nenhuma DMN deste processo possui coluna de saida que acuse/confirme fraude ou recomende um efeito adverso. As DMNs apenas **montam indicadores e roteiam** (intensidade de investigacao, grupo, SLA). Indicio ambiguo, score alto, indicador presente, indisponibilidade de DMN, snapshot de feature store indisponivel e estouro de SLA **todos fail-safe para a User Task humana** via row catch-all → `INVESTIGACAO_HUMANA` (allowlist fechada). O efeito adverso e materializado apenas pelo worker `operadora.fraude.register_fraud_accusation`, guardado por `ERR_FRAUD_ACCUSATION_NOT_HUMAN`, que recusa executar se `decisao_fraude != ACUSAR_FRAUDE` setado por humano (carrega `investigator_id` + `tier` na cadeia de auditoria ADR-0007). O terminal `End_FraudeConfirmadaHumano` (e os terminais de referral derivados) so e alcancavel apos `UT_DecisaoInvestigador` concluida por humano sobre um dossie selado.

As cinco partes do padrao estrutural no-adverse (ADR-0018), materializadas neste processo:

1. **Tipos de rota/decisao sem variante adversa (agente + BPMN).** `intensidade_investigacao`/`grupo_investigador` e os enums de roteamento NAO contem `ACUSAR`/`FRAUD_DETECTED`/`BLOQUEAR`/`DESCREDENCIAR`. O score e os indicadores nao sao expressaveis como decisao automatizada; `decisao_fraude` so existe como valor que `UT_DecisaoInvestigador` (humano) pode setar.
2. **Tabelas DMN sem coluna/saida de acusacao.** Nenhuma `decisionTable` confirma/acusa fraude; as DMNs `fraude_*` montam indicadores e roteiam (ADR-0012). Shape engine-deployavel (`typeRef ∈ {string, boolean, integer, long, double, date}`; **`number` invalido** — inverte o `score: number` do reference; score interno em `integer`; SLA em string ISO 8601; `camunda:historyTimeToLive` namespaced).
3. **Fail-safe a `UT_DecisaoInvestigador` (allowlist fechada).** Toda ambiguidade, score alto, indicador presente, DMN indisponivel, snapshot ausente e estouro de SLA roteia para a User Task humana; row catch-all (ultima) → `INVESTIGACAO_HUMANA`. Sem rota de "default permissivo" que escape o humano. **Nunca** ha auto-acusacao por score nem auto-passagem por timeout.
4. **Worker guard `ERR_FRAUD_ACCUSATION_NOT_HUMAN` (o worker transmite, nunca decide).** `register_fraud_accusation` lanca BPMN error e recusa registrar a acusacao a menos que (a) `decisao_fraude == ACUSAR_FRAUDE` setado por humano em `UT_DecisaoInvestigador`, (b) `investigator_id` + `tier` presentes, (c) campos de fundamentacao presentes, e (d) `bundle_root` selado verificavel na chain. Defesa em profundidade.
5. **Teste de integracao invariante na historia do engine (engine REAL).** Varre as combinacoes de input das DMNs `fraude_*` e prova que nenhuma atinge `End_FraudeConfirmadaHumano`/terminal de referral automaticamente; consulta `history/activity-instance` provando co-ocorrencia obrigatoria com `UT_DecisaoInvestigador` humana; e prova a selagem-antes-da-decisao (custody-seal-before-decision). Ver test-spec.

### Terminais humano-gated (nomeados — §4-bis-F)

| End event | Natureza | So alcancavel via |
|---|---|---|
| `End_FraudeConfirmadaHumano` | **ADVERSO** — investigador constitui indicio de fraude/dolo (constitui `fraud_accusation`) | `UT_DecisaoInvestigador` com `decisao_fraude=ACUSAR_FRAUDE` (humano) sobre `bundle_root` selado |
| `End_EncaminhadoJuridico` | **ADVERSO (downstream)** — referral a juridico/ANS/civel/penal apos acusacao humana | so a jusante de `End_FraudeConfirmadaHumano` (worker `refer_to_legal` gated) |
| `End_EncaminhadoCredenciamento` | **ADVERSO (downstream)** — handoff a SP-OP-CRED-001 (descredenciamento) quando a acusacao referencia prestador | so a jusante de acusacao humana; **CRED-001 tem sua propria UT adversa** (nunca auto-descredencia) |
| `End_EncaminhadoContratual` | **ADVERSO (downstream)** — handoff a SP-OP-CANCEL-001/INADIMPLENCIA-001 (rescisao por fraude) quando referencia beneficiario/contrato | so a jusante de acusacao humana; **CANCEL/INADIMPLENCIA tem sua propria UT adversa** |
| `End_ArquivadoSemIndicio` | neutro — investigador decide arquivar (sem indicio suficiente) | `UT_DecisaoInvestigador` com `decisao_fraude=ARQUIVAR` (humano) |
| `End_MonitorarSemAcao` | neutro — manter sob monitoramento, sem efeito adverso | `UT_DecisaoInvestigador` com `decisao_fraude=MONITORAR` (humano) |

> **Fronteira L0 (OQ — ver Pendencias):** se `decisao_fraude=ACUSAR_FRAUDE` (o ato do investigador) JA constitui `fraud_accusation`, ou se a acusacao e o ato *downstream* (`End_EncaminhadoJuridico`). Default conservador DRAFT: **ambos** sao adversos e ambos exigem a UT humana + `bundle_root` selado; os terminais de referral so sao alcancaveis a jusante de `End_FraudeConfirmadaHumano`. Confirmar com juridico onde desenhar a fronteira.

Os terminais ADVERSOS NUNCA aparecem na history do engine sem `UT_DecisaoInvestigador` humana concluida E sem `bundle_root` selado na chain ANTES da UT (teste de invariante + teste de integridade de custodia — ver test-spec).

## Cadeia de custodia (a classe nova — selagem antes da decisao)

> Materializa o D4 do Phase 3 plan e o WP3.1. Projecao sobre a hash-chain ADR-0007, **nao** fork.

- `src/maezo/gateway/custody.py` expoe `CustodyBundle` — projecao tipada sobre `AuditRecord`/`verify_chain` existentes (ADR-0007). O `bundle_root` e a raiz Merkle calculada sobre os `record_hashes` **ordenados** dos itens do dossie (cada item = referencia/ponteiro pseudonimizado a uma evidencia, mais seu hash — **nunca o conteudo PHI**).
- **Sequencia obrigatoria:** (1) montagem do dossie (`assemble_dossier`) → (2) calculo do `bundle_root` (`seal_bundle`) → (3) **gravacao do `bundle_root` de volta na hash-chain ADR-0007 ANTES de criar `UT_DecisaoInvestigador`** → (4) `UT_DecisaoInvestigador` (decisao sobre corpus selado) → (5) se `ACUSAR_FRAUDE`, `register_fraud_accusation` (gated) re-verifica que o `bundle_root` selado bate antes de registrar o efeito.
- Propriedades verificaveis: **tamper-evidence** (qualquer alteracao/reordenacao de item muda o `bundle_root`; o registro selado na chain nao bate); **no-PHI-in-custody** (o bundle carrega ponteiros + hashes pseudonimizados, nunca PHI bruto — ADR-0006 Zona Geral); **reprodutibilidade** (o bundle referencia um snapshot congelado do feature store — ver OQ sobre janela de 90d). 
- Worker `operadora.fraude.seal_custody_bundle` grava o `bundle_root` na chain e publica `agents.events.fraude.custody_sealed` com `bundle_root` + `record_count` (sem PHI) ANTES da UT. O gate de fluxo `GW_CustodiaSelada` impede que `UT_DecisaoInvestigador` seja criada sem `bundle_root` selado.

## Business key (idempotencia)

```
FRAUDE-{tenant_id}-{numero_caso}
```

`numero_caso` e o identificador estavel do caso de investigacao (gerado no intake; **DRAFT/verify** com produto qual e o gerador/owner do caso). Uma instancia ativa por caso; reenvio do mesmo caso (ex.: um segundo `encaminhar_fraude` de Phase 2 sobre o mesmo prestador/beneficiario ja em investigacao) retorna a instancia ativa e **anexa a evidencia ao dossie do caso existente** (consolidacao no sink — ver Costura com Phase 2). `mcp-cibseven.start_process` DEVE consultar a business key antes de iniciar (start idempotente; nao duplica investigacao do mesmo caso).

> **Regra de correlacao de caso (DRAFT/verify):** como Phase 2 emite `encaminhar_fraude` por entidade investigada (prestador, beneficiario, contrato), a derivacao de `numero_caso` a partir da entidade + competencia precisa de confirmacao de produto/juridico (um caso por prestador-competencia? por evento? por entidade-aberta?). Ate o sign-off, manter `numero_caso` como chave fornecida pelo intake.

## Variaveis de entrada

| Variavel | Tipo | Obrigatoria | Descricao |
|---|---|---|---|
| `tenant_id` | string | sim | Tenant (ex.: `amh`) |
| `numero_caso` | string | sim | Identificador do caso de investigacao (chave de negocio) |
| `origem_encaminhamento` | string | sim | `contas` \| `recurso` \| `reembolso` \| `cancel` \| `nip` \| `cdc_proativo` \| `denuncia` \| `auditoria` (de onde veio o `encaminhar_fraude`) |
| `entidade_tipo` | string | sim | `prestador` \| `beneficiario` \| `contrato` \| `rede` (quem/que e investigado) |
| `entidade_pseudo_id` | string | sim | Pseudonimo da entidade investigada (ADR-0006 — NUNCA CPF/CNPJ/nome real) |
| `prestador_id` | string | nao | Id do prestador investigado (pseudonimizado), quando `entidade_tipo=prestador` |
| `beneficiario_pseudo_id` | string | nao | Pseudonimo do beneficiario, quando `entidade_tipo=beneficiario` |
| `numero_contrato` | string | nao | Contrato referenciado, quando `entidade_tipo=contrato` (handoff a CANCEL/INADIMPLENCIA) |
| `encaminhado_por_id` | string | sim | Id do **humano** de Phase 2 que setou `encaminhar_fraude` (auditoria de procedencia — nunca um worker) |
| `competencia` | string | nao | Competencia de referencia da evidencia (`YYYY-MM`) |
| `evidencia_refs` | json | sim | Referencias (ponteiros pseudonimizados) das evidencias do dossie — itens TISS/CDC/feature-store; **nunca PHI bruto** (pode ser vazio no intake e crescer) |
| `feature_snapshot_ref` | string | nao | Referencia ao snapshot congelado do feature store (`claim_features`/`provider_features`) usado na montagem do dossie (reprodutibilidade — ver OQ 90d) |
| `indicadores_presentes` | json | nao | Lista de indicadores de fraude **presentes** (rotulos de regra), pre-resolvida por worker de montagem a partir do scoring portado (`detect_fraud` v2 invertido) — **fato de montagem, NUNCA veredito** |
| `score_indicadores` | integer | nao | Score agregado dos indicadores (FATO DE ROTEAMENTO; **integer**, nunca `number`/`double`) — pre-resolvido por worker; **NUNCA decide acusacao** |
| `indicio_fraude_sinalizado` | boolean | nao | Sinal **informativo** herdado de Phase 2 (NUNCA decide; preservado para auditoria de procedencia) |

> Notas de tipo: `score_indicadores` e `integer` (contagem/pontuacao agregada — inverte o `score: number` do reference). Valores monetarios eventualmente envolvidos (impacto estimado do indicio) serao `double`/inteiro-centavos na promocao a FINAL — **nunca `number`**. NENHUM campo de entrada carrega PHI bruto (ADR-0006).

## Variaveis de saida (preenchidas pela User Task humana, exceto onde indicado)

| Variavel | Tipo | Descricao |
|---|---|---|
| `decisao_fraude` | string | `ACUSAR_FRAUDE` \| `ARQUIVAR` \| `MONITORAR` \| `SOLICITAR_DILIGENCIA` (preenchida SO por `UT_DecisaoInvestigador` humana — unica origem) |
| `fundamentacao_investigacao` | string | **Obrigatoria** se `ACUSAR_FRAUDE` — fundamentacao da constituicao do indicio sobre o dossie selado |
| `indicadores_fundamentantes` | json | **Obrigatoria** se `ACUSAR_FRAUDE` — quais indicadores do dossie sustentam a acusacao (citacao de evidencia, ADR-0007 `decision_basis`) |
| `referencia_normativa` | string | **Obrigatoria** se `ACUSAR_FRAUDE` — fundamento normativo do referral (ANS/civel/penal — DRAFT/verify juridico) |
| `investigator_id` | string | Id do humano investigador que decidiu (cadeia de auditoria ADR-0007) — carregado no worker do efeito adverso |
| `tier` | string | Alcada/tier do investigador que decidiu (DRAFT — ver escada de alcada) — carregado no audit chain |
| `destino_referral` | json | **Obrigatoria** se `ACUSAR_FRAUDE` — destinos do referral: `{juridico?, ans?, cred?(prestador), contratual?(beneficiario/contrato)}` |
| `destino_referral_juridico` | boolean | GAP-FRAUDE-5 — flag PLANA de gateway, espelho de `destino_referral.juridico` (idioma `encaminhar_fraude` do CONTAS). Seta por `UT_DecisaoInvestigador` (e confirmada/ajustada por `UT_RevisaoReferral`); consumida por `GW_DestinoReferral` (`${destino_referral_juridico == true \|\| destino_referral_ans == true}` — referral juridico/ANS) |
| `destino_referral_ans` | boolean | GAP-FRAUDE-5 — flag PLANA de gateway, espelho de `destino_referral.ans`. Seta por `UT_DecisaoInvestigador`/`UT_RevisaoReferral`; consumida por `GW_DestinoReferral` (ver `destino_referral_juridico`) |
| `destino_referral_cred` | boolean | GAP-FRAUDE-5 — flag PLANA de gateway, espelho de `destino_referral.cred`. So relevante quando `entidade_tipo=prestador`. Seta por `UT_DecisaoInvestigador` (confirmada por `UT_RevisaoReferral`); consumida por `GW_DestinoReferral` (`${entidade_tipo == 'prestador' && destino_referral_cred == true}` — dispara `operadora.fraude.start_credenciamento`) |
| `destino_referral_contratual` | boolean | GAP-FRAUDE-5 — flag PLANA de gateway, espelho de `destino_referral.contratual`. So relevante quando `entidade_tipo ∈ {beneficiario, contrato}`. Seta por `UT_DecisaoInvestigador` (confirmada por `UT_RevisaoReferral`); consumida por `GW_DestinoReferral` (`${(entidade_tipo == 'beneficiario' \|\| entidade_tipo == 'contrato') && destino_referral_contratual == true}` — dispara `operadora.fraude.start_contratual`) |
| `bundle_root` | string | Raiz Merkle do dossie selado (preenchida por `operadora.fraude.seal_custody_bundle` ANTES da UT; re-verificada no efeito adverso) |
| `decisao_diligencia` | string | `prosseguir` \| `arquivar` \| `escalar` (diligencia/SLA — humano `coordenacao-investigacao`) |

## Topicos

Convencao `{dominio}.{contexto}.{acao}`. Kafka = fatos de dominio (`agents.events.fraude.*`); external-task = `operadora.fraude.*` (um topico por service task; registro central em `config/topic_registry.yaml` — **W0.2 e o unico editor**; este contrato apenas declara o que precisa ser registrado — ver §"Registro de topicos exigido"). Contexto = `fraude`.

| Tipo | Topico | Sentido | Quando |
|---|---|---|---|
| Kafka | `agents.events.fraude.intake_received` | produz | apos start neutro (caso de investigacao recebido) — correlacao `FRAUDE-{tenant}-{caso}` |
| Kafka | `agents.events.fraude.custody_sealed` | produz | `bundle_root` selado na chain ANTES da UT (payload: `bundle_root`, `record_count`; **sem PHI**) |
| Kafka | `agents.events.fraude.sla_breached` | produz | SLA de investigacao estourado |
| Kafka | `agents.events.fraude.completed` | produz | fim (payload.desfecho = `fraude_confirmada_humano` \| `arquivado_sem_indicio` \| `monitorar` \| `encaminhado_juridico` \| `encaminhado_credenciamento` \| `encaminhado_contratual`; para os adversos carrega `investigator_id`+`tier`) |
| External task | `operadora.events.publish` | consome (worker) | publicador generico de eventos de dominio (reuso — todos os SP-OP) |
| External task | `operadora.fraude.intake` | consome (worker) | **start neutro** — registra o caso, consolida procedencia do `encaminhar_fraude`; sem efeito adverso |
| External task | `operadora.fraude.gather_evidence` | consome (worker→A2A) | convoca Beatriz (delegacao `fraude.investigate`, **human-gated**): coleta/normaliza evidencia (CDC/TISS/feature store) como ponteiros pseudonimizados; **TASY write DROP** (ADR-0013) |
| External task | `operadora.fraude.score_indicators` | consome (worker) | porta INVERTIDA do `detect_fraud` v2 + 7 DMNs `fraude_*`: produz `indicadores_presentes` + `score_indicadores` como **fatos de montagem** (NUNCA `FRAUD_DETECTED`, NUNCA veredito) |
| External task | `operadora.fraude.assemble_dossier` | consome (worker→A2A) | Beatriz monta o dossie de investigacao (narrativa/montagem; **instrui, nao decide** — principio Rafael/Beatriz `instruct_investigation`) |
| External task | `operadora.fraude.seal_custody_bundle` | consome (worker) | **selagem de custodia** — calcula `bundle_root` (Merkle) e grava na hash-chain ADR-0007 ANTES da UT; publica `fraude.custody_sealed` (porta de `custody.py`) |
| External task | `operadora.fraude.register_fraud_accusation` | consome (worker) | **efeito adverso gated** — registra a acusacao; recusa sem decisao humana (`ERR_FRAUD_ACCUSATION_NOT_HUMAN`); carrega `investigator_id`+`tier`; re-verifica `bundle_root` selado |
| External task | `operadora.fraude.refer_to_legal` | consome (worker) | **handoff downstream** — referral a juridico/ANS/civel/penal (so a jusante de acusacao humana) |
| External task | `operadora.fraude.start_credenciamento` | consome (worker) | **handoff** — inicia SP-OP-CRED-001 (descredenciamento) quando `entidade_tipo=prestador` (CRED tem UT adversa propria) |
| External task | `operadora.fraude.start_contratual` | consome (worker) | **handoff** — inicia SP-OP-CANCEL-001/INADIMPLENCIA-001 quando `entidade_tipo ∈ {beneficiario, contrato}` (CANCEL/INADIMPLENCIA tem UT adversa propria) |
| External task | `operadora.fraude.notify_sla_risk` | consome (worker) | alerta `coordenacao-investigacao` (timer nao-interruptivo) |
| Message BPMN | `msg.fraude.evidencia_anexada` | recebe | correlacao por business key — nova evidencia (incl. novo `encaminhar_fraude` sobre o mesmo caso) chegou; anexa ao dossie e **re-sela** (novo `bundle_root`) se ainda nao decidido |
| Message BPMN | `msg.fraude.diligencia_concluida` | recebe | correlacao por business key — diligencia solicitada (`SOLICITAR_DILIGENCIA`) retornou; reabre `UT_DecisaoInvestigador` |

> **Sem read-back/bloqueio:** FRAUDE-001 nao publica nem consome nenhum topico que leia de volta, bloqueie ou pre-empte glosa/recurso/reembolso/cancelamento de Phase 2. O unico acoplamento com Phase 2 e o **consumo do handoff** (intake), nunca uma volta.

## DMN referenciadas

Shape engine-deployavel (§4-bis-A): `typeRef ∈ {string, boolean, integer, long, double, date}` — **`number` e invalido** (inverte o `score: number` do reference); score → `integer`; dias/SLA → string ISO 8601. Toda `decisionTable` com `hitPolicy`; toda tabela com row catch-all → caminho humano conservador. **Nenhuma DMN tem coluna de saida de acusacao/veredito/bloqueio de fraude.** `camunda:historyTimeToLive` namespaced (`P###D`) em cada `<decision>`.

> **Porta INVERTIDA do scoring de referencia:** as 7 DMNs de score do reference (`upcoding_complexity_ceiling`, `unbundling_partial_bundles`, `phantom_no_diagnosis`, `phantom_suspicious_prefix`, `frequency_zscore_threshold`, `provider_peer_deviation`, `risk_thresholds`) sao portadas (copiar+adaptar+possuir aqui — ADR-0011, nunca importar) **apenas para montar `indicadores_presentes`/`score_indicadores`** dentro de `operadora.fraude.score_indicators`. O `recommendation="flag"`→`FRAUD_DETECTED` do reference e **REMOVIDO**: nenhuma dessas tabelas portadas alimenta um veredito; alimentam o dossie. `fraud_detection.dmn` (output `score: number` + `recommendation`) e `fraud_clearance.dmn` (output `BLOQUEAR`/`PROSSEGUIR`) **nao sao portadas** (sao o anti-padrao puro). O `typeRef="number"` do reference e corrigido para `integer` na portabilidade. **GAP-PERSP-DMN-DEAD-INPUTS:** a portabilidade 1:1 trouxe do doador duas colunas DECORATIVAS — `encounter_class` em `frequency_zscore_threshold` e `tuss_codes` em `unbundling_partial_bundles` — declaradas como input e usadas como `-` em TODA row (efeito zero, e nao lidas por nenhuma saida FEEL). As duas foram REMOVIDAS: a avaliacao no motor e identica e a assinatura de cada tabela passa a declarar exatamente o que ela le. Depois disso `_SCORING_INPUT_KEYS` (`src/maezo/tools/workers/fraude.py`) e a uniao dos `inputExpression` das 7 tabelas coincidem exatamente (10 sinais). Nenhuma regra nova foi escrita: se o z-score deve variar por regime de atendimento, ou se codigos TUSS devem caracterizar desagrupamento, e conteudo de SME — registrado em `docs/review-queue.md`.

### `fraude_indicadores` (hitPolicy FIRST — DRAFT) — monta indicadores, nao acusa

GAP-FAB-NOTIF fix: esta linha marcava `COLLECT`; a tabela deployada (`fraude_indicadores.dmn`,
`hitPolicy="FIRST"`) e a real fonte de verdade — o proprio arquivo documenta o motivo do
rebaixamento (`COLLECT` colide com `mapDecisionResult="singleResult"`+`resultVariable` no engine,
"landmine 7"), rows ordenadas mais-intensas-primeiro, catch-all conservador na ultima linha.
- **in:** `indicadores_presentes: json` (rotulos de regra presentes), `score_indicadores: integer`, `entidade_tipo: string`
- **out:** `intensidade_investigacao: string` (`LEVE` | `APROFUNDADA` | `PRIORITARIA`), `motivo: string`
- **Sem saida `ACUSAR`/`FRAUD_DETECTED`/`BLOQUEAR` por design.** Define apenas a **intensidade** da montagem de dossie/investigacao (mais evidencia a coletar, prioridade na fila), NUNCA um veredito. Score alto → `PRIORITARIA` (investiga mais), **nunca** acusacao. Catch-all → `PRIORITARIA` (conservador: na duvida, investiga mais e leva a humano).

### `fraude_routing` (hitPolicy FIRST — DRAFT; coracao do roteamento — sem variante adversa)
- **in:** `intensidade_investigacao: string`, `entidade_tipo: string`, `origem_encaminhamento: string`
- **out:** `roteamento: string` (`INVESTIGACAO_HUMANA`), `grupo_investigador: string`, `motivo: string`
- O dominio de `roteamento` e **exatamente `{INVESTIGACAO_HUMANA}`** — **toda** instancia roteia para o humano (`UT_DecisaoInvestigador`); nao existe rota automatizada de acusacao nem de "encerrar sem humano". A DMN apenas escolhe o **grupo/tier** investigador e a prioridade. Catch-all (ultima row) → `INVESTIGACAO_HUMANA` + `grupo_investigador` mais senior (conservador). **Sem saida de acusacao.** (Espelha a postura "tudo roteia ao humano" do `nip_routing`/`glosa_triage`, levada ao limite porque fraude e 100% human-gated.)

### `fraude_sla` (hitPolicy FIRST — DRAFT; todos os prazos DRAFT/verify)

GAP-FAB-NOTIF fix: esta linha marcava `UNIQUE`; a tabela deployada (`fraude_sla.dmn`,
`hitPolicy="FIRST"`) e a real fonte de verdade — o proprio arquivo documenta o motivo do
rebaixamento (uma row catch-all sob `UNIQUE` colide com qualquer row especifica no engine,
"landmine 7", idioma `cancel_sla`).
- **in:** `intensidade_investigacao: string`, `entidade_tipo: string`, `origem_encaminhamento: string`
- **out:** `sla_investigacao: string` (ISO 8601), `sla_alerta: string` (ISO 8601), `sla_diligencia: string` (ISO 8601), `fonte_regulatoria: string`
- Prazos como string ISO; converter dias uteis→ISO conservadoramente no worker. Prazo de investigacao/referral **DRAFT/verify** com juridico/compliance (obrigacoes de referral a ANS/civel/penal nao estao pinadas — ver Pendencias).

## Papeis humanos (candidate groups)

> **PROPOSTO — confirmar contra a taxonomia organizacional da operadora** (ver Open Questions / review-queue). Os nomes `investigacao-fraude`, `coordenacao-investigacao` e `juridico-fraude` sao candidatos DRAFT e podem nao corresponder aos grupos reais do IdP/console de User Tasks. Toda `<bpmn:userTask>` traz `camunda:candidateGroups` (gate D1).

| Grupo (candidateGroups) | Papel | Tarefa |
|---|---|---|
| `investigacao-fraude` | Investigador de fraude / auditoria especial | `UT_DecisaoInvestigador` (**acusacao SO aqui**: `decisao_fraude ∈ {ACUSAR_FRAUDE, ARQUIVAR, MONITORAR, SOLICITAR_DILIGENCIA}`) — decide sobre `bundle_root` selado |
| `coordenacao-investigacao` | Coordenacao de investigacao/auditoria | `UT_CoordenacaoInvestigacao` (SLA de investigacao estourado — assume; decisao continua humana, mesmos campos obrigatorios) |
| `juridico-fraude` | Juridico/compliance de fraude | `UT_RevisaoReferral` (revisa/aprova o referral a ANS/civel/penal apos acusacao humana — DRAFT: confirmar se e UT propria ou parte do downstream) |

A decisao adversa NUNCA muda de natureza por estouro de SLA: `UT_CoordenacaoInvestigacao` herda os mesmos campos obrigatorios e o mesmo requisito de `bundle_root` selado de `UT_DecisaoInvestigador`.

**Escada de alcada/tier (DRAFT — exige sign-off):** o `tier` do investigador que pode `ACUSAR_FRAUDE` (e por destino de referral — ANS vs civel/penal) e DRAFT/verify; a escada de alcada por gravidade/destino precisa de definicao de compliance+juridico. Ate o sign-off, manter um unico tier registrado + revisao `juridico-fraude` obrigatoria no referral.

## SLAs

> Todos os prazos sao **DRAFT/verify** com juridico/regulatorio antes de qualquer timer em producao. Prazos legais/processuais sao em dias (por vezes uteis); ISO 8601 usa dias corridos — usar valores conservadores e resolver calendario util no worker.

| Timer | Valor (DRAFT/verify) | Tipo | Fonte |
|---|---|---|---|
| Alerta de risco (`BT_AlertaSlaFraude`) | 50–70% de `${fraude_sla.sla_alerta}` | nao-interruptivo → `operadora.fraude.notify_sla_risk` | politica interna — **DRAFT** |
| Investigacao (`BT_SlaInvestigacao`) | `${fraude_sla.sla_investigacao}` | interruptivo → publica `fraude.sla_breached` → cancela `UT_DecisaoInvestigador`, cria `UT_CoordenacaoInvestigacao` | prazo interno de investigacao / obrigacao de referral — **DRAFT/verify** juridico |
| Diligencia (`SOLICITAR_DILIGENCIA`) | `${fraude_sla.sla_diligencia}` (DRAFT) | event gateway: `msg.fraude.diligencia_concluida` **vs** timer → `UT_DecisaoInvestigador` (humano decide; expiracao NUNCA auto-acusa nem auto-arquiva) | politica interna — **DRAFT** |

Nota: o estouro de SLA leva a coordenacao humana assumir; **nunca** ha auto-acusacao nem auto-arquivamento por timeout (inversao do anti-padrao `Task_AutoApprove`/timeout do reference, aplicada ao dominio de fraude).

## Codigos de erro

| Codigo | Onde | Tratamento |
|---|---|---|
| `ERR_FRAUD_ACCUSATION_NOT_HUMAN` | guard do worker `operadora.fraude.register_fraud_accusation` | o worker **recusa** registrar a acusacao se `decisao_fraude != ACUSAR_FRAUDE` setado por humano, ou se faltar `investigator_id`/`tier`/`fundamentacao_investigacao`/`indicadores_fundamentantes`/`referencia_normativa`/`destino_referral` — isto e, **ausencia/invalidade da decisao humana**. Lanca BPMN error (`Error_FraudAccusationNotHuman`); instancia nao atinge `End_FraudeConfirmadaHumano`. (espelha `ERR_CONTAS_GLOSA_NOT_HUMAN`/`ERR_CANCELLATION_NOT_HUMAN`/`ERR_REEMBOLSO_DENIAL_NOT_HUMAN`/`ERR_NIP_NEGATIVA_NOT_HUMAN`). **Nao** cobre falha de custodia (bundle nao selado/incoerente/nao re-verificavel) — ver `ERR_CUSTODY_NOT_SEALED` (GAP-FRAUDE-4: antes deste fix o worker lancava `ERR_FRAUD_ACCUSATION_NOT_HUMAN` tambem para falha de custodia, contradizendo esta tabela). |
| `ERR_CUSTODY_NOT_SEALED` | guard do gate `GW_CustodiaSelada` / worker `register_fraud_accusation` | o fluxo recusa criar `UT_DecisaoInvestigador` (e o worker recusa registrar) se o `bundle_root` nao foi selado na hash-chain ANTES da UT, se o bundle re-montado a partir de `bundle_record_hashes` for incoerente, ou se o `bundle_root` selado nao re-verificar contra a cadeia ADR-0007 (tamper/reorder — `custody.verify_custody_bundle`); garante a ordem selagem→decisao (cadeia de custodia). Em `register_fraud_accusation` este e o codigo correto para **qualquer** falha de custodia (distinto de `ERR_FRAUD_ACCUSATION_NOT_HUMAN`, reservado a ausencia/invalidade da decisao humana). Lanca `Error_CustodyNotSealed` |
| `ERR_PHI_IN_CUSTODY` | guard do worker `operadora.fraude.seal_custody_bundle` | o worker recusa selar um bundle cujo item carregue PHI bruto (so ponteiros pseudonimizados + hashes — ADR-0006). Lanca `Error_PhiInCustody`; defesa em profundidade contra vazamento na evidencia |
| `ERR_FRAUDE_CASO_INVALIDO` | declarado (`Error_FraudeCasoInvalido`) para uso dos workers | worker lanca BPMN error se o caso/intake for inconsistente na origem; tratamento a detalhar na promocao a FINAL |

> **Inversao do reference:** o `FRAUD_DETECTED` (BPMN error que o `detect_fraud_worker_v2` lanca quando o score e alto — veredito automatico) **nao existe** em Maezo. Score alto roteia para `PRIORITARIA`/humano, nunca para um error que constitui veredito. `register_fraud_accusation` so age sobre decisao humana + dossie selado.

## Costura com Phase 2 (sink convergente)

- **Procedencia (preservada):** em Phase 2, `indicio_fraude_sinalizado` e estritamente **INFORMATIVO** (worker de regras sinaliza; nunca decide); um **humano** (analista/auditor/juridico) seta `encaminhar_fraude=true` numa User Task de Phase 2 (CONTAS `UT_AnalistaContas` — **a unica origem ja declarada hoje**; RECURSO/REEMBOLSO/CANCEL/NIP equivalentes **a ligar** na wave de BUILD — DRAFT/verify, ver Pendencias). Esse handoff humano e a **unica** porta de entrada de fraude — `operadora.fraude.intake` registra `encaminhado_por_id` (o humano de origem) na cadeia de auditoria.
- **Start neutro:** intake via `operadora.fraude.intake` + evento `agents.events.fraude.intake_received` correlacionado `FRAUDE-{tenant}-{caso}`. O start nao tem efeito adverso.
- **Sem volta:** FRAUDE-001 **nunca le de volta, bloqueia ou pre-empta** a decisao de glosa/recurso/reembolso/cancelamento que o originou. A glosa segue sua vida em CONTAS/RECURSO independentemente da investigacao; FRAUDE e estritamente downstream. (Se a acusacao humana referenciar prestador/contrato, o efeito e via handoff a CRED/CANCEL/INADIMPLENCIA — cada um com sua propria UT adversa — nunca uma volta a CONTAS.)
- **Consolidacao:** multiplos `encaminhar_fraude` sobre a mesma entidade/caso convergem na MESMA instancia (business key idempotente) e anexam evidencia ao dossie (`msg.fraude.evidencia_anexada`), re-selando o `bundle_root` enquanto nao houver decisao.

## Notas de design / inversao do reference

- **Anti-padrao invertido** (`detect_fraud_worker_v2.py` + `fraud_scoring/*` + `fraud_detection.dmn` + `fraud_clearance.dmn`): no reference o worker soma scores de 7 DMNs e, com `recommendation="flag"`, **lanca `FRAUD_DETECTED`** (veredito automatico por score); `fraud_clearance.dmn` emite `BLOQUEAR`. Em Maezo: (1) o score e **fato de roteamento** (`integer`, alimenta `fraude_indicadores`→`intensidade_investigacao`), nunca veredito; (2) nao ha `FRAUD_DETECTED` nem coluna de saida `ACUSAR`/`BLOQUEAR`/`CONFIRMAR`; (3) o scoring portado so monta `indicadores_presentes` para o dossie; (4) a acusacao e materializada so pelo worker gated `register_fraud_accusation` sobre decisao humana + dossie selado; (5) `typeRef="number"` do reference corrigido para `integer`.
- **Selagem antes da decisao (classe nova):** o `bundle_root` Merkle e gravado na hash-chain ADR-0007 ANTES de `UT_DecisaoInvestigador` — decisao sobre corpus verificavel (ANS/judicialmente defensavel). `GW_CustodiaSelada`/`ERR_CUSTODY_NOT_SEALED` garantem a ordem; `register_fraud_accusation` re-verifica o root antes do efeito.
- **Beatriz (PHI zone)** e convocada via `operadora.fraude.gather_evidence`/`assemble_dossier` (delegacao `fraude.investigate`, **human-gated**) para coletar/montar; **instrui, nao decide** — NAO seta `decisao_fraude` (grafo `instruct_investigation` single-node; ADR-0015 respeita `accepted_task_types` + max-hops). O investigador humano decide na User Task (principio Rafael/Beatriz `zero_auto_accusation`).
- **TASY write DROP** em todo worker de coleta (`gather_evidence`, `score_indicators`): consumimos o CDC do amh-data-platform e preferimos `feature_store.claim_features`/`provider_features` (tenant-scoped por grant LF-Tag — evita leakage cross-tenant), nunca escrevemos no Tasy (MEMORY/ADR-0013).
- **Terminais:** `End_FraudeConfirmadaHumano` + os tres de referral (`End_EncaminhadoJuridico`/`Credenciamento`/`Contratual`) sao adversos e humano-gated; `End_ArquivadoSemIndicio`/`End_MonitorarSemAcao` sao neutros. Nenhum fim ocorre sem evento de dominio publicado antes (auditoria dupla engine+Kafka, ADR-0007).

## Pendencias para promocao a FINAL

- DI (diagrama BPMN) e os corpos `.bpmn`/`.dmn`/worker `fraude.py`/`custody.py` (autorados na quadrupla pathfinder W-A contra este contrato).
- **Fronteira L0 (OQ critica):** `ACUSAR_FRAUDE` constitui `fraud_accusation` por si, ou a acusacao e o ato downstream (`End_EncaminhadoJuridico`)? Onde desenhar a fronteira L0 — confirmar com juridico.
- **Obrigacoes de referral** (ANS/civel/penal) — prazos, forma, autoridade competente, escada de alcada/tier por destino: **DRAFT/verify** com juridico+compliance (nao estao pinadas em nenhum repo).
- Confirmacao das citacoes RN/Lei (RN 593, RN 567, Lei 9.656 art. 13) e da interacao com SP-OP-CRED-001 (descredenciamento) e SP-OP-CANCEL-001/INADIMPLENCIA-001 (rescisao por fraude) — quem detem o terminal adverso de cada efeito (cada handoff tem UT adversa propria).
- **Confirmacao dos candidate groups** (`investigacao-fraude`/`coordenacao-investigacao`/`juridico-fraude`) contra a taxonomia organizacional da operadora (OQ).
- **Cadeia de custodia (juridico/DPO):** validar que o desenho `bundle_root`-Merkle-sobre-hash-chain e probatoriamente aceitavel (Codigo de Processo Civil/Penal — cadeia de custodia); politica de retencao probatoria (regulatorio 5+ anos) vs. retencao LGPD.
- **Reprodutibilidade do snapshot (90d):** snapshots time-travel do feature store rotacionam em ~90d; o dossie precisa de evidencia reproduzivel por anos — materializar/congelar o snapshot referenciado por `feature_snapshot_ref` no bundle (ADR-0020 — PROPOSTO).
- Regra de correlacao de `numero_caso` (um caso por prestador-competencia? por evento? por entidade-aberta?) — produto/juridico.
- **Ligar o seam `encaminhar_fraude` nas demais origens de Phase 2** (RECURSO/REEMBOLSO/CANCEL/NIP) — hoje so CONTAS-001 o declara; o sink convergente exige o handoff humano simetrico nas 4 restantes (PR em main sobre os contratos Phase 2, fora do escopo deste worktree DRAFT) — arquitetura + PO.
- Mapa de portabilidade das 7 DMNs de score (copiar+adaptar+re-testar+possuir; `number`→`integer`; remover `recommendation→FRAUD_DETECTED`) e definicao de `indicadores_presentes` a partir de `feature_store`.
- Mapeamento persona Beatriz↔dominio fraude (R-PERSONA-MAP DRAFT — "a chamada mais fragil"; confirmar com PO/produto antes de autorar o agente).

## Registro de topicos exigido (REPORTE — nao editar `topic_registry.yaml` aqui; dono W0.2)

Adicionar a `config/topic_registry.yaml` — Kafka `agents.events.fraude.*`: `intake_received`, `custody_sealed`, `sla_breached`, `completed`. External-task `operadora.fraude.*`: `intake`, `gather_evidence`, `score_indicators`, `assemble_dossier`, `seal_custody_bundle`, `register_fraud_accusation`, `refer_to_legal`, `start_credenciamento`, `start_contratual`, `notify_sla_risk`. Message BPMN `msg.fraude.evidencia_anexada`, `msg.fraude.diligencia_concluida`. (`operadora.events.publish` ja registrado.)

## Allowlist de inicio de processo (REPORTE — gate humano W0.1/ADR-0016)

Adicionar `SP-OP-FRAUDE-001` (process key) a `KNOWN_PROCESS_KEYS` exige PR humano CODEOWNERS (ADR-0016 — start atras de gate humano). Este contrato apenas declara a necessidade; o start em runtime so e habilitado pelo gate.
