# Tabela de correspondencia lexical — vocabulario de decisao do pagador (CONTAS / RECURSO)

**Origem:** `OWNER-DECISIONS-REGISTER` **R-018** (procedimento aprovado — os agentes montam a
tabela de correspondencia lexical como insumo, cada linha marcada `PROPOSTO`) e **R-019**
(compromisso datado — a sessao conjunta e convocada com esta tabela como **pauta unica**).
**Gap:** `PERSP-VOCAB` (P0). **Work package:** `WP-PERSP-CONTAS-RECURSO`.

**Sessao:** conjunta — **regulatorio + auditoria de contas** (com financas), a ser convocada
**ate 2026-09-19**. O evento `antes de PERSP-CONTAS`/`PERSP-RECURSO` continua valendo como
gatilho mais cedo.

**Campos de ratificacao (VAZIOS — nenhum agente os preenche):**

| Campo | Valor |
|---|---|
| `ratificado` | *(vazio)* |
| `revisor` | *(vazio)* |
| `ratificado_em` | *(vazio)* |

> **NENHUMA LINHA `PROPOSTO` E MERGEAVEL EM `spec/` COMO DECISAO.** Este documento e insumo de
> sessao. A ratificacao do vocabulario de decisao do pagador e ato de regulatorio e auditoria
> de contas; nenhum agente remove rotulo `PROPOSTO`, redige signoff ou mergeia vocabulario em
> `spec/` como se fosse a decisao. Rotulo desta entrega: *recomendacao — pendente de assinatura
> de regulatorio + auditoria de contas*.

**Gemeo legivel por maquina:** `payer-vocab-correspondence.yaml` (mesmo conteudo, mesmos
`arquivo:linha`). Ambos sao conferidos por `tests/unit/docs/test_sme_dispatch_dossiers.py`:
cada `arquivo:linha` tem de resolver e a linha tem de conter a ancora declarada, de modo que a
tabela nao possa apodrecer em silencio quando os artefatos se moverem.

---

## 0. Como ler a tabela (e o que o estado atual do repositorio ja fez)

A coluna **elemento anterior** e o lexico da versao doada (perspectiva do **prestador**), tal
como levantado em `PERSPECTIVE-MATRIX.md` §2.1/§2.4 e §5.1 *(fonte gitignored, ver disclosure
abaixo)* e re-derivado em ADR-0040 §Contexto. A coluna **termo do pagador PROPOSTO** e a
substituicao proposta — e, **no estado atual do repositorio, ja e o que os artefatos dizem**: o
redesenho de CONTAS/RECURSO ja esta em `main`.

> **Disclosure — `PERSPECTIVE-MATRIX.md` nao faz parte do repositorio nem desta PR.** O arquivo
> vive em `docs/audits/maezo-deep-audit/remediation/PERSPECTIVE-MATRIX.md`, esta em
> `.gitignore` e e editado ao vivo por sessoes irmas — `git ls-files` nao o lista. As quatro
> referencias a ele neste documento (aqui, e nas secoes 1, 2 e 4) sao, portanto, de segunda mao e
> nao reproduziveis a partir do clone: a coluna **elemento anterior** herda dele a contagem e a
> descricao do lexico doado. Para tornar essa dependencia auditavel sem versionar um arquivo
> vivo, esta entrega tirou um snapshot no momento da preparacao — sha256
> `6c3afd0a2673b82beeb12704c86686368e6120239be930c3f3ef3dadca6ac7e4`, 1139 linhas, 2026-09-04 —
> e confirmou que o arquivo ao vivo ainda bate byte-a-byte com esse snapshot. Cada
> `arquivo:linha` das tabelas abaixo, ao contrario, aponta para um artefato **rastreado** no
> repositorio e resolve independentemente desta fonte.

Isso **nao** ratifica nada, e a distincao e o ponto central desta pauta:

- `docs/adr/0040-perspectiva-operadora-contas-recurso.md` continua com **`Status: Proposed`**;
- os dois contratos continuam **`DRAFT (v0.1.0)`**, sem `signoff.yaml`;
- os candidate groups continuam marcados **`PROPOSTO`** (lista exata em §3);
- as citacoes normativas (RN 501/2022, RN 424/2017, prazos contratuais) continuam
  **`DRAFT/verify`**.

A sessao portanto **nao aprova uma mudanca futura**: ela ratifica (ou corrige) um vocabulario
que ja esta escrito e ainda nao esta assinado. Cada `arquivo:linha` abaixo aponta para **onde o
termo do pagador vive hoje**, para que o revisor leia o artefato real em vez de uma proposta em
prosa. Quando o termo proposto e uma **supressao** (o construto do recorrente nao tem
equivalente de pagador), o `arquivo:linha` aponta para a **cerca de perspectiva**
(`src/maezo/platform/validation/perspective.py`), que e o mecanismo que impede o retorno do
token — a supressao e verificavel ali, e nao numa ausencia.

### O teste de perspectiva (ADR-0040 D2), resumido — e a ressalva metodologica

- **Q1 — quem executa?** Areas da operadora (analise/auditoria de contas, medico auditor,
  coordenacao) ou sistemas dela. Nunca o prestador, nunca o beneficiario.
- **Q2 — direcao TISS.** A operadora **RECEBE** guias, lotes e recursos; **EMITE** demonstrativos,
  autorizacoes, negativas, glosas e respostas de recurso.
- **Q3 — quem sofre o adverso e de quem e o SLA.** O adverso recai sobre o prestador
  (`GLOSAR`, `INDEFERIR`, `DEFERIR_PARCIAL`) ou o beneficiario, nunca sobre o dono do processo;
  o prazo cronometrado e o da **resposta do pagador**.
- **Q4 — vocabulario de saida.** DMN e User Tasks nomeiam decisoes do pagador.

> **Ressalva metodologica obrigatoria (ADR-0040 D2, Q3).** Q3 **nao** pode ser fundamentado em
> "RN 424/2017 vincula a resposta do pagador ao recurso": o proprio repositorio classifica essa
> atribuicao como ERRADA — RN 424/2017 rege **junta medica/odontologica**
> (`docs/compliance/rn-currency-review.md`), e o redline resolve prazo de glosa/recurso para
> **RN 501/2022**, ele proprio marcado `[verify SME]`. Q3 se sustenta por **quem sofre o efeito**
> e por **qual relogio o processo de fato conta**, nunca por citacao normativa nao confirmada.
> Confirmar essa cadeia de citacoes e item de pauta desta mesma sessao.

---

## 1. SP-OP-CONTAS-001 — 12 elementos

Inventario de origem: `PERSPECTIVE-MATRIX.md` §5.1 ("CONTAS-001 (A §3-bis)") *(fonte gitignored,
ver §0)*, que enumera 12 itens — os 11 da lista original mais `ST_RegisterGlosaAccept`, adicao do verificador adversarial
(era o **unico** elemento de efeito adverso L0 do processo e estava ausente da matriz original).

| # | Elemento anterior (perspectiva prestador) | Verbo/termo do pagador **PROPOSTO** | Razao — teste de perspectiva (ADR-0040 D2) | `arquivo:linha` | Status |
|---|---|---|---|---|---|
| C01 | `Start_LoteRecebido` — "Lote/demonstrativo TISS recebido" | `Start_LoteTissRecebido` — "Lote de guias TISS recebido do prestador" | Q2 (direcao TISS): a operadora RECEBE o lote de guias; o demonstrativo e o que ela EMITE. Quem executa (Q1) e o intake da operadora. | `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:90` | **PROPOSTO** |
| C02 | `ST_IdentifyGlosa` — "Identificar glosas candidatas no demonstrativo" | `ST_ApurarDivergencias` — "Apurar divergencias e glosas candidatas na conta apresentada" | Q1/Q2: quem apura divergencias e a analise de contas da operadora, sobre a conta que o prestador APRESENTOU — nao sobre um demonstrativo recebido de terceiro. | `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:127` | **PROPOSTO** |
| C03 | `decisao_contas in {RECORRER, ACEITAR_GLOSA, REENVIAR}` (as tres acoes de quem SOFRE a glosa) | `decisao_contas in {PAGAR, GLOSAR, PAGAR_PARCIAL, DEVOLVER, ENCAMINHAR_FRAUDE}` | Q4 (vocabulario de saida) + Q3 (quem sofre o adverso): a operadora adjudica a conta; o adverso (GLOSAR/PAGAR_PARCIAL) recai sobre o prestador, nunca sobre o dono do processo. | `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:193` | **PROPOSTO** |
| C04 | `ST_RegisterGlosaAccept` — unico elemento de efeito adverso L0 do processo (registrava o ACEITE de uma glosa alheia) | `ST_RegistrarGlosa` (+ `ST_RegistrarGlosaParcial`) — registra a APLICACAO da glosa | Q3: mesma mecanica de gating, objeto invertido. O ato adverso do pagador e aplicar a glosa; aceitar uma glosa e ato de quem a sofre. | `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:330` | **PROPOSTO** |
| C05 | `Error_GlosaAcceptNotHuman` / `ERR_GLOSA_ACCEPT_NOT_HUMAN` — "Aceite de glosa sem decisao humana" | `Error_ContasGlosaNotHuman` / `ERR_CONTAS_GLOSA_NOT_HUMAN` — "Aplicacao de glosa sem decisao humana" | Q3: o guard tem de proteger o ato do dono do processo. Sufixo `_NOT_HUMAN` preservado (reconhecimento automatico por `harness.is_guard_refusal_code`); semantica ADR-0030 (declarado-e-nao-capturado, PermissionError -> incidente) inalterada. | `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:5` | **PROPOSTO** |
| C06 | worker `register_glosa_accept` / topico `operadora.contas.register_glosa_accept` | worker `registrar_glosa` / topico `operadora.contas.registrar_glosa` | Q1: o worker materializa o ato da operadora. O nome pt-BR alinha-se ao dominio (`registrar_glosa`), e o topico e a superficie que o BPMN chama. | `src/maezo/tools/workers/contas.py:486` | **PROPOSTO** |
| C07 | `End_GlosaAceitaHumano` — "Glosa aceita pelo analista (L0 — unico adverso)" | `End_GlosaAplicadaHumano` — "Glosa aplicada pelo analista (L0 — adverso, humano-gated)" | Q3: o terminal nomeia o ato do dono do processo. `aceita` descreve o prestador; `aplicada` descreve a operadora. | `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:356` | **PROPOSTO** |
| C08 | `End_Reenviada` — "Reenviada para correcao (decisao humana)" (+ `ST_ReconcilePayment` "Conciliar/registrar reenvio") | `End_ContaDevolvidaPrestador` — "Conta devolvida ao prestador para correcao" (+ `ST_DevolverConta`) | Q1/Q2: reapresentar a conta e ato do prestador; devolver a conta para correcao e ato da operadora. A reapresentacao volta por `Msg_ContasLinhasAtualizadas` (a operadora RECEBE). | `spec/processes/bpmn/SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:425` | **PROPOSTO** |
| C09 | `ST_StartRecurso` + `ST_PublishEncaminhadaRecurso` + `End_EncaminhadaRecurso` — o handoff CONTAS -> RECURSO disparado por `decisao_contas == RECORRER` | SEM equivalente de pagador — a aresta e DELETADA: a operadora nao interpoe recurso contra a propria glosa. SP-OP-RECURSO-001 passa a nascer do recurso que o prestador transmite. | Q1/Q2: o recorrente e o prestador. Manter o handoff seria a plataforma recorrendo de si mesma. A supressao e o proprio termo do pagador nesta linha. | `docs/processes/contracts/SP-OP-RECURSO-001.md:55` | **PROPOSTO** |
| C10 | desfechos publicados `{sem_glosa, encaminhada_recurso, glosa_aceita_humano, reenviada}` (3 de 4 sao do recorrente) | desfechos `{pagar_integral, pagamento_aprovado_humano, glosa_aplicada_humano, pagamento_parcial_humano, conta_devolvida_humano, encaminhada_fraude}` | Q4: o evento de dominio e a declaracao publica do que o processo fez. Um desfecho `encaminhada_recurso` afirma que quem publicou recorreu. | `docs/processes/contracts/SP-OP-CONTAS-001.md:150` | **PROPOSTO** |
| C11 | DMN `glosa_triage`: dominio de `roteamento` = `{SEM_GLOSA, RECORRER, ANALISE_HUMANA}`; unica regra substantiva nao-humana `r_valor_recorrer` emite `"RECORRER"` | dominio EXATAMENTE `{PAGAR, ANALISE_HUMANA}`; a regra substantiva nao-humana passa a ser `r_pagar` (favoravel ao prestador); toda candidata a glosa vai a `ANALISE_HUMANA` | Q4 + ADR-0018 parte 2: na perspectiva do pagador o adverso E `GLOSAR`, logo ele nao pode existir como valor de saida de DMN. Achado M-4 (o conjunto negativo ainda admite `desconhecida`) segue ABERTO — ADR-0040 OQ-10. | `spec/processes/dmn/glosa_triage.dmn:16` | **PROPOSTO** |
| C12 | `data_recebimento_lote` descrita como "recebimento do demonstrativo" e `reason_codes_tiss` como "sinalizados no demonstrativo" (ancora e fatos vindos de terceiro) | `data_recebimento_lote` = "data em que a operadora **recebeu** o lote do prestador" (ancora contratual do prazo); `reason_codes_tiss` = "apurados pela analise de contas da operadora" | Q2/Q3: o relogio que este repositorio conta e o da RESPOSTA do pagador, ancorado num evento de RECEPCAO por ele. Os motivos de glosa sao apurados por ele, nunca recebidos prontos de terceiro. | `docs/processes/contracts/SP-OP-CONTAS-001.md:61` | **PROPOSTO** |

## 2. SP-OP-RECURSO-001 — 26 elementos

Inventario de origem: `PERSPECTIVE-MATRIX.md` §5.1 ("RECURSO-001 (A §3-bis)") *(fonte gitignored,
ver §0)*, descrito no registro de gaps como "~25 elementos + KPI + prompts + 6 nomes de teste". A enumeracao explicita
abaixo fecha em **26 linhas** (a diferenca e de agrupamento: R21 reune os dois campos obrigatorios
`justificativa_desistencia`/`valor_glosa_aceito` e R26 reune KPI + prompts do agente Marina). Os
**6 nomes de teste** estao em §4, fora da numeracao, por serem consequencia e nao decisao.

| # | Elemento anterior (perspectiva prestador) | Verbo/termo do pagador **PROPOSTO** | Razao — teste de perspectiva (ADR-0040 D2) | `arquivo:linha` | Status |
|---|---|---|---|---|---|
| R01 | `ST_SubmitAppeal` — "Interpor recurso a operadora (TISS)" (protocolo `RECAPPEAL-`) | REMOVIDO sem shim. A entrada do processo passa a ser `Start_RecursoRecebido` — "Recurso de glosa recebido do prestador (TISS)" | Q1/Q2: a operadora nao interpoe recurso a si mesma; ela RECEBE o recurso. Esta era a evidencia estrutural do enxerto do doador. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:114` | **PROPOSTO** |
| R02 | `GW_AguardarResposta` — "Aguardar resposta da operadora" | REMOVIDO. Nao ha termo de pagador: quem espera a resposta da operadora e o prestador. A regressao fica barrada pelo token `AguardarResposta` da familia R1 `ciclo-recorrente` da cerca de perspectiva. | Q1: esperar a resposta da operadora prova que o dono do processo NAO e a operadora. | `src/maezo/platform/validation/perspective.py:361` | **PROPOSTO** |
| R03 | `ICE_RespostaRecebida` + `Msg_RecursoRespostaRecebida` / `msg.recurso.resposta_recebida` | REMOVIDOS. Regressao barrada pela familia R1 `espera-resposta` (`resposta_recebida`, `RespostaRecebida`). | Q2: uma mensagem chamada "resposta recebida" so existe do lado de quem pediu a decisao a outrem. | `src/maezo/platform/validation/perspective.py:382` | **PROPOSTO** |
| R04 | `ICE_AguardarResposta` — "Aguardar resposta (P5D — loop TISS)" | REMOVIDO. O unico prazo intermediario que sobra e `ICE_PrazoPendencia` (P5D), que fecha a pendencia de DOCUMENTACAO que a operadora abriu ao prestador. | Q1/Q3: o prazo que este processo cronometra e o da resposta do pagador e o da pendencia que ele abriu — nunca a espera por uma decisao de terceiro. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:239` | **PROPOSTO** |
| R05 | `ST_TrackStatus` — "Acompanhar status do recurso interposto" | REMOVIDO. Regressao barrada pelos tokens `track_status`/`TrackStatus` (familia `ciclo-recorrente`), e pelos pt-BR `acompanhar_recurso`/`acompanhar_status` acrescentados no PR-1. | Q1: acompanhar o status de um recurso e o que faz quem o interpos; o julgador o decide. | `src/maezo/platform/validation/perspective.py:358` | **PROPOSTO** |
| R06 | `GW_LoopLimite` + variavel `loop_counter` (teto por CONTAGEM de iteracoes de espera) | REMOVIDOS. O que limita o ciclo remanescente e o teto ABSOLUTO `sla.prazo_max_absoluto_iso` (ancora + P30D), identico nos 3 boundary de teto (GAP-RECURSO-1). | Q3 + ADR-0018: um teto por contagem produz auto-desfecho por esgotamento. O relogio do pagador e absoluto e nao e adiado por reentrada no ciclo. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:397` | **PROPOSTO** |
| R07 | `GW_RecursoResolvido` — "Resposta da operadora", lendo `${resposta_operadora == ...}`; default `Flow_GWResolvido_Indeferido` | `GW_DecisaoRecurso` — "Decisao do recurso (humana)", lendo `${decisao_recurso == ...}`; default `Flow_GWDec_Invalida` -> `ERR_RECURSO_DECISAO_INVALIDA` (fail-closed) | Q1/Q4: a decisao do processo passa a ser produzida DENTRO dele, por humano da operadora, e a omissao vira erro visivel em vez de acao por default. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:431` | **PROPOSTO** |
| R08 | `ST_ReconcilePaymentDeferido` — "Conciliar re-pagamento ao prestador (deferido)", disparada por `${resposta_operadora}` | `ST_HandoffPagamentoRecurso` — "Encaminhar pagamento da glosa revertida (handoff SP-OP-PAGTO-001)", `tipo_pagamento=glosa_revertida` | Q1/Q3: o pagador nao concilia um credito recebido — ele ORIGINA a ordem de pagamento da glosa que reverteu. (A matriz rebaixou o elemento a MISTO bifacial: o nome era direcional-pagador; o invertido era o GATILHO.) | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:491` | **PROPOSTO** |
| R09 | `ST_ReconcilePaymentParcial` — "Conciliar re-pagamento parcial ao prestador" | `ST_HandoffPagamentoParcial` — "Encaminhar pagamento da parcela revertida (handoff SP-OP-PAGTO-001)" | Q1/Q3: idem R08, na perna do deferimento parcial. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:537` | **PROPOSTO** |
| R10 | `End_RecursoDeferido` — "Recurso deferido (re-pagamento conciliado)" | `End_RecursoDeferido` — "Recurso deferido pela operadora (decisao humana — glosa revertida)". MESMO id, semantica invertida. | Q1: o terminal deixa de nomear a concessao de um terceiro e passa a nomear o proprio ato do dono do processo. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:514` | **PROPOSTO** |
| R11 | `End_RecursoParcial` — terminal nomeado pela concessao parcial de um terceiro | `End_RecursoDeferidoParcial` — "Recurso parcialmente deferido (decisao humana — L0, reducao adversa)" | Q3: o parcial MANTEM parte da glosa contra o prestador; e adverso L0 por analogia ao precedente ja ratificado `End_ReembolsoParcial` ("reducao adversa"). Confirmar essa classificacao e OQ-8. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:560` | **PROPOSTO** |
| R12 | `End_RecursoIndeferido` — "Recurso indeferido **pela operadora** (resposta de terceiro)"; doc: "Indeferimento pela operadora externa NAO e uma desistencia da Maezo — e resposta de terceiro" | `End_RecursoIndeferido` — "Recurso indeferido (decisao humana do analista — L0 adverso)". Somem "(resposta de terceiro)" E "pela operadora". | Q1: a evidencia isolada mais forte do repositorio. O dono do processo nao se nomeia como parte externa; a auto-referencia em terceira pessoa E a inversao. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:593` | **PROPOSTO** |
| R13 | variavel de entrada `resposta_operadora` (`deferido\|parcialmente_deferido\|indeferido`) — a decisao do processo chegando de FORA | ELIMINADA. A decisao e `decisao_recurso` (analista/coordenacao) ou `decisao_auditor_recurso` (medico auditor), produzidas dentro do processo. Regressao barrada pela familia R1 `resposta-terceiro`. | Q1/Q4: so um terceiro nomeia a decisao da operadora como variavel de ENTRADA. | `src/maezo/platform/validation/perspective.py:350` | **PROPOSTO** |
| R14 | `decisao_recurso in {RECORRER, NAO_RECORRER, SOLICITAR_INFO, ESCALAR_AUDITOR}` | `decisao_recurso in {DEFERIR, DEFERIR_PARCIAL, INDEFERIR, SOLICITAR_INFO, ESCALAR_AUDITOR}` | Q4: quem julga defere ou indefere; recorrer/nao-recorrer sao movimentos do recorrente. `SOLICITAR_INFO` e `ESCALAR_AUDITOR` ja eram vocabulario legitimo de julgador e ficam. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:318` | **PROPOSTO** |
| R15 | `decisao_auditor_recurso in {MANTER_RECURSO, ACEITAR_GLOSA, RECURSO_PARCIAL}` (doc: "MANTER_RECURSO/RECURSO_PARCIAL -> interpoe o recurso") | `decisao_auditor_recurso in {DEFERIR, DEFERIR_PARCIAL, INDEFERIR}` | Q3/Q4: o medico auditor decide o MERITO como julgador (espelha `JUNTA_MEDICA` de SP-OP-AUTH-001), nao mantem/desiste de um recurso proprio. | `spec/processes/dmn/recurso_eligibility.dmn:17` | **PROPOSTO** |
| R16 | `UT_EscalonamentoPrazo` — TERCEIRO canal humano emitindo `{RECORRER, NAO_RECORRER, SOLICITAR_INFO, ESCALAR_AUDITOR}` (achado MATERIAL do verificador, ausente das listas originais) | `UT_EscalonamentoPrazo` — mesmo id, mesmas saidas do analista no vocabulario do pagador; a INADMISSIBILIDADE por prazo continua SEMPRE humana | Q4: os quatro canais humanos tem de falar o MESMO vocabulario de pagador, senao o teto de prazo reintroduz o lexico do recorrente por uma porta lateral. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:424` | **PROPOSTO** |
| R17 | `End_RecursoNaoInterposto` — "desistencia humana — adverso L0" (+ `ST_PublishNaoInterposto`) | REMOVIDO. O adverso equivalente do julgador ja existia e permanece: `End_RecursoInadmissivel` (inadmitir e ato do pagador). | Q3: o adverso L0 do dono do processo nao pode ser a propria desistencia. Familia R1 `manutencao` (`NAO_INTERPOSTO`) barra o retorno. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:659` | **PROPOSTO** |
| R18 | `End_GlosaMantida` — "Glosa mantida pelo auditor" (mas materializada pelo worker `register_desistencia`, desfecho `nao_interposto_humano`) | `End_RecursoIndeferidoAuditor` — "Recurso indeferido pelo medico auditor (decisao humana — L0 adverso)" | Q3/Q4: a linguagem ja era de pagador, mas o mecanismo por tras era de recorrente; o terminal passa a nomear o ato que de fato ocorre. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:626` | **PROPOSTO** |
| R19 | worker `register_desistencia` / topico `operadora.recurso.register_desistencia`, servindo TRES tarefas — inclusive a inadmissibilidade, que ja era ato do pagador | worker `registrar_indeferimento` / topico `operadora.recurso.registrar_indeferimento`, servindo as QUATRO tarefas adversas (indeferir, deferir parcial, indeferir pelo auditor, inadmitir) | Q1/Q3: o unico worker de efeito adverso da cadeia se chamava "desistencia" — o ato de quem recorre. Sob a perspectiva do pagador, o adverso e o indeferimento. | `src/maezo/tools/workers/recurso.py:1590` | **PROPOSTO** |
| R20 | `Error_DesistenciaNotHuman` / `ERR_DESISTENCIA_NOT_HUMAN` — "Desistencia/manutencao de glosa sem decisao humana" | `Error_RecursoIndeferimentoNotHuman` / `ERR_RECURSO_INDEFERIMENTO_NOT_HUMAN` — "Indeferimento/manutencao de glosa sem decisao humana" | Q3: substituicao um-para-um do guard; sufixo `_NOT_HUMAN` preservado e semantica ADR-0030 (declarado-e-nao-capturado, `PermissionError` -> incidente auditado) inalterada. | `spec/processes/bpmn/SP-OP-RECURSO-001_Recurso_Glosa.bpmn:24` | **PROPOSTO** |
| R21 | campos obrigatorios `justificativa_desistencia` e `valor_glosa_aceito` | `fundamentacao_indeferimento`, `valor_glosa_mantido_brl` e `referencia_contratual` (e `valor_deferido_brl` no parcial) | Q3: o que o julgador tem de fundamentar e a manutencao da glosa contra o prestador, com a clausula contratual que a sustenta — nao a justificativa da propria desistencia. | `docs/processes/contracts/SP-OP-RECURSO-001.md:99` | **PROPOSTO** |
| R22 | desfecho publicado `nao_interposto_humano` (no conjunto `{deferido, indeferido, parcialmente_deferido, inadmissivel, nao_interposto_humano}`) | `desfecho in {deferido_humano, deferido_parcial_humano, indeferido_humano, inadmissivel_humano}` — todos humano-gated e todos atos do julgador | Q4: o evento de dominio declara publicamente o que o processo fez; `nao_interposto` declara que o publicador desistiu de recorrer. | `docs/processes/contracts/SP-OP-RECURSO-001.md:108` | **PROPOSTO** |
| R23 | DMN `recurso_eligibility`: dominio de `roteamento` = `{RECORRIVEL, ANALISE_HUMANA}` | dominio EXATAMENTE `{SEGUE_MERITO, ANALISE_HUMANA}` | Q4: "recorrivel" e predicado do recorrente sobre uma decisao alheia. O julgador decide se o recurso SEGUE PARA O MERITO. | `spec/processes/dmn/recurso_eligibility.dmn:14` | **PROPOSTO** |
| R24 | regras `r_administrativa_recorrivel` e `r_tecnica_clinica_auditor` emitindo ambas `"RECORRIVEL"` (a segunda ausente da lista original — adicao do verificador) | `r_administrativa_merito` e `r_tecnica_clinica_auditor` emitindo `"SEGUE_MERITO"`; `grupo_revisor` (`analista-recurso-glosa` \| `medico-auditor`) inalterado | Q4: as DUAS regras substantivas emitiam o predicado do recorrente; renomear so uma deixaria metade da tabela invertida. | `spec/processes/dmn/recurso_eligibility.dmn:41` | **PROPOSTO** |
| R25 | `recurso_sla` com DUAS ancoras de partes opostas: o recebimento pelo pagador e o fail-safe `data_ciencia_glosa` (o relogio do RECORRENTE) | ancora UNICA `data_recebimento_recurso_iso` (data em que a operadora recebeu o recurso), normalizada pelo intake; a data alegada pelo prestador vira `data_ciencia_alegada_prestador`, registro do que ele declarou e nunca base do prazo da operadora | Q3: o prazo que este repositorio cronometra e o da RESPOSTA do pagador. O prazo de interposicao e do prestador e nao e responsabilidade nossa. Familia R1 `ancora-kpi` barra o retorno de `data_ciencia_glosa`. | `spec/processes/dmn/recurso_sla.dmn:27` | **PROPOSTO** |
| R26 | KPI `recurso_recovery_rate` do agente Marina (taxa de recuperacao de receita glosada — metrica de CREDOR) + prompts na voz do recorrente ("Voce NUNCA desiste de um recurso"; "Voce nao autora a peticao de recurso") | KPIs de pagador (`dossier_completeness`, `glosa_rate`, ...); `recurso_recovery_rate` removida e a remocao registrada em COMENTARIO YAML (Tier B da cerca le nos parseados, nao comentarios); prompts reescritos na voz do julgador | Q3: a metrica de quem teve dinheiro retido nao e metrica de quem reteve. O prompt de sistema de um LLM e a superficie de maior consequencia da inversao. | `spec/agents/marina/agent.yaml:58` | **PROPOSTO** |

---

## 3. Rotulos `PROPOSTO` que a sessao remove — lista exata

Estes sao os rotulos que **a sessao** retira ao ratificar. Nenhum agente os toca. Localizados
**por conteudo** (o registro de origem cita `SP-OP-CONTAS-001.md:166` e
`SP-OP-RECURSO-001.md:183,:189,:191`; os contratos foram reescritos desde entao e as linhas
correntes sao as abaixo — a divergencia esta registrada como INFO no relatorio da entrega):

| # | `arquivo:linha` | O que e |
|---|---|---|
| L1 | `docs/processes/contracts/SP-OP-CONTAS-001.md:198` | Nota de taxonomia OQ-6: `auditoria-contas` e `coordenacao-contas` sao nomes **PROPOSTOS** |
| L2 | `docs/processes/contracts/SP-OP-RECURSO-001.md:204` | Nota OQ: `analista-recurso-glosa` e `coordenacao-recurso` sao nomes **PROPOSTOS** |
| L3 | `docs/processes/contracts/SP-OP-RECURSO-001.md:210` | Rotulo *(PROPOSTO)* na linha do grupo `analista-recurso-glosa` |
| L4 | `docs/processes/contracts/SP-OP-RECURSO-001.md:212` | Rotulo *(PROPOSTO)* na linha do grupo `coordenacao-recurso` |

**Relacionado, e fora do escopo desta remocao:**
`docs/adr/0040-perspectiva-operadora-contas-recurso.md` OQ-6 registra os mesmos candidate groups
como PROPOSTOS. `docs/adr/` e CODEOWNED e o ADR tem status proprio (`Proposed`): promover o ADR e
um ato separado, em PR de owner-review, e nao decorre automaticamente da remocao dos rotulos nos
contratos.

**O que a sessao produz (R-019, `acao_seguinte`):**

1. `docs/processes/contracts/signoffs/SP-OP-CONTAS-001.signoff.yaml`;
2. `docs/processes/contracts/signoffs/SP-OP-RECURSO-001.signoff.yaml`;
3. a remocao dos rotulos L1–L4 acima.

Esquema do artefato de signoff: `docs/sme-dispatch/README.md` §"Signoff artifact spec". Este
pacote **nao cria, nao pre-preenche e nao infere** nenhum arquivo de signoff.

---

## 4. Consequencias que a ratificacao arrasta (nao sao itens de decisao)

**Nomes de teste** que carregavam o lexico do recorrente e ja foram reescritos junto com a cadeia
(`PERSPECTIVE-MATRIX.md` §5.1 *(fonte gitignored, ver §0)* lista os 6 originais):
`test_happy_path_recurso_indeferido_pela_operadora`, `test_happy_path_nao_recorrer_humano`,
`test_happy_path_recorrer_e_deferido`, `test_nao_recorrer_exige_campos_obrigatorios`,
`test_worker_guard_register_desistencia_*` e `test_coordenacao_assume_e_mantem_glosa_humano`.
Hoje o mesmo comportamento e coberto, entre outros, por
`tests/integration/processes/test_sp_op_recurso_001.py::test_happy_path_auditor_indefere_humano`,
`::test_indeferir_exige_campos_obrigatorios`,
`::test_worker_guard_registrar_indeferimento_recusa_sem_humano` e
`::test_coordenacao_assume_e_indefere_humano`.

**Cerca de regressao de vocabulario** (`src/maezo/platform/validation/perspective.py`, ADR-0040
D7): e uma cerca de **regressao lexical**, nao um validador de perspectiva — ela garante que os
construtos enumerados nao voltem, e nao detecta uma inversao renomeada com vocabulario que o
lexico nunca viu (limite declarado, pinado por `test_declared_limit_*`). O controle primario
continua sendo o teste humano de perspectiva desta sessao.

---

## 5. O que a sessao precisa decidir alem da tabela

Itens que a tabela **nao** resolve e que o mesmo quorum (regulatorio + auditoria de contas +
financas) tem a competencia para resolver na mesma sentada:

1. **Taxonomia organizacional** dos candidate groups `auditoria-contas`, `coordenacao-contas`,
   `analista-recurso-glosa`, `coordenacao-recurso` (OQ-6). `faturamento` NAO e variante aceitavel:
   e a funcao de cobranca do **prestador**.
2. **Cadeia de citacoes normativas** — RN 501/2022 como norma corrente do fluxo TISS e a retirada
   de RN 424/2017 (que so se aplica a junta medica/odontologica). A propria revisao interna esta
   marcada `[verify SME]`.
3. **ADR-0040 OQ-10 / achado M-4**: o conjunto negativo de `glosa_triage` ainda admite
   `categoria_normalizada = desconhecida` na row permissiva. Fecha-lo e ato do dono da tabela.
4. **OQ-7**: `DEVOLVER` e efeito adverso? Hoje classificado neutro -> prestador-adjacente.
5. **OQ-8**: `DEFERIR_PARCIAL` e adverso L0 para o prestador, por analogia a `End_ReembolsoParcial`?
6. **OQ-1 / OQ-5**: nomes e estrutura dos artefatos TISS ("Demonstrativo de Analise de Conta",
   "Recurso de Glosa", "Resposta ao Recurso de Glosa") e a tabela TISS de motivos de glosa.
   `docs/compliance/` nao contem o dicionario TISS.
7. **OQ-14**: dever e momento de comunicar o prestador no terminal de fraude (o unico dos seis
   terminais de negocio de CONTAS que nao emite comunicacao).

Nenhum destes itens foi decidido aqui. Onde o repositorio nao carrega fonte, este documento diz
que nao carrega, em vez de sugerir uma.
