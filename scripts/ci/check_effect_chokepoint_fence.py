#!/usr/bin/env python3
"""CI gate: the Onda-1 effect chokepoint is INEVITABLE, not merely available (design §8, I-11).

Purpose
-------
`docs/design/wave1-effect-chokepoint.md` §8 specifies FIVE static rules that together make it a
CI failure — not a review nicety — to reintroduce an ungated effect after B1/B2 landed the
registry (`maezo.gateway.tool_registry`), the seven gated seam decorators
(`maezo.gateway.seams.*`) and the closed operation catalogue (`maezo.gateway.effect_classes`):

  §8.1  reject raw construction of a raw effect class outside the registry.
  §8.2  reject a hand-rolled `httpx` transport / a duplicated raw effect REST-path literal.
  §8.3  reject a policy-plane import of a concrete transport, or an `os.environ` read of one of
        the three chokepoint-relevant env vars, outside `maezo/gateway/` (+ `agents/__init__.py`
        for the spec-dir var).
  §8.4  reject a test double (`Fake*`/`*Mock*`/`Noop*`) reachable from a production composition
        root.
  §8.5  non-vacuity + completeness: the registry actually constructs what §8.1 fences, every
        declared agent tool id resolves to a catalogued operation (one disclosed exception), every
        catalogued operation/class round-trips through the shipped manifest, and every class has a
        declared denial shape covered by a passing mutation-style test.

Design, mirroring `check_start_process_fence.py`
-------------------------------------------------
Pure `scan_tree(src_dir)` core (§8.1-8.4, one AST pass per file) + a separate
`check_completeness(src_dir, repo_root)` (§8.5, needs YAML + the closed catalogue, not just AST)
+ a thin `main`. An unparseable file is a hard, fail-closed violation — never silently skipped.
Every rule carries a non-vacuity counter: a rule that never fires because nothing in the real tree
exercises its allowlisted path is exactly the kind of gate this repo has been bitten by before.

The existing `check_start_process_fence.py` (T3.4 F1, the `start_process_instance` chokepoint) is
UNCHANGED and separate — it protects a different invariant (audit-before-effect on the ONE
already-fenced call) and its passing history is independent evidence. This gate covers the SEVEN
other effect seams the design's §8 introduces (`fhir`, `whatsapp`, `cibseven.correlate`/`.status`,
`dmn`, `inference`, `population`, `a2a`) plus the import/env/double dimensions §8.1's sibling gate
never touched.

Deviations from the design doc's most literal §8.1/§8.2/§8.3 text, each disclosed in the relevant
allowlist's own comment rather than silently patched over (re-derived against the ACTUAL B1/B2
tree, not the pre-implementation plan):

  * §8.1's base allowlist ("registry + seams/*.py + defining module") does not, on its own, cover
    every legitimate construction site the landed registry actually uses — several composition
    roots construct a raw inner and immediately hand it to a `build_*_seam`/`gate_*` call as the
    documented `inner=` escape hatch (`tool_registry.py`'s own module docstring, the O4 analysis).
    Each such file+name pair is allowlisted individually below, with the reason inline — never by
    widening the base rule.
  * `AioKafkaEventsProducer` is named in the design's §8.1 list, but Kafka event publishing is not
    one of the seven `EFFECT_SEAM_KEYS` the registry governs (it is a best-effort OBSERVABILITY
    producer, same posture as `a2a_composition.py`'s own `_NoopKafkaProducer` fact-emission note) —
    disclosed as an out-of-scope construction site, not silently dropped from the forbidden list.
  * `RealAnsGatewayTransport` is fenced (§8.1) but, per the design's own R-2 finding, is
    DELIBERATELY constructed nowhere in this tree (`resolve_ans_gateway` always returns the
    refusing transport in production) — §8.5 item 1 names it a disclosed exception to
    "constructed at least once" rather than failing on proven-safe dead code.
  * §8.3's "names other than the Protocols/value types" carve-out is written against
    `mcp_cibseven.transport` only; applied literally to `mcp_whatsapp`/`mcp_fhir` (whole-module) it
    is correct (nothing legitimately imports anything else from them), but applied literally to
    `dmn_transport` and `runtime.inference` it would flag every agent graph's Protocol/type-hint
    imports (`DmnTransport`, `evaluate_sync`, `DmnEvaluationError`, `InferenceProvider` itself —
    `InferenceProvider` has no separate Protocol in this codebase and is imported for typing in
    every graph). This gate applies the SAME concrete-provider-only carve-out to all three module
    families; re-derived against the tree (`grep` census below each allowlist) rather than assumed.
  * §8.2's REST-path list is implemented with `/message` — the design text says `/message/`, WITH
    a trailing slash, and this gate deliberately DEVIATES from that one character. The actual CIB
    Seven correlate-message literal is `"/message"` (`transport.py:411`), so the spec's spelling
    matches nothing in the tree: it was an INERT rule, and no other rule compensated — a
    hand-rolled correlate through an INJECTED client (no httpx construction, no fenced class name)
    cleared the whole fence. Between transcribing the spec verbatim and actually fencing the C3
    `correlacao_processo` class the spec wrote the rule FOR, verbatim was the worse fidelity.
    Strictly widening, and it costs no false positive on the real tree: the
    `8.2_rest_path_sanctioned` counter moves 5 -> 7, the two extra hits being sanctioned
    `transport.py` literals the rule always intended to cover (`/messages` contains `/message`,
    so one literal legitimately counts against both patterns).
  * §8.1 matches CLASS NAMES in the AST, so INDIRECTION defeats it, and no name-based fence can
    close that: `_C = InferenceProvider; _C()` (aliased through a local) and
    `getattr(mod, "InferenceProvider")()` (resolved from a string) both construct a fenced class
    without the name ever appearing at a call site. Nothing here claims otherwise, and
    `check_start_process_fence.py` carries the identical limit for the identical reason — an AST
    scan is not a sandbox, and the design accepts this per-design rather than pretending.
    WHAT ACTUALLY COVERS IT is the runtime half of I-11: `tool_registry.effect_seams_gated`, B2's
    §5.5 boot assertion, whose CONSEQUENCE is pinned at all four composition roots
    (`tests/unit/gateway/seams/test_boot_assertion_wiring.py`). An indirected raw construction
    still has to reach a dep map, and that map is `isinstance`-checked against `GatedSeam` — so
    the webhook receiver drops its dispatcher (`/webhook` -> 501), the notifications bridge
    refuses to start, and both runtimes go red on `/readyz`. This gate is therefore the CHEAP,
    EARLY layer that catches the honest mistake in review; it is not, and is not claimed to be,
    the control that stops a determined bypass.

Usage (CI / local)
-------------------
    python3 scripts/ci/check_effect_chokepoint_fence.py
    python3 scripts/ci/check_effect_chokepoint_fence.py --src-dir src/maezo --repo-root .
"""

from __future__ import annotations

import argparse
import ast
import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Final

# =================================================================================================
# §8.1 — raw construction of a raw effect class outside the registry
# =================================================================================================

#: The class list verbatim from design §8.1. `CibSevenServer` is DELIBERATELY ABSENT from this
#: set (R-6: the module used to advertise it as "the enforcement point"; it was deleted) — fencing
#: construction of a class that does not exist would be vacuous, so §8.1 instead asserts it stays
#: ABSENT (see `_cibseven_server_violations` below), never re-added under the old docstring's claim.
FORBIDDEN_CONSTRUCTION_NAMES: Final[frozenset[str]] = frozenset(
    {
        "CibSevenHttpTransport",
        "FreshClientCibSevenTransport",
        "CibSevenDmnTransport",
        "FhirServer",
        "WhatsAppServer",
        "AnthropicInferenceProvider",
        # W-BEDROCK: the second REAL general-zone LLM provider. Fenced on exactly the same
        # grounds as `AnthropicInferenceProvider` — a concrete strategy that opens a billable
        # vendor connection must be reachable only through the registry, never constructed or
        # imported ad hoc by an agent graph (AGENTS.md rule 6: the `runtime.inference` package is
        # the single import point for any LLM SDK).
        "BedrockInferenceProvider",
        "InferenceProvider",
        "AioKafkaEventsProducer",
        # R-113 (GAP-SC-04-a's escalation, resolved 2026-09-06): a second real Kafka producer —
        # the dead-letter shunt cannot reuse `AioKafkaEventsProducer` because the DLQ needs the
        # raw bytes VERBATIM, so it is its own concrete construction site, not a scope exclusion.
        # A fence with an undisclosed exception proves nothing about the next such class.
        "AioKafkaDlqPublisher",
        "FactProducer",
        "DelegationDispatcher",
        "build_dispatcher",
        "RealAnsGatewayTransport",
        "FhirServerReader",
        "WhatsAppServerSender",
        "ValentinaFhirServerReader",
        "LucasWhatsAppServerSender",
    }
)

#: The deleted enforcement-point class (R-6). Never constructed (it does not exist today), and
#: re-adding a `class CibSevenServer` definition ANYWHERE is itself the violation — assert-absent,
#: not fence-a-call, exactly as the B3 brief specifies.
DELETED_CLASS_NAME: Final[str] = "CibSevenServer"

#: Base allowlist (any §8.1 name above, anywhere in the file): the registry itself, and every
#: gated-seam decorator module. A `def`/`class` statement is not a `Call`, so a class's OWN
#: defining module never needs a blanket entry here for that reason alone (same reasoning as
#: `check_start_process_fence.py:28-29`) — the per-name table below exists only for the residual
#: cases where a file legitimately CALLS (not defines) one of these names.
_CONSTRUCTION_BASE_FILES: Final[frozenset[str]] = frozenset({"gateway/tool_registry.py"})
_CONSTRUCTION_BASE_PREFIX: Final[str] = "gateway/seams/"

#: Per-name allowlist ADDITIONS, beyond the base rule. Every entry re-derived by grepping
#: `NAME(` across `src/maezo` this session (see the module docstring's disclosed-deviation list)
#: and reading the call site: each one is either (a) the class's own defining module (a `class`
#: statement inside it never trips this rule anyway, but a same-named internal factory call can),
#: or (b) a composition-root/assembly module that constructs the raw inner and IMMEDIATELY hands
#: it to a sanctioned `build_*_seam`/`gate_*` call — the documented `inner=` escape hatch
#: (`tool_registry.py` module docstring, "THE PER-REQUEST-TENANT KNOT", O3/O4).
CONSTRUCTION_ALLOWLIST_BY_NAME: Final[dict[str, frozenset[str]]] = {
    "CibSevenHttpTransport": frozenset(
        {
            "tools/mcp_cibseven/transport.py",  # own defining module
            # `FreshClientCibSevenTransport._new_transport` builds a FRESH inner per call (the
            # established Protocol-decorator precedent, ALSO allowlisted in the sibling
            # start-process fence for the same "wraps, does not bypass" reasoning).
            "tools/workers/cibseven_engine.py",
            # The package's own re-export point (`from .transport import CibSevenHttpTransport,
            # ...` + `__all__`) — the public-API surface of the class's own package, not a
            # separate construction site.
            "tools/mcp_cibseven/__init__.py",
        }
    ),
    "FreshClientCibSevenTransport": frozenset({"tools/workers/cibseven_engine.py"}),
    "CibSevenDmnTransport": frozenset({"tools/workers/dmn_transport.py"}),
    "FhirServer": frozenset({"tools/mcp_fhir/server.py"}),
    "WhatsAppServer": frozenset(
        {
            "tools/mcp_whatsapp/server.py",  # own defining module
            # THE LIVE agent path (module's own docstring): the raw client becomes the `client=`
            # of `_ScopedWhatsAppSender`, which is per-turn wrapped by `gate_whatsapp` in
            # `dispatch.py` (O4) — the raw object never reaches a graph unwrapped.
            "platform/webhooks/service.py",
        }
    ),
    # D2-02 split (docs/reports/inference-split-plan.md, step 8): the class is now DEFINED in
    # `runtime/inference/providers.py`, not here — but a `class` statement is never a `Call`
    # (comment above), so the defining module needs no entry of its own for that reason alone.
    # This entry is for the CALLING site: `runtime/inference/__init__.py`'s `_build_anthropic`
    # factory constructs `AnthropicInferenceProvider(...)` and hands it straight to the registry
    # (`_build_provider` -> `_PROVIDER_FACTORIES`) — the facade's own composition root, same
    # footing as the four composition-root files below it on `InferenceProvider`.
    "AnthropicInferenceProvider": frozenset({"runtime/inference/__init__.py"}),
    # Same entry, same reason: `_build_bedrock` (the registry factory) calls it from the facade
    # package's `__init__.py`, not from `providers.py` where the class is defined.
    "BedrockInferenceProvider": frozenset({"runtime/inference/__init__.py"}),
    "InferenceProvider": frozenset(
        {
            "runtime/inference/__init__.py",  # own defining module
            # All three: the raw provider is constructed once and immediately threaded into
            # `build_agent_seams(..., inference=...)` / `_build_tool_deps(..., inference=...)`,
            # which wraps it via `build_inference_seam` before any graph receives it (C-A2 closure).
            "platform/webhooks/service.py",
            "runtime/agent_runtime/a2a_composition.py",
            "runtime/agent_runtime/service.py",
        }
    ),
    "AioKafkaEventsProducer": frozenset(
        {
            "platform/integrations/events_kafka_producer.py",  # own defining module
            # DISCLOSED SCOPE EXCLUSION (module docstring): Kafka event publishing is not one of
            # the seven `EFFECT_SEAM_KEYS` this wave's registry governs — best-effort observability,
            # not a catalogued chokepoint operation. Out of THIS wave's scope, not silently dropped.
            "runtime/worker_runtime/service.py",
        }
    ),
    # R-113: `build_dlq_shunt` (the DLQ's own composition-root factory) constructs the raw
    # producer and immediately hands it to `BridgeDlqShunt(publisher=...)` — same file as the
    # class's own defining module, so one entry covers both the definition and the sole
    # construction site (`grep -n "AioKafkaDlqPublisher(" src/maezo` finds exactly one call).
    "AioKafkaDlqPublisher": frozenset({"platform/integrations/notifications_bridge.py"}),
    "FactProducer": frozenset(
        {
            "a2a/dispatcher.py",  # own defining module
            # `facts=` is an OBSERVABILITY producer (a2a_composition.py's own docstring: "Facts …
            # are an OBSERVABILITY surface, not the T-F audit"), immediately passed into
            # `build_dispatcher(...)`, itself immediately wrapped by `build_a2a_seam`.
            "runtime/agent_runtime/a2a_composition.py",
        }
    ),
    "DelegationDispatcher": frozenset(
        {
            "a2a/dispatcher.py",  # own defining module
            # `build_dispatcher`'s own defining module necessarily constructs the class it wraps —
            # the factory-function analogue of "class's own defining module".
            "a2a/assembly.py",
        }
    ),
    "build_dispatcher": frozenset(
        {
            "a2a/assembly.py",  # own defining module (the function itself)
            # `inner=build_dispatcher(...)` is immediately passed to `build_a2a_seam` — never
            # returned or stored unwrapped.
            "runtime/agent_runtime/a2a_composition.py",
        }
    ),
    "RealAnsGatewayTransport": frozenset({"tools/workers/ans_gateway.py"}),
    "FhirServerReader": frozenset(
        {
            # THREE modules each define their own `FhirServerReader` (federated per-agent adapters,
            # ADR-0004) — none of them CALLS another's, and the only external caller of any of them
            # is the registry (base-allowlisted above).
            "agents/rafael/adapters.py",
            "agents/valentina/adapters.py",
            "agents/marina/adapters.py",
        }
    ),
    "WhatsAppServerSender": frozenset(
        {
            "agents/helena/adapters.py",
            "agents/lucas/adapters.py",
        }
    ),
    # Both are IMPORT ALIASES (`... import FhirServerReader as ValentinaFhirServerReader`) used
    # ONLY inside `tool_registry.py` itself — already covered by the base allowlist; no separate
    # defining module exists for the alias name.
    "ValentinaFhirServerReader": frozenset(),
    "LucasWhatsAppServerSender": frozenset(),
}


def _call_func_name(node: ast.Call) -> str | None:
    if isinstance(node.func, ast.Name):
        return node.func.id
    if isinstance(node.func, ast.Attribute):
        return node.func.attr
    return None


# =================================================================================================
# §8.2 — a hand-rolled transport (httpx client construction, or a duplicated raw REST-path literal)
# =================================================================================================

_HTTPX_CLIENT_ATTRS: Final[frozenset[str]] = frozenset({"AsyncClient", "Client"})

#: The five modules design §8.2 names as legitimately owning an `httpx` client TODAY.
_HTTPX_DESIGN_MODULES: Final[frozenset[str]] = frozenset(
    {
        "tools/mcp_cibseven/transport.py",
        "tools/mcp_fhir/server.py",
        "tools/mcp_whatsapp/server.py",
        "tools/workers/dmn_transport.py",
        "tools/workers/ans_gateway.py",
    }
)

#: DISCLOSED ADDITION beyond the design's five: two modules that own an `httpx` client for a
#: categorically DIFFERENT concern than the seven effect seams, re-derived by grep this session.
#: Scoped to the CLIENT-CONSTRUCTION check only — neither is in `REST_PATH_SANCTIONED_MODULES`
#: below, so a duplicated raw effect-path literal in either would still fail the gate.
_HTTPX_CLIENT_ADDITIONAL_MODULES: Final[frozenset[str]] = frozenset(
    {
        # `CibSevenWorkerTransport` — the External-Task WORKER polling/completion transport
        # (`/engine-rest/external-task/*`), a pre-existing mechanism independently audited via
        # `ActionExecutionGateway`/the harness (I-1's own anchor). Not one of the seven seams.
        "tools/workers/harness.py",
        # `EngineDeployClient` — a DEPLOY-TIME ops tool (`POST /deployment/create`, `GET
        # /deployment`) that pushes `spec/processes/**` to the engine at deploy time. Never part of
        # a live agent/worker request path a beneficiary or agent triggers.
        "platform/deploy/engine_deploy.py",
        # ADICOES DE 25/08/2026 — tres modulos da MESMA categoria que `engine_deploy.py` acima:
        # ferramenta de operacao/diagnostico, invocada por gente por linha de comando, nunca
        # alcancada por um pedido de beneficiario ou por um turno de agente. Nenhuma delas
        # produz efeito de negocio: uma configura identidade, duas LEEM para imprimir.
        #
        # A distincao que justifica admitir aqui e recusar no `check_start_process_fence`, onde
        # eu NAO estendi o allowlist no mesmo dia: la' a lista e' de modulos que IMPLEMENTAM a
        # trava (transporte), e entrar nela seria dizer "este arquivo e' o chokepoint", o que
        # seria falso. Aqui a lista e' CATEGORICA — "tem cliente HTTP por outra finalidade que
        # nao os selos de efeito" — e essas tres se encaixam na categoria de verdade. Allowlists
        # diferentes, perguntas diferentes.
        #
        # `EngineIdentityBootstrap` — bootstrap de identidade do motor (apaga usuario `demo`,
        # cria o admin real, concede o grupo de leitura). Roda uma vez por ambiente, como tarefa.
        "platform/engine_bootstrap/bootstrap.py",
        # Coletor de evidencia de uma instancia: LE historico, variaveis e decisoes para
        # imprimir. Desde 25/08 nem inicia mais o processo — entra pela rota do agente.
        "platform/evidence/auth_instance.py",
        # Sweep de cobertura de DMN: avalia tabelas com entradas conhecidas e imprime o que
        # disparou. O caminho REST dele foi trocado pelo transporte sancionado no mesmo commit,
        # entao o cliente que resta aqui e' so' o que o transporte constroi por dentro.
        "platform/evidence/dmn_sweep.py",
    }
)

HTTPX_SANCTIONED_MODULES: Final[frozenset[str]] = _HTTPX_DESIGN_MODULES | _HTTPX_CLIENT_ADDITIONAL_MODULES

# PFSU-01 / ADR-0049 D4-D6: exact construction expressions in exact lexical scopes.
# Neither file joins HTTPX_SANCTIONED_MODULES or REST_PATH_SANCTIONED_MODULES.
# AST shape pins TLS/proxy/redirect controls; runtime endpoint/identity guards have
# separate adversarial tests. Duplicate, nested and adjacent constructions refuse.
_HTTPX_SCOPED_SEAMS: Final[dict[tuple[str, str], str]] = {
    (
        "gateway/human/transport.py",
        "MTLSHumanEngineTransport._request",
    ): "httpx.AsyncClient(verify=self._tls, timeout=self._timeout, trust_env=False, follow_redirects=False)",
    (
        "gateway/human/identity_composition.py",
        "build_human_identity_adapters",
    ): "httpx.AsyncClient(verify=True, trust_env=False, follow_redirects=False, timeout=10.0)",
}
_SECRET_SCOPED_SEAM: Final[tuple[str, str]] = (
    "gateway/human/identity_composition.py",
    "build_human_identity_adapters",
)


#: The effect REST-path fragments from design §8.2. `process-definition/key` stays in the
#: OLD (`check_start_process_fence.py`) gate only, per the brief — deliberately absent here.
#:
#: `/message` DEVIATES from the spec's `/message/` by one character, deliberately — see the module
#: docstring's Deviations block. The slashed form matches NOTHING in this tree (the real literal is
#: `"/message"`, `transport.py:411`), so as written the rule was INERT and a hand-rolled correlate
#: through an injected client cleared the fence. A rule that cannot fire is not fidelity to the
#: spec; it is the C3 `correlacao_processo` class going unfenced by the very rule written for it.
FORBIDDEN_REST_PATH_SUBSTRINGS: Final[frozenset[str]] = frozenset(
    {"/message", "/Patient/", "/messages", "/decision-definition/key"}
)

#: Tighter than the client-construction allowlist on purpose: the two disclosed additions above
#: never need to embed one of these four fragments, so a duplicate appearing there would still be
#: worth flagging rather than silently permitted by a blanket file exemption.
REST_PATH_SANCTIONED_MODULES: Final[frozenset[str]] = _HTTPX_DESIGN_MODULES


# =================================================================================================
# §8.3 — policy-plane imports + env reads outside the gateway
# =================================================================================================

#: Whole-module fence: ANY imported name from these modules, outside the allowlist, is forbidden.
#: Re-derived (module docstring): nothing legitimately imports anything else from either module —
#: no graph catches an mcp_whatsapp/mcp_fhir-specific error type (both catch bare `Exception`,
#: `gateway/seams/_base.py`'s own docstring), so there is no "Protocol/value type" carve-out to make.
_WHOLE_MODULE_FENCED: Final[frozenset[str]] = frozenset(
    {
        "maezo.tools.mcp_whatsapp",
        "maezo.tools.mcp_whatsapp.server",
        "maezo.tools.mcp_fhir",
        "maezo.tools.mcp_fhir.server",
    }
)

_WHOLE_MODULE_SANCTIONED_FILES: Final[frozenset[str]] = frozenset(
    {
        "gateway/tool_registry.py",
        "tools/mcp_whatsapp/__init__.py",
        "tools/mcp_fhir/__init__.py",
        "platform/webhooks/service.py",
        "platform/webhooks/whatsapp/dispatch.py",
        "agents/helena/adapters.py",
        "agents/lucas/adapters.py",
        "agents/rafael/adapters.py",
        "agents/valentina/adapters.py",
        "agents/marina/adapters.py",
    }
)

#: Concrete-provider-only fence: `maezo.tools.mcp_cibseven.transport` and
#: `maezo.tools.workers.dmn_transport` export Protocols/value types (`CibSevenTransport`,
#: `ProcessInstance`, `DmnTransport`, `DmnEvaluationError`, `evaluate_sync`, …) that every agent
#: graph legitimately imports to catch/type-hint against (re-derived by grep this session — see
#: the module docstring's disclosed-deviation list). Only the CONCRETE transport classes are
#: fenced, matching §8.1's own name set for the same two modules. `maezo.runtime.inference` gets
#: the SAME carve-out for its own reason: `InferenceProvider` (the façade) has no separate Protocol
#: in this codebase and is imported for typing/`cast()` in every graph — only its concrete
#: `BaseInferenceProvider` strategy subclasses are fenced.
_CONCRETE_PROVIDER_IMPORT_MODULES: Final[dict[str, frozenset[str]]] = {
    "maezo.tools.mcp_cibseven.transport": frozenset(
        {"CibSevenHttpTransport", "FreshClientCibSevenTransport"}
    ),
    "maezo.tools.workers.dmn_transport": frozenset({"CibSevenDmnTransport"}),
    "maezo.runtime.inference": frozenset(
        {
            "AnthropicInferenceProvider",
            "BedrockInferenceProvider",  # W-BEDROCK, same footing as the 1P provider above
            "NoopInferenceProvider",
            "PhiZoneMockProvider",
        }
    ),
}

#: The three chokepoint-relevant env var names verbatim from design §8.3 (A-6).
FORBIDDEN_ENV_VAR_NAMES: Final[frozenset[str]] = frozenset(
    {
        "MAEZO_SPEC_DIR",
        "MAEZO_ACTION_APPROVALS_PATH",
        "MAEZO_ACTION_APPROVALS_ALLOW_OVERRIDE_ENFORCEMENT",
    }
)

#: B1 fact (do not fence): the runtime-mode discriminator reads BOTH spellings inside the gateway
#: — explicitly NOT part of the three names above, and not fenced by this rule at all.
RUNTIME_MODE_ENV_VAR_NAMES: Final[frozenset[str]] = frozenset({"RUNTIME_MODE", "AGENT_RUNTIME_MODE"})

_ENV_READ_SANCTIONED_PREFIX: Final[str] = "gateway/"
_ENV_READ_SANCTIONED_FILES: Final[frozenset[str]] = frozenset({"agents/__init__.py"})

_OS_ENVIRON_READ_ATTRS: Final[frozenset[str]] = frozenset({"get"})


# =================================================================================================
# §8.4 — test doubles reachable from a production composition root
# =================================================================================================

_COMPOSITION_ROOT_PREFIXES: Final[tuple[str, ...]] = ("runtime/agent_runtime/", "runtime/worker_runtime/")
_COMPOSITION_ROOT_FILES: Final[frozenset[str]] = frozenset(
    {
        "platform/webhooks/service.py",
        "platform/integrations/notifications_bridge.py",
        "gateway/tool_registry.py",
        # Onda 3 / Train C leg 2. `a2a/outbox_relay.py` is a runnable composition root in the same
        # sense `notifications_bridge.py` is — `build_relay` constructs the production
        # `PostgresFactOutbox` + `AioKafkaFactPublisher` and `main()` is a process entry point —
        # so §8.4 must govern it. It declares NO exception below and needs none: that module
        # deliberately defines no test double (the in-memory publisher its unit tests drive lives
        # in `tests/unit/a2a/`, which this gate never scans). Adding the file EXTENDS coverage; it
        # widens no pattern and grants no new allowance.
        "a2a/outbox_relay.py",
    }
)

#: Declared exceptions BY (file, name) pair — never by pattern (design §8.4). Each entry is a real
#: test double DEFINED (not imported) inside its composition-root module, re-derived by grep this
#: session for the whole `_COMPOSITION_ROOT_PREFIXES`/`_COMPOSITION_ROOT_FILES` set.
DECLARED_TEST_DOUBLE_EXCEPTIONS: Final[frozenset[tuple[str, str]]] = frozenset(
    {
        # `a2a_composition.py:192-201` — the documented "no production Kafka producer exists
        # anywhere in this platform yet" default for agent-runtime FACTS (an observability
        # surface, not the T-F audit, which rides the real `PostgresAuditSink`).
        ("runtime/agent_runtime/a2a_composition.py", "_NoopKafkaProducer"),
        # `notifications_bridge.py:242-270` — own docstring: "In-memory `BridgeKafkaConsumer`
        # double for unit tests. NEVER imported by production code" (same "never imported by
        # production" discipline as `FakeKafkaPublisher`/`FakeCibSevenTransport` elsewhere).
        ("platform/integrations/notifications_bridge.py", "FakeBridgeKafkaConsumer"),
    }
)


def _is_test_double_name(name: str) -> bool:
    # `_NoopKafkaProducer` is the live, REAL example (a2a_composition.py) — a single leading
    # underscore (Python's "module-private" convention) must not hide it from `Noop*`/`Fake*`.
    #
    # `Stub*` is DELIBERATELY ABSENT, as a decision rather than an oversight: design §8.4 names
    # exactly `Fake*`/`*Mock*`/`Noop*`, and this gate does not widen a spec'd pattern set on its
    # own authority. Census this session — `class Fake*` x22, `class Noop*` x1, `class Stub*` x1,
    # and the single `Stub` (`tests/unit/tools/workers/test_worker_registry.py:23::StubWorker`)
    # lives in `tests/`, which this gate never scans. So adding it would fence zero real names
    # today. The residual, stated plainly: a NEW production double named `Stub*` would not be
    # caught here. It is still caught at runtime — a stub handed to a composition root is not a
    # `GatedSeam`, so `effect_seams_gated` refuses it at boot (see the Deviations block's
    # layered-defense bullet). Widening §8.4 to `Stub*` is a one-line spec amendment for a human.
    unprefixed = name.lstrip("_")
    return unprefixed.startswith("Fake") or unprefixed.startswith("Noop") or "Mock" in name


# =================================================================================================
# Violations + non-vacuity bookkeeping
# =================================================================================================


@dataclass(frozen=True)
class Violation:
    """One offending construction/import/literal/double found outside its rule's allowlist."""

    path: Path
    lineno: int
    rule: str
    detail: str

    def render(self) -> str:
        return f"{self.path}:{self.lineno}: [{self.rule}] {self.detail}"


@dataclass
class _ModuleScan:
    """Raw, allowlist-agnostic findings for ONE file — `scan_tree` applies per-rule allowlists."""

    unparseable: str | None = None
    # §8.1: (name, lineno)
    constructions: list[tuple[str, int]] = field(default_factory=list)
    cibseven_server_defined: list[int] = field(default_factory=list)
    # §8.2
    httpx_clients: list[tuple[str, int]] = field(default_factory=list)
    rest_path_literals: list[tuple[str, int]] = field(default_factory=list)
    scoped_httpx: dict[int, tuple[str, str]] = field(default_factory=dict)
    secret_reads: list[tuple[int, str, str]] = field(default_factory=list)
    # §8.3
    whole_module_imports: list[tuple[str, str, int]] = field(default_factory=list)  # (module, name, lineno)
    concrete_provider_imports: list[tuple[str, int]] = field(default_factory=list)
    env_reads: list[tuple[str, int]] = field(default_factory=list)
    # §8.4
    test_double_imports: list[tuple[str, int]] = field(default_factory=list)
    test_double_defs: list[tuple[str, int]] = field(default_factory=list)


def _docstring_constant_ids(tree: ast.AST) -> set[int]:
    """`id()` of every `ast.Constant` that IS a module/class/function docstring (its body's first
    statement, an `Expr` wrapping a string `Constant`) — never a REST-path literal, only prose.
    Mirrors `ast.get_docstring`'s own definition of "docstring", applied tree-wide."""
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = node.body
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                ids.add(id(body[0].value))
    return ids


def _string_alias_bindings(tree: ast.AST) -> dict[str, str]:
    """`NAME = "literal"` module/function-level bindings, anywhere in the file (best-effort).

    Powers the env-var-read check's symbolic-constant resolution: this tree's actual reads are
    `os.environ.get(MAEZO_SPEC_DIR_ENV)`, not a literal string — the alias has to be resolved
    within the SAME file to catch it (or a hypothetical future direct-literal read, which the
    plain `ast.Constant` arm below still catches with no alias involved).
    """
    bindings: dict[str, str] = {}
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    bindings[target.id] = node.value.value
        elif (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, str)
        ):
            bindings[node.target.id] = node.value.value
    return bindings


def _env_arg_names(call: ast.Call, aliases: dict[str, str]) -> list[str]:
    """The string(s) an `os.environ.get(...)`/`os.getenv(...)`/`os.environ[...]`-shaped call reads,
    resolving a same-file symbolic alias when the argument is a bare `Name`."""
    names: list[str] = []
    for arg in call.args[:1]:
        if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
            names.append(arg.value)
        elif isinstance(arg, ast.Name) and arg.id in aliases:
            names.append(aliases[arg.id])
    return names


def scan_module(path: Path) -> _ModuleScan:
    """AST-scan ONE file for every §8.1-§8.4 raw pattern. Unparseable => fail-closed marker."""
    try:
        source = path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(path))
    except (SyntaxError, OSError, UnicodeDecodeError) as exc:
        return _ModuleScan(
            unparseable=f"could not parse ({exc}) — fail-closed, cannot prove absence of a bypass"
        )

    scan = _ModuleScan()
    aliases = _string_alias_bindings(tree)
    docstring_ids = _docstring_constant_ids(tree)
    # Resolve import aliases before scanning calls (ast.walk ordering is not source ordering).
    imported_from_httpx: dict[str, str] = {}
    httpx_modules = {"httpx"}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == "httpx":
            imported_from_httpx.update(
                (a.asname or a.name, a.name) for a in node.names if a.name in _HTTPX_CLIENT_ATTRS
            )
        elif isinstance(node, ast.Import):
            httpx_modules.update(a.asname or a.name for a in node.names if a.name == "httpx")
    # Conservative same-file alias propagation. Dynamic runtime imports remain outside
    # this static fence's claim; simple assignments must not defeat a new scoped seam.
    changed = True
    while changed:
        changed = False
        value: ast.expr | None
        for binding in ast.walk(tree):
            if isinstance(binding, ast.Assign):
                targets, value = binding.targets, binding.value
            elif isinstance(binding, ast.AnnAssign):
                targets, value = [binding.target], binding.value
            else:
                continue
            name = None
            if isinstance(value, ast.Name):
                name = imported_from_httpx.get(value.id)
            elif (
                isinstance(value, ast.Attribute)
                and value.attr in _HTTPX_CLIENT_ATTRS
                and isinstance(value.value, ast.Name)
                and value.value.id in httpx_modules
            ):
                name = value.attr
            for target in targets:
                if isinstance(target, ast.Name) and name and target.id not in imported_from_httpx:
                    imported_from_httpx[target.id] = name
                    changed = True

    parents = {id(child): node for node in ast.walk(tree) for child in ast.iter_child_nodes(node)}

    def scope_of(node: ast.AST) -> str:
        parts = []
        while id(node) in parents:
            child, node = node, parents[id(node)]
            if isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                if child not in node.body:
                    parts.append("<definition>")
                parts.append(node.name)
            elif isinstance(node, ast.Lambda | ast.ListComp | ast.SetComp | ast.DictComp | ast.GeneratorExp):
                parts.append("<nested>")
        return ".".join(reversed(parts))

    for node in ast.walk(tree):
        # -- §8.1: construction calls -----------------------------------------------------------
        if isinstance(node, ast.Call):
            name = _call_func_name(node)
            if name is not None and name in FORBIDDEN_CONSTRUCTION_NAMES:
                scan.constructions.append((name, node.lineno))

            # -- §8.2a: httpx client construction --------------------------------------------
            func = node.func
            if (
                isinstance(func, ast.Attribute)
                and func.attr in _HTTPX_CLIENT_ATTRS
                and isinstance(func.value, ast.Name)
                and func.value.id in httpx_modules
            ):
                scan.httpx_clients.append((func.attr, node.lineno))
            elif isinstance(func, ast.Name) and func.id in imported_from_httpx:
                scan.httpx_clients.append((imported_from_httpx[func.id], node.lineno))

            if scan.httpx_clients and scan.httpx_clients[-1][1] == node.lineno:
                scan.scoped_httpx[node.lineno] = (scope_of(node), ast.dump(node))
            if (
                isinstance(func, ast.Name)
                and func.id == "getattr"
                and len(node.args) >= 2
                and isinstance(node.args[1], ast.Constant)
                and node.args[1].value == "get_secret_value"
            ):
                scan.secret_reads.append((node.lineno, scope_of(node), "dynamic"))

            # -- §8.3: os.environ / os.getenv reads -------------------------------------------
            is_environ_read = (
                isinstance(func, ast.Attribute)
                and func.attr in _OS_ENVIRON_READ_ATTRS
                and isinstance(func.value, ast.Attribute)
                and func.value.attr == "environ"
                and isinstance(func.value.value, ast.Name)
                and func.value.value.id == "os"
            ) or (
                isinstance(func, ast.Attribute)
                and func.attr == "getenv"
                and isinstance(func.value, ast.Name)
                and func.value.id == "os"
            )
            if is_environ_read:
                for read_name in _env_arg_names(node, aliases):
                    if read_name in FORBIDDEN_ENV_VAR_NAMES:
                        scan.env_reads.append((read_name, node.lineno))

        elif isinstance(node, ast.Attribute) and node.attr == "get_secret_value":
            scan.secret_reads.append((node.lineno, scope_of(node), ast.dump(node)))

        elif isinstance(node, ast.Subscript):
            # os.environ["NAME"] / os.environ[ALIAS]
            value = node.value
            if (
                isinstance(value, ast.Attribute)
                and value.attr == "environ"
                and isinstance(value.value, ast.Name)
                and value.value.id == "os"
            ):
                sl = node.slice
                if (
                    isinstance(sl, ast.Constant)
                    and isinstance(sl.value, str)
                    and sl.value in FORBIDDEN_ENV_VAR_NAMES
                ):
                    scan.env_reads.append((sl.value, node.lineno))
                elif isinstance(sl, ast.Name) and aliases.get(sl.id) in FORBIDDEN_ENV_VAR_NAMES:
                    scan.env_reads.append((aliases[sl.id], node.lineno))

        # -- §8.2b: raw REST-path literal (never a docstring — prose is not a bypass) -----------
        elif isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstring_ids:
            for substring in FORBIDDEN_REST_PATH_SUBSTRINGS:
                if substring in node.value:
                    scan.rest_path_literals.append((substring, node.lineno))

        # -- §8.1 assert-absent: the deleted CibSevenServer class ------------------------------
        elif isinstance(node, ast.ClassDef):
            if node.name == DELETED_CLASS_NAME:
                scan.cibseven_server_defined.append(node.lineno)
            if _is_test_double_name(node.name):
                scan.test_double_defs.append((node.name, node.lineno))

        # -- §8.3 imports + §8.4 test-double imports -------------------------------------------
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if module in _WHOLE_MODULE_FENCED:
                for alias in node.names:
                    scan.whole_module_imports.append((module, alias.name, node.lineno))
            if module in _CONCRETE_PROVIDER_IMPORT_MODULES:
                fenced_names = _CONCRETE_PROVIDER_IMPORT_MODULES[module]
                for alias in node.names:
                    if alias.name in fenced_names:
                        scan.concrete_provider_imports.append((alias.name, node.lineno))
            for alias in node.names:
                imported_name = alias.asname or alias.name
                if _is_test_double_name(imported_name):
                    scan.test_double_imports.append((imported_name, node.lineno))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                imported_name = alias.asname or alias.name
                if _is_test_double_name(imported_name):
                    scan.test_double_imports.append((imported_name, node.lineno))

    return scan


# =================================================================================================
# scan_tree — applies each rule's allowlist to the raw per-file findings
# =================================================================================================


@dataclass(frozen=True)
class GateResult:
    """Outcome of the repo-wide effect-chokepoint fence gate."""

    ok: bool
    violations: tuple[str, ...] = ()
    scanned_files: int = 0
    #: Non-vacuity proof, per rule: how many SANCTIONED (allowed) occurrences were found. A rule
    #: whose counter stays 0 found nothing to exempt — meaning either the rule is dead, or the
    #: pattern genuinely never occurs, and the accompanying unit test must tell the two apart.
    counters: dict[str, int] = field(default_factory=dict)

    def render(self) -> str:
        status = "PASS" if self.ok else "FAIL"
        lines = [f"[effect-chokepoint-fence] {status} ({self.scanned_files} files scanned)"]
        if self.violations:
            lines.append(f"  violations ({len(self.violations)}):")
            lines.extend(f"    - {v}" for v in self.violations)
        lines.append("  non-vacuity counters:")
        for key in sorted(self.counters):
            lines.append(f"    {key} = {self.counters[key]}")
        return "\n".join(lines)


def _is_construction_sanctioned(rel: str, name: str) -> bool:
    if rel in _CONSTRUCTION_BASE_FILES or rel.startswith(_CONSTRUCTION_BASE_PREFIX):
        return True
    return rel in CONSTRUCTION_ALLOWLIST_BY_NAME.get(name, frozenset())


def _is_httpx_client_sanctioned(rel: str) -> bool:
    return rel in HTTPX_SANCTIONED_MODULES


def _is_rest_path_sanctioned(rel: str) -> bool:
    return rel in REST_PATH_SANCTIONED_MODULES


def _is_whole_module_import_sanctioned(rel: str) -> bool:
    return (
        rel in _WHOLE_MODULE_SANCTIONED_FILES
        or rel in _CONSTRUCTION_BASE_FILES
        or rel.startswith(_CONSTRUCTION_BASE_PREFIX)
    )


def _is_concrete_provider_import_sanctioned(rel: str, name: str) -> bool:
    return _is_construction_sanctioned(rel, name)


def _is_env_read_sanctioned(rel: str) -> bool:
    return rel.startswith(_ENV_READ_SANCTIONED_PREFIX) or rel in _ENV_READ_SANCTIONED_FILES


def _is_composition_root(rel: str) -> bool:
    return rel.startswith(_COMPOSITION_ROOT_PREFIXES) or rel in _COMPOSITION_ROOT_FILES


def scan_tree(src_dir: Path) -> GateResult:
    """Scan every `*.py` under `src_dir` for §8.1-§8.4 violations. Pure — no process exit."""
    violations: list[Violation] = []
    scanned = 0
    counters: Counter[str] = Counter()

    for path in sorted(src_dir.rglob("*.py")):
        rel = path.relative_to(src_dir).as_posix()
        scanned += 1
        result = scan_module(path)

        if result.unparseable is not None:
            violations.append(Violation(path, 0, "unparseable", result.unparseable))
            continue

        # §8.1 — construction -----------------------------------------------------------------
        for name, lineno in result.constructions:
            if _is_construction_sanctioned(rel, name):
                counters["8.1_construction_sanctioned"] += 1
            else:
                violations.append(
                    Violation(
                        path,
                        lineno,
                        "8.1",
                        f"raw construction of {name}(...) outside the registry "
                        "(maezo.gateway.tool_registry) bypasses the effect chokepoint — route "
                        "through a build_*_seam()/gate_*() call instead",
                    )
                )
        for lineno in result.cibseven_server_defined:
            violations.append(
                Violation(
                    path,
                    lineno,
                    "8.1-absent",
                    f"class {DELETED_CLASS_NAME} redefined — this class was DELETED (R-6): its "
                    "docstring used to advertise it as the enforcement point, which is now the "
                    "registry + gated seams. Re-adding it re-opens the stale-documentation defect.",
                )
            )

        # §8.2 — hand-rolled transport -----------------------------------------------------------
        scope_counts = Counter(result.scoped_httpx[line][0] for _, line in result.httpx_clients)
        for attr, lineno in result.httpx_clients:
            scope, shape = result.scoped_httpx[lineno]
            expected = _HTTPX_SCOPED_SEAMS.get((rel, scope))
            scoped = (
                expected is not None
                and scope_counts[scope] == 1
                and shape == ast.dump(ast.parse(expected, mode="eval").body)
            )
            if scoped:
                counters["8.2_httpx_scoped_seam_sanctioned"] += 1
            if _is_httpx_client_sanctioned(rel) or scoped:
                counters["8.2_httpx_client_sanctioned"] += 1
            else:
                violations.append(
                    Violation(
                        path,
                        lineno,
                        "8.2",
                        f"raw httpx.{attr}(...) construction outside the sanctioned transport "
                        "modules — a hand-rolled HTTP client bypasses the gated seam it should "
                        "route through",
                    )
                )
        for substring, lineno in result.rest_path_literals:
            if _is_rest_path_sanctioned(rel):
                counters["8.2_rest_path_sanctioned"] += 1
            else:
                violations.append(
                    Violation(
                        path,
                        lineno,
                        "8.2",
                        f"raw effect REST-path literal {substring!r} duplicated outside the "
                        "sanctioned transport module — bypasses the gated seam's own client",
                    )
                )

        # SecretStr extraction is owned by one credential composition function only.
        for lineno, scope, shape in result.secret_reads:
            if (
                (rel, scope) == _SECRET_SCOPED_SEAM
                and len(result.secret_reads) == 1
                and shape == ast.dump(ast.parse("config.database_url.get_secret_value", mode="eval").body)
            ):
                counters["8.3_secret_scoped_seam_sanctioned"] += 1
            else:
                violations.append(
                    Violation(
                        path,
                        lineno,
                        "8.3-secret",
                        "credential extraction outside exact gateway identity composition seam",
                    )
                )

        # §8.3 — policy-plane imports + env reads -------------------------------------------------
        for module, name, lineno in result.whole_module_imports:
            if _is_whole_module_import_sanctioned(rel):
                counters["8.3_whole_module_import_sanctioned"] += 1
            else:
                violations.append(
                    Violation(
                        path,
                        lineno,
                        "8.3",
                        f"import of {name!r} from {module} outside the registry/adapters — a "
                        "policy-plane import that can construct an ungated transport",
                    )
                )
        for name, lineno in result.concrete_provider_imports:
            if _is_concrete_provider_import_sanctioned(rel, name):
                counters["8.3_concrete_provider_import_sanctioned"] += 1
            else:
                violations.append(
                    Violation(
                        path,
                        lineno,
                        "8.3",
                        f"import of concrete provider {name!r} outside the registry/adapters — "
                        "route through the registry instead of importing the transport directly",
                    )
                )
        for name, lineno in result.env_reads:
            if _is_env_read_sanctioned(rel):
                counters["8.3_env_read_sanctioned"] += 1
            else:
                violations.append(
                    Violation(
                        path,
                        lineno,
                        "8.3",
                        f"os.environ read of {name!r} outside maezo/gateway/ (and "
                        "agents/__init__.py for MAEZO_SPEC_DIR) — a policy-source bypass surface "
                        "(A-6)",
                    )
                )

        # §8.4 — test doubles reachable from a composition root ----------------------------------
        if _is_composition_root(rel):
            for name, lineno in result.test_double_imports:
                if (rel, name) in DECLARED_TEST_DOUBLE_EXCEPTIONS:
                    counters["8.4_declared_exception"] += 1
                else:
                    violations.append(
                        Violation(
                            path,
                            lineno,
                            "8.4",
                            f"import of test double {name!r} inside a production composition "
                            "root — reachable from live traffic unless explicitly declared",
                        )
                    )
            for name, lineno in result.test_double_defs:
                if (rel, name) in DECLARED_TEST_DOUBLE_EXCEPTIONS:
                    counters["8.4_declared_exception"] += 1
                else:
                    violations.append(
                        Violation(
                            path,
                            lineno,
                            "8.4",
                            f"test double {name!r} DEFINED inside a production composition root "
                            "— reachable from live traffic unless explicitly declared",
                        )
                    )

    return GateResult(
        ok=not violations,
        violations=tuple(v.render() for v in violations),
        scanned_files=scanned,
        counters=dict(counters),
    )


# =================================================================================================
# §8.5 — non-vacuity and completeness assertions
# =================================================================================================

_LADDER_TEST_RELATIVE_PATH: Final[str] = "tests/unit/gateway/test_effect_enforcement.py"
_LADDER_CONSTANT_NAME: Final[str] = "_DESIGN_6_1_LADDER"
_LADDER_TEST_FUNCTION_NAME: Final[str] = (
    "test_every_class_carries_the_exact_rung_and_denial_shape_design_6_1_assigns"
)

#: §8.5 item 1's disclosed exception: `RealAnsGatewayTransport` is fenced by §8.1 but, per design
#: R-2, DELIBERATELY constructed nowhere in this tree — `resolve_ans_gateway` always resolves the
#: refusing transport in production, so "zero construction sites" is the PROVEN-SAFE state, not a
#: gap. Named, not silently excluded from the forbidden-construction list itself (it stays fenced).
_ITEM1_DISCLOSED_EXCEPTIONS: Final[frozenset[str]] = frozenset({"RealAnsGatewayTransport"})

#: §8.5 item 2's ONE disclosed exception, verbatim from `effect_classes.py`'s own "KNOWN GAP"
#: docstring: `mcp-memory.read_write` is declared by every agent but has no ratified action class
#: (a human decision, not an agent inference) — recorded, never silently catalogued or skipped.
_ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS: Final[frozenset[str]] = frozenset({"mcp-memory.read_write"})

#: §8.5 item 3: how short a `choked: false` reason may be before it counts as "undocumented".
_MIN_CHOKED_FALSE_REASON_LENGTH: Final[int] = 15


def _find_ladder_class_names(tree: ast.AST) -> list[str] | None:
    """Extract the class-name column of `_DESIGN_6_1_LADDER` (a tuple of 3-tuples) via AST —
    never imports the test module (pytest need not be on the fence's own import path)."""
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == _LADDER_CONSTANT_NAME for t in node.targets)
        ) or (
            isinstance(node, ast.AnnAssign)
            and isinstance(node.target, ast.Name)
            and node.target.id == _LADDER_CONSTANT_NAME
        ):
            value = node.value
            if not isinstance(value, ast.Tuple):
                return None
            names: list[str] = []
            for element in value.elts:
                if (
                    isinstance(element, ast.Tuple)
                    and element.elts
                    and isinstance(element.elts[0], ast.Constant)
                ):
                    first = element.elts[0].value
                    if isinstance(first, str):
                        names.append(first)
            return names
    return None


def _has_function_named(tree: ast.AST, name: str) -> bool:
    return any(isinstance(node, ast.FunctionDef) and node.name == name for node in ast.walk(tree))


def check_completeness(src_dir: Path, repo_root: Path) -> tuple[list[str], dict[str, int]]:
    """§8.5's four non-AST assertions. Returns `(violations, counters)`.

    Needs the closed catalogue (a pure-data import, `maezo.gateway.effect_classes` — "Nothing here
    performs I/O, imports a transport, or decides anything", its own module docstring) plus two
    YAML reads and one AST read of a test file. Deliberately does NOT import
    `maezo.gateway.action_execution` or any transport module — kept as dependency-light as the
    sibling fences' "stdlib-only, no maezo import" precedent allows while still proving what §8.5
    actually requires.
    """
    violations: list[str] = []
    counters: dict[str, int] = {}

    try:
        import yaml  # kept local so a missing dep only breaks §8.5, not the AST half
    except ImportError as exc:  # pragma: no cover - dev-dependency, always present in CI
        return [f"§8.5: PyYAML unavailable ({exc}) — cannot verify manifest completeness"], counters

    sys.path.insert(0, str(repo_root / "src"))
    try:
        from maezo.gateway import effect_classes
    except Exception as exc:  # an unimportable catalogue is itself a fail-closed finding
        return [f"§8.5: could not import maezo.gateway.effect_classes ({exc})"], counters

    # -- item 1: the registry constructs every §8.1 class at least once (one disclosed exception) -
    registry_path = src_dir / "gateway" / "tool_registry.py"
    try:
        registry_tree = ast.parse(registry_path.read_text(encoding="utf-8"), filename=str(registry_path))
    except (SyntaxError, OSError) as exc:
        violations.append(f"§8.5 item 1: could not parse {registry_path} ({exc})")
        registry_tree = None
    if registry_tree is not None:
        # "Constructed at least once" is proven over the UNION of every allowlisted construction
        # site for that name (registry + seams + its documented composition-root residuals) —
        # `scan_tree`'s own non-vacuity counters already prove those sites are real and exercised;
        # this item additionally proves NO name in the forbidden list is entirely dead.
        constructed_names: set[str] = set()
        for path in sorted(src_dir.rglob("*.py")):
            rel = path.relative_to(src_dir).as_posix()
            scan = scan_module(path)
            for name, _lineno in scan.constructions:
                if _is_construction_sanctioned(rel, name):
                    constructed_names.add(name)
        missing = FORBIDDEN_CONSTRUCTION_NAMES - constructed_names - _ITEM1_DISCLOSED_EXCEPTIONS
        counters["8.5_item1_classes_constructed"] = len(constructed_names)
        for name in sorted(missing):
            violations.append(
                f"§8.5 item 1: {name} is fenced by §8.1 but is constructed NOWHERE in the "
                "sanctioned allowlist — either it is dead code (remove it from the forbidden "
                "list with a disclosed reason) or the registry lost its construction site"
            )

    # -- item 2: every declared mcp-<server>.<action> tool id resolves to a catalogued operation --
    declared_tool_ids: set[str] = set()
    for agent_yaml in sorted((repo_root / "spec" / "agents").glob("*/agent.yaml")):
        data = yaml.safe_load(agent_yaml.read_text(encoding="utf-8")) or {}
        declared_tool_ids.update(data.get("tools") or [])
    counters["8.5_item2_tool_ids_declared"] = len(declared_tool_ids)
    uncatalogued = (
        declared_tool_ids - effect_classes.CATALOGUED_TOOL_IDS - _ITEM2_DISCLOSED_TOOL_ID_EXCEPTIONS
    )
    for tool_id in sorted(uncatalogued):
        violations.append(
            f"§8.5 item 2: agent.yaml declares tool id {tool_id!r}, which resolves to no "
            "operation in effect_classes.OPERATIONS and is not a disclosed exception "
            "(the ONLY disclosed exception is mcp-memory.read_write)"
        )
    # The disclosed exception must actually be exercised by the real tree — otherwise its
    # "disclosed, not silently catalogued" claim is unverifiable.
    if "mcp-memory.read_write" not in declared_tool_ids:
        violations.append(
            "§8.5 item 2: mcp-memory.read_write is declared as the disclosed tool-id exception "
            "but no agent.yaml actually declares it — the exception has gone stale"
        )

    # -- item 3: catalogue <-> manifest round-trip + choked:false must be documented ---------------
    manifest_path = repo_root / "spec" / "policies" / "autonomy" / "action-approvals.yaml"
    manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8")) or {}
    acoes = manifest.get("acoes") or {}
    mapeamento_acoes = manifest.get("mapeamento_acoes") or {}
    mapeamento_topicos = manifest.get("mapeamento_topicos") or {}

    catalogue_classes = set(effect_classes.ACTION_CLASSES)
    manifest_classes = set(acoes)
    for missing_class in sorted(catalogue_classes - manifest_classes):
        violations.append(
            f"§8.5 item 3: action class {missing_class!r} is catalogued in effect_classes.py but "
            "absent from action-approvals.yaml acoes — a class the loader has never heard of can "
            "never be approved"
        )
    for extra_class in sorted(manifest_classes - catalogue_classes):
        violations.append(
            f"§8.5 item 3: action-approvals.yaml declares class {extra_class!r}, which is not in "
            "effect_classes.ACTION_CLASSES — manifest/catalogue drift"
        )

    expected_action_map = {
        effect_classes.agent_action_ref(op): spec.action_class
        for op, spec in effect_classes.OPERATIONS.items()
    }
    if mapeamento_acoes != expected_action_map:
        violations.append(
            "§8.5 item 3: action-approvals.yaml mapeamento_acoes does not exactly match "
            "effect_classes.OPERATIONS' operation->class routing — round-trip drift between the "
            "Python catalogue and the YAML manifest"
        )
    for topic, class_name in mapeamento_topicos.items():
        if class_name not in manifest_classes:
            violations.append(
                f"§8.5 item 3: mapeamento_topicos entry {topic!r} routes to undeclared class {class_name!r}"
            )

    documented_false = 0
    undocumented_false = 0
    choked_true = 0
    for class_name, entry in acoes.items():
        for surface in entry.get("superficies") or []:
            choked = surface.get("choked")
            if choked is True:
                choked_true += 1
            elif choked is False:
                detalhe = surface.get("detalhe")
                if isinstance(detalhe, str) and len(detalhe.strip()) >= _MIN_CHOKED_FALSE_REASON_LENGTH:
                    documented_false += 1
                else:
                    undocumented_false += 1
                    violations.append(
                        f"§8.5 item 3: {class_name!r} surface {surface.get('referencia')!r} is "
                        "choked:false with no documented reason (detalhe) — an undocumented "
                        "choked:false is indistinguishable from an omission"
                    )
            else:
                violations.append(
                    f"§8.5 item 3: {class_name!r} surface {surface.get('referencia')!r} has a "
                    f"non-boolean choked value {choked!r}"
                )
    counters["8.5_item3_choked_true"] = choked_true
    counters["8.5_item3_choked_false_documented"] = documented_false
    counters["8.5_item3_choked_false_undocumented"] = undocumented_false

    # -- item 4: every class has a declared denial shape AND a passing mutation-style test --------
    ladder_path = repo_root / _LADDER_TEST_RELATIVE_PATH
    if not ladder_path.is_file():
        violations.append(f"§8.5 item 4: ladder test file missing: {_LADDER_TEST_RELATIVE_PATH}")
    else:
        try:
            ladder_tree = ast.parse(ladder_path.read_text(encoding="utf-8"), filename=str(ladder_path))
        except SyntaxError as exc:
            violations.append(f"§8.5 item 4: could not parse {_LADDER_TEST_RELATIVE_PATH} ({exc})")
            ladder_tree = None
        if ladder_tree is not None:
            for name, spec in effect_classes.ACTION_CLASSES.items():
                if spec.denial_shape not in effect_classes.DENIAL_SHAPES:
                    violations.append(f"§8.5 item 4: class {name!r} declares an unbounded denial shape")
            ladder_names = _find_ladder_class_names(ladder_tree)
            if ladder_names is None:
                violations.append(
                    f"§8.5 item 4: could not find {_LADDER_CONSTANT_NAME} in {_LADDER_TEST_RELATIVE_PATH}"
                )
            else:
                counters["8.5_item4_ladder_classes"] = len(ladder_names)
                missing_from_ladder = catalogue_classes - set(ladder_names)
                extra_in_ladder = set(ladder_names) - catalogue_classes
                for name in sorted(missing_from_ladder):
                    violations.append(
                        f"§8.5 item 4: class {name!r} has no row in {_LADDER_CONSTANT_NAME} — no "
                        "mutation-style test references its declared denial shape"
                    )
                for name in sorted(extra_in_ladder):
                    violations.append(
                        f"§8.5 item 4: {_LADDER_CONSTANT_NAME} declares row {name!r}, which is not "
                        "a catalogued action class — stale/typo'd ladder entry"
                    )
            if not _has_function_named(ladder_tree, _LADDER_TEST_FUNCTION_NAME):
                violations.append(
                    f"§8.5 item 4: {_LADDER_TEST_RELATIVE_PATH} no longer defines "
                    f"{_LADDER_TEST_FUNCTION_NAME}() — the ladder table is declared but unproven"
                )

    return violations, counters


# =================================================================================================
# main
# =================================================================================================


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_effect_chokepoint_fence",
        description=(
            "CI gate (Onda 1 design §8): every raw effect-class construction, hand-rolled "
            "transport, policy-plane import/env-read, and reachable test double in src/maezo/ "
            "must be inside its rule's pinned allowlist; §8.5 additionally proves the registry, "
            "the manifest and the catalogue are complete and consistent with each other."
        ),
    )
    parser.add_argument(
        "--src-dir", default="src/maezo", help="Source tree to AST-scan (default: src/maezo)."
    )
    parser.add_argument(
        "--repo-root",
        default=".",
        help="Repo root, for §8.5's spec/ and tests/ reads (default: current directory).",
    )
    parser.add_argument(
        "--skip-completeness",
        action="store_true",
        help="Skip §8.5 (useful for isolating an §8.1-§8.4 AST-only failure).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point. Returns 0 (pass) or 1 (fail); never raises on ordinary input."""
    args = build_arg_parser().parse_args(argv)
    src_dir = Path(args.src_dir)
    repo_root = Path(args.repo_root)

    if not src_dir.is_dir():
        print(f"[effect-chokepoint-fence] FAIL: src dir not found: {src_dir}", file=sys.stderr)
        return 1

    result = scan_tree(src_dir)
    print(result.render())

    ok = result.ok
    if not args.skip_completeness:
        completeness_violations, completeness_counters = check_completeness(src_dir, repo_root)
        status = "PASS" if not completeness_violations else "FAIL"
        print(f"[effect-chokepoint-fence:§8.5] {status}")
        if completeness_violations:
            print(f"  violations ({len(completeness_violations)}):")
            for v in completeness_violations:
                print(f"    - {v}")
        print("  non-vacuity counters:")
        for key in sorted(completeness_counters):
            print(f"    {key} = {completeness_counters[key]}")
        ok = ok and not completeness_violations

    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
