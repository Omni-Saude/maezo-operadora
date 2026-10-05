# Measurement/export contract — distinct third repair R2

State: PROPOSED, awaiting original measurement verifier delta. This package
repairs only MEAS-01–04 from gate `7653eee5857e0fce9f753908e5ffc5eff6e4aa69a500889192c6477f4297e8c4`.
It grants no source access, emitter, export publication, policy, channel or
production acceptance. No measurements or provider receipts were supplied.

Original dossier author `7126847303533a334129c55be36619b2dbe232a9b6a75ed3dde438f39bc2e1d8`
and all 15 named outputs remain byte-preserved. The exact M01–M13 canonical
definition objects, including original qualifications, are copied unchanged.
All actual values, new targets, baseline periods and ROI remain UNMEASURED.
M09's original correctness-target-zero wording is preserved; it is not a measured
zero or a new business target. Other dossier/financial/matrix files are untouched.

## MEAS-01: private lineage and minimized export are different records

`measurement-protocol.proposed-v2.json` separates each metric's
`provider_private_computation_inputs_proposed` from its exact
`allowed_aggregate_output_components_proposed`. Intention/offer/version/commitment/
case/journey/message/operation/event identifiers, private authority/receipt refs,
protected-individual joins and per-event timestamps stay with their qualified
provider. These are conceptual private computational dependencies, not a new
provider API, source-purpose admission or request to query a lake.

`measurement-export-closed-shapes.proposed-v2.json` closes four proposal shapes:
provider-private lineage row, pending metric definition admission, pending
component mapping admission, and minimized aggregate export. All object shapes
forbid extra properties. The aggregate shape contains no private reference,
per-event timestamp, history, raw content or arbitrary dimension-value channel.
Public definition/contract/policy/cohort/aggregate publication revisions are
metadata about approved definitions/releases; they cannot alias private records
or individually identifiable actor/customer data. Aggregate window boundaries
and aggregate as-of are aggregate release metadata, not copied event timestamps.

Allowed outputs are only the listed aggregate count components, permitted ratio
components and approved distributions. M02 has separate accepted-decision and
rejected-decision distributions; combining them is not the declared metric.
M07's repeated-step distribution uses customer-repeated-step units, not latency
seconds. M13 cannot publish a vanity reuse percentage or a latency distribution;
its independently qualified scenario/evidence classification remains pending.
No numerical result is present in this package.

## MEAS-02: every metric has explicit blocking definition/component admission

Each M01–M13 carries a closed `definition_admission_proposed` record and a
closed `component_mapping_admission_proposed` record. They have versioned schemas,
metric identity, required fields, explicit PENDING_OWNER/BLOCKED_PENDING_OWNER
state and null publication/role/ratification revisions. There is no omission
that could be mistaken for an approved default.

The required per-metric owner rules cover eligibility, actor-authority class,
lifecycle stage, cohort-window assignment event, source clock/timezone, window
boundary convention, horizon/maturity, right-censoring, finality/watermark,
quantile estimator/histogram bins and units, vocabularies, receipt observability,
allowed nonclinical groupings, protected-person support, lawful-basis/consent
applicability, withdrawal/revocation/erasure, correction/retraction/reissue,
reconstruction resistance and role-bound owner admission. Actor-class rules are
categorical source eligibility rules; an individual actor identity is never an
export field. No actor/class/stage/status/vocabulary membership is ratified here.

Every proposed count separately requires source category, partition-axis,
mutual-exclusion scope, subset-parent, join/dedup, maturity/censoring and release
support-group revisions. These remain null/PENDING_OWNER. A generic count sum,
receipt-bearing subset or absent classification does not qualify a denominator.
Any missing required rule or component mapping prevents metric/export admission.
An estimator or rule genuinely inapplicable to a metric still requires an
owner-admitted applicability decision/version; null is not such a decision.

| Metric | Owner-specific mapping that must be resolved |
|---|---|
| M01 | Qualified authenticated intention cohort; usable-offer sample and all-intention/no-offer/censoring population; intention assigns window |
| M02 | Exact presentation-receipt offer version; current customer/delegate decision; separate accept/reject distributions and pending/expiry/withdrawn population |
| M03 | Deduplicated presented versions and matured denominator; replaced versions and withdrawn membership are distinct axes, not silently dropped rows |
| M04 | Approved intention horizon; technical/policy-block exclusions reported; no-decision before maturity remains pending |
| M05 | Original accepted commitment and same authoritative endpoint; all accepted failed/pending/censored partitions remain visible |
| M06 | Explicit original commercial promise and source-approved revision; original lateness retained; regulatory clock remains a separate series |
| M07 | Resolve the canonical completed-versus-matured cohort mapping, rework vocabulary and follow-up; customer rework differs from technical retry |
| M08 | Distinct mature journey, source contact-session delimiter and fixed follow-up; unmatched links distinct, messages are not contacts |
| M09 | Intended operation identities and independent source-effect observability; callback dedup is not absence of duplicate partner action |
| M10 | Original independently known commit cohort, valid/missing/receipt-uncertain partition, commitment-unknown reconciliation and current/withdrawn membership |
| M11 | Source-qualified eligible message-key/channel/purpose attempts; delivery/read/ACK/inbox/prepared states distinguished; delivery maturity/source cutoff |
| M12 | Same-case maturity requiring confirmation; source resolution plus current requester manifestation; disagreement/silence/timeout/closure never implicit confirmation |
| M13 | Exact implementation/contract version and independent actual Compras plus Suporte scenario/recovery evidence; UNIT/STAGING/operational source scopes separated |

Current records select no event-time timezone, boundary convention, fixed horizon,
estimator, histogram bins, new status, retention duration or legal basis. These
are required decisions for the source/CX/domain/Security/DPO owners, not values
the agent fills to permit export. Numerical future provisional/final shapes
require non-null public definition, owner-admission, source, stage, window,
maturity, censoring, privacy/lifecycle and correction revisions. Mere shape,
revision text, digest or signature does not authenticate those owner acts.

## MEAS-03: protected-person support and complete release lifecycle

The pinned population contract commits floor k=10. It does not publish these
business-metric exports. The proposed business output requires source/DPO-owned
same-or-stronger protection with support counted as DISTINCT protected individuals
inside protected source custody. Ten events, messages, cases, operation keys or
receipts are never evidence of ten protected individuals. Identity joins and
support witnesses stay private; they are not a new MAEZO dataset.

The source/DPO release decision covers every count, ratio numerator/denominator,
histogram bin, quantile support and related release group. It covers complementary
totals, overlapping windows, correction versions and baseline/pilot differences
together. Small/withheld constituents cannot be reconstructed by subtraction.
Suppressing one primitive can therefore require suppressing another primitive,
derived ratio/distribution or total. A withheld count is None, never zero. A real
observed zero requires verified eligible observation and privacy release. A
publishable zero denominator yields N/A and a null ratio, never a zero ratio.

Before admission, source/DPO must resolve permitted purpose, lawful basis,
consent applicability and source snapshot lineage, plus current withdrawal,
revocation, erasure and source-retraction epochs. Applicability is not assumed
false when no evidence exists. No retention duration or new basis is chosen.
Clinical variables, proxies for clinical/care risk, commercial risk selection,
individual segmentation and free-text/person histories are prohibited in export
cells, labels and selection rules. Event/person/receipt IDs never become Prometheus
labels. A future nonclinical categorical-dimension extension requires its own
published closed enum/schema, owner decision and reconstruction review.

The proposed lifecycle is PROPOSED_UNADMITTED → source-admitted PROVISIONAL/FINAL
→ SUPERSEDED, RETRACTED or WITHHELD when affected by a correction or lifecycle
change. Every disclosure and derived calculation requires current source
aggregate-head/lifecycle verification. On withdrawal/revocation/erasure or source
fact retraction, the qualified source owner determines affected membership,
cohorts, windows, prior exports and derivatives under the admitted policy. Affected
numeric publications/caches/comparisons are invalidated before further disclosure.
Uncertain/missing current authority withholds output; it never revives an older
snapshot. A still-valid old signature or content digest cannot override that state.

Any replacement requires fresh source recomputation against current allowed
membership and current facts, independent reconciliation, privacy/reconstruction
review across release history, a new aggregate revision with explicit supersession
and current source-order/head witness, and fresh owner admission. Opaque revision
refs are never numerically/lexically ordered. Previously withdrawn/retracted/
superseded exports cannot become current through cache replay or reused approval.
If no authorized replacement exists, values remain UNMEASURED/None. This concerns
measurement publication; it grants no deletion or mutation of authoritative
business ledgers, receipts, domain clocks or protected custody records.

M13 can obtain an engineering-only privacy applicability exemption solely through
independently admitted evidence that it contains no customer/protected-person data.
That exemption is currently pending. Any included customer counts inherit the
same protected-person/k/lifecycle controls. Source-unqualified synthetic domains
do not prove operational reuse.

## MEAS-04: M10 committed denominator and source reconciliation

Keep the canonical definition exactly: valid authoritative receipts / committed
effects; incomplete/unknown outcomes remain explicit. A terminal receipt is not
the definition of a committed effect. Independent source commitment evidence is
required, and source receipt verification is a separate fact. Neither dispatch
attempt, transport ACK, local journal fence nor callback dedup proves either.

Use source-private mutually exclusive receipt states for a known committed effect:
valid, missing, or receipt-uncertain. Cross them with current versus withdrawn
export membership; unresolved membership is separately withheld. Counts are
aggregates only and each is subject to its own support/reconstruction release:

```
C_current = V_current + M_current + U_current
C_withdrawn = V_withdrawn + M_withdrawn + U_withdrawn
Candidate_total = C_current + C_withdrawn
                + CommitUnknown_current + CommitUnknown_withdrawn
                + NotCommitted_current + NotCommitted_withdrawn
                + MembershipUnknown
```

`C_current` is the canonical current eligible committed denominator, including
known committed effects whose receipt is missing or uncertain. The ratio is
`V_current / C_current` only when both are positively reconciled and releasable
at the same admitted definition/cohort/aggregate revision. It is never
`valid / settled-only`. Known committed missing/uncertain facts cannot disappear
because a terminal receipt is absent. Immature/right-censored categories are
source-owned tags/subsets, not additional top-level terms or a silent reason to
delete a known committed effect from the canonical denominator.

CommitUnknown describes effects for which commitment itself is not independently
proven; no commitment is fabricated for that category. It remains in the original
candidate reconciliation/observability report, with current/withdrawn membership.
Verified NotCommitted and MembershipUnknown remain separate. Source/CX/domain
owners must admit the exact cohort, cutoff, maturity, independent commit criterion
and finality/coverage mapping before any displayed result. Unknown commitment or
missing observation may require provisional/partial/UNMEASURED status rather than
false complete coverage. A missing receipt requires receipt-first source
reconciliation, never a blind resend of the business effect.

Withdrawn business facts remain governed by their source business authority.
Their measurement membership and affected prior releases are handled by the
source/DPO lifecycle above. Withdrawal counts/related totals themselves can be
protected: if release is forbidden they are None, and no public subtraction may
recover them. The private owner/verifier still reconciles the entire candidate
population. No mixed old numerator/new denominator, reused withdrawn release or
hidden settled-only export can pass these constraints.

## Review custody and scope

`projection-evidence.v2.json` records field-name/schema and symbolic relation
controls, not measured values or data. Proposal schemas and all 13 pending metric
and component mapping records are checked locally. Negative shape controls must
reject private columns, extra fields, suppressed/withdrawn numbers, pending
admission numerical releases, empty-denominator zero ratios, merged M02 decisions
and M13 vanity percentages. All original author outputs are rehashed.

The distinct original measurement verifier must evaluate exact repaired bytes
and close or retain each MEAS finding. This author does not self-approve. No
source/runtime/collector/emitter, actual export data, policy/clinical rule, ADR,
Git, database, engine, shared financial file or remaining-gate matrix was changed.
W0-MEASUREMENT remains blocked pending actual owner/source admission and authorized
aggregate evidence; independent core engineering can continue under its own gates.
