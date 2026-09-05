# Dossiê CANDIDATO — matriz de bases legais / retenção (AF-07)

> **rascunho — pendente de designação e assinatura do encarregado (LGPD art. 41)**
>
> **recomendação — pendente de assinatura de DPO (+ jurídico)**
>
> `CANDIDATO-NÃO-RATIFICADO`. Este documento **não** é a matriz. Ele é a folha de trabalho
> que o encarregado revisa e assina; enquanto a assinatura não existir, nada muda em runtime.

**Ratificado por (DPO):** ______________________  **data:** ______________________

**Ratificado por (jurídico):** ______________________  **data:** ______________________

---

## 1. O que este dossiê é, e o que ele não é

| É | Não é |
|---|---|
| Uma proposta linha-a-linha (`categoria`, `base_legal`, `retencao`, `acao`) para o encarregado revisar | A matriz ratificada |
| Um mapa entre cada categoria e as **relações persistentes reais** que os 3 CronJobs de lifecycle tocam | Uma autorização para eliminar qualquer dado |
| O bloco YAML exato que o encarregado assinaria, como bloco cercado **dentro deste .md** | Um arquivo sob `spec/policies/retention/` — esse diretório continua intocado |

Nenhum agente preenche `Ratificado por`. `docs/sme-dispatch/README.md` §"Signoff artifact spec"
(regra 5 do protocolo de redline, `docs/sme-dispatch/README.md:109-111`) proíbe explicitamente que
um agente supra assinatura humana.

### Piso que continua valendo depois deste rascunho (não é retórica — é o código)

- `src/maezo/platform/lifecycle/legal_bases_matrix.py::load_retention_matrix` **recusa sempre**
  enquanto não houver um arquivo ratificado apontado por `MAEZO_RETENTION_MATRIX_PATH`
  (`RetentionMatrixUnavailableError`, razões `REASON_PATH_NOT_SET` … `REASON_UNRATIFIED_TEMPLATE`).
- O template de escopo continua marcado `unratified: true`
  (`spec/policies/retention/UNRATIFIED-retention-matrix.template.yaml`) e o próprio carregador
  recusa esse arquivo por essa raiz.
- `src/maezo/platform/erasure.py::ErasureManager.erase()`/`.verify()` levantam
  `ErasureNotImplementedError` **sempre**, hoje.
- Os 3 CronJobs (`deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml`) invocam
  `python -m maezo.platform.lifecycle <cmd>`, que **recusa e sai com `REFUSAL_EXIT_CODE = 78`**
  em toda invocação (`src/maezo/platform/lifecycle/__init__.py`). Assinar esta matriz **não**
  liga nenhum dos três: o mecanismo de execução continua ausente (ver §5).

---

## 2. Escopo — opção **B** (decisão aprovada do dono, R-022)

Cobertura: **apenas as camadas que os 3 CronJobs de lifecycle tocam**. Todas as demais relações
enumeradas em `src/maezo/platform/lifecycle/erasure_plan.py::PERSISTENCE_LAYERS` (16 relações)
ficam **declaradas como NÃO COBERTAS** por esta matriz — não são omissão, são exclusão explícita.

| CronJob (`values.yaml::lifecycle.jobs`) | Comando | Camadas / relações que ele tocaria |
|---|---|---|
| `lifecycle-expurgo-working` (`0 3 * * *`) | `expurgo-working` | `trabalho`: `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`, `checkpoint_migrations` (+ `agent_checkpoints`/`agent_checkpoint_writes`, RETIRADAS na migração 0006) |
| `lifecycle-verify-erasure` (`0 4 1 * *`) | `verify-erasure` | `episodica`: `agent_memory`; `registro_de_eliminacao`: `erasure_log` (+ `semantica`/`agent_memory.embedding`, **RETIRADA** na migração `0009_drop_pgvector`) |
| `lifecycle-audit-retention` (`0 5 1 * *`) | `audit-retention` | `auditoria`: `audit_chain`, `audit_emit_dedup` |

**Camadas RETIRADAS por migração (a entrada fica, o dado não existe):** `trabalho`/
`agent_checkpoints` e `trabalho`/`agent_checkpoint_writes` (`0006_retire_dead_checkpoint_tables`)
e, **novo em `abb9d60`**, `semantica`/`agent_memory.embedding` (`0009_drop_pgvector`, DU-01-b,
decisão do dono R-005). As três continuam enumeradas em `PERSISTENCE_LAYERS` com
`resolucao: RETIRADA` e sem probe — o encarregado não assina retenção sobre elas porque não há
dado; assina o reconhecimento de que a camada existiu. Ver §8.2.

**Fora do escopo B (declaradas não cobertas):** `custodia`/`custody_bundles`,
`idempotencia`/`a2a_idempotency`, `idempotencia`/`driver_idempotency`, `inbox_amh`/`amh_inbox`,
`outbox_a2a`/`a2a_fact_outbox`. Essas cinco relações continuam sem decisão de retenção — e sem
qualquer mecanismo que as toque.

---

## 3. Proposta linha-a-linha

Origem material das colunas `base_legal`/`retencao`: o rascunho inicial em
`docs/sme-dispatch/AI-SUGGESTED-ANSWERS-2026-07-25.md` §A2, que o próprio template cita como a
fonte das categorias. **Cada prazo abaixo é uma sugestão a confirmar pelo jurídico, nunca um
decreto.** O princípio de controle proposto: LGPD art. 18 VI (eliminação) cede a LGPD art. 16 I
(retenção exigida por obrigação legal/regulatória).

### 3.1 Categorias DENTRO do escopo B

| # | `categoria` | Onde materializa (relação → CronJob) | `base_legal` a confirmar | `retencao` proposta | `acao` proposta | Mecanismo de eliminação que **existe** hoje em `src/` |
|---|---|---|---|---|---|---|
| 1 | `dados_saude_prontuario` | `checkpoint_blobs` (BYTEA que carrega PHI) → `expurgo-working`; `agent_memory` → `verify-erasure` (a coluna `agent_memory.embedding` era a camada `semantica` e foi **RETIRADA** por `0009_drop_pgvector`) | LGPD art. 11 + art. 16 I; **Lei 13.787/2018 art. 6**; CFM Res. 1.821/2007 | **≥ 20 anos** do último lançamento | `RETER_COM_BASE_LEGAL` | **Nenhum.** `ErasureManager._erase_working/_erase_episodic/_erase_semantic` não existem como SQL; `erase()` levanta `ErasureNotImplementedError` (`src/maezo/platform/erasure.py::ErasureManager.erase`) |
| 2 | `cadastrais_contratuais` | `agent_memory` (linhas com `fhir_patient_id`) → `verify-erasure`; `checkpoints`/`checkpoint_writes` (variáveis de processo) → `expurgo-working` | LGPD art. 7 V (execução de contrato) + art. 16 I; Cód. Civil art. 206 | **vínculo + 5 anos** | `ELIMINAR` após o prazo | **Nenhum.** Mesmo bloqueio do item 1; ver também a ponte de identidade ausente em §5 |
| 3 | `consentimento_revogacao` | `audit_chain` (`decision_basis` jsonb, migração `0002:36`) → `audit-retention` | LGPD art. 16 I; accountability art. 37/50 | enquanto durar + **5 anos** pós-cessação | `RETER_COM_BASE_LEGAL` | **Nenhum.** A poda de `audit_chain` está bloqueada (§5, ADR-0020/ADR-0029) |
| 4 | `auditoria_nao_repudio` | `audit_chain`, `audit_emit_dedup` (`0005:59-67`) → `audit-retention` | ADR-0007; LGPD art. 16 I + art. 7 VI (defesa em processo) | **5 anos** (a mesma janela que `retention.py` assume) | `RETER_COM_BASE_LEGAL` — nunca eliminável a pedido do titular | **Bloqueado por desenho.** `RetentionManager.retention_query()` (`src/maezo/platform/retention.py`) constrói o `DELETE FROM audit_chain WHERE ts < cutoff` **sem** predicado de legal-hold e **sem** re-âncora; tem ZERO chamadores de produção, travado em CI |

> Observação para a revisão (item 3): a rastreabilidade de consentimento hoje mora **dentro** do
> `decision_basis` jsonb de `audit_chain`, que não tem coluna de titular
> (`erasure_plan.py::PERSISTENCE_LAYERS`, `IdentityResolution.SEM_COLUNA_DE_TITULAR`). O
> encarregado pode preferir tratar `consentimento_revogacao` como subconjunto de
> `auditoria_nao_repudio` em vez de categoria própria; as duas formas estão preparadas no bloco §4.

### 3.2 Categorias enumeradas no template e **FORA** do escopo B

| `categoria` do template | Por que fica fora | Onde ela materializaria |
|---|---|---|
| `financeiros_faturamento` | Nenhuma das relações dos 3 CronJobs guarda mensalidade/boleto/CNAB/glosa como linha própria | fora da árvore de persistência enumerada em `PERSISTENCE_LAYERS` |
| `regulatorios_ans` | Idem — dado agregado de envio periódico, não materializado nessas camadas | idem |
| `evidencia_fraude` | Vive em `custody_bundles` (camada `custodia`), que **nenhum** dos 3 CronJobs toca | `custody_bundles` (`0004:31-45`) |

Essas três permanecem **sem base legal e sem prazo ratificados** — e é exatamente isso que a
opção B declara.

---

## 4. O bloco YAML exato que o encarregado assinaria

> **Este bloco NÃO é um arquivo.** Ele vive aqui dentro de propósito. `spec/policies/retention/`
> é CODEOWNED (`.github/CODEOWNERS`, regra `/spec/policies/retention/`, donos `@rodaquino-OMNI
> @Omni-Saude/security-team @lucasreisEvah`) e continua intocado até a designação do encarregado
> (R-027 / D7-03) estar registrada em `docs/compliance/`.
>
> A raiz `unratified: true` está **presente de propósito**: se alguém copiar este bloco para um
> arquivo antes da assinatura, `load_retention_matrix` o recusa
> (`REASON_UNRATIFIED_TEMPLATE`). **Remover essa raiz é o ato do encarregado, não do agente.**

```yaml
# spec/policies/retention/retention-matrix.yaml  (NOME PROPOSTO — arquivo ainda não criado)
#
# CANDIDATO-NÃO-RATIFICADO. Escopo B (R-022): cobre APENAS as camadas dos 3 CronJobs de
# lifecycle; as demais categorias/relações estão declaradas não cobertas em `escopo_b` abaixo.
#
# PARA RATIFICAR: (1) remover a raiz `unratified` (ou deixá-la `false`); (2) confirmar cada
# `base_legal`/`retencao` com o jurídico; (3) preencher `ratificacao` com nome real e data;
# (4) apontar `MAEZO_RETENTION_MATRIX_PATH` para este arquivo.

unratified: true          # <- o encarregado remove esta linha ao assinar

# `ratificacao` e `escopo_b` são IGNORADOS pelo carregador (ele lê apenas `unratified` e
# `categorias` — legal_bases_matrix.py::load_retention_matrix). Ficam no arquivo para a
# auditoria humana, não para o código.
ratificacao:
  ratificado: false
  revisor: null           # nome + função do encarregado (LGPD art. 41)
  ratificado_em: null     # YYYY-MM-DD
  revisor_juridico: null
  escopo: "B — apenas as camadas dos 3 CronJobs de lifecycle"

escopo_b:
  camadas_cobertas:
    - camada: trabalho            # CronJob lifecycle-expurgo-working
      tabelas: [checkpoints, checkpoint_blobs, checkpoint_writes, checkpoint_migrations]
    - camada: episodica           # CronJob lifecycle-verify-erasure
      tabelas: [agent_memory]
    - camada: semantica           # CronJob lifecycle-verify-erasure
      tabelas: ["agent_memory.embedding"]
      retirada: true              # <- NAO ha dado a reter nesta camada; entrada mantida de proposito
      motivo_retirada: >-
        RETIRADA pela migracao `0009_drop_pgvector` (DU-01-b, decisao do dono R-005, 2026-09-04).
        Em `erasure_plan.py::PERSISTENCE_LAYERS` a entrada ordem 8 esta `resolucao: RETIRADA` e
        `count_statement: None` -- mesmo tratamento de `agent_checkpoints`/`agent_checkpoint_writes`
        (criadas por 0001, removidas por 0006). A linha PERMANECE: apagar uma camada encolhe em
        silencio um escopo de revisao que so o encarregado pode encolher.
    - camada: registro_de_eliminacao
      tabelas: [erasure_log]
    - camada: auditoria           # CronJob lifecycle-audit-retention
      tabelas: [audit_chain, audit_emit_dedup]
  nao_cobertas:
    - camada: custodia
      tabelas: [custody_bundles]
      motivo: "nenhum dos 3 CronJobs toca esta relacao (evidencia_fraude)"
    - camada: idempotencia
      tabelas: [a2a_idempotency, driver_idempotency]
      motivo: "fora do escopo B; sem decisao de retencao ratificada"
    - camada: inbox_amh
      tabelas: [amh_inbox]
      motivo: "fora do escopo B; sem decisao de retencao ratificada"
    - camada: outbox_a2a
      tabelas: [a2a_fact_outbox]
      motivo: "fora do escopo B; sem decisao de retencao ratificada"
  categorias_nao_cobertas:
    - financeiros_faturamento
    - regulatorios_ans
    - evidencia_fraude

categorias:
  - categoria: "dados_saude_prontuario"
    base_legal: "LGPD art. 11 + art. 16 I; Lei 13.787/2018 art. 6; CFM Res. 1.821/2007 -- CONFIRMAR JURIDICO"
    retencao: "20 anos a contar do ultimo lancamento -- CONFIRMAR JURIDICO"
    acao: "RETER_COM_BASE_LEGAL"
  - categoria: "cadastrais_contratuais"
    base_legal: "LGPD art. 7 V + art. 16 I; Codigo Civil art. 206 -- CONFIRMAR JURIDICO"
    retencao: "vinculo ativo + 5 anos -- CONFIRMAR JURIDICO"
    acao: "ELIMINAR apos o prazo"
  - categoria: "consentimento_revogacao"
    base_legal: "LGPD art. 16 I; accountability art. 37/50 -- CONFIRMAR JURIDICO"
    retencao: "enquanto durar o tratamento + 5 anos pos-cessacao -- CONFIRMAR JURIDICO"
    acao: "RETER_COM_BASE_LEGAL"
  - categoria: "auditoria_nao_repudio"
    base_legal: "ADR-0007; LGPD art. 16 I + art. 7 VI -- CONFIRMAR JURIDICO"
    retencao: "5 anos -- CONFIRMAR JURIDICO"
    acao: "RETER_COM_BASE_LEGAL -- nunca eliminavel a pedido do titular"
```

**Conferência de schema (feita, não presumida):** `REQUIRED_ENTRY_FIELDS` é
`("categoria", "base_legal", "retencao", "acao")`; cada valor precisa ser string **não vazia**;
`categoria` duplicada é recusada. As quatro entradas acima satisfazem todas essas regras — o
único motivo pelo qual o carregador ainda recusaria é a raiz `unratified: true`, que é
deliberada.

---

## 5. O que a assinatura desta matriz **não** destrava

Assinar aqui não é um gatilho destrutivo. Três bloqueios independentes continuam de pé:

1. **`ErasureManager` não executa nada.** `erase()`/`verify()` levantam
   `ErasureNotImplementedError` (`src/maezo/platform/erasure.py::ErasureManager.erase` e
   `::ErasureManager.verify`); o SQL por camada
   não existe.
2. **Duas pontes de identidade não existem** (`erasure_plan.py`, seção "THE TWO MISSING IDENTITY
   BRIDGES"): `titular_pseudo_id → fhir_patient_id` e `thread_id → fhir_patient_id`. Sem elas,
   um dry-run reporta `NOT_COUNTED_IDENTITY_BRIDGE_ABSENT` para **toda** camada — e a camada
   `trabalho` inteira é `IdentityResolution.NAO_PROVISIONADA`.
3. **`audit-retention` tem bloqueio próprio, alheio a esta matriz:** registro de legal-hold
   (ADR-0020 amendment) + re-âncora de checkpoint assinado (ADR-0029). Ambos não ratificados.
   Sem os dois, o `DELETE` apagaria evidência sob legal-hold e cortaria a contiguidade genesis
   do `verify_chain()`.

Consequência prática: mesmo com esta matriz assinada, `python -m maezo.platform.lifecycle`
continua saindo com 78 nos três CronJobs. É por isso que existe o segundo artefato,
independente, `spec/policies/retention/erasure-plan.template.yaml` (Chave A / Chave B) — e é por
isso que a anotação `maezo.io/expected-fail-until` (SC-07) é necessária **mesmo depois** desta
assinatura.

---

## 6. Perguntas abertas que a revisão precisa fechar

1. O prazo de 20 anos para prontuário é o da casa, ou a política interna retém por mais tempo?
2. `consentimento_revogacao` é categoria própria ou subconjunto de `auditoria_nao_repudio`
   (§3.1, observação)?
3. Alguma categoria carrega prazo **contratual mais curto** que a LGPD art. 15/16 forçaria a
   eliminar antes? (Levantado em §A2 do rascunho de 2026-07-25, ainda em aberto.)
4. As três categorias fora do escopo B ficam sem prazo até quando? (Precisa de uma data, ou a
   opção B vira uma pendência sem prazo — exatamente o defeito que a opção C evitaria.)
5. A matriz de **categorias** (este arquivo) e o plano de **relações**
   (`erasure-plan.template.yaml`) precisam ser assinados na mesma sessão? Nenhum ratifica o
   outro; o encarregado pode assinar um e não o outro.

---

## 7. Rastreabilidade

- Decisão do dono que autoriza este rascunho: `OWNER-DECISIONS-REGISTER` **R-022** (escopo B +
  dossiê candidato) e **R-055** (os cinco artefatos prontos antes da designação).
- Pré-requisito não cumprido: **R-027 / D7-03** — designação do encarregado registrada com nome
  e data em `docs/compliance/`. Enquanto isso não existir, não há assinante.
- Gap fechado por esta cadeia quando assinado: **AF-07** (e, atrás dele, `AF-16`, `F-2`,
  `SC-03`, `SC-07`).

---

## 8. Adendo de re-verificação — 2026-09-05 (`ce38100`)

Este dossiê foi escrito sobre `0433db0` e re-verificado contra `ce38100` (merge de #318). **Nenhum
fato material mudou:** `PERSISTENCE_LAYERS` continua com 16 relações, os 3 CronJobs e seus
`schedule` continuam os mesmos em `values.yaml::lifecycle.jobs`, `load_retention_matrix` continua
recusando (incluindo a recusa pela raiz `unratified`), `ErasureManager.erase()/.verify()` continuam
levantando `ErasureNotImplementedError`, e `spec/policies/retention/` continua intocado.

O que mudou neste rascunho: a citação por linha de `.github/CODEOWNERS` foi trocada por citação da
**regra** (`/spec/policies/retention/`), que é o que não se move entre merges. As afirmações de
citação deste dossiê passam a ser seguradas por `tests/unit/docs/test_dpo_drafts_citations.py`,
que também relê o bloco YAML da §4 com o carregador real e fica vermelho se a raiz
`unratified: true` sumir ou se uma entrada deixar de satisfazer `REQUIRED_ENTRY_FIELDS`.

### 8.2 Reancoragem pós-merge `abb9d60` (trem #326) — 2026-09-05 (§Delta-3 Δ3·1)

`abb9d60` trouxe a migração **`0009_drop_pgvector`** (DU-01-b, decisão do dono **R-005**), que
**aposentou a camada `semantica`**: em `erasure_plan.py::PERSISTENCE_LAYERS` a entrada de ordem 8
(`agent_memory.embedding`) passou a `resolucao: RETIRADA` com `count_statement: None` — o mesmo
enum que `agent_checkpoints`/`agent_checkpoint_writes` já carregavam desde `0006`.

Este dossiê ainda a nomeava como camada **coberta e viva** em **três** lugares: a linha do CronJob
`lifecycle-verify-erasure` na §2, a linha 1 da §3.1 e — o mais grave — `escopo_b.camadas_cobertas`
**dentro do bloco YAML assinável da §4**. Um encarregado que assinasse aquele bloco estaria
declarando retenção sobre uma relação que não existe mais. Os três lugares foram corrigidos com a
mesma marcação que o dossiê já usava para o par de `0006`, e a entrada da §4 ganhou
`retirada: true` + `motivo_retirada`.

**A entrada NÃO foi apagada, de propósito** — apagá-la encolheria em silêncio um escopo de revisão
que só o encarregado pode encolher, que é exatamente o argumento que o próprio `erasure_plan.py`
registra para manter a linha. O carregador ignora `escopo_b` inteiro (lê apenas `unratified` e
`categorias`), então as duas chaves novas não mudam nada em runtime.

A cerca passou a **derivar** `escopo_b` de `PERSISTENCE_LAYERS`: toda camada/tabela que o YAML
nomeia precisa existir na enumeração, e toda camada com `resolucao: RETIRADA` precisa estar marcada
`retirada: true` (e vice-versa). Nenhum dos dois lados pode envelhecer em silêncio de novo.
