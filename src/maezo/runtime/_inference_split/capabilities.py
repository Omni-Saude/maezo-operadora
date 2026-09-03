"""Provider capability schema (D2-02 split, step 2/8).

Moved verbatim out of ``maezo/runtime/inference.py`` (Onda 2, audit W2 §2) — cut-and-paste, never
a redefinition (``docs/reports/inference-split-plan.md`` §5 step 2). ``phi_capable: bool`` alone
could not be audited, so every provider declares a :class:`ProviderCapabilities` — deployment
region, retention policy, training prohibition, maximum data classification, credential source and
sanctioned model ids — over CLOSED vocabularies, checked fail-closed by
:func:`phi_zone_denial_reasons`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from maezo.runtime._inference_split.settings import DEFAULT_ANTHROPIC_MODEL, DEFAULT_BEDROCK_MODEL

# ---------------------------------------------------------------------------
# Provider capability schema (Onda 2, audit W2 §2)
#
# `phi_capable: bool` answered ONE question ("may this provider see PHI?") and
# silently conflated the four independent facts that question actually depends
# on: WHERE inference runs, WHAT the vendor retains, WHETHER our input trains
# their model, and HOW HIGH a data classification the provider is sanctioned
# for. A single boolean cannot be audited against ADR-0006/ADR-0017 — it can
# only be trusted. This schema makes each fact separately declarable, separately
# false-ifiable, and separately checkable.
#
# CLOSED vocabularies where the domain is closed (region, retention,
# classification, credential source) — a free string would let a provider
# declare "brazil-ish" or "zero retention (mostly)" and pass a `==` check that
# means nothing. `supported_model_versions` stays an open set of ids because
# model ids genuinely are an open vocabulary.
# ---------------------------------------------------------------------------


class DeploymentRegion(StrEnum):
    """Where a provider's inference actually EXECUTES (ADR-0006 data residency)."""

    #: BR-resident inference (sa-east-1 / São Paulo) — the ADR-0006 PHI zone proper.
    #: No provider in this repo declares it yet: the real BR-resident endpoint is
    #: blocked(external) (T1.7 charter). Declared here so the vocabulary is complete
    #: BEFORE the adapter exists, not retrofitted around whatever gets built.
    BR_SAO_PAULO = "br-sao-paulo"

    #: Vendor-managed multi-region cloud with no BR-residency guarantee — the
    #: general zone (ADR-0006). Cross-border transfer is possible by construction.
    GLOBAL_MULTI_REGION = "global-multi-region"

    #: Executes entirely inside this Python process; nothing is transmitted anywhere.
    #: See `PHI_ELIGIBLE_REGIONS` for why this is residency-eligible and what stops it
    #: from being mistaken for a production PHI endpoint.
    LOCAL_NO_EGRESS = "local-no-egress"


class RetentionPolicy(StrEnum):
    """What the provider persists of request/response payloads."""

    #: Nothing is persisted past the request. Required for the PHI zone.
    ZERO_RETENTION = "zero-retention"

    #: The vendor's standard retention window applies. NOT zero — an explicit,
    #: honest "we retain", never a hopeful blank.
    VENDOR_DEFAULT = "vendor-default"

    #: No retention terms are contractually established in-repo. Treated exactly
    #: as harshly as VENDOR_DEFAULT: unknown is never assumed to be zero.
    UNSPECIFIED = "unspecified"


class DataClassification(StrEnum):
    """Highest sensitivity of data a provider is SANCTIONED to receive.

    Ordered least→most sensitive by :data:`_CLASSIFICATION_ORDER`; compare with
    :meth:`admits`, never with ``<``/``>`` (``StrEnum`` would compare the string
    values alphabetically, which is not the sensitivity order).
    """

    PUBLIC = "public"
    INTERNAL = "internal"

    #: LGPD "dado pessoal" that is not health data.
    PERSONAL = "personal"

    #: LGPD "dado pessoal sensível" (saúde) — the ADR-0006 PHI zone's data class.
    PHI = "phi"

    def admits(self, data: DataClassification) -> bool:
        """True if a provider capped at ``self`` may receive ``data``."""
        return _CLASSIFICATION_ORDER.index(self) >= _CLASSIFICATION_ORDER.index(data)


#: Sensitivity order, least→most. Completeness against ``DataClassification`` is
#: guarded by ``test_classification_order_covers_every_member`` — a member added
#: without a rank would otherwise raise ``ValueError`` deep inside ``admits()``.
_CLASSIFICATION_ORDER: Final[tuple[DataClassification, ...]] = (
    DataClassification.PUBLIC,
    DataClassification.INTERNAL,
    DataClassification.PERSONAL,
    DataClassification.PHI,
)


class CredentialSource(StrEnum):
    """Where the provider's credential comes from, if it needs one."""

    #: Needs no credential at all (in-process provider).
    NONE = "none"

    #: STRICTLY read from the process environment at construction, never from a
    #: settings object, never committed (see ``_ANTHROPIC_API_KEY_ENV_VARS``).
    ENVIRONMENT = "environment"

    #: Resolved by the AWS SDK's own credential chain at REQUEST time — environment
    #: variables, ``AWS_PROFILE``/shared config, IRSA/web-identity, instance metadata —
    #: never read, held or validated by this process.
    #:
    #: A SEPARATE MEMBER, NOT ``ENVIRONMENT``, and the distinction is the whole reason
    #: this vocabulary is closed. ``ENVIRONMENT`` makes two auditable claims that are both
    #: FALSE here: that the credential comes from the process environment (the AWS chain
    #: may equally resolve a profile file or a pod identity token), and that it is read AT
    #: CONSTRUCTION (so a missing one fails at startup — a Bedrock credential is not
    #: consulted until the first request). Declaring ``ENVIRONMENT`` would therefore make
    #: :class:`BedrockInferenceProvider` look startup-validated when it is not. Naming the
    #: chain honestly is what keeps the field auditable.
    AWS_DEFAULT_CHAIN = "aws-default-chain"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    """An inference provider's self-declared, auditable capability set.

    Deliberately a PURE DESCRIPTION with NO validation in ``__post_init__``:
    every combination — including self-inconsistent ones like
    ``phi_allowed=True`` with ``retention_policy=VENDOR_DEFAULT`` — must remain
    CONSTRUCTIBLE. Policy lives exclusively in
    :func:`phi_zone_denial_reasons`. If the type refused to model an
    inconsistent declaration, the startup check against that declaration would
    be unreachable, i.e. vacuous, and could never be proven to fire.
    """

    #: Whether this provider is designated to serve the ADR-0006 PHI zone. NOT
    #: sufficient on its own — see :func:`phi_zone_denial_reasons`, which also
    #: requires the region/retention/training facts to line up. A provider
    #: asserting this while failing the others is a self-inconsistent
    #: declaration that fails startup, LOUDLY, rather than being believed.
    phi_allowed: bool

    deployment_region: DeploymentRegion
    retention_policy: RetentionPolicy

    #: True when the provider is contractually barred from training on our input.
    training_on_input_prohibited: bool

    max_data_classification: DataClassification
    credential_source: CredentialSource

    #: Model ids THIS REPO has declared for the provider — the sanctioned set, not
    #: the vendor's catalogue. DECLARED, NOT ENFORCED in this leg: enforcing a
    #: per-provider model allowlist is a routing decision that belongs with the
    #: real BR-zone adapter (later leg), and wiring it now would silently break
    #: `MAEZO_INFERENCE_MODEL` overrides. Empty for a provider with no real model.
    supported_model_versions: frozenset[str]


#: Regions in which PHI may lawfully be processed under ADR-0006/ADR-0017.
#:
#: `LOCAL_NO_EGRESS` is in this set on purpose, and it is the one entry that
#: deserves an argument. ADR-0006's residency requirement is about CROSS-BORDER
#: TRANSFER; a provider that transmits nothing performs no transfer, so it cannot
#: violate residency. That is what makes today's `phi_zone_mock` a legitimate way
#: to exercise the PHI seam end-to-end. What stops it from being mistaken for a
#: production PHI endpoint is NOT this set — it is `is_mock=True`, the loud
#: `health_check()` warning, and the "[SYNTHETIC RESPONSE …]" prefix on every
#: completion. Residency eligibility and production readiness are different
#: claims, and this constant only makes the first one.
#:
#: RESIDUAL, DISCLOSED: `LOCAL_NO_EGRESS` is an UNVERIFIED SELF-DECLARATION. Nothing in
#: this module structurally ties the enum member to actually-not-transmitting — a provider
#: that opens a socket and declares `LOCAL_NO_EGRESS` boots under
#: `phi_zone_required=True` and serves `phi=True` while egressing, and this schema would
#: not notice. The real egress control is DEPLOY-LEVEL, not here: ADR-0017 network
#: enforcement, `deploy/helm/maezo-tenant/templates/networkpolicy.yaml`. Today the claim
#: is sound only because both declarers (`noop`, `phi_zone_mock`) are in-process mocks
#: that make no call at all.
#: Therefore: any future NON-MOCK provider declaring `LOCAL_NO_EGRESS` owes a
#: NETWORK-LEVEL proof that it egresses nothing — a leg-2/canary obligation, not something
#: a capability declaration can discharge on its own.
#:
#: `GLOBAL_MULTI_REGION` is deliberately ABSENT: that is the general zone.
PHI_ELIGIBLE_REGIONS: Final[frozenset[DeploymentRegion]] = frozenset(
    {DeploymentRegion.BR_SAO_PAULO, DeploymentRegion.LOCAL_NO_EGRESS}
)

#: Stable denial reason codes returned by :func:`phi_zone_denial_reasons`.
#: These are part of the module's contract — tests and operators match on them,
#: so they are enum-shaped constants, never prose that may be reworded. They
#: carry NO request/response content: each names a DECLARED CAPABILITY that
#: failed, never the data that would have been sent.
PHI_DENIAL_NOT_ALLOWED: Final[str] = "phi_allowed=False"
PHI_DENIAL_REGION: Final[str] = "deployment_region_not_phi_eligible"
PHI_DENIAL_RETENTION: Final[str] = "retention_policy_not_zero_retention"
PHI_DENIAL_TRAINING: Final[str] = "training_on_input_not_prohibited"
PHI_DENIAL_CLASSIFICATION: Final[str] = "max_data_classification_below_phi"


def phi_zone_denial_reasons(capabilities: ProviderCapabilities) -> tuple[str, ...]:
    """Every reason ``capabilities`` may NOT serve the ADR-0006 PHI zone.

    Empty tuple == eligible. Reasons come back in a FIXED order (declaration
    order below), so an error message is deterministic and diffable.

    ALL FOUR facts are checked independently and none short-circuits another:
    a provider that declares ``phi_allowed=True`` but retains payloads is
    refused on retention, and a provider with impeccable retention that never
    declared ``phi_allowed`` is still refused. That independence is the whole
    point of replacing the boolean — see the module-level schema comment.
    """
    reasons: list[str] = []
    if not capabilities.phi_allowed:
        reasons.append(PHI_DENIAL_NOT_ALLOWED)
    if capabilities.deployment_region not in PHI_ELIGIBLE_REGIONS:
        reasons.append(PHI_DENIAL_REGION)
    if capabilities.retention_policy is not RetentionPolicy.ZERO_RETENTION:
        reasons.append(PHI_DENIAL_RETENTION)
    if not capabilities.training_on_input_prohibited:
        reasons.append(PHI_DENIAL_TRAINING)
    if not capabilities.max_data_classification.admits(DataClassification.PHI):
        reasons.append(PHI_DENIAL_CLASSIFICATION)
    return tuple(reasons)


# --- Per-provider declarations ---------------------------------------------
# Each constant is the single source of truth for its provider: the class's
# `phi_capable` ClassVar is DERIVED from `.phi_allowed` below, and
# `BaseInferenceProvider.__init_subclass__` refuses at class-creation time to
# let the two drift apart.

#: `noop` — in-process deterministic mock. Note it satisfies EVERY PHI facet
#: except `phi_allowed`: nothing is transmitted, nothing is retained, nothing is
#: trained on. It is still refused for PHI, which is the point — a provider does
#: not become PHI-eligible by being harmless, only by being DESIGNATED.
#: (`NoopInferenceProvider` docstring: "Never PHI-capable … not silently
#: absorbed by noop".)
NOOP_CAPABILITIES: Final[ProviderCapabilities] = ProviderCapabilities(
    phi_allowed=False,
    deployment_region=DeploymentRegion.LOCAL_NO_EGRESS,
    retention_policy=RetentionPolicy.ZERO_RETENTION,
    training_on_input_prohibited=True,
    max_data_classification=DataClassification.INTERNAL,
    credential_source=CredentialSource.NONE,
    supported_model_versions=frozenset(),
)

#: `anthropic` — the GENERAL-ZONE cloud provider (class docstring: "General-zone
#: cloud provider (ADR-0006) — never PHI-capable"). Retention is declared
#: UNSPECIFIED rather than ZERO_RETENTION because no zero-retention agreement
#: exists anywhere in this repo's tree; declaring one would be fabricating a
#: contract. `training_on_input_prohibited` is False for the same reason — not a
#: claim that training happens, a refusal to assert a prohibition nobody signed
#: here. `max_data_classification` is INTERNAL: ADR-0006 puts "dado pessoal
#: sensível" in the PHI zone, and nothing in-repo sanctions PERSONAL for the
#: general zone either, so INTERNAL is the conservative floor pending the
#: MZO-040 Medical/ANS review.
ANTHROPIC_CAPABILITIES: Final[ProviderCapabilities] = ProviderCapabilities(
    phi_allowed=False,
    deployment_region=DeploymentRegion.GLOBAL_MULTI_REGION,
    retention_policy=RetentionPolicy.UNSPECIFIED,
    training_on_input_prohibited=False,
    max_data_classification=DataClassification.INTERNAL,
    credential_source=CredentialSource.ENVIRONMENT,
    supported_model_versions=frozenset({DEFAULT_ANTHROPIC_MODEL}),
)

#: `bedrock` — the GENERAL-ZONE cloud provider reached through AWS Bedrock. Same zone,
#: same conservative floors and same honesty rules as `ANTHROPIC_CAPABILITIES`; only the
#: transport and the credential mechanism differ. Two fields deserve their argument stated:
#:
#:  * `deployment_region=GLOBAL_MULTI_REGION` — NOT `BR_SAO_PAULO`, even though
#:    `DEFAULT_BEDROCK_REGION` is `sa-east-1`. FOUR independent reasons, any one of which is
#:    sufficient. (0) THE FACTUAL ONE, established by a live probe AFTER the other three were
#:    written: the only model id this account can actually serve is a `global.*` INFERENCE
#:    PROFILE (`DEFAULT_BEDROCK_MODEL`), and a global profile ROUTES CROSS-REGION BY
#:    CONSTRUCTION. So the region here is not merely unenforced — inference knowingly is NOT
#:    pinned to São Paulo. What began as a prudential refusal to over-declare turned out to be
#:    a plain statement of fact, which is the outcome this schema is built to produce.
#:    (1) The region is OPERATOR-CONFIGURABLE at runtime
#:    (`MAEZO_BEDROCK_REGION`), while `capabilities` is a `ClassVar` fixed at import — a
#:    static `BR_SAO_PAULO` would be a flat lie for a deployment that sets `us-east-1`.
#:    (2) NOTHING IN THIS CLASS ENFORCES THE REGION. `BrResidentInferenceProvider` earns its
#:    `BR_SAO_PAULO` with a construction-time AND per-call endpoint allowlist plus a
#:    per-response `served_region` attestation; this provider hands a region string to an SDK
#:    and checks nothing back, which is precisely the "UNVERIFIED SELF-DECLARATION" the
#:    `PHI_ELIGIBLE_REGIONS` comment books as owing a network-level proof. (3) `BR_SAO_PAULO`
#:    is in `PHI_ELIGIBLE_REGIONS`; declaring it here would leave `phi_allowed=False` as the
#:    SOLE thing standing between this provider and PHI eligibility, turning one future edit
#:    into a PHI route. `GLOBAL_MULTI_REGION` keeps the general zone structurally general.
#:  * `credential_source=AWS_DEFAULT_CHAIN` — see that member: no credential is read, held or
#:    validated by this process, so `ENVIRONMENT`'s "read at construction" claim is not ours
#:    to make.
#:
#: Retention/training/classification are copied from the Anthropic reasoning verbatim and for
#: the same reason: no zero-retention or no-training agreement with AWS exists anywhere in this
#: repo's tree, and declaring one would fabricate a contract.
BEDROCK_CAPABILITIES: Final[ProviderCapabilities] = ProviderCapabilities(
    phi_allowed=False,
    deployment_region=DeploymentRegion.GLOBAL_MULTI_REGION,
    retention_policy=RetentionPolicy.UNSPECIFIED,
    training_on_input_prohibited=False,
    max_data_classification=DataClassification.INTERNAL,
    credential_source=CredentialSource.AWS_DEFAULT_CHAIN,
    #: The id THIS REPO sanctions — the one PROVEN to serve on this account (see
    #: `DEFAULT_BEDROCK_MODEL`), not the account's catalogue. DECLARED, NOT ENFORCED — same
    #: leg-1 note as every other provider: enforcing a per-provider model allowlist would
    #: silently break `MAEZO_INFERENCE_MODEL`/`MAEZO_BEDROCK_MODEL_ID` overrides, which is
    #: exactly the escape hatch an operator needs when a profile id changes.
    supported_model_versions=frozenset({DEFAULT_BEDROCK_MODEL}),
)

#: `phi_zone_mock` — explicitly-labeled in-process stand-in for the
#: not-yet-provisioned BR-resident endpoint (`PhiZoneMockProvider` docstring).
#: It is the ONLY provider that satisfies `phi_zone_denial_reasons`, and it does
#: so honestly: `LOCAL_NO_EGRESS` (not a fabricated `BR_SAO_PAULO`), and
#: `supported_model_versions=frozenset()` because it runs no model at all. Its
#: mock-ness stays visible through `is_mock=True`, NOT through the capability
#: set — capabilities describe the zone contract, `is_mock` describes whether
#: the output is real (constraint 3). Both are asserted per-provider in
#: `tests/unit/runtime/test_inference_capabilities.py`.
PHI_ZONE_MOCK_CAPABILITIES: Final[ProviderCapabilities] = ProviderCapabilities(
    phi_allowed=True,
    deployment_region=DeploymentRegion.LOCAL_NO_EGRESS,
    retention_policy=RetentionPolicy.ZERO_RETENTION,
    training_on_input_prohibited=True,
    max_data_classification=DataClassification.PHI,
    credential_source=CredentialSource.NONE,
    supported_model_versions=frozenset(),
)

#: `br_resident` — the BR-resident, zero-retention adapter (Onda 2, W2 leg 2).
#:
#: ============================================================================================
#: READ THIS BEFORE BELIEVING THE DECLARATION BELOW
#: ============================================================================================
#: This is the FIRST capability set in this module that claims `BR_SAO_PAULO` + `ZERO_RETENTION`
#: + `training_on_input_prohibited` for a NON-MOCK provider, and leg 1 established (GK-verified)
#: that no DPA / zero-retention contract exists anywhere in this repo's tree. So the obvious
#: reading — "somebody signed a BR zero-retention agreement" — is FALSE, and this comment exists
#: so nobody reaches it.
#:
#: What this constant actually declares is the contract `BrResidentInferenceProvider` ENFORCES
#: CLIENT-SIDE, per field:
#:
#:  * `BR_SAO_PAULO` — the adapter refuses, at construction AND on every call, any endpoint
#:    outside `BR_REGIONAL_ENDPOINT_HOST_SUFFIXES`, and refuses a response that came back from a
#:    different URL than the one it dialed (redirect escape) or that fails to attest
#:    `served_region`. That is a real, testable, client-side residency check.
#:  * `ZERO_RETENTION` / `training_on_input_prohibited` — the adapter SENDS the zero-retention
#:    and no-training request flags (`HEADER_ZERO_RETENTION`, `HEADER_TRAINING_PROHIBITED`) on
#:    every request and REFUSES any response that does not echo both acknowledgements. That is
#:    the strongest claim a client can make on its own.
#:  * `max_data_classification=PHI` — the zone this adapter is designated for (ADR-0006).
#:  * `credential_source=ENVIRONMENT` — `MAEZO_PHI_API_KEY`, read from the process environment at
#:    construction, exactly like the Anthropic key; never a settings field, never committed.
#:  * `supported_model_versions=frozenset()` — THIS REPO SANCTIONS NO BR-ZONE MODEL ID. The
#:    vendor catalogue is unknown pending the DPA, and inventing a plausible model id would be
#:    the same fabrication leg 1 refused for the retention field. Empty is the honest set. (Leg 1
#:    already records that this field is DECLARED, NOT ENFORCED, so the empty set gates nothing;
#:    the adapter instead requires `MAEZO_INFERENCE_MODEL` to be set EXPLICITLY, with no default,
#:    so no model id is ever invented here either.)
#:
#: VENDOR-SIDE ATTESTATION IS NOT DISCHARGED BY ANY OF THAT. A vendor that ignores the flags,
#: retains anyway, and lies in the acknowledgement fields defeats every check above — client-side
#: enforcement cannot prove a counterparty's behaviour. That gap is closed by exactly two things,
#: neither of which is code in this file: the owner's DPA (gated here by
#: `MAEZO_PHI_VENDOR_DPA_REF` — WITHOUT IT THE PROVIDER REFUSES TO CONSTRUCT, so this
#: declaration cannot reach production on an agent's say-so), and the NETWORK-LEVEL proof the
#: `PHI_ELIGIBLE_REGIONS` comment books as owed by any non-mock PHI-eligible provider (ADR-0017
#: NetworkPolicy + the leg-3 canary harness).
#:
#: So: PHI-eligible BY DESIGN, UN-BOOTABLE UNTIL AN OWNER ACTS. Both halves are load-bearing and
#: both are tested (`tests/unit/runtime/test_inference_br_resident.py`).
BR_RESIDENT_CAPABILITIES: Final[ProviderCapabilities] = ProviderCapabilities(
    phi_allowed=True,
    deployment_region=DeploymentRegion.BR_SAO_PAULO,
    retention_policy=RetentionPolicy.ZERO_RETENTION,
    training_on_input_prohibited=True,
    max_data_classification=DataClassification.PHI,
    credential_source=CredentialSource.ENVIRONMENT,
    supported_model_versions=frozenset(),
)
