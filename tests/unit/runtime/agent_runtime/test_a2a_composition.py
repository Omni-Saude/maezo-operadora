"""F2 — the A2A composition roots must FAIL-CLOSED when no Card-signing key is present.

The defect: `build_auth_delegation_dispatcher` resolved the signing key and, when it was absent,
SILENTLY produced `signer=None` -> unsigned Cards -> `build_dispatcher(verifier=None)` ->
`A2ARegistry(verifier=None)`, which admits ANY (unsigned/forged) Card. In production that is a
signature-enforcement fail-open with no prod-mode guard.

The fix (`_require_signer_or_fail_closed`): an absent key RAISES at composition in production
runtime mode (`runtime_mode != "local"`, un-bypassable — the dev opt-out is IGNORED there),
and in a non-production runtime proceeds unsigned ONLY behind the EXPLICIT
`MAEZO_A2A_ALLOW_UNSIGNED_CARDS` opt-out, never as a silent default.

Prod/dev discriminator: agent-runtime uses `agent_runtime_mode` (Helm injects "kubernetes",
settings default "local"); the worker-runtime dossier edge (`build_dossier_delegation_dispatcher`,
DL-0033 real wiring) uses `worker_runtime_mode_from_env()` — the generic `RUNTIME_MODE` env
contract, FAIL-CLOSED to "production" when unset since the worker-daemon Helm template injects no
mode var. ANY value other than "local" is treated as production. This mirrors the fail-closed
startup precedents `AnthropicInferenceProvider` (absent credential -> raise, "no silent fallback
to noop") and `RefusingAnsGatewayTransport` (production default refuses).

The dossier-dispatcher tests also prove the edge END TO END against fakes: a REAL dispatcher
(cards from spec/agents, real registry/audit/idempotency-in-memory) routing a worker-originated
envelope into Carolina's/Andre's REAL graphs — including `DelegationResult.meta` riding back to
the originator. Live-PG durable-replay proof is DEFERRED to the PR CI lane (no docker here).
"""

from __future__ import annotations

import textwrap
from typing import Any

import pytest
from tests.support.audit_fakes import FakeStartAuditSink
from tests.unit.a2a.fakes import LabeledFakeTenantKeyset, RecordingProducer

from maezo.a2a import TOPIC_COMPLETED, TOPIC_REQUESTED, CardSigner, per_tenant_key_env_var
from maezo.agents.andre.delegation import delegate_adequacao_dossier
from maezo.agents.carolina.delegation import delegate_cred_dossier
from maezo.agents.fernando.delegation import delegate_arrears_followup
from maezo.gateway.tool_registry import whatsapp_adapter_for
from maezo.runtime.agent_runtime import a2a_composition
from maezo.runtime.agent_runtime.a2a_composition import (
    ALLOW_UNSIGNED_CARDS_ENV_VAR,
    WORKER_RUNTIME_MODE_ENV_VAR,
    _require_signer_or_fail_closed,
    build_auth_delegation_dispatcher,
    build_dossier_delegation_dispatcher,
    worker_runtime_mode_from_env,
)
from maezo.runtime.agent_runtime.settings import AgentRuntimeSettings
from maezo.tools.mcp_cibseven.transport import FakeCibSevenTransport
from maezo.tools.workers.dmn_transport import FakeDmnTransport

#: Leg E2 (ADR-0039 §4.4): the composition roots now resolve the PER-TENANT signing key
#: (`MAEZO_A2A_CARD_SIGNING_KEY__AMH`), not the bare repo-wide var. Every test in this module
#: composes for tenant "amh", so this is the variable that provisions their key.
_TENANT = "amh"
_OTHER_TENANT = "outra"  # a DIFFERENT tenant, for the cross-tenant key-confusion probes
_SIGNING_KEY_ENV = per_tenant_key_env_var(_TENANT)
_VALID_KEY = "unit-test-card-signing-key-0123456789abcdef"


@pytest.fixture(autouse=True)
def _clean_signing_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Never depend on ambient signing-key / opt-out / mode state; each test sets what it needs."""
    monkeypatch.delenv(_SIGNING_KEY_ENV, raising=False)
    monkeypatch.delenv(per_tenant_key_env_var(_OTHER_TENANT), raising=False)
    monkeypatch.delenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, raising=False)
    monkeypatch.delenv(WORKER_RUNTIME_MODE_ENV_VAR, raising=False)


def _settings(*, mode: str) -> AgentRuntimeSettings:
    return AgentRuntimeSettings(agent_id="rafael", agent_runtime_mode=mode)


def _gate(*, mode: str) -> CardSigner | None:
    return _require_signer_or_fail_closed(runtime_mode=mode, tenant="amh", edge="test-edge")


# --- The F2 logic, isolated on `_require_signer_or_fail_closed` --------------------------------


def test_prod_mode_absent_key_refuses_to_compose(monkeypatch: pytest.MonkeyPatch) -> None:
    """PRODUCTION (kubernetes) + no key -> RAISE. The core fail-closed guard."""
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _gate(mode="kubernetes")


def test_prod_mode_absent_key_ignores_dev_optout(monkeypatch: pytest.MonkeyPatch) -> None:
    """UN-BYPASSABLE: in production the explicit dev opt-out is IGNORED — an absent key STILL
    raises. Revert-RED guard for the `is_production or ...` clause: dropping `is_production` would
    let the opt-out bypass production enforcement, and this test would go green when it must not."""
    monkeypatch.setenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, "1")
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _gate(mode="kubernetes")


def test_unrecognized_mode_treated_as_production(monkeypatch: pytest.MonkeyPatch) -> None:
    """An unknown/misconfigured runtime mode (anything != 'local') fails CLOSED, even with
    the opt-out set — only the literal dev default 'local' may run unsigned."""
    monkeypatch.setenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, "1")
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _gate(mode="docker-compose")


def test_local_mode_absent_key_without_optout_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """Even in dev, an absent key is NEVER a silent default to unsigned — without the explicit
    opt-out it raises. Revert-RED guard for the `not _unsigned_cards_opt_out()` clause."""
    with pytest.raises(RuntimeError, match=ALLOW_UNSIGNED_CARDS_ENV_VAR):
        _gate(mode="local")


def test_local_mode_absent_key_with_explicit_optout_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """Dev/test ergonomics: local mode + the EXPLICIT opt-out -> unsigned Cards (signer=None)."""
    monkeypatch.setenv(ALLOW_UNSIGNED_CARDS_ENV_VAR, "true")
    assert _gate(mode="local") is None


@pytest.mark.parametrize("mode", ["local", "kubernetes", "anything"])
def test_present_key_always_returns_a_signer(monkeypatch: pytest.MonkeyPatch, mode: str) -> None:
    """A present, well-formed key yields a real `CardSigner` regardless of runtime mode."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    signer = _gate(mode=mode)
    assert isinstance(signer, CardSigner)


# --- PER-TENANT key custody: NO cross-tenant fallback (ADR-0039 §4.4, decision 4) ---------------


def test_gate_resolves_the_per_tenant_var_not_the_bare_repo_wide_var(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The gate reads `MAEZO_A2A_CARD_SIGNING_KEY__AMH`, NOT the bare repo-wide
    `MAEZO_A2A_CARD_SIGNING_KEY`: with only the bare var set, production REFUSES (no silent reuse of
    the repo-wide key as a per-tenant default — the migration shim §4.4 forbids)."""
    monkeypatch.setenv("MAEZO_A2A_CARD_SIGNING_KEY", _VALID_KEY)  # bare repo-wide var only
    monkeypatch.delenv(_SIGNING_KEY_ENV, raising=False)
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _gate(mode="kubernetes")


def test_gate_no_cross_tenant_fallback_prod_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    """KEY-CONFUSION PROBE (env-backed): ONLY tenant-B's per-tenant key is present; composing for
    tenant-A ("amh") in production REFUSES — the gate never borrows tenant-B's key for tenant-A.
    This is the exact cross-tenant key-confusion decision 4 exists to prevent."""
    monkeypatch.setenv(per_tenant_key_env_var(_OTHER_TENANT), _VALID_KEY)  # tenant-B only
    monkeypatch.delenv(_SIGNING_KEY_ENV, raising=False)  # tenant-A ("amh") absent
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _gate(mode="kubernetes")  # _gate composes for tenant "amh"


def test_gate_resolves_each_tenants_own_key_when_both_present(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The positive twin: with BOTH tenants' per-tenant keys present, tenant-A resolves tenant-A's
    key (a real signer), never confused with tenant-B's — each tenant its own key."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)  # tenant-A "amh"
    monkeypatch.setenv(per_tenant_key_env_var(_OTHER_TENANT), "tenant-b-distinct-key-0123456789abcd")
    assert isinstance(_gate(mode="kubernetes"), CardSigner)


def test_gate_injected_keyset_no_cross_tenant_fallback() -> None:
    """KEY-CONFUSION PROBE (injected keyset): a `LabeledFakeTenantKeyset` holding ONLY tenant-B's
    key, resolving tenant-A in production, REFUSES — proving the gate consults `key_for(tenant)`
    with the EXACT tenant and honors its no-fallback contract (never tenant-B's key for tenant-A)."""
    keyset = LabeledFakeTenantKeyset({_OTHER_TENANT: _VALID_KEY.encode("utf-8")})
    with pytest.raises(RuntimeError, match="no Agent Card signing key"):
        _require_signer_or_fail_closed(
            runtime_mode="kubernetes", tenant=_TENANT, edge="test-edge", keyset=keyset
        )
    assert keyset.calls == [_TENANT]  # the gate asked for tenant-A's key (got None) and refused


def test_gate_injected_keyset_resolves_the_matching_tenant() -> None:
    """Positive twin of the injected probe: the same keyset resolving tenant-B yields a real signer
    — the no-fallback contract refuses the WRONG tenant, never the right one."""
    keyset = LabeledFakeTenantKeyset({_OTHER_TENANT: _VALID_KEY.encode("utf-8")})
    signer = _require_signer_or_fail_closed(
        runtime_mode="kubernetes", tenant=_OTHER_TENANT, edge="test-edge", keyset=keyset
    )
    assert isinstance(signer, CardSigner)


# --- The guard is wired into the composition ROOT (un-bypassable) ------------------------------


def test_build_auth_delegation_dispatcher_prod_absent_key_never_returns_dispatcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """End-to-end at the composition root: in production runtime mode with no key,
    `build_auth_delegation_dispatcher` RAISES — no unsigned dispatcher object is ever returned, so
    there is nothing to `.delegate()` a forged Card against. `_build_tool_deps` is faked so the test
    is hermetic (no Postgres / no transports) and isolates the F2 guard as the thing that fires."""

    def _fake_tool_deps(_settings_obj: AgentRuntimeSettings, inference: Any = None) -> dict[str, Any]:
        # ONDA 1 B2: `_build_tool_deps` now threads the inference seam so the provider a graph
        # receives is the GATED one (§5.5 / counterexample C-A2). The fake mirrors the signature;
        # the F2 guard this test isolates is unaffected.
        return {
            "dmn": object(),
            "cibseven": object(),
            "audit_sink": object(),
            "inference": inference,
        }

    monkeypatch.setattr(a2a_composition, "_build_tool_deps", _fake_tool_deps)

    with pytest.raises(RuntimeError, match="Refusing to compose an unsigned dispatcher"):
        build_auth_delegation_dispatcher(_settings(mode="kubernetes"))


# --- worker_runtime_mode_from_env (the dossier edge's discriminator) ---------------------------


def test_worker_runtime_mode_defaults_to_production_when_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """FAIL-CLOSED: the worker-daemon Helm template injects no mode var — absent/blank resolves
    to 'production' (any value != 'local'), never silently to the permissive local mode."""
    assert worker_runtime_mode_from_env() == "production"
    monkeypatch.setenv(WORKER_RUNTIME_MODE_ENV_VAR, "   ")
    assert worker_runtime_mode_from_env() == "production"


def test_worker_runtime_mode_reads_explicit_local(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(WORKER_RUNTIME_MODE_ENV_VAR, "local")
    assert worker_runtime_mode_from_env() == "local"


# --- build_dossier_delegation_dispatcher (DL-0033 real wiring, worker-runtime edge) ------------


class _FakeInference:
    async def generate(
        self,
        prompt: str,
        *,
        phi: bool = False,
        agent_id: str | None = None,
        tenant_id: str | None = None,
        # CC-12 x integracao lote3 (LOTE3-INTEGRATION-FAKES-TASK-KIND): assinatura acompanha o
        # Protocol real (`runtime/inference::InferenceProvider.generate`), mesma especie do
        # defeito f1bc87f.
        task_kind: str | None = None,
    ) -> str:
        assert phi is True
        return "dossie sintetico"


def _dossier_deps() -> dict[str, Any]:
    dmn = FakeDmnTransport()
    dmn.register("cred_admissibility", [{"roteamento": "SEGUE_ANALISE"}])
    dmn.register("cred_route", [{"roteamento": "ANALISE_DESCREDENCIAMENTO"}])
    dmn.register("cred_sla", [{"sla_analise": "P10D", "sla_alerta": "P7D"}])
    dmn.register("cred_prior_notice", [{"exige_notificacao_previa": True}])
    return {
        "dmn": dmn,
        "cibseven": FakeCibSevenTransport(),
        "audit_sink": FakeStartAuditSink(),
        "inference": _FakeInference(),
    }


def test_dossier_dispatcher_prod_absent_key_never_returns_dispatcher() -> None:
    """SAME F2 gate as the auth edge: production mode (the unset-env default) + no key -> RAISE.
    The worker-runtime composition root catches this and degrades to dispatcher-absent
    (dossier_delegation_ready=false; workers gap-mark) — but an unsigned dispatcher NEVER exists."""
    with pytest.raises(RuntimeError, match="Refusing to compose an unsigned dispatcher"):
        build_dossier_delegation_dispatcher(
            tenant="amh", runtime_mode=worker_runtime_mode_from_env(), **_dossier_deps()
        )


def test_dossier_dispatcher_fails_closed_on_missing_deps(monkeypatch: pytest.MonkeyPatch) -> None:
    """Missing dmn/cibseven/audit_sink -> ValueError BEFORE any card/signer work (mirrors
    build_auth_delegation_dispatcher's fail-closed dep check)."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    deps = _dossier_deps()
    deps["audit_sink"] = None
    with pytest.raises(ValueError, match="audit_sink"):
        build_dossier_delegation_dispatcher(tenant="amh", runtime_mode="local", **deps)


async def test_dossier_dispatcher_assembles_and_routes_cred_edge_end_to_end(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With a signing key present the REAL dispatcher assembles (signed Cards from
    spec/agents/{carolina,andre}/agent.yaml) and a worker-originated `credentialing.analyze`
    envelope routes into Carolina's REAL graph — `output_ref` is the case's process reference and
    the handler's bounded meta rides back on `DelegationResult.meta` (the originating worker's
    only channel to the UT variables)."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    # Onda 3 / Train C: an INJECTED producer is what this test always wanted (it asserts routing,
    # not fact durability). It bypasses the leg-2 FACT gate. It does NOT, however, satisfy leg 3's
    # durable-IDEMPOTENCY gate — that gate refuses `runtime_mode="production"` with no
    # `database_url` regardless of the producer, because this test needs the dispatcher's in-memory
    # `_inflight` Guard 4 to prove the replay below (a fake DSN cannot: `.delegate()` would then try
    # to reach a real Postgres). In-memory idempotency is legitimate ONLY in EXPLICIT local mode
    # now, so this routing test runs there. Leg 2's fact-gate contract is untouched: the injected
    # producer still wins, proven by `producer.topics()` below.
    producer = RecordingProducer()
    dispatcher = build_dossier_delegation_dispatcher(
        tenant="amh", runtime_mode="local", kafka_producer=producer, **_dossier_deps()
    )

    # Leg E3 (ADR-0039 §4.4): with a key present the composed dispatcher now VERIFIES envelopes. The
    # LIVE worker path is `delegate_cred_dossier`, which retrieves the edge's origin signer from the
    # dispatcher (`origin_signer_of`) and signs at construction — so the wired verifier admits it.
    # (A hand-built UNSIGNED envelope through `dispatcher.delegate` would now be SIGNATURE_INVALID,
    # which is exactly the fail-closed gate this leg adds.)
    result = await delegate_cred_dossier(
        dispatcher,
        tenant="amh",
        prestador_id="P-COMP-1",
        case_meta={"direcao": "descredenciamento", "tipo_prestador": "clinica"},
    )

    assert result.success is True
    assert result.output_ref == "process://CRED-amh-P-COMP-1"
    assert result.meta["route"] == "human_review"
    assert result.meta["grupo_destino"] == "juridico-rede"

    # Guard 4: a re-delivery of the same task_id replays without re-running Carolina — and the
    # meta comes back IDENTICAL (the replay-shape invariant the meta persistence exists for).
    replay = await delegate_cred_dossier(
        dispatcher,
        tenant="amh",
        prestador_id="P-COMP-1",
        case_meta={"direcao": "descredenciamento", "tipo_prestador": "clinica"},
    )
    assert replay.idempotent_replay is True
    assert replay.output_ref == result.output_ref
    assert dict(replay.meta) == dict(result.meta)

    # Non-vacuity for the injected producer: the facts really did flow THROUGH it (requested +
    # completed on the first delivery; the replay short-circuits before `_execute`, so it emits
    # nothing) — so this test would notice if the composition root stopped honouring the
    # injection and silently substituted its own sink.
    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]


async def test_dossier_dispatcher_routes_adequacao_edge_with_shared_task_type(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The SHARED `analytics.population` type passes Andre's card gate and the origin
    (`adequacao-worker`) disambiguates into his `adequacao_dossier` flow: always human,
    `gestao-rede`, no process started — never a payment triage."""
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    # EXPLICIT local: this asserts routing + in-memory replay, which leg 3 makes legal only in local
    # mode (production+no-DSN now refuses for durable idempotency — see the cred test's note above
    # and test_a2a_composition_idempotency.py). The injected producer still bypasses the fact gate.
    producer = RecordingProducer()
    dispatcher = build_dossier_delegation_dispatcher(
        tenant="amh", runtime_mode="local", kafka_producer=producer, **_dossier_deps()
    )

    # Leg E3: the LIVE worker path signs via the edge's origin signer (see the cred test above).
    result = await delegate_adequacao_dossier(
        dispatcher,
        tenant="amh",
        regiao_saude="SP-01",
        especialidade="cardiologia",
        case_meta={"gap_adequacao": "GAP_CRITICO"},
    )

    assert result.success is True
    assert result.output_ref == "process://ADEQ-amh-SP-01-cardiologia"
    assert result.meta["route"] == "human_review"
    assert result.meta["grupo_destino"] == "gestao-rede"
    assert result.meta["process_started"] == "False"


async def test_dossier_dispatcher_routes_the_registered_fernando_edge(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """R-081: `arrears.followup` chega ao grafo REAL de Fernando pela raiz de composicao.

    ANTES desta mudanca `make_fernando_handler` nao estava em `handlers={...}` (nem fernando em
    `_DOSSIER_EDGE_AGENT_IDS`), entao este envelope morria no dispatcher — o alvo existia, era
    testado, e nada podia alcanca-lo. Este teste e' a prova de que a metade de REGISTRO da decisao
    do dono aterrissou: nao ha' `make_fernando_handler` construido aqui, so' a raiz de producao.

    O caminho `intencao="rescisao"` (J3) e' escolhido de proposito: ele NUNCA consulta DMN
    (`agents/fernando/graph.py`), entao o `FakeDmnTransport` de `_dossier_deps()` — que registra
    so' as tabelas de credenciamento — basta, e o turno chega ao `start_process` de verdade.

    Este teste afirma ALCANCABILIDADE — a distincao que
    `tests/unit/a2a/test_agent_card_handlers_parity.py` faz no seu docstring. A metade de ORIGEM
    (`operadora.inadimplencia.prepare_dossier` chamando `delegate_arrears_followup`) tambem
    aterrissou, e e' provada do OUTRO lado, no worker:
    `tests/unit/tools/workers/test_inadimplencia.py::
    test_registered_prepare_dossier_threads_the_dossier_dispatcher_seam`. As duas provas sao
    deliberadamente separadas: esta fixa o alvo na raiz de producao, aquela fixa a origem no
    bootstrap do worker, e nenhuma das duas depende da outra para nao ser vacua.
    """
    monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
    producer = RecordingProducer()
    dispatcher = build_dossier_delegation_dispatcher(
        tenant="amh", runtime_mode="local", kafka_producer=producer, **_dossier_deps()
    )

    result = await delegate_arrears_followup(
        dispatcher,
        tenant="amh",
        numero_contrato="CTR-COMP-1",
        case_meta={
            "intencao": "rescisao",
            "tipo_plano": "individual",
            "meses_inadimplencia": 3,
            "valor_total_devido_cents": 30000,
            "ja_em_rescisao_cancel": False,
        },
    )

    assert result.success is True
    assert result.output_ref == "process://INAD-amh-CTR-COMP-1"
    assert result.meta["route"] == "escalate"
    assert result.meta["motivo_humano"] == "indicio_rescisao"
    assert result.meta["process_started"] == "True"
    # O dossie NUNCA viaja pela costura: `meta` so' carrega tokens de classe.
    assert "dossier" not in result.meta and "dossie" not in str(dict(result.meta)).lower()

    # Guard 4: a reentrega do mesmo `task_id` replica sem reexecutar Fernando.
    replay = await delegate_arrears_followup(
        dispatcher,
        tenant="amh",
        numero_contrato="CTR-COMP-1",
        case_meta={"intencao": "rescisao", "ja_em_rescisao_cancel": False},
    )
    assert replay.idempotent_replay is True
    assert replay.output_ref == result.output_ref

    assert producer.topics() == [TOPIC_REQUESTED, TOPIC_COMPLETED]


def test_the_dossier_edge_registers_exactly_its_declared_agent_set() -> None:
    """Nao-vacuidade do teste acima: o mapa de handlers da raiz e' EXATAMENTE
    `_DOSSIER_EDGE_AGENT_IDS`. Sem esta cerca, um handler acrescentado ao mapa sem entrar na
    tupla (ou o contrario) passaria despercebido — a tupla e' o que assina os Cards
    (`build_agent_cards`), e um Card sem handler e' um envelope aceito que nada serve."""
    import ast
    import inspect

    fonte = inspect.getsource(a2a_composition.build_dossier_delegation_dispatcher)
    arvore = ast.parse(textwrap.dedent(fonte))
    registrados: set[str] = set()
    for no in ast.walk(arvore):
        if not isinstance(no, ast.Call):
            continue
        for kw in no.keywords:
            if kw.arg == "handlers" and isinstance(kw.value, ast.Dict):
                registrados |= {
                    k.value for k in kw.value.keys if isinstance(k, ast.Constant) and isinstance(k.value, str)
                }
    assert registrados == set(a2a_composition._DOSSIER_EDGE_AGENT_IDS) == {"carolina", "andre", "fernando"}


def test_the_root_builds_fernandos_whatsapp_seam_from_the_one_agent_adapter_map() -> None:
    """A escolha agente->adaptador WhatsApp tem UMA definicao, e esta raiz usa ELA.

    `build_whatsapp_seam` ramifica so' em `adapter == "lucas"`; qualquer outra string cai no
    `else` do sender da Helena. Passar o ID DO AGENTE (`"fernando"`) daria hoje o MESMO objeto
    que o mapa escolhe (`_WHATSAPP_ADAPTER_BY_AGENT["fernando"] == "helena"`) — por acidente, nao
    por decisao. Esta cerca prende o valor que a raiz efetivamente passa ao construtor ao valor
    do mapa, entao no dia em que um ramo `fernando` existir, ou o `else` mudar, a raiz nao pode
    divergir em silencio do que `build_agent_seams` escolheria para o mesmo agente.
    """
    passados: list[str] = []
    real = a2a_composition.build_whatsapp_seam

    def espiao(*, seam: Any, inner: Any = None, adapter: str = "helena") -> Any:
        passados.append(adapter)
        return real(seam=seam, inner=inner, adapter=adapter)

    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setenv(_SIGNING_KEY_ENV, _VALID_KEY)
        monkeypatch.setattr(a2a_composition, "build_whatsapp_seam", espiao)
        build_dossier_delegation_dispatcher(
            tenant="amh", runtime_mode="local", kafka_producer=RecordingProducer(), **_dossier_deps()
        )
    finally:
        monkeypatch.undo()

    assert passados == [whatsapp_adapter_for("fernando")]
    # Nao-vacuidade: o mapa realmente nomeia fernando, e o valor NAO e' o id do agente.
    assert whatsapp_adapter_for("fernando") == "helena"


def test_no_call_site_in_src_passes_build_whatsapp_seam_an_unknown_adapter_literal() -> None:
    """FECHA A CLASSE do achado F3, nao so' a instancia: em `src/`, um `adapter=` LITERAL passado
    a `build_whatsapp_seam` tem de ser um ADAPTADOR conhecido, nunca um id de agente.

    `build_whatsapp_seam` ramifica so' em `"lucas"`; toda outra string cai no `else` da Helena.
    Passar `adapter="fernando"` (o id do AGENTE) funcionava por acidente. Sem esta cerca, o
    proximo call site repete o erro e ninguem ve: o teste acima prende UMA raiz, este prende
    TODAS as chamadas com literal do repositorio, derivando o conjunto valido dos VALORES do
    proprio `_WHATSAPP_ADAPTER_BY_AGENT`. Chamadas com expressao (o mapa/`whatsapp_adapter_for`)
    passam por construcao — sao justamente a forma correta.
    """
    import ast
    from pathlib import Path

    from maezo.gateway import tool_registry

    adaptadores_validos = set(tool_registry._WHATSAPP_ADAPTER_BY_AGENT.values())
    src_root = Path(tool_registry.__file__).parent.parent
    literais: list[tuple[str, int, str]] = []
    chamadas = 0
    for caminho in sorted(src_root.rglob("*.py")):
        arvore = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
        for no in ast.walk(arvore):
            if not isinstance(no, ast.Call):
                continue
            alvo = no.func
            nome = alvo.attr if isinstance(alvo, ast.Attribute) else getattr(alvo, "id", "")
            if nome != "build_whatsapp_seam":
                continue
            chamadas += 1
            for kw in no.keywords:
                if kw.arg == "adapter" and isinstance(kw.value, ast.Constant):
                    if kw.value.value not in adaptadores_validos:
                        literais.append(
                            (str(caminho.relative_to(src_root)), no.lineno, str(kw.value.value))
                        )

    assert chamadas >= 2, f"a cerca varreu {chamadas} chamadas — vacua (esperava >= 2)"
    assert not literais, (
        f"call site(s) passando um `adapter=` literal desconhecido a `build_whatsapp_seam`: "
        f"{literais}. Adaptadores validos: {sorted(adaptadores_validos)}. Use "
        "`gateway.tool_registry.whatsapp_adapter_for(<agent_id>)` — a UNICA definicao da escolha "
        "agente->adaptador; um id de agente NAO e' um id de adaptador, e o `else` de "
        "`build_whatsapp_seam` aceitaria qualquer string em silencio."
    )
