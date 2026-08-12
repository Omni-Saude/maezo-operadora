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
            # DEFAULT_ANTHROPIC_MODEL — the one model id this repo declares (inference.py:63).
            supported_model_versions=frozenset({"claude-opus-4-8"}),
        ),
        False,  # real provider, real completions
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
}


def _concrete_providers_defined_in_module() -> set[type[BaseInferenceProvider]]:
    """Every provider class DEFINED in ``maezo.runtime.inference``.

    Filtered by ``__module__`` so that provider subclasses defined inside this test file
    (the ``__init_subclass__`` drift probes below) never leak into the guard.
    """
    return {cls for cls in BaseInferenceProvider.__subclasses__() if cls.__module__ == _INFERENCE_MODULE}


def test_capability_table_covers_every_provider_in_the_module() -> None:
    """SET-EQUALITY GUARD: a provider added or removed without updating ``_EXPECTED`` fails.

    Without this, a fourth provider could ship with any capability declaration at all —
    or none reviewed — and every per-provider assertion below would still pass, because
    they only iterate over what the table already knows about.
    """
    assert _concrete_providers_defined_in_module() == set(_EXPECTED)


def test_provider_factory_registry_matches_hardcoded_names() -> None:
    """The selectable ``MAEZO_INFERENCE_PROVIDER`` values are pinned to a hardcoded set."""
    assert set(_PROVIDER_FACTORIES) == {"noop", "anthropic", "phi_zone_mock"}


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


def test_exactly_one_provider_is_phi_eligible_today() -> None:
    """Hardcoded: only ``phi_zone_mock`` satisfies the PHI contract at this commit.

    Stated as a whole-table fact so that a provider quietly gaining PHI eligibility —
    the single most consequential change this schema governs — cannot land unnoticed.
    """
    eligible = {
        cls.__name__
        for cls in _concrete_providers_defined_in_module()
        if not phi_zone_denial_reasons(cls.capabilities)
    }

    assert eligible == {"PhiZoneMockProvider"}


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
    assert {c.value for c in CredentialSource} == {"none", "environment"}


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
    """A capability declaration cannot be edited at runtime to grant itself PHI access."""
    with pytest.raises(dataclasses.FrozenInstanceError):
        _SATISFYING.phi_allowed = True  # type: ignore[misc]


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
    made unrepresentable rather than merely asserted against after the fact.
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
