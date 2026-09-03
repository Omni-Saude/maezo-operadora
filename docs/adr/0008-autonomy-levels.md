# ADR-0008: Niveis de autonomia L0-L3 por acao

**Status:** Accepted (2026-07-05) · **Data:** 2026-06-12 · **Area:** Governanca
**Amended by ADR-0034:** a clausula "avaliada pelo PEP a cada tool call" (§Decisao) descreve o modelo
intencao/donor; no v2 a avaliacao de autonomia e enforce-ada ESTRUTURALMENTE via BPMN (ADR-0018) e o
PEP e um objeto de validacao/readiness de politica, nao um gate por-call — logo a "revisao por
amostragem" L2 esta intencionalmente fora do v2 (ADR-0034, gap #24). ADR-0034 amends, nao supersede.

## Contexto
"Quanto o agente faz sozinho" deve ser politica explicita, versionada, auditavel e diferente por
tenant (modo Copilot vs Full-stack) — nao decisao difusa de prompt.

## Decisao
Matriz **acao x nivel** em YAML versionado (`src/maezo/policies/autonomy/`), artefato federado
L0-L3 (ADR-0004), avaliada pelo PEP a cada tool call:
- **L0** somente humano (agente prepara dossie): negativa de autorizacao, acusacao de fraude,
  cancelamento de contrato, QUALQUER decisao clinica (hard, nao rebaixavel por tenant — CI rejeita).
- **L1** agente propoe, humano aprova: pagamento de alcada, descredenciamento, resposta NIP, envio ANS.
- **L2** agente executa, revisao por amostragem: aprovacao de auth com DMN favoravel sob teto, glosa padrao.
- **L3** autonomo com telemetria: triagem, agendamento, respostas informativas, lembretes.

O "dial" Copilot -> Full-stack e diff de YAML revisavel — nunca mudanca de codigo.

## Consequencias
**Positivas:** ANS-defensible; evolucao de autonomia auditavel.
**Negativas (aceitas):** manutencao da matriz exige ownership (compliance humano + Gustavo).

## Supersedes
—

---

## Emenda 2026-09-03 — `src/maezo/policies/autonomy/` nunca existiu; a matriz vive em `spec/` (GAP AF-04)

**Status:** Proposto (amendment) — DRAFT/verify · **Data:** 2026-09-03 · **Autor:** `adr-reconciler` (R1, AGENTE)
**Marcadores:** `amended-by`: ADR-0034 (ja registrado em `:4-7`) + esta Emenda 2026-09-03
(WP-ADR-RECONCILIACAO, GAP AF-04) · `obsolete-section`: apenas o CAMINHO citado em `:14`.
**Base de verificacao:** worktree em `71dd4da`.

> APPEND-ONLY. Nenhuma linha do texto original acima foi alterada ou removida. Um AGENTE nao ratifica
> nada: enquanto este bloco carregar `DRAFT/verify`, ele e um fato reconciliado com o codigo, nao uma
> decisao ratificada. Assinatura humana pendente (`.github/CODEOWNERS:61`).

### 1. O que a ADR afirma

`:14` (Decisao): "Matriz **acao x nivel** em YAML versionado (`src/maezo/policies/autonomy/`)".

### 2. O que e verdade hoje

- **O diretorio citado nao existe e nunca existiu.** `ls src/maezo/policies` -> `No such file or
  directory`. Duas fontes independentes ja registravam isso antes desta emenda:
  - `.github/CODEOWNERS:55-59` — "AUDITORIA 2026-08-13: `/src/maezo/policies/` NUNCA EXISTIU
    (`git log --all --diff-filter=A` vazio para o caminho; o diretorio nao esta em disco)"; a regra
    `:60` e mantida deliberadamente como gate PRE-POSICIONADO, nao como cobertura de hoje.
  - `Makefile:44` — "src/maezo/processes and src/maezo/policies paths never existed (T0.4)".
- **A matriz real vive em `spec/policies/autonomy/`**: `ls spec/policies/autonomy/` ->
  `L0-core.yaml`, `_hard_frozen.yaml`, `action-approvals.yaml`, `tenants-amh.yaml`.
- **Resolucao unica de caminho:** `src/maezo/gateway/pep.py:327-333` (`_default_autonomy_dir` ->
  `resolve_spec_dir() / "policies" / "autonomy"`, honrando `MAEZO_SPEC_DIR`, fail-closed) — nao ha um
  segundo esquema de resolucao.
- **Validacao de CI:** `make validate-artifacts` valida `spec/processes spec/policies spec/agents`
  (`Makefile:47`) — ou seja, a matriz REAL e coberta pelo gate; o caminho citado na ADR nao teria como
  ser coberto por nada.
- **Substancia intacta.** Nem os niveis L0-L3 (`:16-20`), nem o congelamento hard do L0, nem o "dial
  Copilot -> Full-stack como diff de YAML" (`:22`) mudam: o unico erro e o CAMINHO.

### 3. Consequencia

1. `:14` e `obsolete-section` **apenas quanto ao caminho**. Leia `src/maezo/policies/autonomy/` como
   `spec/policies/autonomy/` em todo o texto acima.
2. Precedente seguido: o repo ja corrigiu citacao de ADR por documento novo (ADR-0032 corrigindo
   ADR-0015). Aqui a correcao e in-loco e append-only por instrucao do WP-ADR-RECONCILIACAO — o texto
   original permanece integro e as ancoras de linha citadas por terceiros nao se deslocam.
3. A clausula "avaliada pelo PEP a cada tool call" (`:15`) **ja** carrega o amend do ADR-0034 em `:4-7`;
   o estado ATUAL desse mecanismo (construido, porem em `modo: shadow`) esta registrado na
   `## Emenda 2026-09-03` de `docs/adr/0005-hitl-architectural-guarantee.md`.
4. Nenhum comportamento de runtime muda com esta emenda: ela e documental.
