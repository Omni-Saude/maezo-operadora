"""Worker: fraude (SP-OP-FRAUDE-001).

Investigacao de Fraude — Cadeia de Custodia.
MOST COMPLEX: custody sealing (Merkle), Beatriz A2A, L0-hard accusation guard.
Guards: ERR_FRAUD_ACCUSATION_NOT_HUMAN, ERR_CUSTODY_NOT_SEALED, ERR_PHI_IN_CUSTODY.
Inverts the reference detect_fraud v2: score is routing FACT, NEVER verdict.

`score_indicators` (T2.7 phase 2) evaluates the 7 `fraude_scoring/*` decision tables (T2.7 phase
1, PR #48) engine-side via the `dmn=` seam (ADR-0028) — replaces the `len(evidencia_refs) * 10`
placeholder (defect B10). See that function's docstring for the aggregation contract, the
fail-closed divergence from the v1 donor, and why `fraude_indicadores`/`fraude_routing` are
deliberately NOT evaluated here (they are already engine-native `businessRuleTask`s downstream in
spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn).
"""

from __future__ import annotations

import asyncio
import functools
import math
from typing import TYPE_CHECKING, Any

import structlog

from maezo.gateway.custody import CustodyBundle
from maezo.tools.mcp_cibseven.transport import AgentDecisionProvenance, start_process_idempotent
from maezo.tools.workers.base import (
    CANCEL_KEY_FAMILY,
    INADIMPLENCIA_KEY_FAMILY,
    FunctionWorker,
    mint_contract_business_key,
    non_blank,
)
from maezo.tools.workers.dmn_transport import DmnTransport, evaluate_sync, first_row, require_dmn
from maezo.tools.workers.harness import AUDIT_AGENT_ID, _resolve_app_version

if TYPE_CHECKING:
    from maezo.tools.mcp_cibseven.transport import AuditStartSink, CibSevenTransport
    from maezo.tools.workers.harness import KafkaPublisher, WorkerHarness

logger = structlog.get_logger(__name__)

# ---------------------------------------------------------------
# Error codes
# ---------------------------------------------------------------

ERR_FRAUD_ACCUSATION_NOT_HUMAN = "ERR_FRAUD_ACCUSATION_NOT_HUMAN"
ERR_CUSTODY_NOT_SEALED = "ERR_CUSTODY_NOT_SEALED"
ERR_PHI_IN_CUSTODY = "ERR_PHI_IN_CUSTODY"
ERR_FRAUDE_CASO_INVALIDO = "ERR_FRAUDE_CASO_INVALIDO"
ERR_FRAUDE_HANDOFF_SEM_ALVO = "ERR_FRAUDE_HANDOFF_SEM_ALVO"

# Decision values
DECISAO_ACUSAR_FRAUDE = "ACUSAR_FRAUDE"
DECISAO_ARQUIVAR = "ARQUIVAR"
DECISAO_MONITORAR = "MONITORAR"

# Downstream handoff process keys (only reached DOWNSTREAM of a human accusation + sealed bundle;
# each target owns its OWN human-gated adverse User Task — FRAUDE never auto-descredencia/rescinde).
CRED_PROCESS_KEY = "SP-OP-CRED-001"
CANCEL_PROCESS_KEY = "SP-OP-CANCEL-001"
INADIMPLENCIA_PROCESS_KEY = "SP-OP-INADIMPLENCIA-001"

# PHI markers that must NEVER appear in evidence references
_PHI_MARKERS = frozenset({"cpf", "nome", "nome_social", "endereco", "telefone", "email"})

#: Class-token da UNICA lacuna que hoje impede `gather_evidence`/`assemble_dossier` de fazerem o
#: que seus nomes prometem: a delegacao A2A `fraude.investigate` a Beatriz nao esta ligada (BEA-09).
#: NAO e mensagem de erro nem texto livre — e um token fechado, sem PHI, declarado no contrato
#: (docs/processes/contracts/SP-OP-FRAUDE-001.md, secao "Variaveis de saida") e por isso seguro
#: para viver no escopo do processo, entrar na trilha ADR-0007 e ser lido pelo humano na
#: `UT_DecisaoInvestigador`. Quando o handler A2A de Beatriz (`agents/beatriz/delegation.py` +
#: registro em `a2a_composition`, WP SEPARADO e owner-gated) for ligado, o token deixa de ser
#: emitido pelas duas funcoes — nao ha um segundo valor a acrescentar aqui.
GAP_BEATRIZ_A2A_NAO_LIGADO = "beatriz_a2a_nao_ligado"

#: Class-token da lacuna que hoje impede `refer_to_legal` de fazer o que seu nome promete: NAO
#: existe integracao com juridico/ANS/esfera civel/penal neste repo, e as proprias obrigacoes de
#: referral (prazo, forma, autoridade competente) seguem DRAFT/verify no contrato — nao ha nem o
#: que integrar antes de juridico+compliance as pinarem (FAB-REFER-TO-LEGAL). Mesmo formato e
#: mesma disciplina de `GAP_BEATRIZ_A2A_NAO_LIGADO` acima: token fechado, sem PHI, declarado no
#: contrato (docs/processes/contracts/SP-OP-FRAUDE-001.md, secao "Variaveis de saida"), seguro
#: para o escopo do processo e para a trilha ADR-0007. Quando o referral real for ligado, o token
#: deixa de ser emitido — ausencia passa a significar "encaminhamento real ocorreu".
GAP_REFERRAL_JURIDICO_NAO_LIGADO = "referral_juridico_nao_ligado"


# ---------------------------------------------------------------
# intake — neutral start (register case, no adverse effect)
# ---------------------------------------------------------------


def intake(variables: dict[str, Any]) -> dict[str, Any]:
    """Registra a PROCEDENCIA do encaminhamento no log. Retorna `{}` — NAO afirma nada.

    Serve `ST_Intake` (topico `operadora.fraude.intake`,
    SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn). Start neutro, jamais adverso.

    FAB-INTAKE-CASO-REGISTRADO (o motivo desta docstring). O retorno era, em toda entrega e sem
    calcular nada, `{"caso_registrado": True, "intake_ts": "now"}` — o segundo com o comentario
    `# placeholder` no proprio codigo. A harness grava o retorno no escopo do processo no
    `complete`, entao as duas constantes entravam na instancia como trilha de auditoria de um
    registro que ESTA funcao nunca fez: seu corpo tinha uma unica instrucao (`logger.info`) e
    nenhum sink. Mesma especie da familia FAB-* (`notify_sla_risk` neste mesmo modulo,
    FAB-SLA-RISK-NOTIFIED-SLICE4).

    ZERO CONSUMIDORES (mapa refeito antes de editar, `grep -rnw 'caso_registrado|intake_ts' spec/
    src/ tests/ docs/`): nenhuma `conditionExpression` de BPMN, `inputExpression` de DMN, worker a
    jusante, golden de eval ou linha de contrato — so as duas atribuicoes e a assercao do teste
    unitario.

    POR QUE `{}` E NAO UM TOKEN DE LACUNA (a diferenca deliberada para BEA-09). O registro do caso
    na cadeia de auditoria DE FATO acontece — so nao aqui: (i) a harness emite a linha ADR-0007
    ANTES de todo `complete` (emit-before-complete, fail-closed: sem sink o task nem completa), e
    (ii) a task seguinte do BPMN, `ST_PublishIntakeReceived`, publica
    `agents.events.fraude.intake_received` carregando `encaminhado_por_id` no
    `event_payload_vars`. Emitir um `intake_gap` afirmaria uma lacuna INEXISTENTE — o mesmo
    defeito de honestidade na direcao oposta. `evidencia_gap`/`dossie_gap` existem porque la a
    coleta e a montagem nao acontecem em lugar NENHUM; aqui acontecem, apenas fora desta funcao.

    `intake_ts` foi removida sem substituicao por relogio real: alem de nao ter consumidor, o
    instante do intake ja e fato do engine (`GET /history/activity-instance` de `ST_Intake`), e um
    segundo carimbo no escopo do processo seria uma fonte de verdade redundante, nao uma correcao.

    ACHADO COLATERAL, NAO CORRIGIDO AQUI: o BPMN e a tabela de codigos de erro do contrato dizem
    que esta task lanca `ERR_FRAUDE_CASO_INVALIDO` "se o caso for inconsistente na origem"; a
    constante existe (`:50`) mas NENHUM call site a levanta e esta funcao nao valida nada.
    Definir o que torna um caso inconsistente e produto/juridico (a regra de correlacao de
    `numero_caso` esta ela propria em aberto), nao engenharia — registrado em Pendencias do
    contrato.
    """
    numero_caso = variables.get("numero_caso", "")
    origem = variables.get("origem_encaminhamento", "")
    encaminhado_por = variables.get("encaminhado_por_id", "")

    # `registro_asserted=False` diz no log o que o retorno diz no escopo do processo: esta etapa
    # nao registrou o caso em sink algum (o registro e da trilha da harness + ST_PublishIntake-
    # Received). `info`, nao `warning`: ao contrario de `gather_evidence`, aqui nao ha lacuna —
    # so uma atribuicao de responsabilidade que estava sendo afirmada no lugar errado.
    logger.info(
        "fraude_intake",
        numero_caso=numero_caso,
        origem=origem,
        encaminhado_por=encaminhado_por,
        registro_asserted=False,
    )

    return {}


# ---------------------------------------------------------------
# gather_evidence — collect evidence (convocates Beatriz, A2A)
# ---------------------------------------------------------------


def gather_evidence(variables: dict[str, Any]) -> dict[str, Any]:
    """NORMALIZA `evidencia_refs` e declara a lacuna. NAO coleta evidencia de lugar nenhum.

    Serve `ST_GatherEvidence` (topico `operadora.fraude.gather_evidence`,
    SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn). O contrato e o BPMN descrevem esta task como
    "convoca Beatriz (delegacao A2A `fraude.investigate`, human-gated): coleta/normaliza evidencia
    (CDC/TISS/feature store)". Desta funcao sai a NORMALIZACAO e nada mais: nenhuma chamada A2A,
    nenhuma consulta a CDC/TISS/feature store, nenhum canal contatado. As `evidencia_refs` que ela
    devolve sao as que ja chegaram no start (handoff `encaminhar_fraude` de Phase 2) ou pela
    mensagem `msg.fraude.evidencia_anexada` — coercidas a lista quando vem com tipo errado, que e
    o unico efeito real desta funcao hoje.

    BEA-09 (o motivo desta docstring). O retorno era
    `{"evidencia_refs": <o que ja veio>, "evidencia_coletada_em": "now"}`: um instante de coleta
    constante, afirmado por uma funcao cujo proprio corpo carregava o comentario "Placeholder:
    real implementation calls Beatriz via A2A". A harness grava o retorno no escopo do processo no
    `complete` (`harness.py:1779-1783`), entao a constante entrava na instancia como trilha de
    auditoria de uma coleta que nunca ocorreu — a jusante de um processo cuja proxima parada e a
    selagem de custodia e a User Task L0-hard `UT_DecisaoInvestigador`. Mesma especie da familia
    FAB-* (`notify_sla_risk`, logo abaixo neste modulo, e os dez handlers de
    FAB-SLA-RISK-NOTIFIED-SLICE4).

    ZERO CONSUMIDORES (mapa refeito antes de editar, `grep -rn evidencia_coletada_em spec/ src/
    tests/ docs/`): `evidencia_coletada_em` nao aparece em `conditionExpression` de BPMN,
    `inputExpression` de DMN, worker a jusante, golden de eval ou linha de contrato — so na
    atribuicao que o criava. Removido, portanto, sem caminho a religar.

    A LACUNA NAO E SILENCIOSA. Em lugar da afirmacao falsa sai `evidencia_gap`
    (`GAP_BEATRIZ_A2A_NAO_LIGADO`), token fechado e sem PHI, DECLARADO no contrato
    (SP-OP-FRAUDE-001.md, "Variaveis de saida"): quem ler a instancia — incluindo o investigador
    humano na UT, que decide um L0-hard — ve que nao houve coleta, em vez de ver um carimbo de
    coleta. Espelha a perna de VISIBILIDADE do M-1 de `adequacao.measure_gap` ("FABRICATION IS NO
    LONGER SILENT"), com a diferenca de que aqui a lacuna e uma variavel DECLARADA e nao so um log,
    porque o consumidor que precisa ve-la e um humano dentro do processo.

    Ligar a delegacao A2A e WP SEPARADO (handler em `agents/beatriz/delegation.py` + registro em
    `a2a_composition` — registro e owner-decision, `FERNANDO-DELEGATION-CALL-SITE`). Ate la o token
    e o estado honesto, nao um placeholder.

    Zona PHI/ADR-0006 inalterada: so ponteiros pseudonimizados; a varredura final continua em
    `seal_custody_bundle` (`ERR_PHI_IN_CUSTODY`). TASY write DROP (ADR-0013) — nada e escrito.
    """
    numero_caso = variables.get("numero_caso", "")
    entidade_tipo = variables.get("entidade_tipo", "")

    evidencia_refs = variables.get("evidencia_refs", [])
    if not isinstance(evidencia_refs, list):
        evidencia_refs = []

    # `warning`, nao `info` (precedente `adequacao_medidas_fabricadas`): uma etapa de coleta que
    # nao coleta e defeito operacional, nao progresso de rotina. `coleta_asserted=False` diz no
    # log a mesma coisa que o retorno diz no escopo do processo.
    logger.warning(
        "fraude_evidencia_nao_coletada",
        numero_caso=numero_caso,
        entidade_tipo=entidade_tipo,
        evidencia_count=len(evidencia_refs),
        coleta_asserted=False,
        gap=GAP_BEATRIZ_A2A_NAO_LIGADO,
    )

    return {
        "evidencia_refs": evidencia_refs,
        "evidencia_gap": GAP_BEATRIZ_A2A_NAO_LIGADO,
    }


# ---------------------------------------------------------------
# score_indicators — engine-side fraude_scoring chain (T2.7 phase 2)
# ---------------------------------------------------------------

# The 7 `fraude_scoring/*` decision tables (T2.7 phase 1, PR #48) — ported 1:1 (decision-logic
# byte-faithful) from the v1 donor's already-inverted `fraud_scoring/*` tables: every one emits
# ONLY `indicador_score` (integer) + `indicador_label` (string) + `motivo` (string) — ZERO
# verdict/ACUSAR/BLOQUEAR/FRAUD_DETECTED columns (verified against every `.dmn` file under
# spec/processes/dmn/). Each table is STANDALONE (hitPolicy FIRST; no informationRequirement/DRD
# between them — verified), so this worker evaluates each individually via the `dmn=` seam
# (ADR-0028) and aggregates below.
#
# `fraude_indicadores`/`fraude_routing` are DELIBERATELY NOT evaluated here: they are already
# engine-native `businessRuleTask`s (`BRT_Indicadores`/`BRT_Routing`,
# spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn:135-165) that consume
# `score_indicadores`/`indicadores_presentes`/`entidade_tipo` immediately downstream of
# `ST_ScoreIndicators` in the SAME BPMN — the engine evaluates them directly on
# `camunda:decisionRef`, no worker call needed. Re-evaluating them here would not be "more
# engine-side," it would be a redundant second engine call duplicating what the BPMN already
# does natively (root-caused against the BPMN's own documentation at ST_ScoreIndicators: "7 DMNs
# fraude_scoring/* (rodadas pelo WORKER, NAO como businessRuleTasks)" — i.e. exactly these 7,
# not fraude_indicadores/fraude_routing).
_SCORING_DECISIONS: tuple[str, ...] = (
    "risk_thresholds",
    "upcoding_complexity_ceiling",
    "frequency_zscore_threshold",
    "phantom_no_diagnosis",
    "phantom_suspicious_prefix",
    "provider_peer_deviation",
    "unbundling_partial_bundles",
)

# Evidence signal keys the 7 tables' inputExpressions reference (verified per-.dmn-file). Every
# table receives the SAME collected dict — each ignores whatever inputs its own inputExpressions
# don't reference (DMN FEEL evaluation semantics, not a worker-side per-table filter).
#
# NOTE (root-caused, not fabricated): the v1 donor's own `_collect_scoring_inputs`/
# `_SCORING_INPUT_KEYS` do NOT derive these signals from raw claim/procedure data either — they
# are a direct pass-through of process variables already computed upstream (CDC /
# `feature_store.claim_features`/`provider_features` per ADR-0013). The contract
# (docs/processes/contracts/SP-OP-FRAUDE-001.md §"Pendencias para promocao a FINAL") explicitly
# lists "definicao de indicadores_presentes a partir de feature_store" as an OPEN item. This
# worker ports that same architecture faithfully: it does NOT invent business-derivation formulas
# for code_tier/z_score/deviation_pct/etc. (that would be fabrication outside a worker-WIRING
# task's scope) — it collects whatever of these signals gather_evidence/the feature-store
# integration has already attached to process variables, with only mechanical type coercion
# (never business logic) applied in `_collect_scoring_inputs`.
#
# `tuss_codes` is DELIBERATELY ABSENT from this tuple (T1.5 DMN-input hardening) and, since
# GAP-PERSP-DMN-DEAD-INPUTS, NO table declares it either. History: it was a list-per-convention
# field bound to `unbundling_partial_bundles`' `ie_tuss_codes` (typeRef="string") whose column was
# `-` (wildcard) in EVERY rule — no rule ever read it. A generic `str()` coercion would turn
# `["30101012"]` into the Python repr `"['30101012']"`; a future FEEL `starts with(...)`/
# `contains(...)` on that repr would SILENTLY FLIP the decision, so T1.5 stopped forwarding it.
# That left the column dead on BOTH ends (nothing read it, nothing supplied it), so
# GAP-PERSP-DMN-DEAD-INPUTS removed the column from the table as well — the table's signature now
# matches what this worker actually sends. If a future RATIFIED rule needs the codes, re-declare
# the input with a collection `typeRef` and normalize deliberately (join) — never a blanket cast.
_SCORING_INPUT_KEYS: tuple[str, ...] = (
    "risk_score",  # risk_thresholds (integer)
    "encounter_class",  # upcoding_complexity_ceiling (string) — the ONLY table that reads it
    "code_tier",  # upcoding_complexity_ceiling (integer)
    "z_score",  # frequency_zscore_threshold (double)
    "has_tuss_codes",  # phantom_no_diagnosis (boolean)
    "has_cid10_codes",  # phantom_no_diagnosis (boolean)
    "tuss_prefix",  # phantom_suspicious_prefix (string — FEEL `starts with(...)`)
    "deviation_pct",  # provider_peer_deviation (double)
    "provider_volume",  # provider_peer_deviation (integer)
    "bundle_group_id",  # unbundling_partial_bundles (string: partial|complete|none)
)

# Boolean-typed evidence keys — coerced defensively (native bool OR string "true"/"false"),
# mirroring this codebase's `contas._is_true` idiom (and the v1 donor's own `_is_true`).
_SCORING_BOOL_KEYS: frozenset[str] = frozenset({"has_tuss_codes", "has_cid10_codes"})

# Numeric-typed evidence keys -> the DMN-declared typeRef of the input they feed (verified per
# `.dmn` file). "int" == an `integer` typeRef (risk_thresholds/upcoding/provider_peer_deviation),
# "double" == a `double` typeRef (frequency_zscore/provider_peer_deviation). These arrive RAW from
# untyped upstream sources (feature-store pass-through, ADR-0013); `_coerce_numeric` types a valid
# numeric to the declared type and OMITS anything non-coercible (fail-closed — see its docstring).
_SCORING_NUMERIC_KEYS: dict[str, str] = {
    "risk_score": "int",
    "code_tier": "int",
    "z_score": "double",
    "deviation_pct": "double",
    "provider_volume": "int",
}

# Neutral label every table emits on its catch-all/no-indicator row — excluded from
# `indicadores_presentes` (it is the ABSENCE of an indicator, never itself an indicator).
_NO_INDICATOR_LABEL = "none"


def _is_true(value: Any) -> bool:
    """Coerce an engine variable (native bool OR string 'true'/'false') to bool."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return False


def _coerce_numeric(value: Any, kind: str) -> tuple[bool, int | float | None]:
    """Coerce a raw process variable to the DMN-declared numeric type — FAIL-CLOSED (T1.5).

    Returns `(ok, coerced)`:
    - a native `int` (not a `bool`) or a clean integer string like `"80"`/`"80.0"` -> `(True, int)`
      for `kind == "int"`, `(True, float)` for `kind == "double"`;
    - a native `float` or clean decimal string -> `(True, float)` for `"double"`; for `"int"` only
      an INTEGRAL float/string (`80.0`) coerces (`-> int(80)`), a fractional one (`80.5`) fails;
    - ANYTHING else -> `(False, None)`: a `bool` (an int subclass but never a numeric fraud
      signal), a non-numeric or empty string, a NaN/inf, a `list`/`dict`, a fractional value for an
      integer input. The caller then OMITS the key so NO wrong-typed value reaches DMN evaluation —
      the table's own missing-input catch-all row handles the absence conservatively. A coercion
      that could silently flip a DMN decision (e.g. `int("80.5") -> 80`) is worse than fail-closed.

    Integer inputs are typed WITHOUT a float round-trip so a large `provider_volume` never loses
    precision past 2**53.
    """
    if isinstance(value, bool):
        return False, None
    if isinstance(value, int):
        return (True, value) if kind == "int" else (True, float(value))
    if isinstance(value, float):
        if not math.isfinite(value):
            return False, None
        if kind == "double":
            return True, value
        return (True, int(value)) if value.is_integer() else (False, None)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return False, None
        if kind == "int":
            try:
                return True, int(text)
            except ValueError:
                pass  # fall through: accept an integral decimal string ("80.0"), reject "80.5"
            try:
                parsed = float(text)
            except ValueError:
                return False, None
            if math.isfinite(parsed) and parsed.is_integer():
                return True, int(parsed)
            return False, None
        try:
            parsed = float(text)
        except ValueError:
            return False, None
        return (True, parsed) if math.isfinite(parsed) else (False, None)
    return False, None


def _collect_scoring_inputs(variables: dict[str, Any]) -> dict[str, Any]:
    """Collect the evidence signals the 7 `fraude_scoring/*` tables reference.

    Direct pass-through filter (see `_SCORING_INPUT_KEYS` docstring) — only keys PRESENT and
    non-None on process variables are forwarded; an absent signal falls through to that table's
    conservative catch-all row (never a fabricated default). Only mechanical type coercion happens
    here (never business derivation): booleans via `_is_true`; the numeric signals in
    `_SCORING_NUMERIC_KEYS` via `_coerce_numeric` (coerce-or-DROP, fail-closed — a non-coercible
    value is omitted, never passed wrong-typed into DMN evaluation). `tuss_codes` is deliberately
    not in `_SCORING_INPUT_KEYS` (repr-cast footgun — see that tuple's comment) and no longer
    declared by any table. All remaining keys are genuine `string`-typeRef inputs, forwarded
    unchanged, and every one of them is read by at least one of the 7 tables (pinned by
    `tests/unit/tools/workers/test_fraude.py::test_scoring_input_keys_are_exactly_the_seven_tables_inputs`).
    """
    evidence: dict[str, Any] = {}
    for key in _SCORING_INPUT_KEYS:
        value = variables.get(key)
        if value is None:
            continue
        if key in _SCORING_BOOL_KEYS:
            evidence[key] = _is_true(value)
        elif key in _SCORING_NUMERIC_KEYS:
            ok, coerced = _coerce_numeric(value, _SCORING_NUMERIC_KEYS[key])
            if ok:
                evidence[key] = coerced
            # else: OMIT — fail-closed, no wrong-typed value reaches the DMN
        else:
            evidence[key] = value
    return evidence


def _evaluate_scoring_chain(
    dmn: DmnTransport, evidence: dict[str, Any]
) -> tuple[int, list[str], dict[str, dict[str, Any]]]:
    """Evaluate the 7 `fraude_scoring/*` tables and aggregate — FAIL-CLOSED, no partial fallback.

    Sums `indicador_score` into a total (integer); collects `indicador_label` != "none"
    (de-duplicated, table order) into a list. Each table is STANDALONE (verified: no
    informationRequirement/DRD) so it is evaluated independently against the SAME evidence dict.

    Deliberate divergence from the v1 donor (root-caused, not a workaround — see PR body
    characterization table): the donor's `_score_from_dmn` CAUGHT `DmnEvaluationError` per table
    and SKIPPED the failing one, falling back to a fixed `score=50` / `"PRIORITARIA"` sentinel
    only when *zero* tables evaluated — a reasonable posture for the donor's dead in-process XML
    evaluator, which itself failed OPEN on no-match (ADR-0028's defect B5). This worker instead
    uses the REAL `dmn=` seam (ADR-0028): `evaluate_sync`/`first_row` propagate
    `DmnEvaluationError` (engine unreachable/erroring — transient, engine-side retry) and
    `DmnNoResultError` (empty result — coded, immediate human-visible incident) UNCAUGHT for ANY
    of the 7 tables. A single-table outage no longer silently degrades to a fabricated
    "zero-score-and-continue" or a fixed sentinel score — the WHOLE aggregation fails closed,
    consistent with this codebase's ADR-0028 fail-closed doctrine (neither
    `contas.analyze_reason` nor `recurso.assess_eligibility` swallow a per-call DMN error).
    """
    total_score = 0
    labels: list[str] = []
    dmn_versions: dict[str, dict[str, Any]] = {}
    for decision_id in _SCORING_DECISIONS:
        rows, version = evaluate_sync(dmn, decision_id, evidence)
        row = first_row(rows, decision_id, evidence)
        dmn_versions[decision_id] = version.to_audit_dict()
        raw_score = row.get("indicador_score", 0)
        if isinstance(raw_score, bool):
            pass  # bool is an int subclass in Python — never summed as a score
        elif isinstance(raw_score, (int, float)):
            total_score += int(raw_score)
        label = row.get("indicador_label")
        if label and str(label) != _NO_INDICATOR_LABEL and str(label) not in labels:
            labels.append(str(label))
    return total_score, labels, dmn_versions


def score_indicators(variables: dict[str, Any], *, dmn: DmnTransport | None = None) -> dict[str, Any]:
    """Calculate fraud indicator scores — engine-side DMN evaluation (T2.7 phase 2).

    Replaces the `len(evidencia_refs) * 10` placeholder (defect B10) with the REAL 7-table
    `fraude_scoring/*` chain (T2.7 phase 1, PR #48) evaluated via the `dmn=` seam (ADR-0028):
    `_collect_scoring_inputs` gathers whatever evidence signals `gather_evidence`/the
    feature-store integration has attached to process variables; `_evaluate_scoring_chain`
    evaluates each of the 7 STANDALONE tables and aggregates `indicador_score` ->
    `score_indicadores` (integer) / `indicador_label` -> `indicadores_presentes` (`list[str]`,
    "none" excluded).

    STILL a ROUTING FACT, NEVER a verdict (unchanged invariant): no `FRAUD_DETECTED`, no
    ACUSAR/BLOQUEAR column anywhere in the 7 tables (verified) or in this function's own output.
    `intensidade_investigacao` is INTENTIONALLY not computed here (that was the deleted
    placeholder's own invention) — `fraude_indicadores` is a SEPARATE, already engine-native
    `businessRuleTask` (`BRT_Indicadores`, `camunda:decisionRef="fraude_indicadores"`) that
    consumes `score_indicadores`/`indicadores_presentes`/`entidade_tipo` immediately downstream in
    the SAME BPMN (spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn:135-155) —
    duplicating that evaluation here would not be "more engine-side," it would be a second,
    redundant engine call computing what the BPMN already computes natively.

    FAIL-CLOSED (never zero-score-and-continue): `require_dmn` raises `DmnEvaluationError`
    (transient -> engine retry) if the `dmn=` seam is unwired; `_evaluate_scoring_chain`
    propagates `DmnEvaluationError`/`DmnNoResultError` from ANY of the 7 tables uncaught (a coded
    `DmnNoResultError` -> `ValueError` -> `failure(retries=0)` incident via
    `FunctionWorker.execute`, the same reclassification `contas.py`/`recurso.py` rely on). No
    fallback sentinel score is ever fabricated.

    `dmn_versions` (NEW, additive — not yet reflected in
    docs/processes/contracts/SP-OP-FRAUDE-001.md, a documentation follow-up) carries
    `{decision_id: DmnVersion.to_audit_dict()}` for all 7 tables on the RETURNED FACTS payload
    itself: neither `contas.py` nor `recurso.py` wire an `audit=` seam today (T1.5's own
    residual — `AuditRecord.dmn_versions` has no production caller yet, per
    `tests/unit/gateway/test_audit_dmn_versions.py`), so there is no `AuditLog`/`audit=` seam in
    this module to plumb into yet. Attaching the DMN version provenance to the dossier-bound
    facts themselves (rather than only structured logs) keeps it auditable alongside the score/
    labels it produced, pending that follow-up.
    """
    dmn_transport = require_dmn(dmn, "operadora.fraude.score_indicators")
    evidence = _collect_scoring_inputs(variables)
    score_indicadores, indicadores_presentes, dmn_versions = _evaluate_scoring_chain(dmn_transport, evidence)

    logger.info(
        "fraude_score_indicators",
        numero_caso=variables.get("numero_caso"),
        score_indicadores=score_indicadores,
        indicadores_presentes=indicadores_presentes,
        evidence_keys=sorted(evidence),
    )

    return {
        "score_indicadores": score_indicadores,
        "indicadores_presentes": indicadores_presentes,
        "dmn_versions": dmn_versions,
    }


# ---------------------------------------------------------------
# assemble_dossier — Beatriz assembles dossier (instructs, never decides)
# ---------------------------------------------------------------


def assemble_dossier(variables: dict[str, Any]) -> dict[str, Any]:
    """NAO monta dossie. Declara a lacuna e retorna SO isso — `{"dossie_gap": <token>}`.

    Serve `ST_AssembleDossier` (topico `operadora.fraude.assemble_dossier`,
    SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn), tambem alvo do loop de re-selagem
    (`BME_EvidenciaAnexada` -> re-montar -> re-selar). O contrato descreve a task como "Beatriz
    monta o dossie de investigacao (narrativa/montagem a partir de `evidencia_refs` +
    `indicadores_presentes`); instrui, NAO decide". Nada disso acontece aqui: nao ha chamada A2A,
    nao ha narrativa, nao ha artefato de dossie — nem em memoria, nem persistido.

    BEA-09 (o motivo desta docstring). O retorno era, em toda entrega e sem calcular nada,
    `{"dossie_montado": True, "dossie_items": len(evidencia_refs)}`. As DUAS chaves eram falsas:
    `dossie_montado` afirmava uma montagem que nunca ocorreu, e `dossie_items` contava as
    `evidencia_refs` que ja estavam no escopo como se fossem itens de um dossie que nao existe. A
    harness grava o retorno no escopo do processo no `complete` (`harness.py:1779-1783`), e a
    proxima parada do token e `seal_custody_bundle` seguido da User Task L0-hard
    `UT_DecisaoInvestigador`: o investigador humano decidia sobre uma instancia que AFIRMAVA ter um
    dossie montado. A fence estatica
    (`tests/unit/tools/workers/test_worker_handler_purity.py::_FABRICATED_FACT_KEYS`) passa a
    carregar `dossie_montado` para manter isso fixo.

    ZERO CONSUMIDORES (mapa refeito antes de editar, `grep -rn 'dossie_montado\\|dossie_items'
    spec/ src/ tests/ docs/`): nenhuma `conditionExpression` de BPMN, nenhuma `inputExpression` de
    DMN, nenhum worker a jusante (`seal_custody_bundle` le `evidencia_refs`, nunca estas duas),
    nenhum golden de eval e nenhuma linha de contrato as le — so a atribuicao que as criava e a
    asserção do proprio teste unitario. Removidas, portanto, sem caminho a religar. Nao ha eco:
    `evidencia_refs`/`indicadores_presentes` ja estao no escopo e reescreve-las seria ruido de
    auditoria (precedente `notify_sla_risk`, neste modulo).

    O QUE ENTRA NO LUGAR, e por que nao e `{}`. `notify_sla_risk` pode retornar `{}` porque e
    informativa e nao-adversa: sua ausencia nao muda decisao alguma. Aqui a ausencia do dossie e
    justamente o que o decisor humano do L0-hard precisa saber, entao a lacuna sai como variavel
    DECLARADA — `dossie_gap` = `GAP_BEATRIZ_A2A_NAO_LIGADO`, token fechado, sem PHI, declarado em
    SP-OP-FRAUDE-001.md ("Variaveis de saida") ANTES deste codigo (spec-first). Nada silencioso:
    a instancia diz "nao ha dossie montado, e este e o motivo", em vez de dizer "ha dossie".

    O QUE ESTA FUNCAO DELIBERADAMENTE NAO FAZ. Nao monta um "dossie parcial" deterministico a
    partir de `evidencia_refs` + `indicadores_presentes` para poder afirmar `dossie_montado=True`
    honestamente: a narrativa/montagem que o contrato define e o ato de Beatriz, e um indice
    montado localmente seria de novo uma coisa que "parece funcionar" — trocaria uma constante
    falsa por um artefato fora de contrato com a mesma lacuna escondida dentro. Ligar a delegacao
    A2A e WP SEPARADO (handler em `agents/beatriz/delegation.py` + registro em `a2a_composition`;
    o registro e owner-decision, `FERNANDO-DELEGATION-CALL-SITE`).

    PONTO DE FALHA-FECHADA NAO MOVIDO, e isso e declarado, nao esquecido. O caminho adverso
    continua guardado onde ja estava — `register_fraud_accusation` (decisao humana + `bundle_root`
    selado e re-verificado). Fazer esse guard recusar tambem sobre `dossie_gap` DESLIGARIA por
    inteiro o unico caminho adverso do processo enquanto Beatriz nao for ligada; e uma decisao de
    dono, nao de quem honestifica o worker, e esta registrada como recomendacao no relatorio de
    BEA-09 em vez de tomada aqui.
    """
    evidencia_refs = variables.get("evidencia_refs", [])
    if not isinstance(evidencia_refs, list):
        evidencia_refs = []
    indicadores = variables.get("indicadores_presentes", [])
    if not isinstance(indicadores, list):
        indicadores = []

    # `warning` pela mesma razao de `gather_evidence`: uma etapa de montagem que nao monta e
    # defeito operacional. Os dois contadores sao do que a funcao RECEBEU (fato verdadeiro),
    # nunca do que teria montado; `dossie_asserted=False` fecha a leitura.
    logger.warning(
        "fraude_dossie_nao_montado",
        numero_caso=variables.get("numero_caso"),
        evidencia_recebida_count=len(evidencia_refs),
        indicadores_recebidos_count=len(indicadores),
        dossie_asserted=False,
        gap=GAP_BEATRIZ_A2A_NAO_LIGADO,
    )

    return {"dossie_gap": GAP_BEATRIZ_A2A_NAO_LIGADO}


# ---------------------------------------------------------------
# seal_custody_bundle — Merkle seal BEFORE human decision
# ---------------------------------------------------------------


def seal_custody_bundle(variables: dict[str, Any]) -> dict[str, Any]:
    """Seal the evidence bundle with a Merkle root in the audit chain.

    This MUST happen BEFORE UT_DecisaoInvestigador is created.
    The bundle_root is written to the audit chain (ADR-0007 projection).
    Any PHI in evidence is rejected (ERR_PHI_IN_CUSTODY, ADR-0006).

    Sequence: assemble_dossier → seal_custody_bundle → UT_DecisaoInvestigador
    """
    evidencia_refs = variables.get("evidencia_refs", [])
    if not isinstance(evidencia_refs, list):
        evidencia_refs = []

    # Check for PHI in evidence (ADR-0006)
    for ref in evidencia_refs:
        if isinstance(ref, str):
            for marker in _PHI_MARKERS:
                if marker in ref.lower():
                    logger.error(
                        "fraude_phi_in_custody_detected",
                        ref=ref,
                        marker=marker,
                    )
                    raise FraudeError(
                        ERR_PHI_IN_CUSTODY,
                        f"PHI marker '{marker}' detected in evidence reference",
                    )

    # Compute Merkle root over ordered evidence references
    bundle_root = CustodyBundle.seal_bundle([str(r) for r in evidencia_refs])

    logger.info(
        "fraude_custody_sealed",
        numero_caso=variables.get("numero_caso"),
        bundle_root=bundle_root,
        record_count=len(evidencia_refs),
    )

    return {
        "bundle_root": bundle_root,
        "custody_sealed": True,
        "record_count": len(evidencia_refs),
    }


# ---------------------------------------------------------------
# register_fraud_accusation — GATED L0-hard adverse effect
# ---------------------------------------------------------------


def _norm_str(value: Any) -> str:
    """Normalize an engine variable to a stripped string for guard checks — FAIL-CLOSED.

    Mirrors `pagto._norm_str`/`pagto.register_payment_refusal`'s fix (t2.5-p2b-round2, then
    t3.1-guard-input-hardening for `pagto.release_high_value_payment` and
    `adequacao.register_fallback_commitment`): the pre-fix bare `if not investigator_id` /
    `if not tier` / ... checks let WHITESPACE-ONLY decision + accountability fields pass Guard 1
    — on an L0-hard, adjacent-to-the-L0-invariant fraud accusation ("fraud accusation NEVER
    without a genuine human decision"), a whitespace-only `investigator_id` would defeat
    ADR-0007's audit-chain identification and a whitespace-only `fundamentacao_investigacao`/
    `referencia_normativa` would record an accusation with no real justification. Closes that
    class:
    - a `str` normalizes to `value.strip()` — whitespace-only ("   ", "\\t", "\\n", ...)
      becomes "" and is treated EXACTLY like an absent field (refusal, never registration);
    - a NON-string (None, int, bool, list, dict — engine variables arrive untyped) normalizes
      to "" (fail-closed refusal), never a truthy pass-through and never an AttributeError
      incident from calling `.strip()` on a non-string.

    Scope: applies to the scalar string decision/accountability fields of Guard 1
    (`decisao_fraude`, `investigator_id`, `tier`, `fundamentacao_investigacao`,
    `referencia_normativa`). `indicadores_fundamentantes` additionally receives ELEMENT-level
    normalization inside `register_fraud_accusation` (each element through this same
    str-or-"" rule; empty-after-strip elements dropped) because the contract defines its
    elements as accountability-bearing evidence citations (SP-OP-FRAUDE-001.md:91 — "quais
    indicadores do dossie sustentam a acusacao (citacao de evidencia, ADR-0007
    `decision_basis`)"): a list of whitespace-only "citations" names no indicator and must
    refuse exactly like an empty list. `destino_referral` stays isinstance+len guarded (a dict
    of referral flags, not free-text accountability) and `bundle_root` stays fail-closed via
    `CustodyBundle.verify_bundle`'s cryptographic equality check (Guard 2, a DIFFERENT error
    class, ERR_CUSTODY_NOT_SEALED) — Guard 2's structure is deliberately left untouched.
    """
    if isinstance(value, str):
        return value.strip()
    return ""


def register_fraud_accusation(variables: dict[str, Any]) -> dict[str, Any]:
    """Register a fraud accusation — L0-hard, NEVER automatic.

    GUARDED: ERR_FRAUD_ACCUSATION_NOT_HUMAN (decision) + ERR_CUSTODY_NOT_SEALED (integrity).
    Requires:
      - decisao_fraude == ACUSAR_FRAUDE from human investigator
      - investigator_id + tier present (ADR-0007)
      - fundamentacao, indicadores_fundamentantes, referencia_normativa
      - destino_referral present
      - bundle_root sealed and verifiable

    NORMALIZATION (t3.1-guard-input-hardening, closing the bare-truthiness gap noted in the
    #133 audit — fraude.py:503,505,507,511 pre-fix; L0-ADJACENT — the L0 invariant is that a
    fraud accusation NEVER registers without a genuine human decision behind it): the Guard-1
    scalar string fields `decisao_fraude`, `investigator_id`, `tier`,
    `fundamentacao_investigacao` and `referencia_normativa` are normalized via `_norm_str` (strip;
    non-string -> "") BEFORE any guard check, mirroring `pagto.register_payment_refusal`'s fix.
    Consequences, all fail-closed:
    - whitespace-only `investigator_id`/`tier`/`fundamentacao_investigacao`/
      `referencia_normativa` REFUSES exactly like an absent field (named in the guard's error
      list, unchanged message format);
    - a whitespace-PADDED but otherwise exact `decisao_fraude` literal ("ACUSAR_FRAUDE ")
      normalizes to the literal and still passes Guard 1 (still subject to every other
      accountability-field check and to Guard 2's custody verification) — case
      variants/substrings still refuse (exact `!=` match, no folding);
    - a non-string in ANY of these fields normalizes to "" (refusal), never a truthy
      pass-through and never an AttributeError incident.
    `indicadores_fundamentantes` receives ELEMENT-level normalization (each element through
    `_norm_str`; empty-after-strip/non-string elements dropped, so `["   "]` or `[123]`
    refuses exactly like `[]` — its elements are accountability-bearing evidence citations,
    contract SP-OP-FRAUDE-001.md:91: ADR-0007 `decision_basis`). `destino_referral`,
    `bundle_root` and `evidencia_refs` are UNCHANGED — their existing isinstance/length/
    cryptographic checks already fail closed (see `_norm_str`'s docstring for why).
    """
    decisao = _norm_str(variables.get("decisao_fraude", ""))
    investigator_id = _norm_str(variables.get("investigator_id", ""))
    tier = _norm_str(variables.get("tier", ""))
    fundamentacao = _norm_str(variables.get("fundamentacao_investigacao", ""))
    indicadores = variables.get("indicadores_fundamentantes", [])
    if isinstance(indicadores, list):
        # Element-level normalization (t3.1-guard-input-hardening): keep only genuine,
        # non-empty-after-strip string citations — a whitespace-only or non-string element
        # names no indicator (ADR-0007 decision_basis) and must not count toward presence.
        # A list reduced to [] falls through to the existing "ausente/vazio" refusal below;
        # a non-list keeps failing the existing isinstance check. Same error class, same
        # message — input normalization only.
        indicadores = [_norm_str(item) for item in indicadores if _norm_str(item)]
    ref_normativa = _norm_str(variables.get("referencia_normativa", ""))
    destino = variables.get("destino_referral", {})
    bundle_root = variables.get("bundle_root", "")
    evidencia_refs = variables.get("evidencia_refs", [])

    if not isinstance(evidencia_refs, list):
        evidencia_refs = []

    errors: list[str] = []

    # Guard 1: human decision
    if decisao != DECISAO_ACUSAR_FRAUDE:
        errors.append(f"decisao_fraude != {DECISAO_ACUSAR_FRAUDE} (got: {decisao!r})")
    if not investigator_id:
        errors.append("investigator_id ausente (ADR-0007)")
    if not tier:
        errors.append("tier ausente")
    if not fundamentacao:
        errors.append("fundamentacao_investigacao ausente")
    if not isinstance(indicadores, list) or len(indicadores) == 0:
        errors.append("indicadores_fundamentantes ausente/vazio")
    if not ref_normativa:
        errors.append("referencia_normativa ausente")
    if not isinstance(destino, dict) or len(destino) == 0:
        errors.append("destino_referral ausente")

    # Guard 2: custody integrity
    if not bundle_root:
        errors.append("bundle_root ausente — custodia nao selada")
    elif not CustodyBundle.verify_bundle(bundle_root, [str(r) for r in evidencia_refs]):
        errors.append("bundle_root nao verifica contra evidencia_refs — custodia violada")

    if errors:
        # Determine which error code to use
        custody_errors = [e for e in errors if "custodia" in e.lower() or "bundle_root" in e.lower()]
        if custody_errors and not any(e for e in errors if e not in custody_errors):
            # Pure custody failure
            logger.error(
                "fraude_custody_not_sealed",
                errors=errors,
                numero_caso=variables.get("numero_caso"),
            )
            raise FraudeError(ERR_CUSTODY_NOT_SEALED, "; ".join(errors))
        else:
            # Human decision failure (or mixed)
            logger.error(
                "fraude_accusation_guard_rejected",
                errors=errors,
                numero_caso=variables.get("numero_caso"),
            )
            raise FraudeError(ERR_FRAUD_ACCUSATION_NOT_HUMAN, "; ".join(errors))

    logger.info(
        "fraude_accusation_registered",
        numero_caso=variables.get("numero_caso"),
        investigator_id=investigator_id,
        tier=tier,
    )

    return {
        "acusacao_registrada": True,
        "bundle_root_verificado": bundle_root,
    }


# ---------------------------------------------------------------
# notify_sla_risk — informational SLA alert (non-interruptive timer)
# ---------------------------------------------------------------


def notify_sla_risk(variables: dict[str, Any]) -> dict[str, Any]:
    """Registra que a ETAPA de alerta de risco de SLA rodou. Retorna `{}` — NAO afirma nada.

    Serve `ST_NotifySlaRisk` (topico `operadora.fraude.notify_sla_risk`,
    SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn:247-251), alimentado SO pelo boundary
    NAO-interruptivo `BT_AlertaSlaFraude` (`cancelActivity="false"`) em
    `UT_DecisaoInvestigador`, em `${sla.sla_alerta}` (contrato DRAFT/verify). Informativa e jamais
    adversa: a User Task segue aberta e NENHUM desfecho adverso (acusacao ou outro) nasce deste
    alerta — so a decisao humana em `UT_DecisaoInvestigador` acusa
    (`register_fraud_accusation`, guard inalterado).

    FAB-SLA-RISK-NOTIFIED-SLICE4 (o motivo desta docstring). O retorno era, em toda entrega e sem
    calcular nada, `{"sla_risk_notified": True, ...}`. NENHUM canal e contatado por esta funcao —
    a `coordenacao-investigacao` pode nunca ter sido avisada — e a harness grava o retorno no escopo
    do processo no
    `complete` (`harness.py:1779-1783`), entao a constante entrava na instancia como trilha de
    auditoria. Mesma especie de `contas.notify_sla_risk` (FAB-NOTIFIED-TRIO), sob outra chave.

    ZERO CONSUMIDORES (mapa refeito antes de editar): nem `sla_risk_notified` nem `grupo_alertado`
    aparecem em `conditionExpression` de BPMN, `inputExpression` de DMN, worker a jusante ou linha
    de contrato — `grupo_alertado` so existe nesta funcao e no seu teste. As demais chaves eram
    eco do proprio input, ja no escopo. A observabilidade da etapa fica no `logger` abaixo, que
    declara explicitamente `notified_asserted=False`.
    """
    numero_caso = variables.get("numero_caso", "")

    logger.info(
        "fraude_notify_sla_risk",
        numero_caso=numero_caso,
        grupo_alertado="coordenacao-investigacao",
        notified_asserted=False,
    )

    return {}


# ---------------------------------------------------------------
# refer_to_legal — downstream referral (only after accusation)
# ---------------------------------------------------------------


def refer_to_legal(variables: dict[str, Any]) -> dict[str, Any]:
    """DECLARA que o referral a juridico/ANS/civel/penal NAO foi executado. Nao encaminha nada.

    Serve `ST_ReferToLegal` (topico `operadora.fraude.refer_to_legal`,
    SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn). E o CAMINHO ADVERSO do processo: o engine so
    alcanca esta task a jusante de (i) `UT_DecisaoInvestigador` com `decisao_fraude=ACUSAR_FRAUDE`
    setado por humano (L0 hard `fraud_accusation`), (ii) `bundle_root` selado e re-verificado em
    `register_fraud_accusation`, (iii) o SEGUNDO gate humano `UT_RevisaoReferral`
    (juridico/compliance aprovando os destinos) e (iv) `GW_DestinoReferral`
    (`${destino_referral_juridico == true || destino_referral_ans == true}`). Sua unica saida e
    `ST_PublishEncaminhadoJuridico` -> `End_EncaminhadoJuridico`.

    FAB-REFER-TO-LEGAL (o motivo desta docstring). O retorno era, em toda entrega e sem calcular
    nada, `{"referral_executado": True, "destinos": <eco de destino_referral>}`, de um corpo cuja
    unica instrucao era `logger.info`. NENHUMA autoridade e contatada por esta funcao nem por
    qualquer outra deste repo: `fraude.py` nao tem NENHUM call site de `kafka.publish(`
    (`register_fraude_workers` faz `del kafka  # unused`), nao ha transporte juridico/ANS
    (nenhum equivalente ao `AnsGatewayTransport` de `ans_submit` e injetado aqui — esta funcao nao
    recebe seam algum), e o contrato lista as proprias **Obrigacoes de referral** (prazo, forma,
    autoridade competente, escada de alcada por destino) como DRAFT/verify, "nao estao pinadas em
    nenhum repo" — nao ha nem especificacao do que integrar. A harness grava o retorno no escopo
    do processo no `complete`, entao a constante entrava na instancia — e na trilha ADR-0007 de
    uma acusacao de fraude JA CONSTITUIDA — afirmando a execucao de um ato regulatorio que nunca
    saiu do processo. E a instancia mais grave da familia FAB-* neste modulo, por estar do lado
    adverso da fronteira L0.

    ZERO CONSUMIDORES (mapa refeito antes de editar, `grep -rnw 'referral_executado|destinos'
    spec/ src/ tests/ docs/`): `referral_executado` so aparecia na propria atribuicao e na
    assercao do teste unitario; `destinos` so na atribuicao (as duas ocorrencias da PALAVRA em
    spec/docs sao prosa de `<bpmn:documentation>` de `UT_RevisaoReferral` e da linha de
    `destino_referral` no contrato, nao referencia a variavel). Nenhuma `conditionExpression`
    (o gateway le as flags planas `destino_referral_*`, nunca esta chave), nenhuma
    `inputExpression` de DMN, nenhum worker a jusante, nenhum golden.

    `destinos` NAO foi mantida: era eco byte-a-byte de `destino_referral`, que ja esta no escopo
    desde `UT_DecisaoInvestigador` e foi confirmada em `UT_RevisaoReferral`. Reescreve-la sob um
    segundo nome cria uma segunda fonte de verdade para uma decisao humana sem acrescentar fato
    algum — mesmo tratamento que `notify_sla_risk` deu as suas chaves de eco.

    A LACUNA NAO E SILENCIOSA. Em lugar da afirmacao falsa sai `referral_gap`
    (`GAP_REFERRAL_JURIDICO_NAO_LIGADO`), token fechado e sem PHI, DECLARADO no contrato
    (SP-OP-FRAUDE-001.md, "Variaveis de saida"), com semantica de AUSENCIA = referral real. Quem
    ler a instancia — o revisor de juridico/compliance, o investigador, a auditoria que le o
    desfecho `encaminhado_juridico` — ve que a autoridade NAO foi notificada, em vez de ver um
    carimbo de execucao. Mesma perna de visibilidade de `evidencia_gap`/`dossie_gap` (BEA-09) e do
    M-1 de `adequacao.measure_gap`.

    NAO E FAIL-CLOSED, e por que: levantar aqui pararia toda instancia que chegasse ao referral —
    uma lacuna de DEPLOY (a integracao nao existe para caso nenhum), nao um defeito do caso — e
    deixaria a acusacao humana ja constituida sem terminal. O roteamento dos destinos aprovados
    segue do `GW_DestinoReferral` e o desfecho segue publicado por `ST_PublishEncaminhadoJuridico`
    (que continua funcionando: e o caminho generico `operadora.events.publish`). Fechar o caminho
    e decisao de juridico/compliance, registrada em Pendencias do contrato — nao de engenharia.

    Invariante L0 inalterada: esta funcao nao decide nada, nao acusa, e nunca e alcancada sem a
    UT humana a montante.
    """
    destino = variables.get("destino_referral", {})

    # `warning`, nao `info` (precedente `fraude_evidencia_nao_coletada`): uma etapa de referral que
    # nao refere e defeito operacional num caminho adverso, nao progresso de rotina.
    # `referral_asserted=False` diz no log a mesma coisa que o retorno diz no escopo do processo.
    logger.warning(
        "fraude_referral_nao_executado",
        numero_caso=variables.get("numero_caso"),
        destino=destino,
        referral_asserted=False,
        gap=GAP_REFERRAL_JURIDICO_NAO_LIGADO,
    )

    return {"referral_gap": GAP_REFERRAL_JURIDICO_NAO_LIGADO}


# ---------------------------------------------------------------
# start_credenciamento — handoff to CRED-001
# ---------------------------------------------------------------


def _fenced_start(
    *,
    process_key: str,
    business_key: str,
    payload: dict[str, Any],
    decision_basis: dict[str, Any],
    tenant_id: str,
    engine: CibSevenTransport | None,
    audit_sink: AuditStartSink | None,
    log_event: str,
) -> tuple[str, bool]:
    """Shared fenced-start helper for the FRAUDE downstream handoffs (mirrors
    `inadimplencia.handoff_rescisao`, the merged T-C2 10th start site).

    FAIL-CLOSED: a missing engine seam / missing audit sink RAISES (transient -> retry -> incident)
    — a downstream handoff (CRED/CANCEL/INADIMPLENCIA) can never start un-audited (ADR-0007 L0),
    and the human's accusation decision can never be silently dropped. Returns
    `(instance_id, already_existed)`; the business-key idempotency makes a re-delivery an active hit
    (no double-start, P1-safe).

    GATE PREREQUISITES (GAP-D3-02) — asymmetric across this helper's three targets. `SP-OP-CANCEL-001`
    is an `EXCLUSIVE` start-dedup family (`mcp_cibseven.transport._START_DEDUP_POLICY`), so for the
    `start_contratual` CANCEL leg the chokepoint additionally requires `engine` to satisfy
    `HistoryQueryingTransport` and `audit_sink` to satisfy `DedupReportingAuditSink`; absent either
    it raises `StartDedupGateUnavailableError` before writing anything durable and NEVER degrades to
    the un-gated path. `SP-OP-INADIMPLENCIA-001` and `SP-OP-CRED-001` remain `NON_STRICT` and are
    unaffected. The live daemon satisfies both capabilities for every leg — same
    `engine=`/`audit_sink=` seams as `inadimplencia.handoff_rescisao`
    (`runtime/worker_runtime/service.py:787-790`).
    """
    if engine is None:
        logger.error("fraude_handoff_engine_seam_not_wired", process_key=process_key)
        raise RuntimeError(
            f"fraude handoff to {process_key}: engine seam (CibSevenTransport) not wired — cannot "
            "start; failing closed to a retry/incident (never a silent no-op)"
        )
    if audit_sink is None:
        logger.error("fraude_handoff_audit_sink_not_wired", process_key=process_key)
        raise RuntimeError(
            f"fraude handoff to {process_key}: audit sink (AuditStartSink) not wired — cannot emit "
            "the ADR-0007 start record, so the process is NOT started (fail-closed, "
            "emit-before-effect); failing to a retry/incident (never an un-audited start)"
        )
    provenance = AgentDecisionProvenance(
        agent_id=AUDIT_AGENT_ID,
        agent_version=_resolve_app_version(),
        tenant_id=tenant_id,
        decision_basis=decision_basis,
        model_id=None,
        prompt_version=None,
    )
    instance = asyncio.run(
        start_process_idempotent(
            engine,
            process_key=process_key,
            business_key=business_key,
            variables=payload,
            audit_sink=audit_sink,
            provenance=provenance,
        )
    )
    logger.info(
        log_event,
        process_key=process_key,
        business_key=business_key,
        instance_id=instance.instance_id,
        already_existed=instance.already_existed,
    )
    return instance.instance_id, instance.already_existed


def _cred_business_key(tenant_id: str, prestador_id: str) -> str:
    """`CRED-{tenant}-{prestador_id}` (contract `SP-OP-CRED-001.md`; IDENTICAL to
    `notification_bridge._cred_business_key`) — one active (des)credenciamento cycle per prestador."""
    return f"CRED-{tenant_id}-{prestador_id}"


def start_credenciamento(
    variables: dict[str, Any],
    *,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Handoff: idempotently START SP-OP-CRED-001 for provider de-credentialing.

    Only reached DOWNSTREAM of a human accusation + sealed bundle (BPMN `ST_StartCredenciamento`,
    entidade_tipo=prestador). CRED-001 owns its OWN human-gated adverse UT — FRAUDE NUNCA
    auto-descredencia. Starts CRED-001 (business key ``CRED-{tenant}-{prestador_id}``) through the
    fenced ``start_process_idempotent`` chokepoint (ADR-0007 emit-before-effect, T-C2). Was a stub
    returning a marker dict (NO start). FAIL-CLOSED on missing engine/audit seam or a missing/
    blank/None ``tenant_id``/``prestador_id`` anchor (EB-4 R1: validated with the SHARED
    `non_blank`, the bridge's own semantics — never a degenerate key like ``CRED-{t}-None``)."""
    # non_blank BEFORE str(): explicit None must refuse, never stringify to the truthy "None".
    if not (non_blank(variables.get("tenant_id")) and non_blank(variables.get("prestador_id"))):
        logger.error(
            "fraude_start_credenciamento_no_prestador",
            tenant_id=str(variables.get("tenant_id", "")),
        )
        raise FraudeError(
            ERR_FRAUDE_HANDOFF_SEM_ALVO,
            "start_credenciamento: tenant_id/prestador_id ausente, em branco ou None — nao ha "
            "ancora de business key para iniciar CRED-001 (recusado, nunca inicia com business "
            "key vazia/degenerada)",
        )
    tenant_id = str(variables.get("tenant_id", ""))
    prestador_id = str(variables.get("prestador_id", ""))
    business_key = _cred_business_key(tenant_id, prestador_id)
    payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "prestador_id": prestador_id,
        "numero_caso": str(variables.get("numero_caso", "")),
        "bundle_root": str(variables.get("bundle_root", "")),
        "origem_encaminhamento": "fraude",
    }
    instance_id, already_existed = _fenced_start(
        process_key=CRED_PROCESS_KEY,
        business_key=business_key,
        payload=payload,
        decision_basis={"origem_encaminhamento": "fraude", "entidade_tipo": "prestador"},
        tenant_id=tenant_id,
        engine=engine,
        audit_sink=audit_sink,
        log_event="fraude_start_credenciamento",
    )
    return {
        "handoff_credenciamento": True,
        "handoff_executado": True,
        "processo_destino": CRED_PROCESS_KEY,
        "cred_business_key": business_key,
        "cred_instance_id": instance_id,
        "cred_already_existed": already_existed,
    }


# ---------------------------------------------------------------
# start_contratual — handoff to CANCEL/INADIMPLENCIA
# ---------------------------------------------------------------


def _cancel_business_key(tenant_id: str, numero_contrato: str) -> str:
    """`CANCEL-{tenant}-{numero_contrato}` (IDENTICAL to `notification_bridge._cancel_business_key`).

    NO matricula fallback here — this handoff already refused a blank `numero_contrato` above.
    That asymmetry with `inadimplencia._cancel_business_key` (which DOES fall back) is exactly
    what defeated the anti-dupla-terminacao guard (B-2): the two composers mint different keys
    for the same contract. They now share `base.mint_contract_business_key`, and the guard sweeps
    every derivable form via `base.contract_business_key_forms`, so the asymmetry is visible in
    one place instead of being an invisible mismatch between two f-strings.

    WHY THE MINT COMPOSER AND NOT THE BARE FORMATTER (DL-0043 counter completeness). This used to
    call `base.contract_business_key` directly, which bypassed the shadow counter — so the
    `anchor="contrato"` series under-counted by exactly the CANCEL keys minted on the fraude
    handoff path. Routing through `mint_contract_business_key` with an EMPTY
    `matricula_beneficiario` makes the series complete without changing a byte of output: the
    caller (`start_contratual`) refuses a blank/None `numero_contrato` with `non_blank` BEFORE
    reaching here, so `resolve_contract_identity` always short-circuits on the truthy contract
    number and returns `(numero_contrato, "contrato")` — the same string
    `contract_business_key(family, tenant_id, numero_contrato)` returned, in every policy mode
    (the `pseudo_keys` branch is unreachable when a contract number is present). Calling this
    helper DIRECTLY with a blank `numero_contrato` would still produce the identical degenerate
    string, but would record `anchor="matricula"` for a matricula that is not there; that input
    is refused upstream and never occurs in production.
    """
    return mint_contract_business_key(
        CANCEL_KEY_FAMILY,
        tenant_id,
        numero_contrato=numero_contrato,
        matricula_beneficiario="",
    )


def _inadimplencia_business_key(tenant_id: str, numero_contrato: str) -> str:
    """`INAD-{tenant}-{numero_contrato}` — DISTINCT prefix from CANCEL for the SAME contract
    (IDENTICAL to `notification_bridge._inadimplencia_business_key`; the two processes coordinate via
    topology + a runtime active-instance check, not a shared key).

    Same `mint_contract_business_key` routing, and the same byte-identity argument, as
    `_cancel_business_key` above — this is the INAD half of the DL-0043 counter completeness fix.
    """
    return mint_contract_business_key(
        INADIMPLENCIA_KEY_FAMILY,
        tenant_id,
        numero_contrato=numero_contrato,
        matricula_beneficiario="",
    )


def start_contratual(
    variables: dict[str, Any],
    *,
    engine: CibSevenTransport | None = None,
    audit_sink: AuditStartSink | None = None,
) -> dict[str, Any]:
    """Handoff: idempotently START SP-OP-CANCEL-001 (and, for contract fraud, INADIMPLENCIA-001).

    Only reached DOWNSTREAM of a human accusation + sealed bundle (BPMN `ST_StartContratual`,
    entidade_tipo in {beneficiario, contrato}). CANCEL/INADIMPLENCIA own their OWN human-gated
    adverse UTs — FRAUDE NUNCA auto-rescinde. Matches the two `notification_bridge` rules for
    `fraude.acusacao_registrada`: CANCEL for {beneficiario, contrato}; INADIMPLENCIA additionally
    for {contrato}. Both keyed on ``numero_contrato`` through the fenced chokepoint (ADR-0007). Was a
    stub returning a marker dict (NO start). FAIL-CLOSED on missing engine/audit seam or a missing/
    blank/None ``tenant_id``/``numero_contrato`` anchor (EB-4 R1: validated with the SHARED
    `non_blank`, the bridge's own semantics — never a degenerate key like ``CANCEL-{t}-None``)."""
    # non_blank BEFORE str(): explicit None must refuse, never stringify to the truthy "None".
    if not (non_blank(variables.get("tenant_id")) and non_blank(variables.get("numero_contrato"))):
        logger.error(
            "fraude_start_contratual_no_contrato",
            tenant_id=str(variables.get("tenant_id", "")),
        )
        raise FraudeError(
            ERR_FRAUDE_HANDOFF_SEM_ALVO,
            "start_contratual: tenant_id/numero_contrato ausente, em branco ou None — nao ha "
            "ancora de business key para iniciar CANCEL/INADIMPLENCIA (recusado, nunca inicia "
            "com business key vazia/degenerada)",
        )
    tenant_id = str(variables.get("tenant_id", ""))
    numero_contrato = str(variables.get("numero_contrato", ""))
    entidade_tipo = str(variables.get("entidade_tipo", ""))
    base_payload: dict[str, Any] = {
        "tenant_id": tenant_id,
        "numero_contrato": numero_contrato,
        "beneficiario_pseudo_id": str(variables.get("beneficiario_pseudo_id", "")),
        "numero_caso": str(variables.get("numero_caso", "")),
        "bundle_root": str(variables.get("bundle_root", "")),
        "origem_encaminhamento": "fraude",
    }

    # CANCEL-001 — always started for the contract/beneficiario rescisao path.
    cancel_key = _cancel_business_key(tenant_id, numero_contrato)
    cancel_id, cancel_existed = _fenced_start(
        process_key=CANCEL_PROCESS_KEY,
        business_key=cancel_key,
        payload=base_payload,
        decision_basis={"origem_encaminhamento": "fraude", "entidade_tipo": entidade_tipo or "contrato"},
        tenant_id=tenant_id,
        engine=engine,
        audit_sink=audit_sink,
        log_event="fraude_start_contratual_cancel",
    )
    result: dict[str, Any] = {
        "handoff_contratual": True,
        "handoff_executado": True,
        "processo_destino": CANCEL_PROCESS_KEY,
        "cancel_business_key": cancel_key,
        "cancel_instance_id": cancel_id,
        "cancel_already_existed": cancel_existed,
    }

    # INADIMPLENCIA-001 — additionally for a CONTRATO (secondary handoff; mirrors the bridge's
    # FRAUDE->INADIMPLENCIA rule, entidade_tipo == "contrato"). A beneficiario does not trigger it.
    if entidade_tipo == "contrato":
        inad_key = _inadimplencia_business_key(tenant_id, numero_contrato)
        inad_id, inad_existed = _fenced_start(
            process_key=INADIMPLENCIA_PROCESS_KEY,
            business_key=inad_key,
            payload=base_payload,
            decision_basis={"origem_encaminhamento": "fraude", "entidade_tipo": "contrato"},
            tenant_id=tenant_id,
            engine=engine,
            audit_sink=audit_sink,
            log_event="fraude_start_contratual_inadimplencia",
        )
        result["processo_destino"] = f"{CANCEL_PROCESS_KEY} / {INADIMPLENCIA_PROCESS_KEY}"
        result["inadimplencia_business_key"] = inad_key
        result["inadimplencia_instance_id"] = inad_id
        result["inadimplencia_already_existed"] = inad_existed

    return result


# ---------------------------------------------------------------
# publish_completed — APOSENTADA (FAB-PUBLISH-CONTACT / NEW-A2-1)
#
# Havia aqui um `publish_completed(variables)` registrado como `FunctionWorker` no topico
# `operadora.fraude.publish_completed`. Foi REMOVIDO — funcao e registro — por tres motivos que se
# somam, e nao por um so:
#
#  1. ORFAO. Nenhum `serviceTask` de SP-OP-FRAUDE-001 (nem de qualquer outro BPMN em
#     `spec/processes/bpmn/**`) declara esse `camunda:topic`. Todo `ST_Publish*` deste processo
#     roteia pelo generico `operadora.events.publish`. Um topico que nenhum `serviceTask` declara
#     nunca recebe external task: a funcao era codigo morto do ponto de vista do engine.
#  2. FATO FABRICADO. O corpo tinha uma unica instrucao (`logger.info`) e mesmo assim devolvia
#     `evento_publicado: True` — chave que a allowlist de escrita de escopo da harness
#     (`harness.py`, `_SAFE_DECISION_BASIS_KEYS`) DEIXAVA entrar na instancia, ou seja, a afirmacao de um
#     publish que nunca ocorreu entrava na trilha ADR-0007 de uma investigacao de fraude. Este
#     modulo nao tem UM call site de `kafka.publish(` (`register_fraude_workers` faz
#     `del kafka  # unused`). Quem publica de verdade e `events.py`, e ele reporta
#     `event_published` a partir do bool de entrega REAL do produtor (mais
#     `event_publish_best_effort_failure` quando a falha e engolida).
#  3. SEGUNDA FONTE DE VERDADE, E ERRADA. O `desfecho` era recalculado em Python a partir de
#     `decisao_fraude`, enquanto o BPMN ja fixa o vocabulario como literal `event_desfecho` em
#     cada uma das SEIS tasks de publicacao de desfecho. A copia em Python conhecia dois valores e
#     mapeava TUDO que nao fosse `ACUSAR_FRAUDE` para `arquivado_sem_indicio` — errado para 4 dos
#     5 terminais (`monitorar`, `encaminhado_credenciamento`, `encaminhado_contratual`,
#     `encaminhado_juridico`). Decisao de negocio pertence ao BPMN/DMN, nao ao worker (C3).
#
# POR QUE NAO UM TOKEN `GAP_*` (a diferenca deliberada para `refer_to_legal`/`gather_evidence`).
# Nao ha lacuna a declarar: o evento de desfecho E publicado, por `ST_Publish*` ->
# `operadora.events.publish` -> `events.py`. Emitir um `publish_gap` afirmaria uma lacuna
# INEXISTENTE — o mesmo defeito de honestidade na direcao oposta, como ja registrado na docstring
# de `intake` (FAB-INTAKE-CASO-REGISTRADO). Remover e o mesmo tratamento dado a
# `operadora.lgpd.publish_completed` (LGPD-PUBLISH-COMPLETED-ORPHAN-TOPIC, decisao do dono R-103,
# remedio R-H) e a `operadora.programa.monitor_programa` (PERSP-C5-MONITOR-PROGRAMA); registro da
# decisao em `docs/processes/catalog.md`, nao so nesta mensagem de commit.
# ---------------------------------------------------------------


# ---------------------------------------------------------------
# Custom error
# ---------------------------------------------------------------


class FraudeError(Exception):
    """Worker guard error for fraude adverse effects."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


# ---------------------------------------------------------------
# Bootstrap — FunctionWorker adapter (T1.2/ADR-0026 Decisao §2a).
#
# Topic mapping vs spec/processes/bpmn/SP-OP-FRAUDE-001_Investigacao_Fraude.bpmn
# (excl. shared/out-of-scope `operadora.events.publish`) — all 10 spec-declared
# `operadora.fraude.*` topics have an EXACT 1:1 name match with their implementing
# function (t2.5-p2b-round2 added `notify_sla_risk`, closing the prior gap), and
# NOTHING ELSE is registered: FAB-PUBLISH-CONTACT retired the 11th topic,
# `operadora.fraude.publish_completed` (see the block above `FraudeError` for why).
# Both directions are pinned — no missing worker AND no orphan — by
# tests/integration/processes/test_sp_op_fraude_001.py::
# test_bpmn_fraude_topics_vs_registered_workers.
# ---------------------------------------------------------------


def register_fraude_workers(
    harness: WorkerHarness,
    kafka: KafkaPublisher | None = None,
    **seams: Any,
) -> None:
    """Register the SP-OP-FRAUDE-001 function workers on `harness`.

    `kafka` is accepted (donor contract, ADR-0026 §2) but unused — no fraude.py worker declares a
    Kafka dependency (`notify_sla_risk` included — dict-first, informational-only, mirrors
    `inadimplencia.notify_sla_risk`/`cancel.notify_sla_risk`). `dmn` (ADR-0028 §1 seam, T2.7 phase
    2) is threaded via `functools.partial` into `score_indicators` ONLY — the 7
    `fraude_scoring/*` tables it evaluates (see that function's docstring); no other function in
    this module evaluates a DMN.
    """
    del kafka  # unused — no fraude.py worker declares a Kafka dependency
    dmn = seams.get("dmn")
    # Fenced-start seams (T-C2, mirrors register_inadimplencia_workers) — threaded into the two
    # downstream handoff workers ONLY. ABSENT (`None`) -> they RAISE before any engine effect (an
    # un-audited CRED/CANCEL/INADIMPLENCIA start is structurally impossible, ADR-0007 L0). In the
    # live daemon: FreshClientCibSevenTransport + FreshSinkAuditEmitter (loop-agnostic).
    engine: CibSevenTransport | None = seams.get("engine")
    audit_sink: AuditStartSink | None = seams.get("audit_sink")
    harness.register_worker(FunctionWorker("operadora.fraude.intake", intake))
    harness.register_worker(FunctionWorker("operadora.fraude.gather_evidence", gather_evidence))
    harness.register_worker(
        FunctionWorker("operadora.fraude.score_indicators", functools.partial(score_indicators, dmn=dmn))
    )
    harness.register_worker(FunctionWorker("operadora.fraude.assemble_dossier", assemble_dossier))
    harness.register_worker(FunctionWorker("operadora.fraude.seal_custody_bundle", seal_custody_bundle))
    harness.register_worker(
        FunctionWorker("operadora.fraude.register_fraud_accusation", register_fraud_accusation)
    )
    harness.register_worker(FunctionWorker("operadora.fraude.notify_sla_risk", notify_sla_risk))
    harness.register_worker(FunctionWorker("operadora.fraude.refer_to_legal", refer_to_legal))
    harness.register_worker(
        FunctionWorker(
            "operadora.fraude.start_credenciamento",
            functools.partial(start_credenciamento, engine=engine, audit_sink=audit_sink),
        )
    )
    harness.register_worker(
        FunctionWorker(
            "operadora.fraude.start_contratual",
            functools.partial(start_contratual, engine=engine, audit_sink=audit_sink),
        )
    )
