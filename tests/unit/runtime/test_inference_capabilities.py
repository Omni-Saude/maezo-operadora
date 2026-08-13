"""Provider capability schema + fail-closed PHI-zone startup validation (Onda 2, audit W2 §2).

Companion to ``test_inference.py`` (which covers ADR-0009/T1.7/T8). Everything here is
offline: no SDK call, no network, no credential.

TEST DISCIPLINE IN THIS FILE — the expected capability table below is HARDCODED, field
by field, each with a provenance comment naming the in-repo statement it encodes. It is
never derived from ``NOOP_CAPABILITIES``/``ANTHROPIC_CAPABILITIES``/
``PHI_ZONE_MOCK_CAPABILITIES``, because a table computed from the constant under test
proves only that ``x == x``. The enum MEMBERS are separately pinned to their string
literals, so the vocabulary cannot be renamed out from under the table either.
"""

from __future__ import annotations

import dataclasses

import pytest

from maezo.runtime.inference import (
    _PROVIDER_FACTORIES,  # noqa: PLC2701 — the fenced registry IS the thing under test
    PHI_DENIAL_CLASSIFICATION,
    PHI_DENIAL_NOT_ALLOWED,
    PHI_DENIAL_REGION,
    PHI_DENIAL_RETENTION,
    PHI_DENIAL_TRAINING,
    PHI_ELIGIBLE_REGIONS,
    AnthropicInferenceProvider,
    BaseInferenceProvider,
    BedrockInferenceProvider,
    BrResidentInferenceProvider,
    CredentialSource,
    DataClassification,
    DeploymentRegion,
    InferenceConfigError,
    InferenceProvider,
    InferenceSettings,
    NoopInferenceProvider,
    PhiZoneMockProvider,
    PhiZoneRoutingError,
    ProviderCapabilities,
    RetentionPolicy,
    phi_zone_denial_reasons,
)

_INFERENCE_MODULE = "maezo.runtime.inference"


@pytest.fixture(autouse=True)
def _clean_inference_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """No ambient credential or ambient PHI declaration may reach a test here.

    ``InferenceSettings`` is a pydantic-settings model reading ``MAEZO_INFERENCE_*``,
    so a developer's exported ``MAEZO_INFERENCE_PHI_ZONE_REQUIRED=1`` would otherwise
    silently flip the very default this file asserts is off.
    """
    for name in (
        "MAEZO_ANTHROPIC_API_KEY",
        "ANTHROPIC_API_KEY",
        "MAEZO_INFERENCE_PROVIDER",
        "MAEZO_INFERENCE_MODEL",
        "MAEZO_INFERENCE_PHI_ZONE_REQUIRED",
        # Onda 2 W2 leg 2: the three owner acts gating `br_resident`. Same reasoning as the
        # line above — an exported `MAEZO_PHI_VENDOR_DPA_REF` on a developer's machine would
        # otherwise make a provider that MUST refuse to construct quietly constructible.
        "MAEZO_PHI_ENDPOINT_URL",
        "MAEZO_PHI_API_KEY",
        "MAEZO_PHI_VENDOR_DPA_REF",
        # W-BEDROCK: `BedrockSettings` reads these; a developer's exported region/model id
        # would otherwise silently change what the `bedrock` factory builds under the
        # population test below. Same reasoning as every line above.
        "MAEZO_BEDROCK_MODEL_ID",
        "MAEZO_BEDROCK_REGION",
    ):
        monkeypatch.delenv(name, raising=False)


# =============================================================================================
# The hardcoded capability table (+ the guard that it stays complete)
# =============================================================================================

#: provider class -> (settings name, expected capabilities, expected is_mock).
#: Every field is a literal with provenance. NOT computed from the module's constants.
_EXPECTED: dict[type[BaseInferenceProvider], tuple[str, ProviderCapabilities, bool]] = {
    NoopInferenceProvider: (
        "noop",
        ProviderCapabilities(
            # NoopInferenceProvider docstring: "Never PHI-capable … a PHI-tagged request
            # must be explicitly routed to PhiZoneMockProvider … not silently absorbed by noop."
            phi_allowed=False,
            # Module docstring: "noop (default) — deterministic mock, no network calls".
            deployment_region=DeploymentRegion.LOCAL_NO_EGRESS,
            # Nothing is transmitted, so nothing can be retained.
            retention_policy=RetentionPolicy.ZERO_RETENTION,
            # No vendor exists to train on the input.
            training_on_input_prohibited=True,
            # Consistent with phi_allowed=False: a dev/test mock is not sanctioned for PHI.
            max_data_classification=DataClassification.INTERNAL,
            # Module docstring: "no credentials".
            credential_source=CredentialSource.NONE,
            # Runs no model.
            supported_model_versions=frozenset(),
        ),
        True,  # module docstring: "deterministic mock"
    ),
    AnthropicInferenceProvider: (
        "anthropic",
        ProviderCapabilities(
            # AnthropicInferenceProvider docstring: "General-zone cloud provider
            # (ADR-0006) — never PHI-capable."
            phi_allowed=False,
            # Same docstring: general zone, i.e. vendor-managed cloud, not BR-resident.
            deployment_region=DeploymentRegion.GLOBAL_MULTI_REGION,
            # No zero-retention agreement exists anywhere in this repo tree; UNSPECIFIED is
            # the honest declaration. Asserting ZERO_RETENTION would fabricate a contract.
            retention_policy=RetentionPolicy.UNSPECIFIED,
            # Likewise: no signed training prohibition in-repo to point at.
            training_on_input_prohibited=False,
            # ADR-0006 places "dado pessoal sensível" in the PHI zone; nothing in-repo
            # sanctions PERSONAL for the general zone either. INTERNAL is the floor.
            max_data_classification=DataClassification.INTERNAL,
            # AnthropicInferenceProvider docstring: "API key is STRICTLY
            # environment-sourced, never committed" (_ANTHROPIC_API_KEY_ENV_VARS).
            credential_source=CredentialSource.ENVIRONMENT,
            # DEFAULT_ANTHROPIC_MODEL — the one model id this repo declares (inference.py:78).
            supported_model_versions=frozenset({"claude-opus-4-8"}),
        ),
        False,  # real provider, real completions
    ),
    BedrockInferenceProvider: (
        "bedrock",
        ProviderCapabilities(
            # BedrockInferenceProvider docstring: "GENERAL-ZONE CLOUD PROVIDER (ADR-0006) — never
            # PHI-capable, exactly like AnthropicInferenceProvider." Adding a second general-zone
            # provider must not add a second way into the PHI zone.
            phi_allowed=False,
            # NOT BR_SAO_PAULO, despite DEFAULT_BEDROCK_REGION being sa-east-1. The region is
            # operator-configurable at runtime (MAEZO_BEDROCK_REGION) while `capabilities` is a
            # ClassVar fixed at import; nothing in the provider ENFORCES the region (contrast
            # BrResidentInferenceProvider's endpoint allowlist + per-response served_region
            # attestation); and BR_SAO_PAULO is in PHI_ELIGIBLE_REGIONS, so declaring it would
            # leave phi_allowed=False as the only thing between this provider and PHI eligibility.
            # See BEDROCK_CAPABILITIES' comment, which states all three reasons.
            deployment_region=DeploymentRegion.GLOBAL_MULTI_REGION,
            # Same leg-1 honesty rule as `anthropic`: no zero-retention agreement with AWS exists
            # anywhere in this repo tree, and UNSPECIFIED is never optimistically read as zero.
            retention_policy=RetentionPolicy.UNSPECIFIED,
            # Likewise: no signed training prohibition in-repo to point at.
            training_on_input_prohibited=False,
            # The same conservative floor the general zone already uses (ADR-0006 puts "dado
            # pessoal sensível" in the PHI zone; nothing in-repo sanctions PERSONAL here either).
            max_data_classification=DataClassification.INTERNAL,
            # THE FIELD THAT IS NOT `ENVIRONMENT`, and deliberately so: SigV4 credentials are
            # resolved by botocore's own chain (env / AWS_PROFILE / IRSA / instance metadata) AT
            # REQUEST TIME. This process never reads, holds or validates one, so `ENVIRONMENT`'s
            # two claims — process-environment-sourced, read at construction — would both be
            # false, and would make the provider look startup-validated when it is not.
            credential_source=CredentialSource.AWS_DEFAULT_CHAIN,
            # DEFAULT_BEDROCK_MODEL — the one Bedrock model id this repo declares. The AWS
            # account's catalogue is larger; the sanctioned set is not the vendor catalogue.
            # Note the `anthropic.` prefix: Bedrock namespaces model ids by provider, so this is
            # a DIFFERENT id from ANTHROPIC_CAPABILITIES' entry, not a spelling of the same one.
            supported_model_versions=frozenset({"global.anthropic.claude-opus-5"}),
        ),
        False,  # real provider, real completions — never a fabricated one
    ),
    PhiZoneMockProvider: (
        "phi_zone_mock",
        ProviderCapabilities(
            # PhiZoneMockProvider docstring: stands in for the PHI-zone endpoint; the
            # module docstring names it the only phi_capable provider today.
            phi_allowed=True,
            # It runs in-process. NOT BR_SAO_PAULO — declaring a São Paulo deployment for
            # a Python object would be exactly the fabrication this schema exists to stop.
            deployment_region=DeploymentRegion.LOCAL_NO_EGRESS,
            # Docstring: "zero-retention … PHI-zone endpoint"; nothing is transmitted.
            retention_policy=RetentionPolicy.ZERO_RETENTION,
            training_on_input_prohibited=True,
            # The zone's whole purpose (ADR-0006 "Zona PHI/Financeira").
            max_data_classification=DataClassification.PHI,
            credential_source=CredentialSource.NONE,
            # Docstring: never "fabricating a real completion" — it runs no model.
            supported_model_versions=frozenset(),
        ),
        True,  # docstring: "EXPLICITLY-LABELED MOCK"; every response is SYNTHETIC
    ),
    BrResidentInferenceProvider: (
        "br_resident",
        ProviderCapabilities(
            # BrResidentInferenceProvider docstring: "PHI-eligible BY DESIGN". It is the adapter
            # ADR-0006's PHI zone was always going to need. NOT a claim it can serve traffic —
            # `test_br_resident_is_unbootable_without_the_owner_acts` (test_inference_br_resident.py)
            # pins that it refuses to construct at all today.
            phi_allowed=True,
            # BR_RESIDENT_CAPABILITIES comment: the adapter refuses, at construction AND per call,
            # any endpoint outside BR_REGIONAL_ENDPOINT_HOST_SUFFIXES, plus any response that came
            # back from a different URL or attested a different region. Unlike `phi_zone_mock`
            # (which honestly declares LOCAL_NO_EGRESS because it transmits nothing), this adapter
            # exists to transmit — to São Paulo and nowhere else.
            deployment_region=DeploymentRegion.BR_SAO_PAULO,
            # THE ONE FIELD TO READ SCEPTICALLY, and the module says so at length: no DPA exists
            # in this tree (the leg-1 finding that made `anthropic` declare UNSPECIFIED). This is
            # NOT a signed contract — it is the flag the adapter SENDS (HEADER_ZERO_RETENTION) and
            # the acknowledgement it REQUIRES back before returning any completion. The vendor-side
            # half is gated by MAEZO_PHI_VENDOR_DPA_REF, without which the class cannot boot.
            retention_policy=RetentionPolicy.ZERO_RETENTION,
            # Same structure: HEADER_TRAINING_PROHIBITED sent, acknowledgement required back.
            training_on_input_prohibited=True,
            # ADR-0006 "Zona PHI/Financeira" — the zone this adapter is designated for.
            max_data_classification=DataClassification.PHI,
            # MAEZO_PHI_API_KEY, read from the process environment at construction (never a
            # settings field, never committed) — the AnthropicInferenceProvider posture.
            credential_source=CredentialSource.ENVIRONMENT,
            # EMPTY, and honestly so: this repo sanctions no BR-zone model id, because the vendor
            # catalogue is unknown pending the DPA. Inventing a plausible id would be exactly the
            # fabrication leg 1 refused for `anthropic`'s retention field. The adapter instead
            # requires MAEZO_INFERENCE_MODEL explicitly, with no default.
            supported_model_versions=frozenset(),
        ),
        # NOT a mock: with no transport wired it RAISES (RefusingBrRegionalTransport) rather than
        # returning synthetic text. `is_mock` describes whether output is REAL; this provider
        # never produces output that is not. It is the mirror image of PhiZoneMockProvider
        # (PHI-designated + synthetic), and representing both is why leg 1 kept the two facts apart.
        False,
    ),
}


def _all_subclasses(root: type) -> set[type]:
    """Every subclass of ``root``, TRANSITIVELY — children, grandchildren, deeper.

    ``type.__subclasses__()`` returns DIRECT children ONLY. That is not a detail here: the
    natural shape for the next provider is a specialization of an existing one — e.g.
    ``class BrResidentProvider(PhiZoneMockProvider)`` for the BR-resident adapter — which
    makes it a GRANDCHILD of :class:`BaseInferenceProvider`. A one-level walk cannot see
    such a class, so a genuinely PHI-eligible provider could be added to the module and
    still pass every guard below, which is precisely the drift they exist to catch.
    """
    found: set[type] = set()
    pending: list[type] = list(root.__subclasses__())
    while pending:
        cls = pending.pop()
        if cls in found:
            continue
        found.add(cls)
        pending.extend(cls.__subclasses__())
    return found


def _concrete_providers_defined_in_module() -> set[type[BaseInferenceProvider]]:
    """Every provider class DEFINED in ``maezo.runtime.inference``, at ANY depth.

    Filtered by ``__module__`` so that provider subclasses defined inside this test file
    (the ``__init_subclass__`` drift probes below) never leak into the guard.
    """
    return {cls for cls in _all_subclasses(BaseInferenceProvider) if cls.__module__ == _INFERENCE_MODULE}


def test_the_provider_population_is_walked_transitively() -> None:
    """REGRESSION CONTROL for the grandchild blind spot in the population helper.

    Anti-vacuity for every guard that consumes
    :func:`_concrete_providers_defined_in_module`: it proves the walk actually descends
    past the first level, rather than the guards merely *appearing* to hold because the
    module happens to have a flat hierarchy today. The two assertions are deliberately
    paired — the first pins the blind spot (a grandchild IS absent from a one-level
    ``__subclasses__()``), the second pins that the helper's walk closes it.

    Asserted on classes defined HERE, which the ``__module__`` filter keeps out of the
    guards themselves.
    """

    class _ProbeChild(BaseInferenceProvider):
        capabilities = _SATISFYING
        phi_capable = _SATISFYING.phi_allowed

        async def generate(
            self,
            prompt: str,
            *,
            agent_id: str | None = None,
            tenant_id: str | None = None,
        ) -> str:  # pragma: no cover - never invoked
            return ""

        def health_check(self) -> dict[str, str]:  # pragma: no cover - never invoked
            return {"status": "warning", "message": "test double"}

    class _ProbeGrandchild(_ProbeChild):
        capabilities = _SATISFYING
        phi_capable = _SATISFYING.phi_allowed

    assert _ProbeGrandchild not in BaseInferenceProvider.__subclasses__()  # the blind spot itself
    assert {_ProbeChild, _ProbeGrandchild} <= _all_subclasses(BaseInferenceProvider)
    # …and the `__module__` filter still keeps these test doubles out of the real guards.
    assert not {_ProbeChild, _ProbeGrandchild} & _concrete_providers_defined_in_module()


def test_capability_table_covers_every_provider_in_the_module() -> None:
    """SET-EQUALITY GUARD: a provider added or removed without updating ``_EXPECTED`` fails.

    Without this, a fourth provider could ship with any capability declaration at all —
    or none reviewed — and every per-provider assertion below would still pass, because
    they only iterate over what the table already knows about. Population is walked
    TRANSITIVELY (see :func:`_all_subclasses`): a provider written as a subclass of an
    existing provider is caught here, not silently exempted from review.
    """
    assert _concrete_providers_defined_in_module() == set(_EXPECTED)


def test_provider_factory_registry_matches_hardcoded_names() -> None:
    """The selectable ``MAEZO_INFERENCE_PROVIDER`` values are pinned to a hardcoded set."""
    assert set(_PROVIDER_FACTORIES) == {"noop", "anthropic", "bedrock", "phi_zone_mock", "br_resident"}


def test_registry_and_class_hierarchy_agree_on_the_provider_population(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BELT AND SUSPENDERS: two independently-anchored populations must name the same providers.

    Population A is the CLASS HIERARCHY (:func:`_concrete_providers_defined_in_module`,
    transitive, anchored on what is actually defined). Population B is what
    ``MAEZO_INFERENCE_PROVIDER`` can actually SELECT: every registered factory is INVOKED
    and the class it builds is read off the object — anchored on construction, not on
    introspection, with the registry's keys separately pinned to literals by
    ``test_provider_factory_registry_matches_hardcoded_names``.

    Neither population subsumes the other, which is the point. A defined-but-unregistered
    provider (the grandchild drift) inflates A only; a provider registered from OUTSIDE
    this module — which A's ``__module__`` filter would drop — inflates B only. The
    hardcoded ``_EXPECTED`` table is the third anchor both are compared against, so
    agreement cannot be manufactured by editing one side.
    """
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    # `br_resident` refuses to construct without its three owner acts + an explicit model, so this
    # population test — which INVOKES every factory — has to supply them, exactly as it already
    # supplies an Anthropic key. Satisfying a provider's config is not the same as asserting the
    # provider is deployable: `test_inference_br_resident.py` owns that, and pins that each of
    # these four values is individually load-bearing.
    monkeypatch.setenv("MAEZO_PHI_ENDPOINT_URL", "https://fake.br-sao-paulo.phi.maezo.internal/v1/generate")
    monkeypatch.setenv("MAEZO_PHI_API_KEY", "phi-key-test")
    monkeypatch.setenv("MAEZO_PHI_VENDOR_DPA_REF", "DPA-TEST-NOT-A-REAL-CONTRACT")
    monkeypatch.setenv("MAEZO_INFERENCE_MODEL", "br-model-test")

    built_by_the_registry = {
        name: type(factory(InferenceSettings(provider=name))) for name, factory in _PROVIDER_FACTORIES.items()
    }

    assert built_by_the_registry == {name: cls for cls, (name, _, _) in _EXPECTED.items()}
    assert set(built_by_the_registry.values()) == _concrete_providers_defined_in_module()


@pytest.mark.parametrize("provider_cls", list(_EXPECTED), ids=lambda c: c.__name__)
def test_provider_declares_expected_capabilities(provider_cls: type[BaseInferenceProvider]) -> None:
    """Each provider's declaration equals the hardcoded, provenance-annotated expectation."""
    _, expected, _ = _EXPECTED[provider_cls]

    assert provider_cls.capabilities == expected


@pytest.mark.parametrize("provider_cls", list(_EXPECTED), ids=lambda c: c.__name__)
def test_provider_is_mock_flag_is_pinned(provider_cls: type[BaseInferenceProvider]) -> None:
    """``is_mock`` semantics are preserved and pinned independently of the capability set.

    Capabilities describe the ZONE contract; ``is_mock`` describes whether the output is
    REAL. Today's only PHI-eligible provider is a mock — folding the two together would
    make that fact unrepresentable.
    """
    _, _, expected_is_mock = _EXPECTED[provider_cls]

    assert provider_cls.is_mock is expected_is_mock


@pytest.mark.parametrize("provider_cls", list(_EXPECTED), ids=lambda c: c.__name__)
def test_phi_capable_is_derived_from_capabilities(provider_cls: type[BaseInferenceProvider]) -> None:
    """No second source of truth for PHI capability."""
    assert provider_cls.phi_capable is provider_cls.capabilities.phi_allowed


def test_exactly_two_providers_are_phi_eligible_today() -> None:
    """Hardcoded: ``phi_zone_mock`` and ``br_resident`` satisfy the PHI contract at this commit.

    THE GUARD FIRED, AND THIS IS THE DOCUMENTED ANSWER. Leg 1 shipped this as
    ``test_exactly_one_provider_is_phi_eligible_today`` and called a provider gaining PHI
    eligibility "the single most consequential change this schema governs". Onda 2 W2 leg 2 is
    that change, so the test name and the expectation move together — a renamed test with an
    unexplained new value would be the guard being edited around rather than answered.

    WHAT THE NEW ENTRY DOES AND DOES NOT CLAIM. `BrResidentInferenceProvider` is PHI-eligible BY
    DESIGN: its capability declaration states the contract the adapter ENFORCES CLIENT-SIDE
    (BR-regional endpoint allowlist checked at construction and per call, zero-retention and
    no-training flags sent and their acknowledgements required back before any completion is
    returned). It is NOT a claim that a BR-resident endpoint exists, that a vendor signed a
    zero-retention DPA, or that PHI can flow today. It cannot: the class refuses to CONSTRUCT
    without an owner-supplied `MAEZO_PHI_VENDOR_DPA_REF`, and with no transport wired every call
    fails closed. Those two facts are pinned in `test_inference_br_resident.py`
    (`test_br_resident_is_unbootable_without_the_owner_acts`,
    `test_unwired_transport_refuses_instead_of_fabricating`) and this test would be dishonest
    without them.

    Still stated as a WHOLE-TABLE fact, and still hardcoded — never derived from
    `BR_RESIDENT_CAPABILITIES` — so a THIRD provider gaining eligibility fails here just as
    loudly as the second one did.
    """
    eligible = {
        cls.__name__
        for cls in _concrete_providers_defined_in_module()
        if not phi_zone_denial_reasons(cls.capabilities)
    }

    assert eligible == {"PhiZoneMockProvider", "BrResidentInferenceProvider"}


# =============================================================================================
# Closed vocabularies — pinned to literals
# =============================================================================================


def test_deployment_region_vocabulary_is_pinned() -> None:
    assert {r.value for r in DeploymentRegion} == {"br-sao-paulo", "global-multi-region", "local-no-egress"}


def test_retention_policy_vocabulary_is_pinned() -> None:
    assert {r.value for r in RetentionPolicy} == {"zero-retention", "vendor-default", "unspecified"}


def test_data_classification_vocabulary_is_pinned() -> None:
    assert {c.value for c in DataClassification} == {"public", "internal", "personal", "phi"}


def test_credential_source_vocabulary_is_pinned() -> None:
    """W-BEDROCK GREW THIS VOCABULARY, and the pin moving is the reviewed act, not a workaround.

    ``aws-default-chain`` was added because neither existing member describes how Bedrock
    authenticates: it is not ``none`` (a credential is absolutely required), and it is not
    ``environment`` — which asserts the credential is read FROM THE PROCESS ENVIRONMENT, AT
    CONSTRUCTION, both of which are false for a chain botocore resolves per request from env,
    profile, IRSA or instance metadata. Reusing ``environment`` would have made
    ``BedrockInferenceProvider`` look startup-validated like ``AnthropicInferenceProvider``,
    which is exactly the kind of unauditable conflation this closed vocabulary replaced a bare
    boolean to prevent. This test is what forced that choice to be stated rather than assumed.
    """
    assert {c.value for c in CredentialSource} == {"none", "environment", "aws-default-chain"}


def test_classification_order_covers_every_member() -> None:
    """A classification without a rank would raise ``ValueError`` inside ``admits()``.

    Guards the one place the sensitivity ladder could silently become partial.
    """
    from maezo.runtime.inference import _CLASSIFICATION_ORDER  # noqa: PLC2701

    assert set(_CLASSIFICATION_ORDER) == set(DataClassification)
    assert len(_CLASSIFICATION_ORDER) == len(DataClassification)  # no duplicates


def test_classification_admits_follows_sensitivity_not_alphabet() -> None:
    """``admits`` orders by sensitivity, NOT by ``StrEnum``'s string comparison.

    Alphabetically "phi" < "public", so a naive ``>=`` on the StrEnum values would let a
    PUBLIC-capped provider accept PHI. This is that trap, asserted shut.
    """
    assert DataClassification.PHI.admits(DataClassification.PHI)
    assert DataClassification.PHI.admits(DataClassification.PUBLIC)
    assert not DataClassification.PUBLIC.admits(DataClassification.PHI)
    assert not DataClassification.INTERNAL.admits(DataClassification.PHI)
    assert not DataClassification.PERSONAL.admits(DataClassification.PHI)


def test_phi_eligible_regions_excludes_the_general_zone() -> None:
    """Hardcoded membership. The general zone must never become PHI-eligible by edit."""
    assert {DeploymentRegion.BR_SAO_PAULO, DeploymentRegion.LOCAL_NO_EGRESS} == PHI_ELIGIBLE_REGIONS
    assert DeploymentRegion.GLOBAL_MULTI_REGION not in PHI_ELIGIBLE_REGIONS


# =============================================================================================
# The PHI predicate — every facet independently load-bearing
# =============================================================================================

#: A capability set that satisfies the PHI contract, built here from literals so the
#: mutation probes below start from a known-GREEN baseline that is not the module's own.
_SATISFYING = ProviderCapabilities(
    phi_allowed=True,
    deployment_region=DeploymentRegion.BR_SAO_PAULO,
    retention_policy=RetentionPolicy.ZERO_RETENTION,
    training_on_input_prohibited=True,
    max_data_classification=DataClassification.PHI,
    credential_source=CredentialSource.ENVIRONMENT,
    supported_model_versions=frozenset({"some-br-model-1"}),
)


def test_satisfying_capabilities_yield_no_denial_reasons() -> None:
    """OVER-FIRE CONTROL. If this fails, the predicate refuses everything and every
    'it refuses X' assertion in this file is worthless."""
    assert phi_zone_denial_reasons(_SATISFYING) == ()


@pytest.mark.parametrize(
    ("field", "broken_value", "expected_reason"),
    [
        ("phi_allowed", False, PHI_DENIAL_NOT_ALLOWED),
        ("deployment_region", DeploymentRegion.GLOBAL_MULTI_REGION, PHI_DENIAL_REGION),
        ("retention_policy", RetentionPolicy.VENDOR_DEFAULT, PHI_DENIAL_RETENTION),
        ("retention_policy", RetentionPolicy.UNSPECIFIED, PHI_DENIAL_RETENTION),
        ("training_on_input_prohibited", False, PHI_DENIAL_TRAINING),
        ("max_data_classification", DataClassification.PERSONAL, PHI_DENIAL_CLASSIFICATION),
    ],
)
def test_each_phi_facet_is_independently_load_bearing(
    field: str, broken_value: object, expected_reason: str
) -> None:
    """Break EXACTLY ONE facet of a satisfying set; exactly that reason must come back.

    This is the anti-vacuity core of the whole schema. It proves the four facts are
    genuinely conjunctive — that none of them is decorative, and that no facet's check
    is short-circuited by another's. Deleting any single clause in
    ``phi_zone_denial_reasons`` turns exactly one of these parameter cases RED.
    """
    broken = dataclasses.replace(_SATISFYING, **{field: broken_value})

    assert phi_zone_denial_reasons(broken) == (expected_reason,)


def test_unspecified_retention_is_treated_as_harshly_as_vendor_default() -> None:
    """ "Unknown" is never optimistically read as "zero"."""
    unknown = dataclasses.replace(_SATISFYING, retention_policy=RetentionPolicy.UNSPECIFIED)
    retained = dataclasses.replace(_SATISFYING, retention_policy=RetentionPolicy.VENDOR_DEFAULT)

    assert phi_zone_denial_reasons(unknown) == phi_zone_denial_reasons(retained) == (PHI_DENIAL_RETENTION,)


def test_denial_reasons_are_deterministically_ordered() -> None:
    """Fixed declaration order — an operator-facing message must be diffable."""
    nothing_satisfied = ProviderCapabilities(
        phi_allowed=False,
        deployment_region=DeploymentRegion.GLOBAL_MULTI_REGION,
        retention_policy=RetentionPolicy.VENDOR_DEFAULT,
        training_on_input_prohibited=False,
        max_data_classification=DataClassification.PUBLIC,
        credential_source=CredentialSource.ENVIRONMENT,
        supported_model_versions=frozenset(),
    )

    assert phi_zone_denial_reasons(nothing_satisfied) == (
        PHI_DENIAL_NOT_ALLOWED,
        PHI_DENIAL_REGION,
        PHI_DENIAL_RETENTION,
        PHI_DENIAL_TRAINING,
        PHI_DENIAL_CLASSIFICATION,
    )


def test_inconsistent_capability_sets_remain_constructible() -> None:
    """Deliberate design fact, asserted so it is not "fixed" into vacuity.

    ``ProviderCapabilities`` performs NO validation. If it refused to model
    ``phi_allowed=True`` alongside vendor-default retention, the startup check against
    exactly that declaration could never be reached, and its tests would be proving
    nothing. The type describes; ``phi_zone_denial_reasons`` decides.
    """
    lying = dataclasses.replace(_SATISFYING, retention_policy=RetentionPolicy.VENDOR_DEFAULT)

    assert lying.phi_allowed is True
    assert phi_zone_denial_reasons(lying) == (PHI_DENIAL_RETENTION,)


def test_capabilities_are_frozen() -> None:
    """Ordinary attribute assignment on a declaration is refused — ACCIDENTAL mutation.

    SCOPE, stated because the stronger claim would be false: this guards a capability
    set from being edited by mistake (a stray ``caps.phi_allowed = True`` in a helper,
    a mutating "normalization" step). It is NOT a security property — ``frozen=True``
    is implemented as a ``__setattr__`` that raises, and ``object.__setattr__`` goes
    straight past it, as the second half of this test demonstrates rather than hides.
    In-process code that wants to lie about its capabilities can; the schema's job is
    to make an AUTHORED declaration reviewable, not to survive a hostile process.
    """
    with pytest.raises(dataclasses.FrozenInstanceError):
        _SATISFYING.phi_allowed = True  # type: ignore[misc]

    # The documented limit, asserted so nobody re-reads the guarantee as stronger than it is.
    escaped = dataclasses.replace(_SATISFYING, phi_allowed=False)
    object.__setattr__(escaped, "phi_allowed", True)

    assert escaped.phi_allowed is True


# =============================================================================================
# Fail-closed STARTUP validation — consequence-level
# =============================================================================================

_MISMATCH_SUBSTRING = "PHI zone required but provider capabilities are insufficient"


def test_phi_zone_required_refuses_general_zone_anthropic(monkeypatch: pytest.MonkeyPatch) -> None:
    """A deployment declaring PHI intent, wired to the general-zone provider, must not boot.

    Credentials are PRESENT here on purpose: the refusal must come from the capability
    mismatch, not from a missing key masquerading as one.
    """
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    settings = InferenceSettings(provider="anthropic", phi_zone_required=True)

    with pytest.raises(InferenceConfigError, match=_MISMATCH_SUBSTRING):
        InferenceProvider(settings=settings)


def test_phi_zone_required_refuses_noop() -> None:
    """Refused for `phi_allowed=False` even though noop leaks nothing and retains nothing.

    Harmlessness is not designation — the exact conflation a lone boolean invited.
    """
    with pytest.raises(InferenceConfigError, match=_MISMATCH_SUBSTRING) as excinfo:
        InferenceProvider(settings=InferenceSettings(provider="noop", phi_zone_required=True))

    assert PHI_DENIAL_NOT_ALLOWED in str(excinfo.value)


def test_phi_zone_required_accepts_the_phi_capable_provider() -> None:
    """OVER-FIRE CONTROL at the facade: a SATISFYING configuration must NOT raise.

    Without this, ``_assert_phi_zone_capability`` could be a bare unconditional
    ``raise`` and every refusal test above would still pass.
    """
    provider = InferenceProvider(settings=InferenceSettings(provider="phi_zone_mock", phi_zone_required=True))

    assert provider.provider_name == "phi_zone_mock"


def test_phi_zone_check_is_inert_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """INERTNESS: default-off means today's general-zone deployments are untouched."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")

    assert InferenceSettings().phi_zone_required is False
    assert InferenceProvider(settings=InferenceSettings(provider="anthropic")).provider_name == "anthropic"
    assert InferenceProvider().provider_name == "noop"


def test_phi_zone_required_is_read_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """The flag is operator-settable as MAEZO_INFERENCE_PHI_ZONE_REQUIRED, not code-only."""
    monkeypatch.setenv("MAEZO_INFERENCE_PHI_ZONE_REQUIRED", "true")

    assert InferenceSettings().phi_zone_required is True

    with pytest.raises(InferenceConfigError, match=_MISMATCH_SUBSTRING):
        InferenceProvider(settings=InferenceSettings(provider="noop"))


def test_capability_mismatch_uses_the_same_error_type_as_a_missing_credential() -> None:
    """ "As loud as a missing credential" — asserted as type identity, not as a comment."""
    with pytest.raises(InferenceConfigError) as missing_key:
        InferenceProvider(settings=InferenceSettings(provider="anthropic"))
    with pytest.raises(InferenceConfigError) as mismatch:
        InferenceProvider(settings=InferenceSettings(provider="noop", phi_zone_required=True))

    assert type(missing_key.value) is type(mismatch.value) is InferenceConfigError
    assert "API key" in str(missing_key.value)
    assert _MISMATCH_SUBSTRING in str(mismatch.value)


def test_capability_mismatch_message_names_every_failing_facet(monkeypatch: pytest.MonkeyPatch) -> None:
    """The refusal is actionable: it lists which facts fell short, in fixed order."""
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")

    with pytest.raises(InferenceConfigError) as excinfo:
        InferenceProvider(settings=InferenceSettings(provider="anthropic", phi_zone_required=True))

    message = str(excinfo.value)
    for reason in (
        PHI_DENIAL_NOT_ALLOWED,
        PHI_DENIAL_REGION,
        PHI_DENIAL_RETENTION,
        PHI_DENIAL_TRAINING,
        PHI_DENIAL_CLASSIFICATION,
    ):
        assert reason in message


def test_capability_mismatch_message_never_leaks_the_credential(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pinned-fields discipline: the refusal carries enums and a provider name, nothing secret."""
    canary = "sk-ant-CANARY-must-never-appear-in-an-error"
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", canary)

    with pytest.raises(InferenceConfigError) as excinfo:
        InferenceProvider(settings=InferenceSettings(provider="anthropic", phi_zone_required=True))

    assert canary not in str(excinfo.value)
    assert canary not in repr(excinfo.value)


# =============================================================================================
# I-6 — the per-call PHI refusal stays independent of all of the above
# =============================================================================================


@pytest.mark.asyncio
async def test_per_call_phi_refusal_is_unchanged_when_the_flag_is_off(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """I-6: ``phi=True`` against a general-zone provider still raises ``PhiZoneRoutingError``.

    The new startup check is an ADDITIONAL, earlier failure — it must not have become the
    only one. With ``phi_zone_required`` off, construction succeeds and the per-call
    structural refusal fires exactly as it did before this schema existed.
    """
    monkeypatch.setenv("MAEZO_ANTHROPIC_API_KEY", "sk-ant-test")
    provider = InferenceProvider(settings=InferenceSettings(provider="anthropic"))

    with pytest.raises(PhiZoneRoutingError):
        await provider.generate("dados PHI", phi=True)


@pytest.mark.asyncio
async def test_phi_traffic_flows_when_both_the_flag_and_the_provider_agree() -> None:
    """End-to-end: PHI-declared deployment + PHI-eligible provider = a served request."""
    provider = InferenceProvider(settings=InferenceSettings(provider="phi_zone_mock", phi_zone_required=True))

    response = await provider.generate("dados PHI", phi=True)

    assert "SYNTHETIC" in response


# =============================================================================================
# Drift guards — __init_subclass__ makes an undeclared/contradictory provider unrepresentable
# =============================================================================================


def test_provider_without_a_capability_declaration_cannot_be_defined() -> None:
    """A provider that forgets to declare capabilities fails at CLASS CREATION.

    Not at first use, not at first PHI request — at import.
    """
    with pytest.raises(TypeError, match=r"must declare its own `capabilities: ClassVar"):

        class UndeclaredProvider(BaseInferenceProvider):  # pragma: no cover - never created
            pass


def test_provider_contradicting_its_own_capabilities_cannot_be_defined() -> None:
    """``phi_capable=True`` over ``phi_allowed=False`` is refused at class creation.

    This is the two-sources-of-truth drift the schema exists to eliminate, and it is
    made unrepresentable IN A CLASS BODY rather than merely asserted against after the
    fact. Not more than that: a ``ClassVar`` reassigned after the class exists, an
    instance attribute shadowing the class one, or an intermediate subclass overriding
    ``__init_subclass__`` without ``super()`` all still reach a contradicted provider at
    runtime — see :meth:`BaseInferenceProvider.__init_subclass__`, which states the same
    boundary. The target is authoring drift, not a hostile in-process actor.
    """
    with pytest.raises(TypeError, match="contradicts its capabilities.phi_allowed"):

        class LyingProvider(BaseInferenceProvider):  # pragma: no cover - never created
            capabilities = dataclasses.replace(_SATISFYING, phi_allowed=False)
            phi_capable = True


def test_a_consistent_new_provider_can_still_be_defined() -> None:
    """OVER-FIRE CONTROL for the drift guard: a well-formed provider is NOT refused.

    Without this, ``__init_subclass__`` could reject every subclass and both probes
    above would still pass.
    """

    class WellFormedProvider(BaseInferenceProvider):
        capabilities = _SATISFYING
        phi_capable = _SATISFYING.phi_allowed
        is_mock = True

        async def generate(
            self,
            prompt: str,
            *,
            agent_id: str | None = None,
            tenant_id: str | None = None,
        ) -> str:  # pragma: no cover - never invoked
            return ""

        def health_check(self) -> dict[str, str]:  # pragma: no cover - never invoked
            return {"status": "warning", "message": "test double"}

    assert WellFormedProvider.phi_capable is True
    assert phi_zone_denial_reasons(WellFormedProvider.capabilities) == ()
