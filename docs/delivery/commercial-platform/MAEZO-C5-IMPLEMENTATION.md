# Maezo C5 implementation candidate

Owner and integrator: ROOT. Author: bounded third-party Maezo C5 integration.
Baseline: `2ff8fc63575026d3c6bff625f6eec1eef2f7ce2d`.
Disposition: implementation ready for independent exact-freeze review; **NOT
PUBLISHED / NOT OPERATIONALLY QUALIFIED**. This does not self-approve the code.

Trace: owner-authorized integrated commercial plan, S07–S08 / NR-05–06 /
CAP-07–10, REQ-C4-05 / COM-012; shared C5 proposal; ADR-0037
XRD-03/04/05/09. The 35-requirement denominator and original findings remain.
`accepted`, issuer contract issuance, enrollment and coverage remain distinct.

The exact upstream candidate is AMH commit
`d96520a16a31dbe0bbc07e3cd83c902bca30fee0`, path
`schemas/proposals/maezo/commercial-v1/shared-contract-c5.proposed.v1.json`,
SHA-256 `6aef2e1ec987834179db1acbd1ff2be32f5c406ca73622af5f905ec5b992d1e6`,
148990 bytes, 34 commands and 33 recoverable commands. Proposal-only independent
reviews are `architecture-security-c5-initial` and `requirements-c5-proposal` in
the Sales commercial package; their disposition does not qualify this code.
Runtime consumes exact bytes injected by trusted composition. ROOT separately
imported the immutable test-only artifact with source provenance; runtime never
loads that artifact or falls back to it. Historical C4 artifacts and published
contract pins remain preserved.

## Implemented behavior

`CommercialWire` accepts `offer.terms`, derives typed selectors only from the
closed source-owned schema and requires `terms_review_ref` in acceptance input
and receipt. Quote, source revision, terms digest, review reference, party linkage
and decision must match exactly. Old C4 bytes and old acceptance input/receipts
are refused rather than promoted.

`IssuerDocumentOrigins` is an independent, current server-source port, separate
from PSP hosted origins. A missing issuer binding refuses fresh document results.
The public `compose_commercial_gateway` factory forwards this optional port
explicitly and defaults to absent; no environment activation exists. The provider
resolves exact issuer origins and authenticated handoff validity for
the quote/version/digest/review; the qualified source owns redirect/authentication
policy. No origin, document content, endpoint, receipt or source grant is invented.
Fresh documents require HTTPS, exact qualified origin, no userinfo/fragment and
only the qualified HTTPS port. Published absolute expiry must be in the future;
null remains source-declared unknown and still requires current source validity.
The existing opaque Stripe fragment exception remains limited to qualified
`https://checkout.stripe.com` payment/mandate actions, never issuer documents.

`TermsReviewAuthority` is an authenticated source observation supplied by the
source preparation port. It binds the exact quote/version/digest/review to the
full scope digest, including tenant, account, actor, represented party,
representation, purpose and source binding. Source currentness/document-validity
references and expiry are required. Presentation-receipt validity is a separate
fact. Pending handoff remains possible without a presentation receipt. Acceptance
requires presented state plus a current receipt; decline permits a currently
bound pending review. Gate checks run before dispatch and disclosure; source
preparation never creates authority from selectors. The recording source must
independently enforce current issuer/actor/representation/presentation evidence
atomically with actual acceptance. A Maezo object or M2M credential cannot replace
that enforcement.

Fresh result presentation lineage must match the prepared proof. After awaited
disclosure-authority checks, the codec rechecks ephemeral URL expiry and current
issuer-origin authority. A late revocation suppresses the URL and returns bounded
uncertainty with the original operation reference. No local retry is introduced.

Recovery schema validation precedes sanitation. `offer.terms` recovery accepts
only its exact sanitized variant, with `hosted_document: null`; wrong kind,
recursive `operation.get` and unsanitized terms URLs are refused. The bounded
pure sanitizer removes hosted documents and actions without mutating its input.
A recovered result has no fresh presentation object and cannot authorize acceptance.
A sanitized original cannot be presented as fresh `offer.terms` success. Direct
completed-command replay refusal and before-journal sanitation remain obligations
of the AMH executor/journal, where durable command custody actually resides;
Maezo has no journal or locally invented replay authority.

## Lane, evidence and custody

ROOT granted targeted UNIT with `--noconftest`, owned Ruff and strict mypy in its
private Python 3.12.13 commercial environment. Nine preimages were retained in
private ignored environment custody before editing. No Git mutation, app mount,
engine/integration test, broad test discovery, infrastructure or shared build
resource was used by this author. No historical restricted provider test was read,
hashed, imported, executed or copied.

Final targeted command:

```sh
.venv-commercial/bin/python -m pytest -q --noconftest tests/unit/test_commercial_gateway.py tests/unit/test_commercial_api.py tests/unit/test_commercial_http.py tests/unit/test_commercial_recovery.py tests/unit/test_commercial_c5.py
```

**106 UNIT passed, zero skips**. The exact C5 loader checks mandatory artifact
provenance and byte equality; missing custody fails rather than skipping. Cases
cover fresh pending/presented handoff, null/future expiry, unsafe origins and
fragments, expired/invalid dates, issuer revocation, mismatched selectors/review,
old C4 refusal, pending acceptance denial, current pending decline, expired or
foreign actor/representation scope proof, strict sanitized recovery, and final
issuer revocation before disclosure. Existing authenticated HTTP/operation
identity, original-owner recovery, uncertainty and Stripe fragment regressions
remain included. Factory tests exercise the actual fixed authenticated HTTP
composition with a qualified synthetic issuer and without it, demonstrating that
a PSP origin cannot admit a document. A minimal HTTP status type narrowing retains
the exact response/operation mapping; malformed null/array/unknown statuses
preserve one-dispatch uncertainty and the original operation reference. Doubles
are explicitly synthetic source authorities; this is
UNIT evidence, not actual source presentation or acceptance.

`ruff check` and `ruff format --check` passed across the five source files
and all five targeted test files. Strict mypy with `--follow-imports=silent`
passed for the same five source files. No external integration, live issuer,
Tasy/PSP operation, source publication, human acceptance or production ran.

## Frozen paths and dependencies

The author changed ports, gateway, adapter, admission composition and HTTP
transport source files, gateway and HTTP fixture tests, and added the C5 test plus
this handoff. ROOT explicitly extended scope for minimal admission forwarding and
HTTP status narrowing. API/recovery tests and router source below are unchanged
dependencies, not additional author changes.
ROOT retains ownership of test artifacts, Git/CI, whole-tree freeze and integration.

| Path | SHA-256 |
|---|---|
| `src/maezo/ports/commercial.py` | `eb12c8aedda8805eb01f264771dccd653ce1dbcd91b3112a86ebf4b116f54b51` |
| `src/maezo/gateway/commercial.py` | `255fb88f1ff813c00125e8b0843b3911fd4b875ff2443aacc5c98ba919c7270f` |
| `src/maezo/adapters/amh/commercial.py` | `b775e06000c45607beee3613f2c74c47b06b4d9d3e9ee44e650ba32c9d128210` |
| `tests/unit/test_commercial_gateway.py` | `287eb7b036aac048d81462277d27278dfd3d12844caf8a63115d6c0bb6e7d47e` |
| `tests/unit/test_commercial_http.py` | `8be3f41972f1cb1e2a3b0a91d78510d458bb9438b24bf5f5516329d9ba24c675` |
| `tests/unit/test_commercial_c5.py` | `3205fa83eebd924b8c51ef3ed8ea1edd95c9558ce4cbf8c55549e2c661771cbb` |
| `tests/unit/test_commercial_api.py` | `36027e624459a3b23394f905663fa27934e19fcd53fc8585573b49a907ee8515` |
| `tests/unit/test_commercial_recovery.py` | `69336cb6c4abc8321989675e6ad3179647673d239765909c199fab98d6ac2a43` |
| `src/maezo/adapters/amh/commercial_http.py` | `1191130f30d721f735961931f8e397c8ad838817fe45d793c8fbada7f37c3dbc` |
| `src/maezo/gateway/commercial_admission.py` | `606758c66b57e869ad7c14dd70b3b5949f16d1f3d861e5e802498cd7871c8407` |
| `src/maezo/portal/api/commercial.py` | `8b444973b211c51613f66bf20371223e196be6e51fe3ed8b75192dc9de9c3b2a` |
| `tests/candidate_artifacts/commercial/contract-c5.json` | `6aef2e1ec987834179db1acbd1ff2be32f5c406ca73622af5f905ec5b992d1e6` |
| `tests/candidate_artifacts/commercial/PROVENANCE-C5.json` | `08ca80bb974c8568db7c74e33a2bb49eadb2b17ce14076cf44c262a90cfb5c6f` |

Next gate: ROOT composition readback, independent original-reviewer rechecks on
this exact candidate plus the final cross-repository dependency freeze, exact-HEAD
CI and qualified external source/issuer acceptance. Keep the checkout and private
preimages under ROOT custody until integration/recovery pointers and consumer pins
are resolved. No source or production acceptance is inferred from these results.
