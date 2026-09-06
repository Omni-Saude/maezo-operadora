"""Unit tests for Beatriz's REAL graph (T1.12 — SP-OP-FRAUDE-001, investigacao de fraude).

Every node is exercised against fakes: an in-file fake inference provider (no LLM SDK, no
network) and a fake `PatientSummaryReader`. Beatriz evaluates NO DMN and starts NO process (the
R1-audited `spec/agents/beatriz/agent.yaml` declares neither transport), so there is no engine
transport to fake — the custody-bound leak surface asserted on here is the DOSSIER itself (the
corpus `operadora.fraude.seal_custody_bundle` seals) plus `evidencia_normalizada`/
`business_key`/`desfecho`/`error`. Live-engine acceptance status is reported in the PR body
(ADR-0011: real engine, never mocked there; never fabricated here).

Synthetic identifiers are deliberately LOW-ENTROPY (`t1`/`000001`/`pseudo-b1`/...) — gitleaks
hygiene, same lesson as the fernando/marina branches' false positives.
"""

from __future__ import annotations

import inspect
import json
from pathlib import Path
from typing import Any, cast, get_args

import pytest
import yaml

from maezo.agents.beatriz.graph import (
    _CALLER_INPUT_FIELDS,
    ERROR_CONTEXTO_RUNTIME_AUSENTE,
    NOTE_FHIR_READER_NAO_CONFIGURADO,
    NOTE_NARRATIVA_INDISPONIVEL,
    NOTE_RESUMO_FHIR_INDISPONIVEL,
    NOTE_SEM_EVIDENCIA_NO_INTAKE,
    PROMPT_VERSIONS,
    BeatrizGraph,
    BeatrizState,
    Desfecho,
    _business_key,
    _normalize_evidence,
    _output_field_resets,
    build,
)

_AGENTS_ROOT = Path(__file__).parent.parent.parent.parent / "spec" / "agents"

# Synthetic raw-PHI values for the custody probes (obviously fake, low-entropy).
_RAW_CPF = "111.222.333-44"
_RAW_NAME = "Fulano De Tal Sintetico"

_SENTINEL = "PLANTED-0xBEA-SENTINEL"


class _FakeInference:
    """Deterministic fake — never a real LLM SDK call. Records (prompt, phi) per call."""

    def __init__(self, responses: list[str] | None = None) -> None:
        self._responses = list(responses) if responses else ["narrativa factual sintetica"]
        self.calls: list[tuple[str, bool]] = []
        #: AF-12 (CC-12): the `task_kind` of each call, in order.
        self.task_kinds: list[str | None] = []

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        self.task_kinds.append(task_kind)
        return self._responses.pop(0) if self._responses else ""


class _RaisingInference(_FakeInference):
    """LLM failure double — the graph must degrade, never block the instruction."""

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        self.calls.append((prompt, phi))
        raise RuntimeError("provider unavailable")


class _AssertingInference(_FakeInference):
    """Raises on ANY call — for paths that must never reach the LLM at all."""

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        task_kind: str | None = None,
    ) -> str:
        raise AssertionError("this path must never call the LLM")


class _FakePatientSummaryReader:
    """`payload` models an UPSTREAM-CONTROLLED answer (BEA-06): the reader is a generic
    Protocol over v2's FHIR server, NOT the donor's PEP-gated `mcp-fhir.read_patient`, so the
    graph must treat whatever it returns as hostile. `None` keeps the well-behaved default."""

    def __init__(self, *, fail: bool = False, payload: Any = None) -> None:
        self._fail = fail
        self._payload = payload
        self.calls: list[str] = []

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        self.calls.append(patient_id)
        if self._fail:
            raise RuntimeError("HAPI FHIR unreachable at http://fhir.internal:8080")
        if self._payload is not None:
            return cast("dict[str, Any]", self._payload)
        return {"resourceType": "Patient", "id": patient_id}


class _AssertingPatientSummaryReader(_FakePatientSummaryReader):
    """Raises on ANY call — for paths that must never touch FHIR at all."""

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        raise AssertionError("this path must never read FHIR")


def _graph(*, inference: Any | None = None, fhir: Any | None = None) -> BeatrizGraph:
    return BeatrizGraph(inference=inference or _FakeInference(), fhir=fhir)


def _case_state(**overrides: Any) -> BeatrizState:
    state: BeatrizState = {
        "tenant_id": "t1",
        "numero_caso": "000001",
        "origem_encaminhamento": "contas",
        "entidade_tipo": "prestador",
        "entidade_pseudo_id": "ent-p1",
        "prestador_id": "prest-1",
        "beneficiario_pseudo_id": "pseudo-b1",
        "encaminhado_por_id": "analista-h1",
        "competencia": "2026-06",
        "evidencia_refs": [
            {"ref": "evd://tiss/lote-1", "hash": "h-1", "origem": "tiss"},
            {"ref": "evd://cdc/item-2", "hash": "h-2", "origem": "cdc", "tipo": "conta"},
        ],
        "feature_snapshot_ref": "snap://prov-1/2026-06",
        "indicadores_presentes": ["upcoding_ceiling", "peer_deviation"],
        "score_indicadores": 7,
        "intensidade_investigacao": "APROFUNDADA",
        "indicio_fraude_sinalizado": True,
        "patient_summary_ref": "pseudo-b1",
    }
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


def _planted_outputs() -> dict[str, Any]:
    """A unique sentinel (or accusation-shaped value) in EVERY output-only `BeatrizState` field.

    Must mirror `graph._output_field_resets()`'s key set exactly —
    `test_receive_resets_every_output_only_field` asserts the two never drift apart. The
    `dossier` plant is deliberately ACCUSATION-SHAPED (a forged `decisao_fraude`/`bundle_root`/
    score) — for Beatriz this is the single most dangerous plant: if it survived, the engine
    would seal a forged accusation into the custody chain as if she had produced it.
    """
    return {
        "business_key": _SENTINEL,
        "gathered": True,
        "summary_facts": {"planted": _SENTINEL},
        "evidencia_normalizada": [{"ref": _SENTINEL, "hash": _SENTINEL}],
        "gather_notes": [_SENTINEL],
        "dossier": {
            "decisao_fraude": "ACUSAR_FRAUDE",  # the planted accusation — must NEVER survive
            "bundle_root": _SENTINEL,
            "destino_referral": {"juridico": True},
            "score_indicadores": 999,  # the planted score — must NEVER survive
            "narrativa": _SENTINEL,
        },
        "desfecho": "dossie_instruido",  # planted "already assembled" signal
        "error": _SENTINEL,
    }


# ---------------------------------------------------------------------------
# spec/agent.yaml sanity (fraude domain — NOT the old chronic-care stub)
# ---------------------------------------------------------------------------


def test_beatriz_agent_yaml_exists() -> None:
    assert (_AGENTS_ROOT / "beatriz" / "agent.yaml").exists()


def test_beatriz_agent_yaml_matches_fraude_domain() -> None:
    with open(_AGENTS_ROOT / "beatriz" / "agent.yaml") as f:
        data = yaml.safe_load(f)
    assert data["id"] == "beatriz"
    assert data["name"] == "Beatriz Salgado"
    assert data["escalation"]["process"] == "SP-OP-FRAUDE-001"
    # Allowlist TIGHT (action-real): FHIR read + memory ONLY — no cibseven/dmn tool, matching
    # this graph's deliberate refusal of those transports (`build`'s docstring).
    # O id FHIR e `read_patient_summary` desde o WP FHIR-TOOL-SURFACE-PARITY (BEA-13): e o metodo
    # que `gather` REALMENTE chama (`PatientSummaryReader.read_patient_summary`) e, portanto, o
    # `tool_id` que o L1 do PEP compara com esta lista. Com o id anterior
    # (`mcp-fhir.read_patient`) a leitura da propria Beatriz era negada com `TOOL_NAO_DECLARADA`.
    assert data["tools"] == ["mcp-fhir.read_patient_summary", "mcp-memory.read_write"]


def test_beatriz_agent_yaml_pins_zero_auto_accusation_kpis() -> None:
    with open(_AGENTS_ROOT / "beatriz" / "agent.yaml") as f:
        data = yaml.safe_load(f)
    kpis = {k["name"]: k["target"] for k in data["kpis"]}
    assert kpis["zero_auto_accusation"] == "==0"
    assert kpis["false_accusation_rate"] == "==0"
    assert kpis["evidence_pseudonymized_rate"] == "==1.0"


def test_prompt_versions_match_agent_yaml() -> None:
    with open(_AGENTS_ROOT / "beatriz" / "agent.yaml") as f:
        data = yaml.safe_load(f)
    assert dict(data["prompt_versions"]) == PROMPT_VERSIONS


# ---------------------------------------------------------------------------
# build(config) — fail-closed contract; fhir OPTIONAL; engine transports IGNORED
# ---------------------------------------------------------------------------


def test_build_requires_inference() -> None:
    with pytest.raises(ValueError, match="missing required dependencies"):
        build({})


def test_build_compiles_with_inference_only() -> None:
    graph = build({"inference": _FakeInference()})
    compiled = graph.compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    assert node_names == {"receive", "gather", "instruct_investigation", "finalize"}


def test_build_ignores_engine_transports() -> None:
    """The harness's shared tool_deps carry dmn/cibseven for the rafael/marina-shaped graphs —
    Beatriz's build must accept and IGNORE them (agent.yaml declares neither transport), and
    her graph class must not even have parameters for them (action surface = allowlist)."""
    graph = build({"inference": _FakeInference(), "dmn": object(), "cibseven": object()})
    assert graph.compile() is not None
    params = set(inspect.signature(BeatrizGraph.__init__).parameters)
    assert "dmn" not in params
    assert "cibseven" not in params


def test_graph_is_linear_single_entry_no_conditional_edges() -> None:
    """L0 structural guarantee: single entry (START->receive), strictly linear, and NO
    conditional edge — there is no branch that could even express an accusation path."""
    compiled = build({"inference": _FakeInference()}).compile()
    edges = compiled.get_graph().edges
    assert {(e.source, e.target) for e in edges} == {
        ("__start__", "receive"),
        ("receive", "gather"),
        ("gather", "instruct_investigation"),
        ("instruct_investigation", "finalize"),
        ("finalize", "__end__"),
    }
    assert all(not e.conditional for e in edges)


# ---------------------------------------------------------------------------
# L0 type-domain / structural guards — no accusation path EXISTS
# ---------------------------------------------------------------------------


def test_desfecho_domain_has_no_adverse_variant() -> None:
    assert set(get_args(Desfecho)) == {"dossie_instruido", "instrucao_incompleta"}
    adverse_markers = ("acus", "fraude_confirmada", "bloquear", "descredenc", "rescind", "referir")
    for value in get_args(Desfecho):
        assert not any(marker in value.lower() for marker in adverse_markers)


def test_state_has_no_decision_channels() -> None:
    """The accusation/seal/referral variables of SP-OP-FRAUDE-001 are not even CHANNELS of this
    graph's state — they cannot be transported, planted, or set through it."""
    for forbidden in (
        "decisao_fraude",
        "bundle_root",
        "destino_referral",
        "destino_referral_juridico",
        "destino_referral_ans",
        "destino_referral_cred",
        "destino_referral_contratual",
        "fundamentacao_investigacao",
        "indicadores_fundamentantes",
        "referencia_normativa",
        "investigator_id",
        "tier",
    ):
        assert forbidden not in BeatrizState.__annotations__


def test_node_names_carry_no_accusation_semantics() -> None:
    compiled = build({"inference": _FakeInference()}).compile()
    node_names = {n for n in compiled.get_graph().nodes if n not in ("__start__", "__end__")}
    for name in node_names:
        assert "acus" not in name.lower()
        assert "fraud" not in name.lower()
        assert "register" not in name.lower()
        assert "seal" not in name.lower()


def test_output_field_partition_is_complete() -> None:
    """Partition completeness (the sanitization drift-guard): every `BeatrizState` field is
    EITHER a caller input OR an output-only reset target — never both, never neither."""
    outputs = set(_output_field_resets())
    annotations = set(BeatrizState.__annotations__)
    assert _CALLER_INPUT_FIELDS & outputs == set()
    assert _CALLER_INPUT_FIELDS | outputs == annotations


# ---------------------------------------------------------------------------
# receive — single entry, sanitization first, fail-safe guard
# ---------------------------------------------------------------------------


def test_business_key() -> None:
    assert _business_key(_case_state()) == "FRAUDE-t1-000001"


async def test_receive_happy_assigns_business_key_and_resets() -> None:
    result = await _graph().receive(_case_state())
    assert result["business_key"] == "FRAUDE-t1-000001"
    assert result["error"] == ""
    assert result["desfecho"] == "instrucao_incompleta"  # fail-safe until instruct upgrades it
    assert result["dossier"] == {}


async def test_receive_missing_tenant_fails_safe() -> None:
    result = await _graph().receive(_case_state(tenant_id=""))
    assert result["error"] == ERROR_CONTEXTO_RUNTIME_AUSENTE
    assert result["desfecho"] == "instrucao_incompleta"
    assert result["business_key"] == ""


async def test_receive_missing_numero_caso_fails_safe() -> None:
    result = await _graph().receive(_case_state(numero_caso=""))
    assert result["error"] == ERROR_CONTEXTO_RUNTIME_AUSENTE
    assert result["business_key"] == ""


async def test_receive_error_is_a_bounded_class_token() -> None:
    """Failure reasons are CLASS TOKENS only — never an echo of state values (custody hygiene)."""
    result = await _graph().receive(_case_state(tenant_id="", numero_caso=""))
    assert result["error"] == ERROR_CONTEXTO_RUNTIME_AUSENTE  # exact token, nothing interpolated


async def test_receive_resets_every_output_only_field() -> None:
    """Structural core of the sanitization: `receive` overwrites every output-only field with
    its benign reset in its OWN state update — nothing caller-planted survives the entry node."""
    planted = _planted_outputs()
    resets = _output_field_resets()
    # Drift guard: the probe fixture and the production reset list cover the SAME key set.
    assert set(planted) == set(resets), "planted fixture must mirror _output_field_resets keys"

    result = await _graph().receive(_case_state(**planted))

    for key in planted:
        assert key in result, f"receive did not overwrite planted output field {key!r}"
    for key, reset_value in resets.items():
        if key == "business_key":
            continue  # re-derived from input identifiers below, not a static reset
        assert result[key] == reset_value, f"{key!r} not reset: {result[key]!r}"
    assert result["business_key"] == "FRAUDE-t1-000001"
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert _SENTINEL not in serialized
    assert "ACUSAR_FRAUDE" not in serialized


# ---------------------------------------------------------------------------
# gather — best-effort FHIR + pointer normalization (no-PHI-in-custody)
# ---------------------------------------------------------------------------


async def test_gather_without_fhir_reader_notes_the_gap() -> None:
    result = await _graph().gather(_case_state())
    assert result["gathered"] is True
    assert NOTE_FHIR_READER_NAO_CONFIGURADO in result["gather_notes"]
    assert result["summary_facts"] == {}


async def test_gather_with_fhir_reader_populates_summary_facts() -> None:
    fhir = _FakePatientSummaryReader()
    result = await _graph(fhir=fhir).gather(_case_state())
    assert fhir.calls == ["pseudo-b1"]
    assert result["summary_facts"] == {"resourceType": "Patient", "id": "pseudo-b1"}


async def test_gather_fhir_failure_is_best_effort_class_token_note() -> None:
    """FHIR failure degrades to the BOUNDED token — the exception text (which may carry
    endpoints/values) never reaches the notes that land in the custody-bound dossier."""
    result = await _graph(fhir=_FakePatientSummaryReader(fail=True)).gather(_case_state())
    assert result["gathered"] is True
    assert NOTE_RESUMO_FHIR_INDISPONIVEL in result["gather_notes"]
    # Fail-closed (BEA-06): a reader that raised NEVER leaves a partial/passthrough summary.
    assert result["summary_facts"] == {}
    serialized = json.dumps(result["gather_notes"], ensure_ascii=False)
    assert "unreachable" not in serialized
    assert "fhir.internal" not in serialized


# ---------------------------------------------------------------------------
# gather — the FHIR summary is PROJECTED, never trusted verbatim (BEA-06)
#
# The `PatientSummaryReader` seam is a generic Protocol over v2's FHIR server, NOT the donor's
# PEP-gated `mcp-fhir.read_patient` ToolInvoker (graph.py's own labeled boundary), so its
# payload is controlled by a server UPSTREAM of this graph. Before the fix, `gather` assigned
# `read_patient_summary`'s return value to `summary_facts` VERBATIM, and `_facts()` re-emitted
# it as `resumo_fhir` into BOTH the `phi=True` dossier prompt and the custody-bound dossier —
# with no allowlist, no refusal, and no backstop downstream (`operadora.fraude.
# seal_custody_bundle` inspects ONLY `variables["evidencia_refs"]` STRING elements, so it never
# sees the summary at all). The projection below is therefore the ONLY barrier.
# ---------------------------------------------------------------------------

# The bounded class token recorded when a summary is refused whole. Asserted as a LITERAL here
# (not via the module constant) so the token's wire text itself is pinned — the dossier
# `lacunas` these land in are read by the human investigator and sealed into the custody chain.
_NOTE_RESUMO_RECUSADO = "resumo_fhir_recusado"

#: The closed projection allowlist, restated as a literal for the same reason.
_SUMMARY_KEYS = {"resourceType", "id"}


class _ShapeShiftingReader:
    """Returns a non-dict (or `None`) where the Protocol promises `dict[str, Any]` — models an
    upstream server answering with an unexpected shape. Deliberately violates the annotation:
    the point is that `gather` must not TRUST the annotation."""

    def __init__(self, payload: Any) -> None:
        self._payload = payload

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        return cast("dict[str, Any]", self._payload)


async def test_gather_refuses_whole_summary_carrying_a_raw_phi_key() -> None:
    """A summary carrying ANY known raw-PHI key is refused WHOLE — never projected/laundered,
    so the incident surfaces as a lacuna instead of disappearing into a stripped dict."""
    fhir = _FakePatientSummaryReader(
        payload={"resourceType": "Patient", "id": "pseudo-b1", "cpf": _RAW_CPF, "nome": _RAW_NAME}
    )
    result = await _graph(fhir=fhir).gather(_case_state())
    assert result["summary_facts"] == {}
    assert _NOTE_RESUMO_RECUSADO in result["gather_notes"]
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert _RAW_CPF not in serialized
    assert _RAW_NAME not in serialized


async def test_gather_refuses_whole_summary_with_a_nested_phi_key() -> None:
    """The refusal scan is RECURSIVE (dicts and lists): a PHI key one level down is exactly how
    a real FHIR payload carries it (`subject`/`contained`/`entry`), and a top-level-only check
    would wave it straight through."""
    fhir = _FakePatientSummaryReader(
        payload={
            "resourceType": "Bundle",
            "id": "pseudo-b1",
            "entry": [{"resource": {"subject": {"cpf": _RAW_CPF, "nome": _RAW_NAME}}}],
        }
    )
    result = await _graph(fhir=fhir).gather(_case_state())
    assert result["summary_facts"] == {}
    assert _NOTE_RESUMO_RECUSADO in result["gather_notes"]
    assert _RAW_CPF not in json.dumps(result, ensure_ascii=False, default=str)


async def test_gather_case_insensitively_refuses_phi_keys() -> None:
    """Key matching folds case — `CPF`/`Nome` are the same contamination as `cpf`/`nome`."""
    fhir = _FakePatientSummaryReader(payload={"resourceType": "Patient", "CPF": _RAW_CPF})
    result = await _graph(fhir=fhir).gather(_case_state())
    assert result["summary_facts"] == {}
    assert _NOTE_RESUMO_RECUSADO in result["gather_notes"]


async def test_gather_projects_summary_to_the_closed_allowlist() -> None:
    """No PHI KEY present, but free text (which could smuggle raw PHI in its VALUE) under keys
    the dossier never consumes: those keys are STRIPPED by the closed projection. This is the
    barrier that matters most — a hostile server simply avoids the known PHI key names."""
    fhir = _FakePatientSummaryReader(
        payload={
            "resourceType": "Patient",
            "id": "pseudo-b1",
            "resumo": f"Beneficiario {_RAW_NAME}, CPF {_RAW_CPF}",
            "text": {"div": _RAW_CPF},
            "identifier": [{"value": _RAW_CPF}],
        }
    )
    result = await _graph(fhir=fhir).gather(_case_state())
    assert set(result["summary_facts"]) <= _SUMMARY_KEYS
    assert result["gather_notes"].count(_NOTE_RESUMO_RECUSADO) == 0
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert _RAW_CPF not in serialized
    assert _RAW_NAME not in serialized


async def test_gather_preserves_the_admitted_summary_keys() -> None:
    """The admitted keys survive: `resourceType` (closed value domain) and `id` (admitted ONLY
    when it echoes the ALREADY-PSEUDONYMIZED reference the graph itself asked for)."""
    result = await _graph(fhir=_FakePatientSummaryReader()).gather(_case_state())
    assert result["summary_facts"] == {"resourceType": "Patient", "id": "pseudo-b1"}


async def test_gather_drops_summary_id_that_is_not_the_requested_subject() -> None:
    """`id` is admitted as an ECHO of the requested pseudonymized ref, never as new content: an
    id the graph did not ask for is upstream-controlled (a CPF-shaped id is exactly the evasion)
    and is dropped. Not a whole-summary refusal — no PHI key is present."""
    fhir = _FakePatientSummaryReader(payload={"resourceType": "Patient", "id": _RAW_CPF})
    result = await _graph(fhir=fhir).gather(_case_state())
    assert result["summary_facts"] == {"resourceType": "Patient"}
    assert _RAW_CPF not in json.dumps(result, ensure_ascii=False, default=str)


async def test_gather_drops_an_out_of_domain_resource_type() -> None:
    """`resourceType`'s admitted VALUES are a closed domain too — a free-text value under an
    allowlisted key is content, not a resource type, and is dropped."""
    fhir = _FakePatientSummaryReader(payload={"resourceType": _RAW_NAME, "id": "pseudo-b1"})
    result = await _graph(fhir=fhir).gather(_case_state())
    assert result["summary_facts"] == {"id": "pseudo-b1"}
    assert _RAW_NAME not in json.dumps(result, ensure_ascii=False, default=str)


@pytest.mark.parametrize(
    "payload",
    [
        f"resumo textual do beneficiario {_RAW_CPF}",
        [{"cpf": _RAW_CPF}],
        42,
        None,
    ],
    ids=["str", "list", "int", "none"],
)
async def test_gather_fail_closed_on_unexpected_summary_shape(payload: Any) -> None:
    """Fail-closed on an unexpected shape: empty summary + the bounded refusal token, NEVER a
    passthrough of whatever the reader returned."""
    graph = BeatrizGraph(inference=_FakeInference(), fhir=_ShapeShiftingReader(payload))
    result = await graph.gather(_case_state())
    assert result["summary_facts"] == {}
    assert _NOTE_RESUMO_RECUSADO in result["gather_notes"]
    assert _RAW_CPF not in json.dumps(result, ensure_ascii=False, default=str)


async def test_gather_fail_closed_on_self_referential_summary() -> None:
    """A self-referential payload must not blow the recursion limit on the gather hot path —
    the depth-bounded scan refuses it instead of raising."""
    payload: dict[str, Any] = {"resourceType": "Patient", "id": "pseudo-b1"}
    payload["self"] = payload
    result = await _graph(fhir=_FakePatientSummaryReader(payload=payload)).gather(_case_state())
    assert result["summary_facts"] == {}
    assert _NOTE_RESUMO_RECUSADO in result["gather_notes"]


async def test_dossier_and_prompt_never_carry_the_upstream_summary_phi() -> None:
    """End-to-end over the compiled graph: the canary the upstream reader answered with reaches
    NEITHER the custody-bound dossier NOR the `phi=True` prompt (`_facts()["resumo_fhir"]` feeds
    both). `seal_custody_bundle` is no backstop here — it inspects only `evidencia_refs`."""
    inference = _FakeInference()
    fhir = _FakePatientSummaryReader(
        payload={
            "resourceType": "Patient",
            "id": "pseudo-b1",
            "subject": {"nome": _RAW_NAME, "cpf": _RAW_CPF},
            "resumo": f"Beneficiario {_RAW_NAME}, CPF {_RAW_CPF}",
        }
    )
    compiled = _graph(inference=inference, fhir=fhir).compile_graph().compile()
    result = await compiled.ainvoke(_case_state())
    assert fhir.calls == ["pseudo-b1"]
    assert result["desfecho"] == "dossie_instruido"
    dossier_blob = json.dumps(result["dossier"], ensure_ascii=False, default=str)
    assert _RAW_CPF not in dossier_blob
    assert _RAW_NAME not in dossier_blob
    assert result["dossier"]["fatos"]["resumo_fhir"] == {}
    assert _NOTE_RESUMO_RECUSADO in result["dossier"]["lacunas"]
    prompt, phi = inference.calls[0]
    assert phi is True
    assert _RAW_CPF not in prompt
    assert _RAW_NAME not in prompt


async def test_gather_normalizes_valid_pointers() -> None:
    result = await _graph().gather(_case_state())
    assert result["evidencia_normalizada"] == [
        {"ref": "evd://tiss/lote-1", "hash": "h-1", "origem": "tiss"},
        {"ref": "evd://cdc/item-2", "hash": "h-2", "origem": "cdc", "tipo": "conta"},
    ]


async def test_gather_refuses_items_with_phi_keys() -> None:
    state = _case_state(
        evidencia_refs=[
            {"ref": "evd://tiss/lote-1", "hash": "h-1"},
            {"ref": "evd://x", "cpf": _RAW_CPF, "nome": _RAW_NAME},
        ]
    )
    result = await _graph().gather(state)
    assert result["evidencia_normalizada"] == [{"ref": "evd://tiss/lote-1", "hash": "h-1"}]
    assert "evidencia_recusada:1" in result["gather_notes"]
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert _RAW_CPF not in serialized
    assert _RAW_NAME not in serialized


async def test_gather_refuses_items_without_ref_and_non_dicts() -> None:
    state = _case_state(evidencia_refs=[{"hash": "h-1"}, "evd://bare-string", {"ref": ""}])
    result = await _graph().gather(state)
    assert result["evidencia_normalizada"] == []
    assert {"evidencia_recusada:0", "evidencia_recusada:1", "evidencia_recusada:2"} <= set(
        result["gather_notes"]
    )


async def test_gather_projection_strips_unknown_keys() -> None:
    """Closed-allowlist projection: an unknown key smuggling raw PHI in its VALUE is stripped —
    the normalized pointer carries only {ref, hash, tipo, origem}."""
    state = _case_state(
        evidencia_refs=[{"ref": "evd://y", "hash": "h-9", "conteudo": f"CPF {_RAW_CPF} {_RAW_NAME}"}]
    )
    result = await _graph().gather(state)
    assert result["evidencia_normalizada"] == [{"ref": "evd://y", "hash": "h-9"}]
    assert _RAW_CPF not in json.dumps(result["evidencia_normalizada"], ensure_ascii=False)


async def test_gather_empty_intake_notes_growth_by_annexation() -> None:
    result = await _graph().gather(_case_state(evidencia_refs=[]))
    assert result["evidencia_normalizada"] == []
    assert NOTE_SEM_EVIDENCIA_NO_INTAKE in result["gather_notes"]


async def test_gather_bails_fail_safe_when_already_errored() -> None:
    """The error bail (reachable only via receive's own guard post-sanitization) re-asserts the
    fail-safe desfecho — never `{}` — and touches neither FHIR nor the evidence."""
    graph = _graph(fhir=_AssertingPatientSummaryReader())
    result = await graph.gather(_case_state(error=ERROR_CONTEXTO_RUNTIME_AUSENTE))
    assert result == {"desfecho": "instrucao_incompleta"}


# ---------------------------------------------------------------------------
# instruct_investigation — the single substantive node: instructs, NEVER decides
# ---------------------------------------------------------------------------


def _gathered_state(**overrides: Any) -> BeatrizState:
    """State as it looks after receive+gather on the happy path."""
    state = _case_state(
        business_key="FRAUDE-t1-000001",
        gathered=True,
        summary_facts={"resourceType": "Patient", "id": "pseudo-b1"},
        evidencia_normalizada=[
            {"ref": "evd://tiss/lote-1", "hash": "h-1", "origem": "tiss"},
            {"ref": "evd://cdc/item-2", "hash": "h-2", "origem": "cdc", "tipo": "conta"},
        ],
        gather_notes=[],
        dossier={},
        desfecho="instrucao_incompleta",
        error="",
    )
    state.update(overrides)  # type: ignore[typeddict-item]
    return state


async def test_instruct_builds_dossier_and_neutral_desfecho() -> None:
    inference = _FakeInference()
    result = await _graph(inference=inference).instruct_investigation(_gathered_state())
    assert result["desfecho"] == "dossie_instruido"
    dossier = result["dossier"]
    assert dossier["business_key"] == "FRAUDE-t1-000001"
    assert dossier["numero_caso"] == "000001"
    assert dossier["narrativa"] == "narrativa factual sintetica"
    assert dossier["evidencia_refs"] == [
        {"ref": "evd://tiss/lote-1", "hash": "h-1", "origem": "tiss"},
        {"ref": "evd://cdc/item-2", "hash": "h-2", "origem": "cdc", "tipo": "conta"},
    ]
    assert dossier["indicadores_observados"] == ["upcoding_ceiling", "peer_deviation"]
    assert dossier["fatos"]["n_evidencia"] == 2


async def test_instruct_dossier_decision_fields_always_none() -> None:
    """L0 hard (zero_auto_accusation): the dossier's decision/seal/referral fields are ALWAYS
    None — a non-None value here is an L0 bug."""
    result = await _graph().instruct_investigation(_gathered_state())
    dossier = result["dossier"]
    assert dossier["decisao_fraude"] is None
    assert dossier["bundle_root"] is None
    assert dossier["destino_referral"] is None


async def test_instruct_llm_call_is_phi_tagged() -> None:
    inference = _FakeInference()
    await _graph(inference=inference).instruct_investigation(_gathered_state())
    assert len(inference.calls) == 1
    prompt, phi = inference.calls[0]
    assert phi is True  # Beatriz is PHI-zone (agent.yaml security_zone: phi; ADR-0006/0017)
    # CC-11: os fatos deixaram de viajar como `fatos={repr}` e vao renderizados — o booleano
    # nomeado com SIM/NAO/SEM DADO, o resto do caso no contexto. A asserção antiga (`"fatos=" in
    # prompt`) so' provava que ALGO chamado fatos foi interpolado; esta prova que o fato apurado
    # chegou distinguivel, que e' o que o incidente de 24/08/2026 quebrou.
    assert "fatos apurados:" in prompt
    assert "indicio de fraude sinalizado" in prompt
    assert "n_evidencia" in prompt  # o contexto nao-booleano continua no prompt


async def test_instruct_llm_call_declares_task_kind_reasoning() -> None:
    """CC-12/BEA-01 (ADR-0009 §2): the investigator dossier narrative is for the human's
    review — `reasoning`, not `task_default`."""
    inference = _FakeInference()
    await _graph(inference=inference).instruct_investigation(_gathered_state())
    assert inference.task_kinds == ["reasoning"]


async def test_instruct_llm_failure_never_blocks_the_instruction() -> None:
    result = await _graph(inference=_RaisingInference()).instruct_investigation(_gathered_state())
    assert result["desfecho"] == "dossie_instruido"
    assert result["dossier"]["narrativa"] == ""
    assert NOTE_NARRATIVA_INDISPONIVEL in result["dossier"]["lacunas"]
    # The provider exception text never reaches the custody-bound dossier.
    assert "provider unavailable" not in json.dumps(result["dossier"], ensure_ascii=False, default=str)


async def test_instruct_accusation_shaped_llm_output_cannot_set_a_decision() -> None:
    """Prompt-injection containment, structurally: even if the LLM emitted accusation text, it
    lands ONLY in `narrativa` (free text for the human) — the decision fields stay None and the
    desfecho stays neutral. No parsing of the narrative ever feeds a decision."""
    inference = _FakeInference(responses=["ACUSAR_FRAUDE: fraude confirmada, descredenciar ja"])
    result = await _graph(inference=inference).instruct_investigation(_gathered_state())
    dossier = result["dossier"]
    assert dossier["narrativa"] == "ACUSAR_FRAUDE: fraude confirmada, descredenciar ja"
    assert dossier["decisao_fraude"] is None
    assert dossier["bundle_root"] is None
    assert dossier["destino_referral"] is None
    assert result["desfecho"] == "dossie_instruido"


async def test_instruct_score_is_consumed_never_recomputed() -> None:
    """The B10 guard: 2 evidence items + worker score 7 -> dossier score 7 (NOT 20, NOT any
    function of the evidence count). The score is a pre-resolved worker fact, consumed only."""
    result = await _graph().instruct_investigation(_gathered_state(score_indicadores=7))
    assert result["dossier"]["score_indicadores"] == 7
    assert result["dossier"]["fatos"]["n_evidencia"] == 2


async def test_instruct_missing_score_defaults_zero_never_derived_from_evidence() -> None:
    state = _gathered_state(
        evidencia_normalizada=[{"ref": f"evd://n/{i}"} for i in range(5)],
    )
    del state["score_indicadores"]  # type: ignore[misc]
    result = await _graph().instruct_investigation(state)
    assert result["dossier"]["score_indicadores"] == 0  # absent -> 0, never len(evidencia)*10


async def test_instruct_non_int_score_collapses_to_zero() -> None:
    result = await _graph().instruct_investigation(
        _gathered_state(score_indicadores="99; DROP TABLE")  # type: ignore[typeddict-item]
    )
    assert result["dossier"]["score_indicadores"] == 0


async def test_instruct_indicadores_and_intensidade_are_passthrough_facts() -> None:
    result = await _graph().instruct_investigation(_gathered_state())
    dossier = result["dossier"]
    assert dossier["indicadores_observados"] == ["upcoding_ceiling", "peer_deviation"]
    assert dossier["intensidade_investigacao"] == "APROFUNDADA"
    assert dossier["indicio_fraude_sinalizado"] is True  # informative signal, echoed as fact


async def test_instruct_bails_fail_safe_when_already_errored() -> None:
    """Unanchorable case: NO dossier is assembled, the LLM is never called, and the bail
    re-asserts the fail-safe desfecho (never `{}`)."""
    graph = _graph(inference=_AssertingInference())
    result = await graph.instruct_investigation(_case_state(error=ERROR_CONTEXTO_RUNTIME_AUSENTE, dossier={}))
    assert result == {"desfecho": "instrucao_incompleta"}


async def test_finalize_is_a_terminal_noop() -> None:
    assert await _graph().finalize(_gathered_state(desfecho="dossie_instruido")) == {}


# ---------------------------------------------------------------------------
# full-turn flows (compiled graph, fakes only — receive -> gather -> instruct -> finalize)
# ---------------------------------------------------------------------------


async def test_full_turn_happy_flow() -> None:
    inference = _FakeInference()
    compiled = _graph(inference=inference, fhir=_FakePatientSummaryReader()).compile_graph().compile()

    result = await compiled.ainvoke(_case_state())

    assert result["desfecho"] == "dossie_instruido"
    assert result["business_key"] == "FRAUDE-t1-000001"
    assert result["error"] == ""
    assert len(result["evidencia_normalizada"]) == 2
    dossier = result["dossier"]
    assert dossier["evidencia_refs"] == result["evidencia_normalizada"]
    assert dossier["score_indicadores"] == 7
    assert dossier["decisao_fraude"] is None
    assert dossier["bundle_root"] is None
    assert len(inference.calls) == 1
    assert inference.calls[0][1] is True  # phi=True on the one LLM call


async def test_full_turn_llm_down_still_instructs_never_silently_decides() -> None:
    compiled = _graph(inference=_RaisingInference()).compile_graph().compile()
    result = await compiled.ainvoke(_case_state())
    assert result["desfecho"] == "dossie_instruido"
    assert result["dossier"]["narrativa"] == ""
    assert NOTE_NARRATIVA_INDISPONIVEL in result["dossier"]["lacunas"]
    assert result["dossier"]["decisao_fraude"] is None


async def test_full_turn_missing_context_yields_incomplete_and_no_dossier() -> None:
    compiled = (
        _graph(inference=_AssertingInference(), fhir=_AssertingPatientSummaryReader())
        .compile_graph()
        .compile()
    )
    result = await compiled.ainvoke(_case_state(tenant_id=""))
    assert result["desfecho"] == "instrucao_incompleta"
    assert result["error"] == ERROR_CONTEXTO_RUNTIME_AUSENTE
    assert result["dossier"] == {}
    assert result["business_key"] == ""
    assert result["gathered"] is False


# ---------------------------------------------------------------------------
# caller-planted-output probes (the mandatory hardening — baked in from the start)
# ---------------------------------------------------------------------------


async def test_full_turn_planted_outputs_are_fully_sanitized() -> None:
    """A hostile caller plants a sentinel/accusation in EVERY output-only field: the final
    state must carry NONE of it — the dossier is rebuilt from genuine inputs, the business key
    is re-derived, and no sentinel reaches any custody-bound surface."""
    compiled = _graph(fhir=_FakePatientSummaryReader()).compile_graph().compile()

    result = await compiled.ainvoke(_case_state(**_planted_outputs()))

    assert result["desfecho"] == "dossie_instruido"  # recomputed, not the planted signal
    assert result["business_key"] == "FRAUDE-t1-000001"
    dossier = result["dossier"]
    assert dossier["decisao_fraude"] is None
    assert dossier["bundle_root"] is None
    assert dossier["destino_referral"] is None
    assert dossier["narrativa"] == "narrativa factual sintetica"
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert _SENTINEL not in serialized
    assert "ACUSAR_FRAUDE" not in serialized


async def test_full_turn_planted_accusation_never_reaches_the_dossier() -> None:
    """The single most dangerous plant for Beatriz: an accusation-shaped dossier. It must be
    cleared at receive and rebuilt — the sealed corpus can never carry a planted verdict."""
    compiled = _graph().compile_graph().compile()
    result = await compiled.ainvoke(
        _case_state(
            dossier={"decisao_fraude": "ACUSAR_FRAUDE", "bundle_root": "forged-root"},
            desfecho="dossie_instruido",
        )
    )
    dossier = result["dossier"]
    assert dossier["decisao_fraude"] is None
    assert dossier["bundle_root"] is None
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert "ACUSAR_FRAUDE" not in serialized
    assert "forged-root" not in serialized


async def test_full_turn_planted_score_and_evidence_echoes_never_reach_the_dossier() -> None:
    """A planted score echo (dossier.score=999) and a forged normalized-evidence list must be
    defeated: the dossier's score comes from the pre-resolved INPUT fact and its evidence from
    gather's own normalization of the genuine input pointers."""
    compiled = _graph().compile_graph().compile()
    result = await compiled.ainvoke(
        _case_state(
            dossier={"score_indicadores": 999},
            evidencia_normalizada=[{"ref": "evd://forged", "hash": "forged"}],
        )
    )
    assert result["dossier"]["score_indicadores"] == 7  # the genuine pre-resolved input fact
    assert result["evidencia_normalizada"] == [
        {"ref": "evd://tiss/lote-1", "hash": "h-1", "origem": "tiss"},
        {"ref": "evd://cdc/item-2", "hash": "h-2", "origem": "cdc", "tipo": "conta"},
    ]
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert "forged" not in serialized
    assert "999" not in serialized


async def test_full_turn_missing_context_with_planted_outputs_stays_incomplete_and_clean() -> None:
    """Missing runtime context + a fully planted output set: the turn must end incomplete,
    assemble NO dossier, resurrect NO planted business key, call neither FHIR nor the LLM, and
    carry no sentinel anywhere in the final state."""
    compiled = (
        _graph(inference=_AssertingInference(), fhir=_AssertingPatientSummaryReader())
        .compile_graph()
        .compile()
    )
    result = await compiled.ainvoke(_case_state(tenant_id="", **_planted_outputs()))

    assert result["desfecho"] == "instrucao_incompleta"
    assert result["error"] == ERROR_CONTEXTO_RUNTIME_AUSENTE
    assert result["dossier"] == {}
    assert result["business_key"] == ""
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    assert _SENTINEL not in serialized
    assert "ACUSAR_FRAUDE" not in serialized


# ---------------------------------------------------------------------------
# PHI probes — pseudonymized pointers + hashes ONLY ever reach the custody-bound surfaces
# ---------------------------------------------------------------------------


async def test_full_turn_raw_phi_in_evidence_never_reaches_the_dossier() -> None:
    """Raw PHI planted in the inbound evidence (as PHI-keyed items AND as PHI smuggled in an
    unknown key's value): the custody-bound surfaces (dossier, evidencia_normalizada,
    gather_notes, error) must never carry it — only pseudonymized pointer projections."""
    compiled = _graph().compile_graph().compile()
    result = await compiled.ainvoke(
        _case_state(
            evidencia_refs=[
                {"ref": "evd://ok/1", "hash": "h-1"},
                {"ref": "evd://bad/2", "cpf": _RAW_CPF, "nome": _RAW_NAME},
                {"ref": "evd://bad/3", "conteudo": f"beneficiario {_RAW_NAME} CPF {_RAW_CPF}"},
            ]
        )
    )

    assert result["evidencia_normalizada"] == [
        {"ref": "evd://ok/1", "hash": "h-1"},
        {"ref": "evd://bad/3"},  # PHI-valued unknown key stripped by the closed projection
    ]
    assert "evidencia_recusada:1" in result["dossier"]["lacunas"]
    custody_bound = {
        "dossier": result["dossier"],
        "evidencia_normalizada": result["evidencia_normalizada"],
        "gather_notes": result["gather_notes"],
        "error": result["error"],
        "business_key": result["business_key"],
    }
    serialized = json.dumps(custody_bound, ensure_ascii=False, default=str)
    assert _RAW_CPF not in serialized
    assert _RAW_NAME not in serialized


async def test_dossier_evidence_carries_only_pointer_projection_keys() -> None:
    """Bite-proof no-PHI-in-custody: EVERY item in the dossier's evidence is a projection whose
    keys are a subset of the closed allowlist {ref, hash, tipo, origem}."""
    compiled = _graph().compile_graph().compile()
    result = await compiled.ainvoke(
        _case_state(
            evidencia_refs=[
                {"ref": "evd://a", "hash": "h", "extra": "x", "obs": "y"},
                {"ref": "evd://b", "tipo": "conta", "origem": "cdc", "livre": "z"},
            ]
        )
    )
    for item in result["dossier"]["evidencia_refs"]:
        assert set(item) <= {"ref", "hash", "tipo", "origem"}


def test_normalize_evidence_is_pure_and_index_stable() -> None:
    normalized, refused = _normalize_evidence([{"ref": "evd://a"}, 42, {"ref": "evd://b", "cpf": _RAW_CPF}])
    assert normalized == [{"ref": "evd://a"}]
    assert refused == ["evidencia_recusada:1", "evidencia_recusada:2"]
