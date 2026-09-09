# Os 4 campos de `phi-business-key-remediation.yaml` — diff CANDIDATO (R-008 / R-009)

> **rascunho — pendente de designação e assinatura do encarregado (LGPD art. 41)**
>
> **recomendação — pendente de assinatura de DPO (+ operação/ANS ouvidos)**
>
> **O arquivo `spec/policies/privacy/phi-business-key-remediation.yaml` NÃO FOI TOCADO.** Ele é
> CODEOWNED (`.github/CODEOWNERS` — regra `/spec/policies/privacy/`, donos `@rodaquino-OMNI
> @Omni-Saude/security-team @lucasreisEvah`) e os quatro campos são assinatura humana. O que
> existe aqui é o diff **aplicável** que o encarregado aplicaria.

**Ratificado por (DPO):** ______________________  **data:** ______________________

**Ouvido (operação/ANS) sobre a busca por matrícula no Cockpit:** ______________________  **data:** ______________________

---

## 1. Estado de hoje (verificado, não presumido)

```
$ grep -n '^status:\|^modo:\|^ratificacao:\|^  ratificado:\|^  revisor:\|^  ratificado_em:' \
      spec/policies/privacy/phi-business-key-remediation.yaml
59:status: DRAFT
112:modo: "off"
119:ratificacao:
120:  ratificado: false
121:  revisor: null
122:  ratificado_em: null
```

Regra do carregador, escrita no próprio arquivo: **faltando, em branco ou não-`true` qualquer um
dos quatro, o modo efetivo é `off`, independentemente do que `modo` diga.** Um arquivo em `DRAFT`
declarando `modo: pseudo_keys` resolve para `off` — o carregador nunca promove um rascunho.

---

## 2. Diff CANDIDATO — os 4 campos

> Placeholders `<...>` são para o **humano** preencher. Um agente que os preenchesse estaria
> fabricando uma aprovação — evento de compliance, não conflito de merge.

```diff
--- a/spec/policies/privacy/phi-business-key-remediation.yaml
+++ b/spec/policies/privacy/phi-business-key-remediation.yaml
@@ -54,9 +54,9 @@ version: 1
 
 # ---------------------------------------------------------------------------------------------
 # status — DRAFT | RATIFICADO. Porta dura: em DRAFT, `modo` NAO vale (resolve para `off`).
-# ESTADO DE HOJE: DRAFT. Nada foi decidido; nada esta ativo.
+# RATIFICADO em <AAAA-MM-DD> pelo encarregado (LGPD art. 41) -- ver `ratificacao` abaixo.
 # ---------------------------------------------------------------------------------------------
-status: DRAFT
+status: RATIFICADO
 
 # ---------------------------------------------------------------------------------------------
 # modo — o que o codigo faz QUANDO ratificado. DECLARACAO, nao efeito.
@@ -117,9 +117,9 @@ modo: "off"
 # preenchimento parcial esquecido.
 # ---------------------------------------------------------------------------------------------
 ratificacao:
-  ratificado: false
-  revisor: null
-  ratificado_em: null
+  ratificado: true
+  revisor: "<nome completo e funcao do encarregado>"
+  ratificado_em: "<AAAA-MM-DD>"
   observacao: >-
     DECISAO DO DONO, NAO DA ENGENHARIA. Sao tres perguntas distintas e uma delas nao e tecnica:
     (1) LGPD/DPO — `matricula_beneficiario` dentro de uma business key persistida (Postgres de
```

> **Este bloco aplica de verdade.** Foi gerado por `git diff` sobre uma cópia do arquivo real em
> `ce38100`, nunca escrito à mão — a 1ª redação deste rascunho tinha as contagens `@@`
> sub-descritas e `git apply --check` respondia *"corrupt patch at line 11"*. Para conferir à mão:
> copie este bloco (sem as cercas) para um arquivo e rode `git apply --check <arquivo>` a partir
> da raiz do repositório; o retorno esperado é 0, sem saída. O teste
> `tests/unit/docs/test_dpo_drafts_citations.py` segura isso automaticamente: ele relê cada hunk
> contra o arquivo real e fica **vermelho** se as contagens `@@` ou as linhas de contexto saírem
> do lugar.

**Conferência de tipos (o carregador é literal):** `ratificado` precisa ser o **booleano** `true`
— a string `"true"` não conta. `revisor` e `ratificado_em` precisam ser **não vazios**.
`status` precisa ser o literal `RATIFICADO`.

---

## 3. Hunk ACOMPANHANTE — `modo` (5ª linha, e a substância da decisão)

Os quatro campos acima **habilitam** o `modo`; eles não escolhem qual. Ratificar os quatro
deixando `modo: "off"` produz uma ratificação que **não muda nada** — estado legítimo, mas que
precisa ser escolhido conscientemente, não por esquecimento.

Recomendação (decisão aprovada do dono, **R-009**): manter `scrub_only`, **não** `pseudo_keys`.

```diff
--- a/spec/policies/privacy/phi-business-key-remediation.yaml
+++ b/spec/policies/privacy/phi-business-key-remediation.yaml
@@ -109,7 +109,7 @@ status: DRAFT
 # `on`/`yes`/`no`). O carregador trata esse caso explicitamente, entao desaspar nao muda o
 # significado — mas a forma com aspas e a que le como o token que e.
 # ---------------------------------------------------------------------------------------------
-modo: "off"
+modo: "scrub_only"
 
 # ---------------------------------------------------------------------------------------------
 # ratificacao — quem decidiu e quando (ADR-0007). Placeholders detectaveis por maquina:
```

### Por que `scrub_only` e não `pseudo_keys`

`scrub_only` **não muda nenhuma identidade persistida** (nenhuma migração). Fecha três saídas:

1. instala o `LogScrubber`/`BusinessKeyScrubber` em `structlog.configure`, cobrindo os campos que
   carregam key (`business_key`, `cancel_business_key`, `_business_key`, …) + `matricula_beneficiario`;
2. remove `_business_key` da allowlist do envelope espelhado em `operadora.notifications.internal`;
3. pseudonimiza a **chave de mensagem Kafka** das famílias CANCEL/INAD.

`pseudo_keys` é **não-ratificável hoje**: três dos quatro itens de `pre_requisitos_pseudo_keys`
estão `atendido: false` no próprio arquivo, e cada um tem efeito adverso nomeado —

| id | efeito se ratificado assim |
|---|---|
| `start_idempotency_dual_read` | **DUPLA ABERTURA** de processo (instância viva sob a forma legada fica invisível para um start keyed no pseudônimo) |
| `start_dedup_key_dual_read` | **DUPLO REGISTRO DE AUDITORIA** (a dedup key muda junto, `emit_once` não retorna `ALREADY_AUDITED`) |
| `beneficiario_pseudo_id_no_handoff` | **DEGRADAÇÃO SILENCIOSA** (o handoff INADIMPLÊNCIA→CANCEL não leva a âncora nova; a remediação parece ativa e não cobre o fluxo) |

Só `dual_read_escopo_atual` está `atendido: true` — e cobre **um** consumidor
(`inadimplencia._query_ja_em_rescisao_cancel`, o guard de anti-dupla-**terminação**), que roda
depois e para outra decisão.

---

## 4. As DUAS pré-condições de merge (R-009) — e a cerca de validação construída

Este PR **não pode ser mergeado** sem as duas:

| # | Pré-condição | Estado | Efeito adverso se ignorada |
|---|---|---|---|
| 1 | `phi/hmac-key` (`PHI_HMAC_KEY`) provisionado — M-24 / **D6-04**, HUMAN-GATED | **não provisionado** | Cada composition root reporta **NOT READY no boot**: `setup_observability` constrói o pseudonimizador via `Pseudonymizer.from_settings`, que levanta `PseudonymizerKeyMissingError` sem chave; `bootstrap_observability` captura e deixa a readiness vermelha. Segunda superfície independente: `key_scrubber.egress_message_key`, chamada pelo handler de `operadora.events.publish`, levanta na escada de retry/incidente do harness |
| 2 | **Janela de drenagem** dos tópicos CANCEL/INAD agendada | **não agendada** | `scrub_only` item (3) **muda o particionamento Kafka** dessas duas famílias (`CANCEL-{tenant}-{id}` → `CANCEL-hk1_{hmac}`). A ordenação por chave continua garantida (HMAC determinístico), mas mensagens em voo publicadas antes e depois da virada podem cair em partições diferentes |

**A cerca de CI de R-009 está construída e reparada contra R009-EIR01–06** em
`scripts/ci/check_phi_scrub_prereqs.py`, `Makefile` (`check-phi-scrub-prereqs`) e
`.github/workflows/ci.yml` (`validate-artifacts`). Antes de consultar o loader canônico,
reprova arquivo ausente/ilegível, UTF-8/YAML inválido, chaves duplicadas em qualquer nível,
modo desconhecido e promoção parcial. Só DRAFT bem formado e RATIFICADO/off deliberado
passam inertes; modos ativos normalizados exigem os dois itens atendidos.

`evidencia_provisionamento` é uma referência não secreta a recibo externo, com sintaxe
estrita definida no bloco `pre_requisitos_scrub_only` do manifesto. O recibo deve cobrir os
overlays Helm/ECS aplicáveis, operador, instante, referências/versionamento e resultados;
o CI **não busca nem verifica o conteúdo do recibo ou a existência atual do segredo**.
Passar com uma referência não comprova provisionamento. A revisão do recibo e a verificação
live continuam atos externos. Nunca inserir o valor de `PHI_HMAC_KEY` no manifesto.

A janela exige strings ISO-8601 com timezone e fim posterior ao início em UTC, sem duração
mínima inventada. Agendamento não comprova drenagem executada; um cutover concluído requer
evidência operacional separada. A cerca nunca escreve `status`/`ratificacao.*`; ambas as
pré-condições permanecem `atendido: false`, assinaturas nulas e modo efetivo `off`.
Ela não aprova os requisitos adicionais de `pseudo_keys`. Configurações escolhidas fora do
arquivo validado em CI dependem da fronteira operacional de deployment.

---

## 5. Evidência que acompanha o PR de ratificação (R-008)

As **seis superfícies duráveis** onde `matricula_beneficiario` sai cru hoje, conforme a própria
política declara: linhas de log structlog · variável de processo do motor · payload Kafka
(`_business_key`) · **chave de mensagem Kafka** · allowlist do espelho de notificações · chave de
dedup em Postgres.

Cunhagem: `src/maezo/tools/workers/base.py::mint_contract_business_key` — caminho único, seis
sítios enumerados no próprio docstring (`inadimplencia`, `fernando.graph`, `fraude` ×2,
`notification_bridge` ×2). Fallback para a matrícula ocorre **só** em planos
individuais/familiares (sem número de contrato) — a população de pessoa física.

Métrica-sombra já rodando sob `modo: off`:
`maezo_phi_business_key_mint_total{family, modo, anchor}` — a série `anchor="matricula"` é a
contagem de keys que hoje carregam matrícula crua. Labels são vocabulário fechado, sem conteúdo.

Precedente da casa para a forma correta: `PROG-…-{beneficiario_pseudo_id}`
(`agents/valentina/graph.py`), `DSR-…-{titular_pseudo_id}`.

---

## 6. A consulta à operação que ainda falta

O próprio campo `ratificacao.observacao` do arquivo declara três perguntas, e **uma não é
técnica**:

> (2) OPERAÇÃO/ANS — a key é visível no Cockpit e é o que um analista usa para achar o caso;
> pseudonimizar quebra essa busca por matrícula e exige um caminho de reconciliação.

**Esta consulta não foi feita por este rascunho.** R-008 exige que a operação seja ouvida
**antes** do PR de ratificação. Registrar a posição dela no corpo do PR e no campo acima.

---

## 7. Checklist de merge (para o revisor humano)

- [ ] Encarregado designado e registrado com nome + data em `docs/compliance/` (R-027 / D7-03)
- [ ] Posição da operação/ANS sobre a busca por matrícula colhida e anexada
- [ ] `PHI_HMAC_KEY` provisionado (M-24 / D6-04)
- [ ] Janela de drenagem CANCEL/INAD agendada, com data
- [ ] `pre_requisitos_scrub_only` do manifesto com os dois itens `atendido: true` e evidência real
      preenchida — `scripts/ci/check_phi_scrub_prereqs.py` (R-009, já landed) reprova o merge
      enquanto um dos dois continuar `false`
- [ ] Os 4 campos preenchidos com nome real e data real — nunca por agente
- [ ] `modo` escolhido conscientemente (`scrub_only` recomendado; `pseudo_keys` **bloqueado**)

---

## 8. Rastreabilidade

- Decisões do dono: **R-008** (assinante = o encarregado de D7-03; operação ouvida antes),
  **R-009** (alvo `scrub_only` + as duas pré-condições viram cerca de CI), **R-055** (os cinco
  artefatos prontos antes da designação).
- Gap: **D7-01** (P0).
- Piso preservado: o arquivo continua `status: DRAFT`, `modo: "off"`, `ratificado: false` — modo
  efetivo `off`, runtime byte-idêntico.

---

## 9. Adendo de re-verificação — 2026-09-05 (`ce38100`)

1. **Os dois blocos `diff` foram REGERADOS.** A 1ª redação (base `0433db0`) tinha as três
   contagens `@@` sub-descritas — `git apply --check` recusava com *"corrupt patch at line 11"*.
   Um documento que se apresenta como "o diff exato que o encarregado aplicaria" e não aplica é um
   defeito de confiança, não de formatação. Os blocos atuais foram produzidos por `git diff` sobre
   uma cópia do arquivo real e verificados com `git apply --check` (rc=0, os dois).
2. **Citação por linha do `.github/CODEOWNERS` trocada por citação da regra** (`/spec/policies/privacy/`),
   que é o que não se move.
3. `spec/policies/privacy/phi-business-key-remediation.yaml` continua **byte-idêntico** ao `main`.
