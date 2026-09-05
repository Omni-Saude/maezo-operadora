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

Distribuição de cada nome pela árvore (`grep -rl <nome> <dir> | wc -l`):

| nome | `spec/` | `src/` | `tests/` | `docs/` |
|---|---|---|---|---|
| `cid10_referencia` | 2 | 6 | 6 | 8 |
| `diagnostico` | 4 | 10 | 4 | 4 |
| `fundamentacao_dut` | 1 | 5 | 4 | 8 |
| `justificativa_clinica` | 1 | 6 | 7 | 7 |
| `laudo` | 0 | 2 | 3 | 4 |
| `matricula_beneficiario` | 4 | 17 | 19 | 16 |
| `notas_resolucao` | 1 | 3 | 3 | 5 |
| `resumo_contexto` | 1 | 5 | 13 | 6 |

---

## 2. Vocabulário das disposições

| Disposição | Significado operacional | Quem pode decidir |
|---|---|---|
| `PSEUDONIMIZAR` | O nome continua existindo, mas o VALOR que sai do worker vira um pseudônimo determinístico (HMAC keyed) | DPO (+ operação, quando quebra busca) |
| `SUPRIMIR` | O valor é substituído por token de classe na egressão (`REDACTED_PHI`); o conteúdo não sai da zona PHI | DPO |
| `RETER_COM_BASE_LEGAL` | O valor continua cru onde está, sob base legal declarada e prazo da matriz de retenção (AF-07) | DPO + jurídico |

---

## 3. Recomendação por NOME

Todos os oito nomes já são **suprimidos na egressão** hoje: `redact_phi_vars`
(`src/maezo/tools/workers/phi_vars.py`) troca todo valor não vazio sob esses nomes por
`REDACTED_PHI` antes de o worker devolver o dicionário. A recomendação abaixo é sobre o que
**falta**, nome a nome — não sobre re-decretar o que já funciona.

| # | Nome | Onde vive hoje (símbolo verificado) | Disposição recomendada | Racional |
|---|---|---|---|---|
| 1 | `justificativa_clinica` | `auth.py:113` (`_REQUIRED_DENIAL_FIELDS`), `auth.py:1539`; egresso barrado em `auth.py:1419-1456` | `SUPRIMIR` (manter como está) | Texto livre clínico exigido pela ANS na negativa. Já é `PHI_PROCESS_VARS`; a negativa vai por canal seguro fora de banda. Nada a mudar |
| 2 | `cid10_referencia` | `auth.py:114`, `auth.py:1540`; `reembolso.py:374` | `SUPRIMIR` (manter) | CID-10 é diagnóstico identificável por si; RN 395 art. 10 é citada em `auth.py:1398` como a razão de nunca transmitir |
| 3 | `fundamentacao_dut` | `auth.py:115`, `auth.py:1541`; `base.py:43` | `SUPRIMIR` (manter) | Fundamentação DUT/ROL revela a condição clínica pela porta dos fundos |
| 4 | `laudo` | apenas `phi_vars.py:60` em `src/` (nenhum produtor/consumidor) | `SUPRIMIR` (manter) — **e revisar se o nome é vivo** | Zero call sites em `src/` fora da própria lista. É defesa preventiva, não cobertura de um fluxo existente. O encarregado deve decidir se mantém como reserva (recomendado) ou se o nome real do fluxo é outro |
| 5 | `diagnostico` | `phi_vars.py:61`; token de forma em `phi_completeness.py:309` | `SUPRIMIR` (manter) | Idem item 4: sem produtor próprio hoje, mas o guard de completude (`STEM_MATCH`) casa `diagnostica`/`diagnosticos` — remover o nome enfraqueceria essa varredura |
| 6 | `notas_resolucao` | `phi_vars.py:57`; `events.py:68-70` (asserção `"notas_resolucao" not in e.payload`) | `SUPRIMIR` (manter) | Já há cerca no publicador de eventos; o nome é load-bearing para essa asserção |
| 7 | `resumo_contexto` | `helena/graph.py:430`, `:680`, `:696`, `:709`, `:835`, `:870` | `SUPRIMIR` + **manter o alerta de `graph.py:835`** | É o único nome desta lista que a casa já documenta como **vetor de vazamento provado ao vivo**: a Helena escreve o resumo em VARIÁVEL DE PROCESSO do motor (`graph.py:430`). O comentário de `:709` restringe o roteamento a tokens limitados; a disposição não muda, mas a revisão deve confirmar que a variável de processo é aceitável sob a base legal declarada |
| 8 | `matricula_beneficiario` | `phi_vars.py:59`; cunhado CRU em business key por `base.py::mint_contract_business_key` (6 sítios) | **`PSEUDONIMIZAR`** — via `modo: scrub_only` de `spec/policies/privacy/phi-business-key-remediation.yaml` | **Este é o único item da lista que exige um ATO.** Ver §3.1 |

### 3.1 `matricula_beneficiario` — o único nome com disposição de MUDANÇA

O nome está declarado PHI pela própria casa (`PHI_PROCESS_VARS`, `phi_vars.py:59`) e mesmo assim
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
este rascunho — é uma consulta humana, e está declarada como pendência em §5.

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

## 5. Pendências que só um humano fecha

1. **Designação do encarregado** (R-027 / D7-03, teto 2026-09-19) registrada com nome e data em
   `docs/compliance/`. Sem isso não há assinante para nada acima.
2. **Posição da operação/ANS** sobre a busca por matrícula no Cockpit (§3.1). O registro de
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
   substituir pelos nomes reais dos fluxos que os produzirão.

---

## 6. Rastreabilidade

- Decisões do dono que autorizam este rascunho: **R-008** (quem assina os 4 campos), **R-009**
  (alvo `scrub_only`, não `pseudo_keys`), **R-055** (os cinco artefatos prontos antes da
  designação).
- Gap: **D7-01** (P0) — `matricula_beneficiario` cunhado cru em business keys duráveis.
- Piso preservado: nenhum campo de `spec/policies/privacy/phi-business-key-remediation.yaml` foi
  alterado; `status` continua `DRAFT` e o modo efetivo continua `off`.
