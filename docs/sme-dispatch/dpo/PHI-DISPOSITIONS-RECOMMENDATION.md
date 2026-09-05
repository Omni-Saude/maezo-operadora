# Bloco de disposições PHI — recomendação por NOME (D7-01 / M-50)

> **rascunho — pendente de designação e assinatura do encarregado (LGPD art. 41)**
>
> **recomendação — pendente de assinatura de DPO (+ operação/ANS)**
>
> Nada aqui é ratificação. Nenhum campo de `spec/policies/**` foi tocado por este documento.

**Ratificado por (DPO):** ______________________  **data:** ______________________

**Ouvido (operação/ANS):** ______________________  **data:** ______________________

---

## 1. Como o inventário foi construído (comando, não memória)

```
$ .venv/bin/python -c "from maezo.tools.workers.phi_vars import PHI_PROCESS_VARS
for n in sorted(PHI_PROCESS_VARS): print(n)"
cid10_referencia
diagnostico
fundamentacao_dut
justificativa_clinica
laudo
matricula_beneficiario
notas_resolucao
resumo_contexto

$ find spec/policies/phi -type f
spec/policies/phi/dossier-narrative-zone.yaml
```

**Correção de citação (drift, informativo).** O caminho canônico do módulo é
`src/maezo/tools/workers/phi_vars.py` — **não** `src/maezo/platform/phi_vars.py`, que não existe
(`ls` retorna "No such file or directory"). O registro de gaps já usa o caminho correto
(`D7-01.reproduction`).

`spec/policies/phi/` contém **um** arquivo, e ele não enumera nomes de variável: é o interruptor
de uma pergunta única (a narrativa do dossiê do Rafael é zona PHI ou zona geral) — tratado em §4.

Distribuição de cada nome pela árvore, **medida em `ce38100` (2026-09-05)**
(`grep -rl <nome> <dir> | wc -l`):

| nome | `spec/` | `src/` |
|---|---|---|
| `cid10_referencia` | 2 | 6 |
| `diagnostico` | 4 | 10 |
| `fundamentacao_dut` | 1 | 5 |
| `justificativa_clinica` | 1 | 6 |
| `laudo` | 0 | 2 |
| `matricula_beneficiario` | 4 | 17 |
| `notas_resolucao` | 1 | 3 |
| `resumo_contexto` | 1 | 6 |

As colunas `tests/` e `docs/` da primeira redação foram **removidas de propósito**, não
esquecidas: elas se movem a cada merge sem que nada da disposição mude, e a coluna `docs/` era
circular — este próprio pacote de rascunhos conta como ocorrência. `spec/` e `src/` são as duas
que respondem à pergunta do assinante ("o nome é vivo?"). Mesmo elas são um retrato datado: a
verdade permanente está nos símbolos citados na §3, não nas contagens.

---

## 2. Vocabulário das disposições

| Disposição | Significado operacional | Quem pode decidir |
|---|---|---|
| `PSEUDONIMIZAR` | O nome continua existindo, mas o VALOR que sai do worker vira um pseudônimo determinístico (HMAC keyed) | DPO (+ operação, quando quebra busca) |
| `SUPRIMIR` | O valor é substituído por token de classe na egressão (`REDACTED_PHI`); o conteúdo não sai da zona PHI | DPO |
| `RETER_COM_BASE_LEGAL` | O valor continua cru onde está, sob base legal declarada e prazo da matriz de retenção (AF-07) | DPO + jurídico |

---

## 3. Recomendação por NOME

### 3.0 O que `redact_phi_vars` cobre, MEDIDO — e o que ele não cobre

A primeira redação desta seção dizia *"todos os oito nomes já são suprimidos na egressão hoje"*.
**Isso é falso como estava escrito**, e é exatamente a frase que sustentava o "7 de 8: nada a
mudar". A verdade medida:

```
$ grep -rn "redact_phi_vars(" src/ | grep -v "^src/maezo/tools/workers/phi_vars.py"
src/maezo/tools/workers/auth.py:1547:            return redact_phi_vars(notice)
src/maezo/tools/mcp_cibseven/transport.py:824:        **redact_phi_vars(provenance.decision_basis),
```

`redact_phi_vars` tem **exatamente dois** call sites de produção em toda a árvore:

| Call site (símbolo) | O que ele protege | O que ele NÃO protege |
|---|---|---|
| `auth.py::SendDenialNoticeWorker.execute` | o dicionário de negativa que ESSE worker devolve | qualquer outro worker |
| `transport.py::build_start_audit_record` | o `decision_basis` que entra na cadeia de auditoria ADR-0007 | as `variables` do start (ligadas por `input_sha256`, não redigidas por este caminho) |

E a própria cerca da casa declara o limite, duas vezes, em
`src/maezo/platform/validation/phi_completeness.py`: *"`redact_phi_vars` runs only at worker
EGRESS toward the general zone"* e *"`redact_phi_vars` acts only at worker egress"*.

**Leitura correta, e é a que o assinante precisa:** os oito nomes são suprimidos **quando passam
por um desses dois pontos** — não "em toda a egressão", e não em repouso. Um worker que copie
process variables para um dict de saída sem chamar `redact_phi_vars` emite o valor cru; o motor
guarda o valor cru na variável de processo; a chave de negócio carrega a matrícula crua (item 8).
Os itens 7 e 8 abaixo são contraexemplos da frase antiga — e estavam, desde a primeira redação,
na mesma seção que ela.

### 3.1 A tabela por NOME

A recomendação abaixo é sobre o que **falta**, nome a nome. "SUPRIMIR (manter)" significa
*"a listagem em `PHI_PROCESS_VARS` está correta e não muda"* — **não** significa "já está
protegido em todo lugar".

| # | Nome | Onde vive hoje (símbolo verificado) | Disposição recomendada | Racional |
|---|---|---|---|---|
| 1 | `justificativa_clinica` | `auth.py::_REQUIRED_DENIAL_FIELDS`; egresso redigido em `auth.py::SendDenialNoticeWorker.execute` (o call site nº 1 de `redact_phi_vars`) | `SUPRIMIR` (manter como está) | Texto livre clínico exigido pela ANS na negativa. Já é `PHI_PROCESS_VARS`; a negativa vai por canal seguro fora de banda. Nada a mudar |
| 2 | `cid10_referencia` | `auth.py::_REQUIRED_DENIAL_FIELDS`; `reembolso.py::ReembolsoDenialInput.cid10_referencia` | `SUPRIMIR` (manter) | CID-10 é diagnóstico identificável por si; RN 395 art. 10 é citada no docstring de `auth.py::SendDenialNoticeWorker` como a razão de nunca transmitir. **Passa CRU pelo chokepoint de start (§3.2)** |
| 3 | `fundamentacao_dut` | `auth.py::_REQUIRED_DENIAL_FIELDS`; docstring de `base.py` (rol de nomes-PHI do harness de auth) | `SUPRIMIR` (manter) | Fundamentação DUT/ROL revela a condição clínica pela porta dos fundos |
| 4 | `laudo` | `phi_vars.py::PHI_PROCESS_VARS` **e** `phi_vars.py::PHI_FREE_TEXT_VARS`; token de forma `ShapeToken("laudo", …)` em `phi_completeness.py::SHAPE_TOKENS` | `SUPRIMIR` (manter) | **Mudou desde a 1ª redação (#318).** A afirmação anterior — "nenhum produtor/consumidor" — está superada: o nome agora é **consumido** pelo scrub de início de processo `redact_free_text_vars` (§3.2). Continua sem produtor próprio em `src/`, então segue sendo defesa pré-posicionada; mas remover o nome hoje enfraqueceria **dois** controles, não um |
| 5 | `diagnostico` | `phi_vars.py::PHI_PROCESS_VARS` **e** `phi_vars.py::PHI_FREE_TEXT_VARS`; token de forma `ShapeToken("diagnostic", …, STEM_MATCH)` | `SUPRIMIR` (manter) | Idem item 4, com o mesmo adendo de #318. O `STEM_MATCH` casa `diagnostica`/`diagnosticos`; remover o nome enfraqueceria a varredura de completude **e** o scrub de start |
| 6 | `notas_resolucao` | `phi_vars.py::PHI_PROCESS_VARS` **e** `PHI_FREE_TEXT_VARS`; cerca de publicação documentada no docstring de `events.py` (asserção `"notas_resolucao" not in e.payload` em `test_sp_op_escalation_001.py::test_happy_path_resolvido_por_humano`) | `SUPRIMIR` (manter) | Já há cerca no publicador de eventos; o nome é load-bearing para essa asserção |
| 7 | `resumo_contexto` | `helena/graph.py::HelenaGraph._start_escalation` (produtor) e `::_resumo_contexto`; `phi_vars.py::PHI_PROCESS_VARS` **e** `PHI_FREE_TEXT_VARS` | `SUPRIMIR` (manter) — **vetor REMEDIADO, ver adendo** | Era, na 1ª redação, o único nome documentado como **vetor de vazamento provado ao vivo**. **HEL-05 pousou em `ce38100`:** o produtor agora envolve o resumo em `redact_free_text` antes de escrevê-lo (`resumo = redact_free_text(await self._resumo_contexto(state, motivo))`), e o chokepoint CC-06 o cobre de novo no start (scrub duplo, idempotente por construção). O que **resta** para a revisão é a pergunta de base legal, não a de vazamento: o resumo — já com identificadores trocados por tokens de classe e **truncado em 500 caracteres** — continua sendo escrito em VARIÁVEL DE PROCESSO do motor |
| 8 | `matricula_beneficiario` | `phi_vars.py::PHI_PROCESS_VARS`; cunhado CRU em business key por `base.py::mint_contract_business_key` (6 sítios) | **`PSEUDONIMIZAR`** — via `modo: scrub_only` de `spec/policies/privacy/phi-business-key-remediation.yaml` | **O único item da lista que exige um ATO** (§3.3). Também **passa CRU pelo chokepoint de start** — §3.2 |


### 3.2 Aresta NOVA em `ce38100` (#318): o chokepoint de start, e os dois nomes que ele deixa passar

Depois que a 1ª redação deste dossiê foi escrita (base `0433db0`), o merge #318 trouxe um segundo
controle ancorado em nome, **diferente** de `redact_phi_vars`:
`phi_vars.py::PHI_FREE_TEXT_VARS` + `redact_free_text_vars`, aplicado por
`transport.py::redact_start_variables` no passo −1 de `start_process_idempotent` (CC-06). Ele age
na direção **agente → motor** (o `redact_phi_vars` age na direção **worker → zona geral**), e roda
a rede de identificadores sobre o TEXTO, não a substituição do valor inteiro.

`PHI_FREE_TEXT_VARS` tem 10 nomes; **seis** deles são de `PHI_PROCESS_VARS`
(`justificativa_clinica`, `fundamentacao_dut`, `notas_resolucao`, `resumo_contexto`, `laudo`,
`diagnostico`). **Os outros dois de `PHI_PROCESS_VARS` estão deliberadamente FORA**, e o próprio
módulo declara isso em maiúsculas:

> `# DISCLOSED, NOT FIXED HERE:` `matricula_beneficiario` `and` `cid10_referencia` `therefore pass
> through this edge unchanged.`

O motivo declarado é coerente: os dois são **identificadores estruturados**, não prosa — uma
matrícula é "um run de 11+ dígitos POR CONSTRUÇÃO", e passar uma rede de identificadores sobre eles
corromperia o processo. O controle certo para um identificador de valor inteiro é
`redact_phi_vars`, não uma rede de substring.

**O que isso significa para esta assinatura, sem eufemismo:** dos oito nomes, dois
(`matricula_beneficiario`, `cid10_referencia`) atravessam a aresta agente→motor **crus**, por
desenho declarado. Para `cid10_referencia` a exposição é limitada pelo formato (um código CID-10 é
um código, não um relato) e a disposição não muda. Para `matricula_beneficiario` esta é uma
**segunda** superfície crua, além da business key do item 8 — e é mais um argumento para o ato do
§3.3, não um argumento contra.

Não há defeito novo a corrigir aqui: a aresta está **declarada** no código. O que havia de defeito
era este dossiê **não a mencionar** ao assinante.

### 3.3 `matricula_beneficiario` — o único nome com disposição de MUDANÇA

O nome está declarado PHI pela própria casa (`phi_vars.py::PHI_PROCESS_VARS`) e mesmo assim
sai cru **dentro da business key** de duas famílias, quando não há número de contrato — ou seja,
exatamente na população de pessoa física (planos individuais/familiares):

- `CANCEL-{tenant}-{numero_contrato OR matricula_beneficiario}` —
  `src/maezo/tools/workers/inadimplencia.py::_cancel_business_key`
- `INAD-{tenant}-{numero_contrato OR matricula_beneficiario}` —
  `src/maezo/agents/fernando/graph.py::_business_key`

Ambos passam por `src/maezo/tools/workers/base.py::mint_contract_business_key`, que é o **único**
caminho de cunhagem (afirmação enumerada no próprio docstring: seis sítios, todos roteados por
ela).

Superfícies duráveis onde essa key aparece (as seis listadas pela própria política):
linhas de log structlog · variável de processo do motor · payload Kafka (`_business_key`) ·
**chave de mensagem Kafka** · allowlist do espelho de notificações · chave de dedup em Postgres.

**Precedente da casa para a forma correta:** `PROG-…-{beneficiario_pseudo_id}`
(`agents/valentina/graph.py`) e `DSR-…-{titular_pseudo_id}`.

**Disposição recomendada:** `PSEUDONIMIZAR` na forma `modo: scrub_only` — que fecha as saídas
(log, allowlist do espelho, chave de mensagem Kafka) **sem** mudar nenhuma identidade persistida
e **sem** migração. Não `pseudo_keys` nesta rodada: os três pré-requisitos de `pseudo_keys`
continuam `atendido: false` no próprio arquivo de política, e ratificar assim produziria
**dupla abertura de processo** (`start_idempotency_dual_read`) e **duplo registro de auditoria**
(`start_dedup_key_dual_read`).

Os quatro campos que materializam essa disposição, com o diff exato, estão no dossiê irmão:
[`PHI-BUSINESS-KEY-REMEDIATION-FIELDS-DRAFT.md`](PHI-BUSINESS-KEY-REMEDIATION-FIELDS-DRAFT.md).

**A pergunta que não é técnica e precisa da operação:** a business key é visível no Cockpit e é
o que um analista usa para **achar o caso pela matrícula**. Pseudonimizar quebra essa busca e
exige um caminho de reconciliação. A posição da operação/ANS sobre isso **não foi colhida** por
este rascunho — é uma consulta humana, e está declarada como pendência em §6.

---

## 4. `spec/policies/phi/dossier-narrative-zone.yaml` — a nona pergunta

Não é um nome de variável, mas é o outro item PHI sob `spec/policies/phi/**` e cai no mesmo
assinante. Estado hoje: `status: DRAFT`, `ratificado: false`, `zona_declarada: PENDING-ZONA`,
`dpo_review: PENDING-DPO-REVIEW`, `medico_auditor_review: PENDING-CLINICAL-REVIEW`.

Enquanto isso, `dossier_narrative_requires_phi_zone()` devolve `True` e a chamada em
`agents/rafael/graph.py` continua `phi=True` — o comportamento de hoje.

**Recomendação: NÃO ratificar `GERAL` nesta rodada.** O próprio arquivo registra que ratificar
`GERAL` significa, na prática, aceitar transferência internacional para essa narrativa (perfil
`global.*` do Bedrock; medição de 18/08/2026 em `sa-east-1`: nenhum perfil BR-resident de Claude
disponível). Essa é uma decisão jurídica **e** clínica: exige DPO **e** médico auditor, e o
arquivo exige um `graph_sha256` que amarra a aprovação aos bytes revisados. Fora do escopo de um
rascunho de agente.

**Ratificado por (DPO + médico auditor):** ______________________  **data:** ______________________

---

## 5. O que este dossiê deliberadamente NÃO cobre (estreitamento DECLARADO)

O inventário da §1 é `PHI_PROCESS_VARS` (8 nomes) + o único arquivo de `spec/policies/phi/`. Isso
é **mais estreito** do que "a disposição por nome na plataforma", e o estreitamento é escolha —
declarada aqui para que não passe por omissão.

**1. O controle ancorado em nome da plataforma são DOZE nomes, não oito.** A própria cerca de
completude abre com isso (`phi_completeness.py`, docstring de módulo): *"`redact_phi_vars` …
replaces a value only when its KEY is one of the eight names in `PHI_PROCESS_VARS`, and
`LogScrubber` only when its key is one of the four in `gateway.pseudonymizer.PHI_FIELDS`"*. Os
quatro do `LogScrubber` são `cpf`, `nome`, `telefone`, `email`
(`gateway/pseudonymizer.py::PHI_FIELDS`) — dados de identificação direta, não conteúdo clínico, e
com caminho reversível próprio (o surrogate store do gateway). Ficam fora **desta rodada** porque
sua disposição não é uma pergunta em aberto do mesmo tipo: nenhum deles é candidato a listar/não
listar; são a linha de base. Se o encarregado quiser revisá-los, é uma rodada própria.

**2. `phi_completeness.py::DISPOSITIONS` carrega NOVE recomendações por nome, todas já endereçadas
a este mesmo assinante, todas em `DRAFT/verify (DPO)` — e nenhuma delas está na tabela da §3.1:**

| Nome | Onde a cerca o encontrou | Recomendação registrada pela engenharia |
|---|---|---|
| `detalhes_requisicao` | `SP-OP-LGPD-DSR-001` (rol VARIAVEIS DE ENTRADA) | LISTAR — "o caso mais forte da tabela" |
| `fundamentacao_legal` | `SP-OP-LGPD-DSR-001` (outputParameter + JUEL do `GW_GuardFundamentacao`) | LISTAR — mesma classe do irmão já listado `fundamentacao_dut` |
| `cid10` | `SP-OP-AUTH-001`, `SP-OP-RECURSO-001`, `SP-OP-REEMBOLSO-001` | LISTAR — é o mesmo dado de `cid10_referencia` sob uma segunda grafia |
| `diagnostico_oncologico_confirmado` | `dut_criteria_oncologia_pet_ct.dmn` | LISTAR — booleano, mas afirma diagnóstico sobre titular identificado |
| `diagnostico_tea_ou_neurodesenvolvimento` | `dut_criteria_terapias_especiais.dmn` | LISTAR — idem, e sobre menor |
| `exames_convencionais_inconclusivos` | `dut_criteria_oncologia_pet_ct.dmn` | LISTAR — afirma evento de cuidado e resultado |
| `has_cid10_codes` | `phantom_no_diagnosis.dmn` | **NÃO** listar — flag de presença sobre uma conta, sem código e sem diagnóstico |
| `sintoma_codigo` | as quatro `triage_redflag_*.dmn` | ver a entrada; decisão do DPO |
| `auditor_id` | `SP-OP-AUTH-001` | ver a entrada; decisão do DPO |

Por que ficam fora deste lote: os nove são **perguntas de ampliação** do conjunto ancorado —
cada uma exige medir custo de decisão (qual DMN lê o nome), e duas delas
(`detalhes_requisicao`, `fundamentacao_legal`) estão amarradas ao procedimento de DSR, que tem
dossiê próprio neste mesmo pacote (`DSR-PROCEDURE-DRAFT.md` §2.4). Este dossiê responde a
**D7-01**, que é a disposição dos oito nomes JÁ listados. Ampliar o conjunto é um segundo ato,
com um segundo PR sob `spec/policies/**`.

**Se o encarregado quiser um único ato**, a ordem recomendada é: (a) assinar os oito desta
tabela; (b) na mesma sessão, decidir `detalhes_requisicao` e `fundamentacao_legal`, porque são as
duas em que um procedimento manual **já em uso** manda um humano digitar texto livre; (c) deixar
as seis restantes para a rodada seguinte.

---

## 6. Pendências que só um humano fecha

1. **Designação do encarregado** (R-027 / D7-03, teto 2026-09-19) registrada com nome e data em
   `docs/compliance/`. Sem isso não há assinante para nada acima.
2. **Posição da operação/ANS** sobre a busca por matrícula no Cockpit (§3.3). O registro de
   decisões (R-008) exige que a operação seja ouvida **antes** do PR de ratificação.
3. **Cerca de CI dos pré-requisitos de `scrub_only`** (R-009): um
   `scripts/ci/check_phi_scrub_prereqs.py` que reprove se o arquivo sair de `DRAFT` sem
   `phi/hmac-key` provisionado e sem a janela de drenagem CANCEL/INAD registrada. **Não foi
   construído neste rascunho** — `scripts/ci/` é CODEOWNED e a cerca não estava no escopo deste
   lote. Fica declarada como PR acompanhante obrigatório.
4. **Provisionamento de `PHI_HMAC_KEY`** (M-24 / D6-04). Sem ele, um `scrub_only` ratificado faz
   cada composition root reportar **NOT READY no boot** — `bootstrap_observability` captura o
   `PseudonymizerKeyMissingError` e deixa a readiness vermelha
   (`src/maezo/platform/observability.py`, seção "WHERE THAT RAISE SURFACES").
5. **Decisão sobre `laudo` e `diagnostico`** (itens 4 e 5): manter como reserva preventiva ou
   substituir pelos nomes reais dos fluxos que os produzirão. **Adendo #318:** os dois agora são
   consumidos por `PHI_FREE_TEXT_VARS` (§3.2), então removê-los custa **dois** controles; a
   recomendação da engenharia passa a ser **manter**, e a pergunta que sobra é só se algum fluxo
   futuro os produzirá sob outro nome.

---

## 7. Rastreabilidade

- Decisões do dono que autorizam este rascunho: **R-008** (quem assina os 4 campos), **R-009**
  (alvo `scrub_only`, não `pseudo_keys`), **R-055** (os cinco artefatos prontos antes da
  designação).
- Gap: **D7-01** (P0) — `matricula_beneficiario` cunhado cru em business keys duráveis.
- Piso preservado: nenhum campo de `spec/policies/privacy/phi-business-key-remediation.yaml` foi
  alterado; `status` continua `DRAFT` e o modo efetivo continua `off`.

---

## 8. Adendo de re-verificação — 2026-09-05 (`ce38100`)

A 1ª redação deste dossiê foi escrita sobre `0433db0`. Re-verificado contra `ce38100` (#318), o
que mudou — declarado, não reescrito em silêncio:

1. **§3.0 é NOVA e substitui uma frase falsa.** "Todos os oito nomes já são suprimidos na egressão
   hoje" foi trocada pela medição: `redact_phi_vars` tem **dois** call sites de produção e age
   **só** na egressão de worker.
2. **§3.2 é NOVA.** #318 trouxe `PHI_FREE_TEXT_VARS` / o chokepoint de start, e declarou que
   `matricula_beneficiario` e `cid10_referencia` **passam crus** por essa aresta. A 1ª redação não
   podia saber; o dossiê agora cobre.
3. **Item 7 (`resumo_contexto`) foi corrigido na direção oposta:** HEL-05 pousou em #318 e o
   produtor agora envolve o resumo em `redact_free_text`. Descrever um vazamento **já remediado**
   como vivo é defeito da mesma espécie que o inverso.
4. **Itens 4 e 5 (`laudo`, `diagnostico`)**: deixaram de ser "sem produtor/consumidor" — passaram a
   ser consumidos pelo scrub de start.
5. **Citações por linha trocadas por citação por símbolo** em toda a §3 (as de `phi_vars.py` e
   `helena/graph.py` já haviam se movido um merge depois de escritas). A regra da casa é citar por
   símbolo; `tests/unit/docs/test_dpo_drafts_citations.py` passa a segurar isso por teste.
6. **§5 declara o estreitamento** (8 nomes de 12, e as 9 `DISPOSITIONS` fora do lote).
7. **§1: as colunas voláteis `tests/`/`docs/` saíram**; `spec/`/`src/` foram remedidas em
   `ce38100`.

Nada em `spec/`, `src/` ou `spec/policies/**` foi alterado; os campos de assinatura continuam
**vazios**.
