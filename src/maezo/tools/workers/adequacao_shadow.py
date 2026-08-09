"""Shadow evaluator for the `adequacao_gap` CANDIDATE rule set (RN 259 rule-order inversion).

THE PROBLEM THIS OBSERVES (it does not solve it). `spec/processes/dmn/adequacao_gap.dmn` is KNOWN
WRONG and the finding is OPEN and human-gated: `hitPolicy=FIRST` with `r_eletivo_leve` (:90-99)
BEFORE `r_conforme` (:110-119), and `r_conforme` carries NO time/distance ceiling at all
(wildcards at :112-114) — so an ARBITRARILY BAD elective access reads `CONFORME`
(PLANS.md:150-159 §0.5.4 item 1; docs/review-queue.md:160). It is runtime-MITIGATED by the
owner-ratified fail-safe in `adequacao.route_remediation` (adequacao.py:338-362), which preserves
the DMN verdict but REFUSES TO ACT on a `CONFORME` the measurements contradict.

WHAT THIS MODULE IS. A pure evaluation of the CANDIDATE rule set declared in
`spec/processes/dmn/adequacao-gap-shadow-candidate.yaml`, plus the ratification gate that keeps it
observational. It has NO engine interaction: no `DmnTransport`, no HTTP, no external task. It reads
one static YAML manifest and evaluates plain numbers.

WHAT THIS MODULE IS NOT. It is NOT a correction of the live table. Per ADR-0028
(docs/adr/0028-dmn-evaluation-engine-side.md:214-216) — "If the DMN itself is wrong, fix forward IN
SPEC, never patch the Python back" — engineering never corrects the table; the REGULATORY OWNER
does, as a spec DATA change. Nothing here can change what the deployed table decides, and nothing
here changes what the worker does with that decision.

THE TWO GATES.

  1. RATIFICATION. `evaluate_for_enforcement` RAISES `EnforcementNotRatifiedError` while the
     manifest is not ratified. There is no enforcing consumer today; that function exists so the
     refusal is a pinned, testable property rather than a comment. Ratification is the manifest's
     three fields (`ratificado: true` + non-blank, non-placeholder `revisor`/`ratificado_em`),
     mirroring `auth_criteria._entry_is_ratified` — including the placeholder guard, since the
     shipped file carries `PLACEHOLDER_*` identity values by design.

  2. SHADOW. `shadow_divergence_event` compares the candidate verdict with the LIVE verdict and
     returns a bounded, non-PHI event mapping when they differ — verdicts, the five rule inputs,
     and the candidate rule id. Nothing else. It NEVER raises: every failure mode (unreadable
     manifest, malformed input, anything) resolves to `None` (= "no event"), because the ONLY
     consumer is a worker whose verdict must not depend on it.

PHI POSTURE. This process carries no beneficiary identifier at all — geography is
region/municipio granularity only (ADR-0006, and the contract's own note at
SP-OP-ADEQUACAO-001.md:70). The event deliberately omits even the cell identity
(`regiao_saude`/`especialidade`): a divergence is a property of the RULE SET, not of a cell, so the
cell adds nothing the reviewer needs and would widen the surface for no gain.

SOURCE OF TRUTH. The manifest is the source of truth for the candidate rules; the constants below
are a transcription of it, and `tests/unit/tools/workers/test_adequacao_shadow.py` pins the two
against each other rule-for-rule (ids, thresholds, outputs and order). A drift fails that test.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import structlog
import yaml

from maezo.agents import resolve_spec_dir

logger = structlog.get_logger(__name__)

#: Narrow env override for the manifest path — test-pinning hook and operator escape hatch, the
#: same shape as `auth_criteria.MANIFEST_PATH_ENV`. Unset, the default resolves through the single
#: T0.3 mechanism (`resolve_spec_dir`), never a second scheme.
MANIFEST_PATH_ENV = "MAEZO_ADEQUACAO_CANDIDATE_MANIFEST_PATH"

#: Manifest filename under `<spec>/processes/dmn/`. `.yaml`, NOT `.dmn`, so no artifact-selection
#: path can pick it up: both `collect_artifacts` (platform/deploy/engine_deploy.py:140) and the
#: validation CLI (platform/validation/cli.py:103) glob `*.dmn` only.
MANIFEST_FILENAME = "adequacao-gap-shadow-candidate.yaml"

#: Ratification fields — same three, same strictness, as `auth_criteria` (ADR-0007 accountability).
_RATIFIED_FLAG = "ratificado"
_RATIFIED_BY = "revisor"
_RATIFIED_AT = "ratificado_em"

#: Machine-detectable placeholder marker. The shipped manifest carries `PLACEHOLDER_*` in `revisor`
#: and `ratificado_em` so a half-finished edit (flag flipped, identity left as shipped) reads as NOT
#: ratified instead of as an anonymous ratification.
PLACEHOLDER_MARKER = "PLACEHOLDER"

# -------------------------------------------------------------------------------------------------
# Candidate thresholds — TRANSCRIBED, never invented. Each is a line of the live table.
# -------------------------------------------------------------------------------------------------

#: `> 30` / `<= 30` — urgencia_emergencia time ceiling (adequacao_gap.dmn:53 and :103).
URGENCIA_TEMPO_MAX_MIN = 30
#: `> 30.0` / `<= 30.0` — urgencia_emergencia distance ceiling (adequacao_gap.dmn:64 and :104).
URGENCIA_DISTANCIA_MAX_KM = 30.0
#: `<= 60` — the ONLY elective time ceiling the table declares (adequacao_gap.dmn:93); the same
#: number the owner-ratified runtime fail-safe already uses (adequacao.py:182).
ELETIVO_TEMPO_MAX_MIN = 60
#: `<= 50.0` — the ONLY elective distance ceiling the table declares (adequacao_gap.dmn:94); same
#: number as the fail-safe (adequacao.py:183).
ELETIVO_DISTANCIA_MAX_KM = 50.0

#: The two `tipo_carater` literals the live table declares (adequacao_gap.dmn:52,62,92,102).
TIPO_ELETIVO = "eletivo"
TIPO_URGENCIA = "urgencia_emergencia"

#: `gap_adequacao`'s closed, NEUTRAL allowlist (adequacao_gap.dmn:16). The candidate emits a strict
#: subset of it — see `CANDIDATE_EMITS_CONFORME`.
GAP_CONFORME = "CONFORME"
GAP_LEVE = "GAP_LEVE"
GAP_MODERADO = "GAP_MODERADO"
GAP_CRITICO = "GAP_CRITICO"

#: The candidate has NO rule emitting `CONFORME` — deliberately, and NOT as an oversight: the live
#: `r_conforme`'s own description declares a ceiling ("dentro dos thresholds") that, if written,
#: would make it identical to the two `*_leve` rules, and the table declares no threshold separating
#: CONFORME from GAP_LEVE. Choosing one would be inventing a regulatory value. See the manifest's
#: `decisao_pendente_do_dono` / DECISAO-A. Pinned by the test module.
CANDIDATE_EMITS_CONFORME = False

#: Candidate rule ids, in the FIRST-hit evaluation order the manifest declares.
#:
#: The RELATIVE order of every PRESERVED rule is the live table's own — the candidate is "insert two
#: rows, delete one row", nothing else. Re-ranking a preserved pair would silently change severities
#: that are not part of the defect (e.g. moving `c_cobertura_insuficiente` above the urgencia
#: ceilings would downgrade an out-of-ceiling urgencia with `cobertura=false` from GAP_CRITICO to
#: GAP_MODERADO). The two NEW rules sit in the ceiling-before-within-band slot, immediately ahead of
#: the `*_leve` rules they gate.
CANDIDATE_RULE_ORDER: tuple[str, ...] = (
    "c_urgencia_tempo_acima_teto",  # = r_urgencia_critico (adequacao_gap.dmn:50-59)
    "c_urgencia_distancia_acima_teto",  # = r_urgencia_distancia_critico (:60-69)
    "c_zero_prestadores_critico",  # = r_zero_prestadores_critico (:70-79)
    "c_cobertura_insuficiente",  # = r_cobertura_insuficiente (:80-89)
    "c_eletivo_tempo_acima_teto",  # NEW — closes the hole (ceiling from :93)
    "c_eletivo_distancia_acima_teto",  # NEW — closes the hole (ceiling from :94)
    "c_eletivo_leve",  # = r_eletivo_leve (:90-99)
    "c_urgencia_leve",  # = r_urgencia_leve (:100-109)
    # r_conforme (:110-119) has NO successor — removed, not "fixed". See DECISAO-A.
    "c_catchall",  # = r_catchall (:120-129)
)


class EnforcementNotRatifiedError(RuntimeError):
    """An ENFORCING use of the candidate rule set was attempted while it is not ratified.

    The candidate is a PROPOSAL to the regulatory owner, not a rule. Until the owner ratifies the
    manifest AND applies the rules to the live table (an ADR-0028 spec data change that is the
    owner's act, not engineering's), the only sanctioned use is observation.
    """


@dataclass(frozen=True, slots=True)
class CandidateVerdict:
    """One candidate evaluation: which rule matched, and what it emits.

    Attributes:
        gap_adequacao: a member of `gap_adequacao`'s closed set (adequacao_gap.dmn:16).
        regra: the candidate rule id that matched (always one of `CANDIDATE_RULE_ORDER`).
    """

    gap_adequacao: str
    regra: str


@dataclass(frozen=True, slots=True)
class CandidateRatification:
    """Ratification state of the candidate manifest. FAIL-CLOSED default: not ratified."""

    ratificado: bool
    revisor: str = ""
    ratificado_em: str = ""


#: The value every load failure resolves to.
_NOT_RATIFIED = CandidateRatification(ratificado=False)


# -------------------------------------------------------------------------------------------------
# The candidate rule set — pure evaluation, no I/O, no engine.
# -------------------------------------------------------------------------------------------------


def evaluate_candidate(
    *,
    tipo_carater: str,
    tempo_acesso_apurado_min: int,
    distancia_apurada_km: float,
    prestadores_disponiveis: int,
    cobertura_geo_suficiente: bool,
) -> CandidateVerdict:
    """Evaluate the CANDIDATE rule set. Pure: same inputs -> same verdict, no I/O, never raises.

    FIRST-hit semantics, in `CANDIDATE_RULE_ORDER`. This mirrors the manifest
    (`spec/processes/dmn/adequacao-gap-shadow-candidate.yaml`, `regras_candidatas`) rule-for-rule;
    the test module pins the two against each other so this transcription cannot drift.

    OBSERVATION ONLY. This is not what the engine decides and not what the worker acts on. The
    live table's verdict remains authoritative (ADR-0028) until the regulatory owner changes the
    table itself.

    Args:
        tipo_carater: `"eletivo"` | `"urgencia_emergencia"`; anything else (including `""`, which
            is what the worker sends when the variable is absent — adequacao.py:297) reaches the
            conservative catch-all, which is exactly ACHADO-1 in the manifest.
        tempo_acesso_apurado_min: measured access time in minutes (contract type `integer`).
        distancia_apurada_km: measured distance in km (contract type `double`).
        prestadores_disponiveis: provider count (contract type `integer`).
        cobertura_geo_suficiente: geographic-coverage sufficiency flag (`boolean`).

    Returns:
        The matching `CandidateVerdict`.
    """
    # 1-2 PRESERVED (r_urgencia_critico, r_urgencia_distancia_critico) — first, exactly as live.
    if tipo_carater == TIPO_URGENCIA:
        if tempo_acesso_apurado_min > URGENCIA_TEMPO_MAX_MIN:
            return CandidateVerdict(GAP_CRITICO, "c_urgencia_tempo_acima_teto")
        if distancia_apurada_km > URGENCIA_DISTANCIA_MAX_KM:
            return CandidateVerdict(GAP_CRITICO, "c_urgencia_distancia_acima_teto")

    # 3-4 PRESERVED (r_zero_prestadores_critico, r_cobertura_insuficiente).
    if prestadores_disponiveis == 0:
        return CandidateVerdict(GAP_CRITICO, "c_zero_prestadores_critico")
    if cobertura_geo_suficiente is False:
        return CandidateVerdict(GAP_MODERADO, "c_cobertura_insuficiente")

    # 5-6 THE TWO NEW RULES — they close exactly the hole PLANS.md:152-159 records. The ceilings are
    # the live table's own (r_eletivo_leve, :93/:94); the severity is the live table's own
    # conservative disposition for an uncovered combination (r_catchall, :120-129).
    if tipo_carater == TIPO_ELETIVO:
        if tempo_acesso_apurado_min > ELETIVO_TEMPO_MAX_MIN:
            return CandidateVerdict(GAP_CRITICO, "c_eletivo_tempo_acima_teto")
        if distancia_apurada_km > ELETIVO_DISTANCIA_MAX_KM:
            return CandidateVerdict(GAP_CRITICO, "c_eletivo_distancia_acima_teto")

    # 7-8 PRESERVED (r_eletivo_leve, r_urgencia_leve).
    if tipo_carater == TIPO_ELETIVO and prestadores_disponiveis > 0 and cobertura_geo_suficiente is True:
        return CandidateVerdict(GAP_LEVE, "c_eletivo_leve")
    if tipo_carater == TIPO_URGENCIA and prestadores_disponiveis > 0 and cobertura_geo_suficiente is True:
        return CandidateVerdict(GAP_LEVE, "c_urgencia_leve")

    # Conservative fail-safe, reachable at last (ACHADO-1): the live table's `r_conforme` used to
    # mask it for the whole {prestadores>0 AND cobertura=true} subspace, whatever `tipo_carater`.
    return CandidateVerdict(GAP_CRITICO, "c_catchall")


def evaluate_for_enforcement(
    *,
    tipo_carater: str,
    tempo_acesso_apurado_min: int,
    distancia_apurada_km: float,
    prestadores_disponiveis: int,
    cobertura_geo_suficiente: bool,
    manifest_path: str | Path | None = None,
) -> CandidateVerdict:
    """ENFORCING entry point — REFUSES while the candidate is not ratified.

    There is NO enforcing consumer of the candidate in this repository. This function exists so
    that "the candidate may not be enforced" is a property a test can fail on, rather than a
    comment someone can miss. It refuses on the manifest's ratification state alone, so the refusal
    lifts by a DATA change (the owner's three fields), never by editing this module.

    Ratifying the manifest is necessary but NOT sufficient for the live behaviour to change: the
    engine evaluates the DEPLOYED table, so the owner must also apply the rules to
    `spec/processes/dmn/adequacao_gap.dmn` — which per ADR-0028 (:214-216) is the owner's act.

    Raises:
        EnforcementNotRatifiedError: whenever the manifest is not ratified — which is its state
            today, and its state after any load failure (fail-closed).
    """
    status = candidate_ratification(manifest_path)
    if not status.ratificado:
        raise EnforcementNotRatifiedError(
            "adequacao_gap candidate rule set is NOT ratified — enforcement refused. "
            f"Ratify {MANIFEST_FILENAME} (ratificado/revisor/ratificado_em) and apply the rules "
            "to spec/processes/dmn/adequacao_gap.dmn; per ADR-0028 the live-table edit is the "
            "regulatory owner's act, never engineering's."
        )
    return evaluate_candidate(
        tipo_carater=tipo_carater,
        tempo_acesso_apurado_min=tempo_acesso_apurado_min,
        distancia_apurada_km=distancia_apurada_km,
        prestadores_disponiveis=prestadores_disponiveis,
        cobertura_geo_suficiente=cobertura_geo_suficiente,
    )


# -------------------------------------------------------------------------------------------------
# Manifest loading — fail-closed, never raises (mirrors auth_criteria.load_criteria_sources).
# -------------------------------------------------------------------------------------------------


def _manifest_default_path() -> str:
    """`<spec>/processes/dmn/adequacao-gap-shadow-candidate.yaml` via the single T0.3 mechanism."""
    return str(resolve_spec_dir() / "processes" / "dmn" / MANIFEST_FILENAME)


def _refuse(reason: str, detail: str) -> CandidateRatification:
    """Log the refusal and return the fail-closed (not ratified) state. NEVER raises."""
    logger.error("adequacao_candidate_manifest_unavailable", reason=reason, detail=detail)
    return _NOT_RATIFIED


def _accountable(value: Any) -> str:
    """`value.strip()` when it is a non-blank, non-PLACEHOLDER string; `""` otherwise.

    The placeholder guard is what makes the shipped manifest's own `revisor`/`ratificado_em` values
    unusable as a ratification: flipping `ratificado: true` without replacing them leaves the source
    NOT ratified, so an unfinished edit cannot read as an anonymous approval (ADR-0007).
    """
    if not isinstance(value, str):
        return ""
    stripped = value.strip()
    if not stripped or PLACEHOLDER_MARKER in stripped.upper():
        return ""
    return stripped


def load_candidate_ratification(path: str | Path | None = None) -> CandidateRatification:
    """Load the candidate manifest's ratification state. FAILS CLOSED — never raises.

    Path resolution: explicit `path` > `MAEZO_ADEQUACAO_CANDIDATE_MANIFEST_PATH` >
    `<spec>/processes/dmn/adequacao-gap-shadow-candidate.yaml`.

    Every failure mode (unresolvable spec dir, missing file, unreadable, non-UTF-8, malformed YAML,
    wrong schema, incomplete/placeholder accountability fields) returns `ratificado=False` plus one
    structured log line. Nothing here can ever mark the candidate ratified.
    """
    raw_path = path if path is not None else os.environ.get(MANIFEST_PATH_ENV)
    if not raw_path:
        try:
            raw_path = _manifest_default_path()
        except Exception as exc:  # noqa: BLE001 - fail-closed: unresolvable spec/ ratifies nothing
            return _refuse("path_unresolved", f"could not resolve the default manifest path: {exc}")

    manifest_path = Path(raw_path)
    if not manifest_path.is_file():
        return _refuse("file_not_found", f"no readable manifest file at {manifest_path}")

    try:
        raw_text = manifest_path.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        # NOT covered by OSError (UnicodeDecodeError subclasses ValueError) — same explicit catch
        # legal_bases_matrix.py / auth_criteria.py document.
        return _refuse("invalid_encoding", f"{manifest_path} is not valid UTF-8: {exc}")
    except OSError as exc:
        return _refuse("unreadable", f"could not read {manifest_path}: {exc}")

    try:
        data = yaml.safe_load(raw_text)
    except yaml.YAMLError as exc:
        return _refuse("invalid_yaml", f"malformed YAML in {manifest_path}: {exc}")

    if not isinstance(data, dict):
        return _refuse(
            "invalid_schema", f"{manifest_path}: root must be a mapping, got {type(data).__name__}"
        )

    if data.get(_RATIFIED_FLAG) is not True:
        return _NOT_RATIFIED

    revisor = _accountable(data.get(_RATIFIED_BY))
    ratificado_em = _accountable(data.get(_RATIFIED_AT))
    missing = [
        field for field, value in ((_RATIFIED_BY, revisor), (_RATIFIED_AT, ratificado_em)) if not value
    ]
    if missing:
        logger.warning(
            "adequacao_candidate_ratification_incomplete",
            missing_fields=missing,
            detail=("ratificado=true but an accountability field is absent/blank/PLACEHOLDER — NOT ratified"),
        )
        return _NOT_RATIFIED

    logger.info(
        "adequacao_candidate_manifest_ratified",
        path=str(manifest_path),
        revisor=revisor,
        ratificado_em=ratificado_em,
    )
    return CandidateRatification(ratificado=True, revisor=revisor, ratificado_em=ratificado_em)


@lru_cache(maxsize=8)
def _load_cached(resolved_path: str | None) -> CandidateRatification:
    return load_candidate_ratification(resolved_path)


def candidate_ratification(path: str | Path | None = None) -> CandidateRatification:
    """Cached `load_candidate_ratification` — same static-governance-config caching rationale (and
    the same restart-to-refresh trade-off) as `auth_criteria.criteria_sources` / `ceilings`."""
    return _load_cached(str(path) if path is not None else None)


# -------------------------------------------------------------------------------------------------
# Shadow observation — the ONLY thing wired into a worker, and it can only produce a log line.
# -------------------------------------------------------------------------------------------------

#: Structured-log event name for a live-vs-candidate divergence.
SHADOW_DIVERGENCE_EVENT = "adequacao_gap_shadow_divergencia"


def shadow_divergence_event(
    *,
    gap_adequacao_dmn: str,
    tipo_carater: str,
    tempo_acesso_apurado_min: int,
    distancia_apurada_km: float,
    prestadores_disponiveis: int,
    cobertura_geo_suficiente: bool,
) -> dict[str, Any] | None:
    """The bounded, non-PHI divergence record — or `None` when the two verdicts agree.

    Returns `None` (never raises) on ANY problem, because the only caller is a worker whose verdict
    must not depend on this. The mapping contains ONLY: the two verdicts, the candidate rule id, and
    the five rule inputs. No beneficiary identifier exists in this process at all (ADR-0006), and the
    cell identity is deliberately omitted — a divergence is a property of the RULE SET, not of a
    cell.

    Deliberately OMITS ratification state: `candidato_ratificado` is constant `False` for every
    event this can ever emit (the manifest ships unratified, and there is no path that flips it
    while the worker is running — ratifying is a DATA change to a file this process only reads at
    call time, via a cached loader). A field whose value is always the same is not evidence; it also
    cost a sync YAML load (via `candidate_ratification()`) on the FIRST divergence of the process's
    lifetime, on the worker's own call path. If ratification state is ever needed here, read it from
    the manifest directly rather than re-adding the call this removed.
    """
    try:
        candidato = evaluate_candidate(
            tipo_carater=tipo_carater,
            tempo_acesso_apurado_min=tempo_acesso_apurado_min,
            distancia_apurada_km=distancia_apurada_km,
            prestadores_disponiveis=prestadores_disponiveis,
            cobertura_geo_suficiente=cobertura_geo_suficiente,
        )
        if candidato.gap_adequacao == gap_adequacao_dmn:
            return None
        return {
            "gap_adequacao_dmn": gap_adequacao_dmn,
            "gap_adequacao_candidato": candidato.gap_adequacao,
            "regra_candidata": candidato.regra,
            "tipo_carater": tipo_carater,
            "tempo_acesso_apurado_min": tempo_acesso_apurado_min,
            "distancia_apurada_km": distancia_apurada_km,
            "prestadores_disponiveis": prestadores_disponiveis,
            "cobertura_geo_suficiente": cobertura_geo_suficiente,
        }
    except Exception as exc:  # noqa: BLE001 — shadow observation may never disturb a caller
        logger.warning("adequacao_gap_shadow_indisponivel", error=str(exc))
        return None


def record_shadow_divergence(
    *,
    gap_adequacao_dmn: str,
    tipo_carater: str,
    tempo_acesso_apurado_min: int,
    distancia_apurada_km: float,
    prestadores_disponiveis: int,
    cobertura_geo_suficiente: bool,
) -> None:
    """Emit the divergence event if there is one. Returns `None` ALWAYS; never raises.

    The house structured-logging seam (`structlog`), the same one the fail-safe itself uses
    (`adequacao_conforme_recusado_por_acesso`, adequacao.py:353-360). Deliberately NOT a metric: a
    metric would need a label set, and the useful labels here are the measurements themselves.
    """
    event = shadow_divergence_event(
        gap_adequacao_dmn=gap_adequacao_dmn,
        tipo_carater=tipo_carater,
        tempo_acesso_apurado_min=tempo_acesso_apurado_min,
        distancia_apurada_km=distancia_apurada_km,
        prestadores_disponiveis=prestadores_disponiveis,
        cobertura_geo_suficiente=cobertura_geo_suficiente,
    )
    if event is None:
        return
    logger.warning(SHADOW_DIVERGENCE_EVENT, **event)
