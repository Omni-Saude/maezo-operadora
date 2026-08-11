"""The Phase-0 EXIT CRITERIA, per seam (design §9.1) — parity, telemetry, denial shape, I-6.

Four obligations, and all four have to hold at once or the wave is not shippable:

  (A) PARITY, PER SEAM (§9.1 bullet 1, invariant I-7). Under the SHIPPED manifest — no fixture, no
      env override, whatever `spec/policies/autonomy/action-approvals.yaml` says today — a gated
      seam and the raw seam must produce IDENTICAL outcomes: same return value, same exception
      type, same call count, same argument capture, same ordering. This is the exact proof shape
      the worker leg already carries (`tools/workers/harness.py:1583-1585`, implemented in
      `tests/unit/gateway/test_action_execution_gateway.py::
      test_shadow_wiring_is_provably_non_behavioural`), lifted to the agent seams.

      Parity alone would also pass if the wrapper had silently become a no-op, so (B) proves the
      gate is actually running — the same pairing the worker leg uses.

  (B) TELEMETRY, PER OPERATION (§9.1 bullet 2). Every catalogued operation emits EXACTLY ONE
      bounded shadow line per call, and the field set is PINNED (the #222 pattern): a field added
      by accident is a potential PHI channel, a field removed breaks the §9.2 evidence packet
      which counts lines by `decision` × `reason` × `tenant`.

  (C) THE DECLARED DENIAL SHAPE, LIVE (§6.1, adversary A-12). Under a test manifest that actually
      enforces, each seam must refuse in the shape its class DECLARES — and, crucially, in an
      exception type the node's EXISTING handler catches. A refusal that escapes the node's
      `except` is the PEP harming the patient, which is the inverse adversary this ladder exists
      to avoid. The inner seam must not be called at all.

  (D) I-6, PER SEAM WHERE IT APPLIES. The independent guards keep refusing with the chokepoint
      NEUTRALIZED: `PhiZoneRoutingError` for the inference seam, and the strict-gate
      `isinstance(transport, HistoryQueryingTransport)` probe for the engine seam.

Plus the structural obligations the design states as constraints rather than behaviours: no gated
wrapper may take a principal from its caller (A-8), the boot assertion must go RED when a raw seam
is smuggled in (I-11), and the two subclass-decorators' assumptions about their base classes must
be pinned so they cannot rot silently.
"""

from __future__ import annotations

import ast
import inspect
from pathlib import Path
from typing import Any

import pytest
import structlog
import yaml

from maezo.gateway import action_execution
from maezo.gateway.action_execution import (
    APPROVER_DOMAINS,
    CLASS_ENFORCEMENT_FIELD,
    ENFORCEMENT_ENFORCING,
    EVENT_SHADOW,
    MODE_ENFORCING,
    STATUS_RATIFIED,
)
from maezo.gateway.effect_pep import AgentCapabilities, DecisionContext
from maezo.gateway.seams import EffectDeniedError, SeamContext, is_gated_seam
from maezo.gateway.seams.a2a import gate_a2a
from maezo.gateway.seams.cibseven import gate_cibseven
from maezo.gateway.seams.dmn import gate_dmn
from maezo.gateway.seams.fhir import gate_fhir
from maezo.gateway.seams.inference import gate_inference
from maezo.gateway.seams.population import gate_population
from maezo.gateway.seams.whatsapp import gate_whatsapp

pytestmark = pytest.mark.anyio

_REPO_ROOT = Path(__file__).resolve().parents[4]
_SRC = _REPO_ROOT / "src" / "maezo"


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@pytest.fixture(autouse=True)
def _clear_manifest_cache() -> Any:
    action_execution._load_cached.cache_clear()
    yield
    action_execution._load_cached.cache_clear()


@pytest.fixture(autouse=True)
def _no_ambient_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv(action_execution.MANIFEST_PATH_ENV, raising=False)


# =================================================================================================
# Fakes — each records every call so parity can compare ARGUMENTS, not just return values
# =================================================================================================


class _Recorder:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple[Any, ...], dict[str, Any]]] = []

    def _record(self, name: str, *args: Any, **kwargs: Any) -> None:
        self.calls.append((name, args, kwargs))


class FakeFhir(_Recorder):
    async def read_patient(self, patient_id: str) -> dict[str, Any]:
        self._record("read_patient", patient_id)
        return {"resourceType": "Patient", "id": patient_id}

    async def read_patient_summary(self, patient_id: str) -> dict[str, Any]:
        self._record("read_patient_summary", patient_id)
        return {"resourceType": "Patient", "id": patient_id}

    async def read_coverage(self, patient_id: str) -> Any:
        self._record("read_coverage", patient_id)
        return {"resourceType": "Coverage"}

    async def search_coverage(self, patient_id: str) -> Any:
        self._record("search_coverage", patient_id)
        return [{"resource": {"resourceType": "Coverage"}}]


class FakeWhatsApp(_Recorder):
    async def send(self, to_hash: str, text: str) -> dict[str, Any]:
        self._record("send", to_hash, text)
        return {"messages": [{"id": "wamid.fake"}]}


class FakeDmn(_Recorder):
    async def evaluate(
        self, decision_key: str, variables: dict[str, Any], *, tenant: str | None = None
    ) -> tuple[list[dict[str, Any]], Any]:
        from maezo.tools.workers.dmn_transport import DmnVersion

        self._record("evaluate", decision_key, variables, tenant=tenant)
        return [{"resultado": "ok"}], DmnVersion(
            id="d:1:abc", key=decision_key, version=1, deployment_id="dep-1"
        )

    async def close(self) -> None:
        self._record("close")


class FakeCibSeven(_Recorder):
    """Implements `CibSevenTransport` AND `HistoryQueryingTransport` (both are runtime_checkable)."""

    async def find_active_instance(self, business_key: str) -> Any:
        from maezo.tools.mcp_cibseven.transport import ProcessInstance

        self._record("find_active_instance", business_key)
        return ProcessInstance(
            instance_id="i-1",
            process_key="SP-OP-AUTH-001",
            business_key=business_key,
            state="ACTIVE",
        )

    async def find_any_instance(self, business_key: str, *, process_key: str = "") -> Any:
        self._record("find_any_instance", business_key, process_key=process_key)
        return None

    async def start_process_instance(
        self, process_key: str, business_key: str, variables: dict[str, Any]
    ) -> Any:
        from maezo.tools.mcp_cibseven.transport import ProcessInstance

        self._record("start_process_instance", process_key, business_key, variables)
        return ProcessInstance(
            instance_id="i-2",
            process_key=process_key,
            business_key=business_key,
            state="ACTIVE",
        )

    async def correlate_message(
        self,
        message_name: str,
        business_key: str,
        variables: dict[str, Any],
        *,
        correlation_keys: dict[str, Any] | None = None,
        all_matching: bool = False,
    ) -> None:
        self._record(
            "correlate_message",
            message_name,
            business_key,
            variables,
            correlation_keys=correlation_keys,
            all_matching=all_matching,
        )

    async def get_process_status(self, business_key: str) -> Any:
        from maezo.tools.mcp_cibseven.transport import ProcessStatus

        self._record("get_process_status", business_key)
        return ProcessStatus(
            instance_id="i-1", process_key="SP-OP-AUTH-001", business_key=business_key, state="ACTIVE"
        )

    async def close(self) -> None:
        self._record("close")


class _NoHistoryCibSeven(FakeCibSeven):
    """A `CibSevenTransport` that is NOT a `HistoryQueryingTransport` (for the I-6 strict probe)."""

    find_any_instance = None  # type: ignore[assignment]


class FakeInference(_Recorder):
    def __init__(self, *, phi_capable: bool = True) -> None:
        super().__init__()
        self._phi_capable = phi_capable

    @property
    def provider_name(self) -> str:
        return "fake"

    @property
    def model_id(self) -> str | None:
        return "fake-model"

    def health_check(self) -> dict[str, str]:
        return {"status": "ok", "message": "fake"}

    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
    ) -> str:
        from maezo.runtime.inference import PhiZoneRoutingError

        self._record("generate", prompt, phi=phi, agent_id=agent_id, tenant_id=tenant_id)
        if phi and not self._phi_capable:
            # The REAL provider's structural refusal (`runtime/inference.py:654`), reproduced so
            # the I-6 proof exercises the same control flow without a network provider.
            raise PhiZoneRoutingError("phi=True but the active provider is not PHI-zone capable")
        return "generated"


class FakePopulation(_Recorder):
    async def actuarial_risk(self, cohort_id: str, *, features: list[str]) -> Any:
        from maezo.agents.andre.graph import CohortAggregate

        self._record("actuarial_risk", cohort_id, features=features)
        return CohortAggregate(cohort_id=cohort_id, dataset_ref="lake://ds/1", cohort_size=42, k_anonymity=5)

    async def population_metrics(self, cohort_id: str, *, features: list[str]) -> Any:
        from maezo.agents.andre.graph import CohortAggregate

        self._record("population_metrics", cohort_id, features=features)
        return CohortAggregate(cohort_id=cohort_id, dataset_ref="lake://ds/2", cohort_size=7, k_anonymity=5)


class FakeDispatcher(_Recorder):
    async def delegate(self, envelope: Any) -> Any:
        from maezo.a2a.dispatcher import DelegationResult

        self._record("delegate", envelope)
        return DelegationResult(task_id="t-1", success=True, output_ref="ref://1")


# =================================================================================================
# Contexts
# =================================================================================================


def _shipped_seam() -> SeamContext:
    """A context over the SHIPPED manifest, with a capability view that would ALLOW at L-1.

    Deliberately NOT a permissive stub end to end: L-2 has no PEP injected, so the decision is a
    would-DENY. That is the honest production posture today (the roots build a real PEP, but no
    class is approved at L-5 either way), and parity must hold for a DENY — which is the only
    verdict the shipped manifest can produce.
    """
    return SeamContext(
        tenant="amh",
        principal="rafael",
        decision=DecisionContext(
            capabilities=AgentCapabilities.of(
                principal="rafael",
                tools=[
                    "mcp-fhir.read_patient",
                    "mcp-fhir.read_patient_summary",
                    "mcp-fhir.read_coverage",
                    "mcp-fhir.search_coverage",
                    "mcp-whatsapp.send_message",
                    "mcp-dmn.evaluate",
                    "mcp-cibseven.get_process_status",
                    "mcp-cibseven.correlate_process_message",
                ],
                process_keys=["SP-OP-AUTH-001"],
            )
        ),
    )


def _enforcing_manifest(tmp_path: Path) -> Path:
    """A manifest that RATIFIES and ENFORCES every class the catalogue knows.

    Nothing here touches the shipped record — it is written to `tmp_path` and injected through
    `DecisionContext.approvals_path`, the composition-root/test seam `load_action_approvals(path)`
    already defines. Approvals are filled because the point is to reach an ENFORCED state, not to
    simulate a governance act; the fixture cannot affect production, which is why §7.3's real
    protections (`status`, `modo`, set-equality approval) are proven separately in
    `test_effect_enforcement.py` rather than here.
    """
    acoes = {
        name: {
            "descricao": f"fixture {name}",
            CLASS_ENFORCEMENT_FIELD: ENFORCEMENT_ENFORCING,
            "dominios_exigidos": sorted(APPROVER_DOMAINS),
            "aprovacoes": {
                domain: {
                    "aprovado": True,
                    "aprovador": "Fixture Approver, fixture role",
                    "data": "2026-01-01",
                    "evidencia_ref": "fixture://evidence",
                }
                for domain in sorted(APPROVER_DOMAINS)
            },
        }
        for name in sorted({spec.action_class for spec in _operations().values()})
    }
    manifest = {
        "version": 1,
        "status": STATUS_RATIFIED,
        "modo": MODE_ENFORCING,
        "acoes": acoes,
    }
    path = tmp_path / "action-approvals.yaml"
    path.write_text(yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return path


def _operations() -> Any:
    from maezo.gateway.effect_classes import OPERATIONS

    return OPERATIONS


def _denying_seam(tmp_path: Path) -> SeamContext:
    """Enforcing manifest + NO capability view => an ENFORCED DENY at L-1 for every operation."""
    return SeamContext(
        tenant="amh",
        principal="rafael",
        decision=DecisionContext(capabilities=None, approvals_path=_enforcing_manifest(tmp_path)),
    )


# =================================================================================================
# (A) PARITY — gated vs raw, under the SHIPPED manifest
# =================================================================================================


#: A STABLE envelope sentinel. Parity compares captured ARGUMENTS by equality, so a fresh
#: `object()` per call would differ by identity and report a false parity break.
_ENVELOPE = object()

#: `(attribute, call factory, is_gated)`. `is_gated=False` marks the ONE deliberate pass-through
#: (`start_process_instance`, see `seams/cibseven.py`) so the telemetry count below is derived
#: from the same table the parity sweep uses rather than hardcoded twice.
_METHOD_MATRIX: tuple[tuple[str, Any, bool], ...] = (
    ("read_patient", lambda s: s.read_patient("pat-1"), True),
    ("read_patient_summary", lambda s: s.read_patient_summary("pat-1"), True),
    ("read_coverage", lambda s: s.read_coverage("pat-1"), True),
    ("search_coverage", lambda s: s.search_coverage("pat-1"), True),
    ("send", lambda s: s.send("hk1_abc", "olá"), True),
    ("evaluate", lambda s: s.evaluate("triage_redflag_adult", {"idade_anos": 30}), True),
    ("find_active_instance", lambda s: s.find_active_instance("ESC-amh-1"), True),
    ("find_any_instance", lambda s: s.find_any_instance("ESC-amh-1", process_key="SP-OP-AUTH-001"), True),
    ("correlate_message", lambda s: s.correlate_message("msg", "ESC-amh-1", {"a": 1}), True),
    ("get_process_status", lambda s: s.get_process_status("ESC-amh-1"), True),
    ("start_process_instance", lambda s: s.start_process_instance("SP-OP-AUTH-001", "ESC-amh-1", {}), False),
    ("generate", lambda s: s.generate("prompt", phi=False, agent_id="rafael", tenant_id="amh"), True),
    # BOTH zone tokens: `inference.generate_phi` is a distinct catalogue operation and would be
    # unreachable — i.e. evidence-less, i.e. unapprovable (I-10) — if only `phi=False` were driven.
    ("generate", lambda s: s.generate("prompt", phi=True, agent_id="rafael", tenant_id="amh"), True),
    ("actuarial_risk", lambda s: s.actuarial_risk("cohort-1", features=["a"]), True),
    ("population_metrics", lambda s: s.population_metrics("cohort-1", features=["a"]), True),
    ("delegate", lambda s: s.delegate(_ENVELOPE), True),
)


def _gated_call_count(seam: Any) -> int:
    """How many shadow lines `_exercise(seam)` must produce for this object."""
    return sum(1 for attr, _factory, gated in _METHOD_MATRIX if gated and hasattr(seam, attr))


async def _exercise(seam: Any) -> list[Any]:
    """Drive every method of every seam family the object exposes, capturing outcomes.

    Returns a list of `("ok", value)` / `("raised", ExceptionType)` entries, so an exception is
    compared as an OUTCOME rather than swallowed — parity must cover the failure paths too.
    """
    out: list[Any] = []
    for attr, factory, _gated in _METHOD_MATRIX:
        if not hasattr(seam, attr):
            continue
        try:
            out.append(("ok", await factory(seam)))
        except Exception as exc:  # noqa: BLE001 - the exception TYPE is part of the observable
            out.append(("raised", type(exc).__name__))
    return out


_SEAM_FACTORIES: dict[str, Any] = {
    "fhir": (FakeFhir, gate_fhir),
    "whatsapp": (FakeWhatsApp, gate_whatsapp),
    "dmn": (FakeDmn, gate_dmn),
    "cibseven": (FakeCibSeven, gate_cibseven),
    "inference": (FakeInference, gate_inference),
    "population": (FakePopulation, gate_population),
    "a2a": (FakeDispatcher, gate_a2a),
}


@pytest.mark.parametrize("seam_name", sorted(_SEAM_FACTORIES))
async def test_parity_gated_vs_neutralized_under_the_shipped_manifest(seam_name: str) -> None:
    """(A) THE INERTNESS PROOF, per seam — §9.1's first exit criterion, invariant I-7.

    "Neutralized" means the gate is REMOVED from the path entirely, not told to allow: the raw
    inner is driven directly. If shadow mode is truly inert, that substitution is unobservable in
    the return values, the exception types, the call count, the arguments, and the ordering.
    """
    fake_cls, gate = _SEAM_FACTORIES[seam_name]

    neutralized_inner = fake_cls()
    neutralized = await _exercise(neutralized_inner)

    gated_inner = fake_cls()
    live = await _exercise(gate(gated_inner, _shipped_seam()))

    assert live == neutralized, f"{seam_name}: gated and neutralized outcomes differ"
    assert gated_inner.calls == neutralized_inner.calls, (
        f"{seam_name}: the gate changed WHICH inner calls happened, or with what arguments"
    )


async def test_the_shipped_manifest_really_decides_and_really_would_deny() -> None:
    """The other half of (A): parity would ALSO pass if the wrapper had become a no-op.

    Same pairing the worker leg uses (`test_the_shipped_manifest_denies_on_the_live_path_but_
    does_not_block`): inspect the decision directly and prove the gate IS evaluating, IS producing
    a WOULD_DENY under the shipped record, and is NOT enforced.
    """
    from maezo.gateway.effect_pep import decide_effect

    seam = _shipped_seam()
    decision = decide_effect(
        tenant=seam.tenant, principal=seam.principal, operation="fhir.read_patient", ctx=seam.decision
    )
    assert decision.allow is False
    assert decision.enforced is False, "the shipped manifest must not enforce — this wave ships inert"
    assert decision.action_class == "leitura_phi_clinica"


# =================================================================================================
# (B) TELEMETRY — exactly one bounded line per call, field set pinned
# =================================================================================================

#: The EXACT field set of a shadow line. Pinned in both directions (the #222 pattern): an added
#: field is a potential PHI channel that no reviewer asked for; a removed one silently breaks the
#: §9.2 packet, which counts lines by `decision` × `reason` × `tenant`.
_EXPECTED_TELEMETRY_FIELDS = frozenset(
    {
        "event",
        "log_level",
        "topic",
        "operation",
        "action_ref",
        "principal",
        "action_class",
        "decision",
        "reason",
        "layer",
        "denial_shape",
        "mode",
        "enforcement",
        "phi_zone",
        "tenant",
    }
)


@pytest.mark.parametrize("seam_name", sorted(_SEAM_FACTORIES))
async def test_every_gated_call_emits_exactly_one_bounded_shadow_line(seam_name: str) -> None:
    """(B) §9.1's second exit criterion. One line per call, no more and no fewer.

    Two lines per call would double-count the evidence packet; zero would make the call invisible
    to it, and Phase 0 exists to produce that evidence.
    """
    fake_cls, gate = _SEAM_FACTORIES[seam_name]
    seam = gate(fake_cls(), _shipped_seam())

    with structlog.testing.capture_logs() as logs:
        outcomes = await _exercise(seam)

    assert outcomes, f"{seam_name}: the sweep exercised nothing"
    expected = _gated_call_count(seam)
    shadow_lines = [entry for entry in logs if entry["event"] == EVENT_SHADOW]
    assert len(shadow_lines) == expected, (
        f"{seam_name}: expected {expected} shadow lines, got {len(shadow_lines)}"
    )
    for line in shadow_lines:
        assert set(line) == _EXPECTED_TELEMETRY_FIELDS, (
            f"{seam_name}: telemetry field set drifted — "
            f"added={set(line) - _EXPECTED_TELEMETRY_FIELDS}, "
            f"removed={_EXPECTED_TELEMETRY_FIELDS - set(line)}"
        )


async def test_no_payload_prompt_recipient_or_patient_id_ever_reaches_a_telemetry_line() -> None:
    """I-3, asserted against the LINE and not merely against the type.

    `EffectCall` has no field these could travel in, so this is belt-and-suspenders — but it is the
    assertion an approver can actually read, and it covers the whole emission path rather than the
    value object alone.
    """
    secrets = ("pat-SECRET", "5511999998888", "prompt-with-PHI", "ESC-amh-SECRETKEY")
    seam = _shipped_seam()
    with structlog.testing.capture_logs() as logs:
        await gate_fhir(FakeFhir(), seam).read_patient(secrets[0])
        await gate_whatsapp(FakeWhatsApp(), seam).send(secrets[1], "olá")
        await gate_inference(FakeInference(), seam).generate(secrets[2], phi=True)
        await gate_cibseven(FakeCibSeven(), seam).get_process_status(secrets[3])

    rendered = repr(logs)
    for secret in secrets:
        assert secret not in rendered, f"{secret!r} leaked into a telemetry line"


async def test_every_catalogued_agent_operation_is_reachable_from_some_seam() -> None:
    """Non-vacuity for (B): the catalogue must not contain an operation no wrapper can emit.

    An operation with no seam produces no shadow evidence, so a class resting on it could never be
    approved (I-10). The two exemptions are DECLARED, not discovered: `cibseven.start_process` is
    deliberately unwired (see `seams/cibseven.py`) and the population pair has no client yet.
    """
    emitted: set[str] = set()
    seam = _shipped_seam()
    for fake_cls, gate in _SEAM_FACTORIES.values():
        wrapper = gate(fake_cls(), seam)
        with structlog.testing.capture_logs() as logs:
            await _exercise(wrapper)
        emitted |= {entry["operation"] for entry in logs if entry["event"] == EVENT_SHADOW}

    catalogued = set(_operations())
    unreachable = catalogued - emitted
    assert unreachable == {"cibseven.start_process"}, (
        "the set of catalogued-but-unreachable operations changed. Only the deliberately-unwired "
        f"engine start may be in it; got {sorted(unreachable)}"
    )


# =================================================================================================
# (C) THE DECLARED DENIAL SHAPE, under a manifest that actually enforces
# =================================================================================================


async def test_dmn_denial_lands_on_the_nodes_declared_dmn_unavailable_path(tmp_path: Path) -> None:
    """`ROTA_DMN_INDISPONIVEL`. The type matters more here than anywhere else.

    Every `_evaluate_dmn` helper catches `(DmnEvaluationError, DmnNoResultError)` and NOTHING
    wider (`rafael/graph.py:549`, `helena/graph.py:761`, `marina/graph.py:807`). A refusal that is
    not one of those escapes the node and kills the turn — adversary A-12. So assert the CATCH,
    not just the raise.
    """
    from maezo.tools.workers.dmn_transport import DmnEvaluationError, DmnNoResultError

    inner = FakeDmn()
    seam = gate_dmn(inner, _denying_seam(tmp_path))

    caught: Exception | None = None
    try:
        await seam.evaluate("triage_redflag_adult", {"idade_anos": 30})
    except (DmnEvaluationError, DmnNoResultError) as exc:  # the node's EXACT handler
        caught = exc
    assert caught is not None, "the denial escaped the node's declared DMN handler"
    assert isinstance(caught, EffectDeniedError)
    assert caught.decision.denial_shape == "ROTA_DMN_INDISPONIVEL"
    assert inner.calls == [], "the inner transport must not be reached on an enforced deny"


@pytest.mark.parametrize(
    ("method", "args", "kwargs"),
    [
        ("find_active_instance", ("ESC-amh-1",), {}),
        ("find_any_instance", ("ESC-amh-1",), {"process_key": "SP-OP-AUTH-001"}),
        ("get_process_status", ("ESC-amh-1",), {}),
        ("correlate_message", ("msg", "ESC-amh-1", {}), {}),
    ],
)
async def test_engine_denial_raises_and_is_never_readable_as_no_active_instance(
    tmp_path: Path, method: str, args: tuple[Any, ...], kwargs: dict[str, Any]
) -> None:
    """`LEITURA_INCONCLUSIVA` / `INCIDENTE_FALHA_FECHADA` — §6.1's highest-risk C0 item.

    A denied engine read must RAISE, in a type the nine `except CibSevenError` start-process nodes
    catch. Returning `None` would make an un-answerable question look like a definite "nothing is
    running", which is exactly the anti-dupla-terminação hole the class exists to protect.
    """
    from maezo.tools.mcp_cibseven.transport import CibSevenError

    inner = FakeCibSeven()
    seam = gate_cibseven(inner, _denying_seam(tmp_path))

    result: Any = "<not raised>"
    try:
        result = await getattr(seam, method)(*args, **kwargs)
    except CibSevenError as exc:  # the graphs' EXACT handler
        assert isinstance(exc, EffectDeniedError)
        assert exc.decision.denial_shape in {"LEITURA_INCONCLUSIVA", "INCIDENTE_FALHA_FECHADA"}
    assert result == "<not raised>", f"{method} returned {result!r} instead of refusing — a denied "
    assert inner.calls == [], "the inner transport must not be reached on an enforced deny"


@pytest.mark.parametrize(
    ("seam_name", "method", "args", "kwargs", "shape"),
    [
        ("fhir", "read_patient", ("pat-1",), {}, "LACUNA_DECLARADA"),
        ("whatsapp", "send", ("hk1_abc", "olá"), {}, "ESCALONAMENTO_HUMANO"),
        ("inference", "generate", ("prompt",), {}, "ROTA_LLM_INDISPONIVEL"),
        ("population", "actuarial_risk", ("cohort-1",), {"features": ["a"]}, "LACUNA_DECLARADA"),
        ("a2a", "delegate", (object(),), {}, "DEGRADACAO_SEM_DOSSIE"),
    ],
)
async def test_the_remaining_seams_refuse_in_their_declared_shape(
    tmp_path: Path, seam_name: str, method: str, args: tuple[Any, ...], kwargs: dict[str, Any], shape: str
) -> None:
    """The four shapes whose nodes catch bare `Exception` — assert the SHAPE, and no inner call.

    Each of these call sites (`rafael/graph.py:349`, `helena/graph.py:713`, every `_llm.generate`
    site, `andre/graph.py:716`, and the dossier workers' "ANY delegation failure" branch) folds the
    exception into a gap note / an `error` field / a `dossier_gap` token, so the refusal degrades
    exactly as the class declares.
    """
    fake_cls, gate = _SEAM_FACTORIES[seam_name]
    inner = fake_cls()
    seam = gate(inner, _denying_seam(tmp_path))

    with pytest.raises(EffectDeniedError) as excinfo:
        await getattr(seam, method)(*args, **kwargs)
    assert excinfo.value.decision.denial_shape == shape
    assert excinfo.value.decision.enforced is True
    assert inner.calls == [], "the inner seam must not be reached on an enforced deny"


async def test_a_denial_message_carries_only_bounded_tokens(tmp_path: Path) -> None:
    """I-3 on the DENIAL path specifically — the graphs interpolate `{exc}` into gap notes.

    `rafael/graph.py:350` writes `f"cobertura FHIR indisponivel: {exc}"` straight into graph state,
    which is checkpointed. So the exception's own text must be bounded-token-only.
    """
    seam = gate_fhir(FakeFhir(), _denying_seam(tmp_path))
    with pytest.raises(EffectDeniedError) as excinfo:
        await seam.read_patient("pat-SECRET-12345")
    rendered = str(excinfo.value)
    assert "pat-SECRET-12345" not in rendered
    for token in ("fhir.read_patient", "leitura_phi_clinica", "L1_CAPACIDADE", "LACUNA_DECLARADA"):
        assert token in rendered


# =================================================================================================
# (D) I-6 — the independent guards refuse with the chokepoint NEUTRALIZED
# =================================================================================================


async def test_phi_zone_routing_fail_close_is_independent_of_the_pep() -> None:
    """I-6 for the inference seam, proven in BOTH directions.

    `PhiZoneRoutingError` (`runtime/inference.py:103`, raised at `:654`) is the structural refusal
    that keeps PHI out of a non-BR-resident model. It must refuse with the gate present AND with
    the gate removed — the chokepoint can only SUBTRACT permission, never restore any.
    """
    from maezo.runtime.inference import PhiZoneRoutingError

    raw = FakeInference(phi_capable=False)
    with pytest.raises(PhiZoneRoutingError):
        await raw.generate("prompt", phi=True)  # neutralized: no gate at all

    gated = gate_inference(FakeInference(phi_capable=False), _shipped_seam())
    with pytest.raises(PhiZoneRoutingError):
        await gated.generate("prompt", phi=True)  # gated: the SAME refusal, unswallowed


async def test_the_gate_never_catches_a_provider_error() -> None:
    """The mechanism behind the previous test: the delegation is BARE.

    A wrapper that wrapped the inner call in `try/except` would be able to convert a structural
    refusal into a soft outcome. Assert on the AST that no gated method body contains a handler.
    """
    tree = ast.parse((_SRC / "gateway" / "seams" / "inference.py").read_text(encoding="utf-8"))
    handlers = [node for node in ast.walk(tree) if isinstance(node, ast.ExceptHandler)]
    assert not handlers, "the inference seam must not catch anything — I-6 requires a bare delegation"


async def test_strict_gate_seam_probe_still_fail_closes_through_the_wrapper() -> None:
    """I-6 for the engine seam: `_require_strict_gate_seams`'s `isinstance` probe stays honest.

    `transport.py:940-947` REFUSES a strict-family start when the transport cannot answer
    "did an instance for this key EVER exist?". A wrapper that always advertised
    `find_any_instance` would make a mis-wired root pass that probe and fail later — fail-closed
    turned into fail-late, on the one path whose whole purpose is not having any.
    """
    from maezo.tools.mcp_cibseven.transport import HistoryQueryingTransport

    seam = _shipped_seam()
    with_history = gate_cibseven(FakeCibSeven(), seam)
    without_history = gate_cibseven(_NoHistoryCibSeven(), seam)

    assert isinstance(with_history, HistoryQueryingTransport)
    assert not isinstance(without_history, HistoryQueryingTransport), (
        "the wrapper advertised a history seam its inner does not have — the strict-start probe "
        "would now pass for a transport that cannot answer it"
    )


# =================================================================================================
# Structural obligations: A-8, I-11, and the two subclass-decorator assumptions
# =================================================================================================

#: The ONE allowlisted `tenant` parameter: `DmnTransport.evaluate`'s own engine-level
#: tenant-scoped-decision-definition selector (`dmn_transport.py:174-177`). It is a different axis
#: from the policy tenant, which is closure-bound; the seam's docstring says so at its definition.
_ALLOWED_TENANT_PARAM = {("GatedDmnTransport", "evaluate", "tenant")}


def test_no_gated_wrapper_method_accepts_a_principal_or_tenant_argument() -> None:
    """Adversary A-8, as a structural fence: a caller-supplied principal gates nothing.

    The principal is bound at CONSTRUCTION, in the frozen `SeamContext`. This test walks every
    public method of every gated wrapper and refuses `agent_id` / `principal` / `tenant`
    parameters, with exactly one declared exception.
    """
    from maezo.gateway import seams as seams_pkg

    forbidden = {"agent_id", "principal", "tenant", "tenant_id"}
    # `tenant_id`/`agent_id` on `generate` are the provider's METERING correlation ids, forwarded
    # verbatim and never read as identity — declared here rather than silently skipped.
    metering = {
        ("GatedInferenceProvider", "generate", "agent_id"),
        ("GatedInferenceProvider", "generate", "tenant_id"),
    }

    offenders: list[tuple[str, str, str]] = []
    for name in seams_pkg.__all__:
        obj = getattr(seams_pkg, name)
        if not (isinstance(obj, type) and issubclass(obj, seams_pkg.GatedSeam)):
            continue
        for method_name, method in vars(obj).items():
            if method_name.startswith("_") or not callable(method):
                continue
            for param in inspect.signature(method).parameters:
                if param in forbidden:
                    entry = (obj.__name__, method_name, param)
                    if entry not in _ALLOWED_TENANT_PARAM and entry not in metering:
                        offenders.append(entry)
    assert not offenders, f"gated methods take an identity argument: {offenders}"


def test_the_boot_assertion_goes_red_when_a_raw_seam_is_smuggled_in() -> None:
    """I-11's runtime half. The CI fence catches source-level bypasses; this catches the rest.

    Three shapes, because they fail differently: a raw object where a gated one belongs, an EMPTY
    dep map (which must NOT be trivially green — "found nothing to check" is what a seam-removing
    bypass looks like), and the fully gated map that must be the only thing that passes.
    """
    from maezo.gateway.tool_registry import effect_seams_gated, gated_seam_violations

    seam = _shipped_seam()
    gated_map = {"dmn": gate_dmn(FakeDmn(), seam), "fhir": gate_fhir(FakeFhir(), seam)}
    ok, detail = effect_seams_gated(gated_map)
    assert ok, detail
    assert "dmn" in detail and "fhir" in detail

    smuggled = dict(gated_map) | {"cibseven": FakeCibSeven()}  # a RAW transport
    ok, detail = effect_seams_gated(smuggled)
    assert not ok, "the boot assertion accepted a raw transport"
    assert "cibseven=FakeCibSeven" in detail
    assert gated_seam_violations(smuggled) == ["cibseven=FakeCibSeven"]

    ok, detail = effect_seams_gated({})
    assert not ok, "an EMPTY dep map must be red, not vacuously green"
    assert "vacuous" in detail

    # Non-effect keys are not seams and must not be demanded to be gated.
    ok, _ = effect_seams_gated(dict(gated_map) | {"audit_sink": object(), "agent_version": "x@v0"})
    assert ok


def test_delegation_dispatcher_public_surface_is_only_delegate() -> None:
    """The price of the a2a subclass-decorator, charged.

    `GatedDelegationDispatcher` overrides `delegate` and inherits everything else. That is only
    safe while `delegate` is the ONLY public method — a second one would be inherited UNGATED.
    """
    from maezo.a2a.dispatcher import DelegationDispatcher

    public = {name for name in vars(DelegationDispatcher) if not name.startswith("_")}
    assert public == {"delegate"}, (
        f"DelegationDispatcher grew public method(s) {sorted(public - {'delegate'})} — "
        "GatedDelegationDispatcher would inherit them UNGATED. Override or re-justify."
    )


def test_inference_provider_public_surface_is_fully_overridden() -> None:
    """Same price, same charge, for the inference subclass-decorator."""
    from maezo.gateway.seams.inference import GatedInferenceProvider
    from maezo.runtime.inference import InferenceProvider

    public = {name for name in vars(InferenceProvider) if not name.startswith("_")}
    overridden = {name for name in vars(GatedInferenceProvider) if not name.startswith("_")}
    assert public <= overridden, (
        f"InferenceProvider public member(s) {sorted(public - overridden)} reach the raw provider "
        "un-gated through GatedInferenceProvider. Override or re-justify."
    )


def test_a_gated_seam_cannot_be_forged_by_an_attribute() -> None:
    """`is_gated_seam` is an `isinstance` check for a reason (see `GatedSeam`'s docstring)."""

    class Impostor:
        _inner = None
        _seam = None
        inner = None
        seam_context = None

    assert not is_gated_seam(Impostor())
    assert is_gated_seam(gate_dmn(FakeDmn(), _shipped_seam()))
