# Maezo commercial implementation candidate

Owner: ROOT; author role: Maezo modules only. Baseline: `2ff8fc63575026d3c6bff625f6eec1eef2f7ce2d`.

This package implements the owner-authorized commercial platform plan recorded in
PLANS.md, CHECKPOINT.md and RUNBOOK.md. It is a new commercial contract proposal,
not a reinterpretation of SP-OP-PAGTO-001 (provider disbursements), the existing
WhatsApp billing-status scope, OP03 acceptance or OP04 issuance.

Authority remains with source systems and their qualified adapters. ADR-0037
XRD-03/04/05/09 and immutable prohibitions remain in force: no source credentials,
raw source identifiers, editable AMH schema or published-pin history is added.
New Sales-side requirements and interfaces are proposals until admitted by the
contract owner. No contract, source integration, legal license or production
qualification is inferred from successful unit tests.

## Composition boundary

The new router is mounted only by ROOT in portal/api/app.py. A separately
composed `SalesAdmission` acquires a server-owned commercial scope before any
body intake. It independently resolves the current Sales session, account,
representation, tenant/legal entity, purpose, lifecycle and source mandate;
C2's command endpoint requires a qualified actor. Public marketing remains static;
C2 does not publish an anonymous catalog policy or anonymous subject intake grant.
A future catalog-only public profile requires its own source-qualified proposal. Portal cookies, request headers/browser roles, environment
flags and a Cognito M2M credential cannot establish that buyer authority.

Missing admission or commercial source bindings return explicit unavailability.
Default deployment cannot admit REAL intake or dispatch any source effect.
No binding to Tasy or PSP credentials is added to the portal.

## Evidence and custody

The current external executable schema is ROOT's independently reviewed C2
successor `shared-contract-c2.proposed.v1.json`, frozen at SHA-256
`02d04c3a3711b227e1f17c118c6b7934c632a836ddbf2ea4029cd6f48eb956c3`.
C0 hash `f3cc82efa032c1fd17a08f8d54d7436b018cb9dd1072f69c56b3839762b0b6be`
and the earlier seven-path author freeze remain historical; the current adapter
rejects C0 bytes rather than treating the old candidate as current.
It is read from the Sales package at trusted composition time; it is not copied
into this repository. Its hash establishes the software candidate only. Actual
AMH publication/source-currentness evidence is independently required at runtime.
ROOT granted a bounded lane using `.venv-commercial/bin/python`. The author does not
commit, mount app.py, mutate manifests, run integration or change any physical
resource. ROOT retains the worktree until integration/review and source admission
gates are resolved. The missing `docs/reports/predeploy-findings.json` referenced
by AGENTS.md was reported to ROOT; no substitute historical evidence was invented.


## Delivered paths and runtime hook

- `src/maezo/ports/commercial.py`: immutable neutral envelopes and explicit
  admission, codec and source ports; opaque account/prospect/party/payer/member
  domains remain separate and no subject is locally minted.
- `src/maezo/gateway/commercial.py`: current authority before source reads,
  preparation, effects and disclosure; command/scope/source proof binding;
  source-issued contract/proceed proof and all-three-method readiness before
  receivable/payment effects. No calendar, amount, price, eligibility or clinical
  decision is computed here.
- `src/maezo/adapters/amh/commercial.py`: external current C2 schema validation, 27-command
  allowlist, result-kind/target pairing, closed statuses, provider/method checks,
  bounded JSON, duplicate-key rejection and qualified hosted-origin restrictions.
  Governed executor publication/preparation/effect seams have no implementation
  or credential default. Only the fixed proposed AMH command target is declared;
  preparation is an internal source port, not an invented HTTP endpoint.
- `src/maezo/portal/api/commercial.py`: exact POST
  `/api/v1/sales/commercial`, no catch-all proxy. Server connection provenance is
  supplied only via the server-owned `sales.commercial.connection_ref` extension;
  no request header, portal cookie or browser role is transplanted.

ROOT integration hook: import `CommercialGateway` and the new `commercial_router`,
`is_commercial_request` and `commercial_error`; add optional factory argument
`commercial_gateway: CommercialGateway | None = None`; install the value at
`app.state.commercial_gateway` and mount the router. The host-validation branch
uses `commercial_error("denied", "invalid_request", http_status=400)` for the exact
commercial path. Existing privacy/security middleware remains applicable. No
legacy subject/WhatsApp gate is widened. The source/admission providers remain
absent in default composition, so REAL request bytes are not consumed.

AMH must atomically revalidate source/mandate/identity/representation, resource
membership, immutable billing/account linkage, current signed designated-issuer
receipt, current proceed evidence, registry readiness and durable dedupe before
its actual effect. A dataclass, matching hash, receipt selector or preparation
lease cannot establish those facts. The designated Tasy write interface remains
unknown and unbound; no vendor write was guessed. Source preparation/publication
or executor missing means unavailable, even if an HTTP caller authenticated M2M.

Cancellation, timeout or a lost/malformed response after dispatch returns
`uncertain/reconciliation_required` and preserves the source operation reference.
There is no local retry or idempotency cache; the durable AMH journal retains
custody and reconciliation uses the original attempt/account under current
authority. Revocation, changed revision, expired proof, cross-tenant evidence or
missing issuer/proceed/registry facts prevent effects. Source failures are
sanitized and result data is suppressed if authority changes before disclosure.

## Historical author validation — C0 (superseded)

The exact targeted command was:

```sh
.venv-commercial/bin/python -m pytest -q --noconftest tests/unit/test_commercial_gateway.py tests/unit/test_commercial_api.py
```

Result: **29 source-independent UNIT passed**, no skips. These exercise unbound
pre-body refusal despite browser/cookie/M2M claims, the exact route allowlist,
currentness ordering, revocation at source/effect/disclosure, changed authority,
tenant/scope/command binding, contract-issued/proceed/method proof requirements,
expiry, cancellation/lost-response uncertainty and current reconciliation/revoke
authority. They use explicitly synthetic source/codec/admission doubles and prove
no real source, buyer identity or issuer/provider authority.

Targeted `ruff check`/`ruff format --check` and strict mypy
(`--follow-imports=silent`) cover the four new source modules; ruff also covers
the two new UNIT files. **PASS**. No broad suite/import scan or engine was run.

A separate author conformance run used the actual external frozen C0 bytes:
**17 synthetic checks passed** for admitted read/result pairing, recursive unknown
keys, duplicate keys, internal command rejection, boolean revision, invalid
provider/method, empty imports, mismatched resource, fractional minor units,
invalid dates and refusal statuses. A synthetic ASGI laboratory using the exact
new router, real C0 codec, gateway and governed adapter returned a correct
receivable read once with no-store; this is source-independent laboratory evidence.
It did not run the existing composed app factory or an actual AMH/source service.

Actual AMH/Tasy/PSP/identity/lifecycle integration, source publication, original
app-factory runtime mounting, human acceptance, operation and production remain
**NOT_RUN / NOT_QUALIFIED**. UNIT counts are not source acceptance. Independent
review, ROOT composition, exact final-tree CI and external qualification are next.

Trace: S01–S06 / NR-01–04 / CAP-01–06 for catalog/quote; S07–S08 /
NR-05–06 / CAP-07–10 for separate acceptance/issuance/member/coverage; S08–S09 /
NR-07/10 / CAP-13–16 for reconciliation/recovery; S10 / NR-09 preserves vendor
limits. New CP-01..06 are explicit proposals as defined by the C2 successor, not historical
capability admissions. Existing ADR-0037 boundaries remain unchanged.


## C2 successor and final author validation

ROOT authorized this successor after independent C2 proposal PASS in
`reviews/architecture-security-c2-recheck.{md,json}` in the Sales package. The
current 27-command typed vocabulary adds `acceptance.record` and `operation.get`.
The exact existing 25 command payloads remain source-schema owned and unchanged.
No editable schema or replacement AMH publication manifest is created here.

`acceptance.record` sends exact quote/source revision/party linkage/terms and
accept-or-decline selection to the admitted source; the returned decision and
all selectors must match. A checkbox, account or Python value object cannot mint
an accepted source receipt. OP03, contract issuance, membership and coverage
remain separate. Missing source bindings still refuse REAL intake/effects.

`operation.get` accepts only the original `target_command_ref` selector. Current
original-owner, source and resource authority must be verified before any lookup
and before disclosure. Foreign and absent targets must use the same non-leaking
source refusal. The source port reads the original operation only; it must never
redispatch the original command. A one-level closed original envelope is validated
against the source-owned schema and original command/result kind. Semantic result
guards also apply to its nested projection. All `hosted_action` values are removed
recursively from recovered results before returning them; unknown fields or
recursive operation-status results are rejected, never laundered by sanitation.
AMH must apply equivalent sanitation before durable journal storage. Maezo has no
local journal/cache, and opaque recovery refs do not recreate source authority.

C2 permits `HostedAction.expires_at = null` only as source-declared unknown
absolute validity. No midnight, boleto due-date or TTL expiry is synthesized.
Fresh delivery still requires current source/account/resource authority and an
approved HTTPS provider origin; a known-expired action is refused. No hosted URL
is replayed from an original result, irrespective of known/unknown expiry.
Post-dispatch uncertainty carries the nonnull original source operation ref and
retains durable source custody; it cannot authorize retry or a provider switch.

Final targeted author command:

```sh
.venv-commercial/bin/python -m pytest -q --noconftest tests/unit/test_commercial_gateway.py tests/unit/test_commercial_api.py tests/unit/test_commercial_recovery.py
```

Result: **37 source-independent UNIT passed, zero skips**. New cases cover
original-owner denial before source lookup, recovery-only dispatch, current
authority for acceptance, nonnull post-dispatch uncertainty reference, recursive
immutable sanitation of hosted URLs with null/future/past expiry, and depth bounds.
All earlier targeted gateway/ingress cases remain included. `ruff check`,
`ruff format --check` across the four source modules and three test files, and
strict mypy across the four source modules: **PASS**.

External exact-C2-byte conformance: **31 synthetic checks PASS**, including the
27-command vocabulary, C0 rejection, acceptance target/revision/terms/decision
matching, closed recovery selector, original result kind/target/nesting guards,
non-null uncertainty refs, recursive semantic checking, unknown inline URL expiry,
known-expired action refusal and unconditional recovery URL removal. These checks
use the external schema bytes and explicit synthetic facts, not a copied schema.

A synthetic ASGI laboratory used the actual C2 codec/router/gateway/governed
adapter for acceptance plus operation recovery: **PASS**. Only
`acceptance.record`, then `operation.get` reached the synthetic executor once each;
the original mandate effect was not redispatched, and its hosted URL was absent
from the recovered response. This remains a source-independent laboratory,
not the existing composed app factory or actual AMH/source service.

C0's 29 UNIT / 17 conformance checks / earlier laboratory remain historical
results, not the current candidate denominator. Current full-app mounting, final
exact-tree CI, source publication, external identity/lifecycle/representation,
Tasy/PSP qualification, human acceptance, operation and production remain
separate gates for ROOT and the competent source owners. No actual REAL or
financial/source integration ran in this author lane.
