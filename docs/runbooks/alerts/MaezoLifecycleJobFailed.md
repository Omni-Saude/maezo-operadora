# Alert Runbook: MaezoLifecycleJobFailed

**Audience:** Platform engineers, SRE, on-call, compliance/DPO liaison
**Last updated:** 2026-09-02
**Applies to:** Kubernetes CronJobs `lifecycle-expurgo-working`, `lifecycle-verify-erasure`,
`lifecycle-audit-retention` (`deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml`)
**Alert source:** `deploy/observability/alert-rules.yml:171-188` (group `maezo_lifecycle`)
**SLO context:** [`docs/observability/SLO.md`](../../observability/SLO.md) §2, §6 row 8

---

## Table of Contents

1. [Trigger](#1-trigger)
2. [What this means in business terms — read this before paging anyone](#2-what-this-means-in-business-terms--read-this-before-paging-anyone)
3. [Expected failure vs. real failure — the distinction that matters](#3-expected-failure-vs-real-failure--the-distinction-that-matters)
4. [First 5 minutes](#4-first-5-minutes)
5. [Triage tree](#5-triage-tree)
6. [Mitigation](#6-mitigation)
7. [Escalation](#7-escalation)
8. [False-alarm checks](#8-false-alarm-checks)

---

## 1. Trigger

**Code:** `deploy/observability/alert-rules.yml:171-188`

```yaml
- alert: MaezoLifecycleJobFailed
  expr: |
    kube_job_status_failed{job_name=~"lifecycle-.*"} > 0
  for: 5m
  labels:
    severity: warning
    category: lifecycle
    component: lifecycle
```

**This alert has no series to evaluate today, but for a different reason than the DLQ alerts.**
`kube_job_status_failed` is a real, standard `kube-state-metrics` metric — it is not a fabricated
or synthetic name — but no `kube-state-metrics` scrape job exists in
`deploy/observability/prometheus.yml` (its 5 jobs: `maezo-app`, `maezo-workers`,
`otel-collector`, `otel-collector-metrics`, `prometheus`). The rule's own comment
(`alert-rules.yml:165-169`) states this precisely: *"requires a kube-state-metrics scrape target
in prometheus.yml (not yet configured)... it does not fire falsely, it simply has nothing to
alert on yet."* Wiring that scrape target (an owner-gated `deploy/` change) is the only thing
needed to make this alert live — the metric itself and the `job_name=~"lifecycle-.*"` selector
are both correctly shaped for when it is.

## 2. What this means in business terms — read this before paging anyone

**Every invocation of the three lifecycle CronJobs fails BY DESIGN, today, on `main`.**
`src/maezo/platform/lifecycle/__main__.py` and its package `__init__.py` implement an
intentional fail-closed refusal stub (T2.8) — the module's own docstring states the reasoning in
full:

> Per the ADR-0020 amendment draft, NO prune may run without BOTH (1) a legal-hold registry and
> (2) a signed-checkpoint re-anchor mechanism (ADR-0029). Neither exists. Therefore every
> invocation of this entrypoint REFUSES and exits non-zero. That is the deliverable: fail-closed
> by design, not by accident.

Every invocation exits with code **78** (`REFUSAL_EXIT_CODE`, `sysexits.h` `EX_CONFIG`) — a
deliberately distinct, non-`1` code so an operator can tell "deliberate refusal" apart from a
generic crash. **A firing `MaezoLifecycleJobFailed` alert today (once the scrape target exists)
is therefore an EXPECTED, recurring signal, not an incident** — it is the intended, loud
visibility for a gap that used to be invisible (`ModuleNotFoundError` swallowed by the CronJob
pod before this module existed).

## 3. Expected failure vs. real failure — the distinction that matters

The three subcommands (`SUBCMD_EXPURGO_WORKING`, `SUBCMD_VERIFY_ERASURE`,
`SUBCMD_AUDIT_RETENTION` — `lifecycle/__init__.py:91-94`) each have a **specific, DPO/ADR-gated
blocker**, not a generic "not implemented" message. Read the pod logs and match the message
against these to confirm EXPECTED failure:

| Subcommand | Expected refusal message (verbatim source) | Blocked on |
|---|---|---|
| `audit-retention` | `"audit-retention refused: prerequisites absent — legal-hold registry (ADR-0020 amendment) + chain re-anchor mechanism (ADR-0029); see docs/compliance/ADR-0020-amendment-draft.md"` (`lifecycle/__init__.py:100-104`, `AUDIT_RETENTION_REFUSAL`) | ADR-0020 amendment + ADR-0029 ratification, both unratified |
| `expurgo-working` | `"expurgo-working refused: the maezo.platform.lifecycle entrypoint is an intentional fail-closed refusal stub (T2.8)... Blocked on the DPO legal-bases/retention matrix: unavailable (<reason>): <detail>..."` (built by `_matrix_gated_refusal`, `lifecycle/__init__.py:124-159`) | DPO legal-bases/retention matrix (`MAEZO_RETENTION_MATRIX_PATH` unset or the matrix itself unratified) |
| `verify-erasure` | Same shape as `expurgo-working`, PLUS: `"verify-erasure is additionally blocked, independent of the matrix, on the thread_id→fhir_patient_id mapping design gap in ErasureManager (see erasure.py)."` (`_VERIFY_ERASURE_EXTRA`, `lifecycle/__init__.py:118-121`) | DPO matrix AND the `erasure.py` thread_id→fhir_patient_id mapping gap (both, independently) |

**EXPECTED failure** = pod logs show one of these exact messages (or a close paraphrase — always
re-read the live message; the exact wording is authored to be self-describing) and exit code
`78`. **REAL failure** = anything else: a Python traceback/`ModuleNotFoundError` (the pre-T2.8
accidental-failure mode this module replaced), exit code `1` or a signal-based exit, a
`CrashLoopBackOff` from something other than the refusal completing normally, or a refusal
message that does NOT match the table above (indicating the module changed since this runbook
was written — re-verify against current `src/maezo/platform/lifecycle/__init__.py` before
treating as expected).

## 4. First 5 minutes

```bash
# 1. Get the failed Job and its pod:
kubectl get jobs -n maezo-{tenant} -l app.kubernetes.io/component=lifecycle
kubectl get pods -n maezo-{tenant} --selector=job-name=<lifecycle-job-name>

# 2. Read the refusal message and exit code — this is the whole diagnosis:
kubectl logs -n maezo-{tenant} <pod>
kubectl get pod -n maezo-{tenant} <pod> -o jsonpath='{.status.containerStatuses[0].state.terminated.exitCode}'

# 3. Match the message against §3's table. If it matches (exit 78, matching text) — this is
#    EXPECTED. No further action needed beyond confirming the match; do not attempt to "fix" the
#    refusal by editing environment/config — every prerequisite is a human ratification gap
#    (ADR-0020 amendment, ADR-0029, DPO matrix), not a runtime configuration problem.

# 4. If the message does NOT match, or exit code isn't 78:
kubectl logs -n maezo-{tenant} <pod> --previous   # in case of a genuine crash/restart
kubectl describe pod -n maezo-{tenant} <pod>       # scheduling/image/OOM signals
```

## 5. Triage tree

```
Exit code 78 AND message matches §3's table for the invoked subcommand?
├── YES → EXPECTED failure. This is the intended fail-closed behavior (T2.8). No incident.
│         Confirm the CronJob's failedJobsHistoryLimit is retaining enough history to keep this
│         visible (deploy/helm/maezo-tenant/templates/cronjob-lifecycle.yaml) — an owner-gated
│         `deploy/` check, not an on-call fix.
└── NO → REAL failure. Diagnose as an ordinary CronJob/pod failure:
    ├── ModuleNotFoundError / ImportError → image build regression; check recent deploy/image tag.
    ├── OOMKilled → resource limits; owner-gated `deploy/` change, escalate.
    └── Traceback inside the refusal-message construction itself (e.g. a bug in
        _matrix_gated_refusal / load_retention_matrix) → this IS a code defect worth an incident,
        distinct from the intentional refusal it was trying to report — escalate per §7.
```

## 6. Mitigation

- **Expected failure (§3 match):** no mitigation — the intended state is "refuse loudly." Do not
  attempt to implement a workaround (e.g. hand-editing the matrix path, stubbing
  `RetentionMatrixUnavailableError`) during an on-call shift; every blocker here is a
  human-ratification gate (ADR-0020 amendment, ADR-0029, DPO legal-bases matrix), and bypassing
  one to silence a page would risk exactly the spoliation/audit-chain-corruption hazard the
  module's docstring documents (`lifecycle/__init__.py:20-30`: an unconditional `DELETE FROM
  audit_chain` with no legal-hold predicate and no re-anchoring).
- **Real failure — image/deploy regression:** roll back per [`cd-rollback.md`](../cd-rollback.md).
- **Real failure — OOM/resource:** flag `resources` in `cronjob-lifecycle.yaml` for an
  owner-gated `deploy/` fix.

## 7. Escalation

**DRAFT/verify — dono do serviço.** No in-repo Alertmanager routing/on-call roster exists (same
gap noted throughout this alert set — see
[`MaezoSLAWorkerLatencyHigh.md`](MaezoSLAWorkerLatencyHigh.md) §6). For an EXPECTED failure, no
escalation is needed beyond a routine acknowledgment (this alert exists specifically so this
state stays visible, not so it pages someone every month). For a REAL failure (§5), escalate to
the platform/SRE rotation like any other CronJob failure. If the underlying blocker itself needs
to move forward (ratifying ADR-0020's amendment / ADR-0029 / the DPO matrix) — that is a separate,
much larger workstream than an alert response; route to the DPO/compliance liaison and the
security/crypto approver track referenced in `PLANS.md` §0.8, not to on-call.

## 8. False-alarm checks

- The alert's selector (`job_name=~"lifecycle-.*"`) matches all three CronJobs, and they run on
  different schedules, so firing frequency (once the scrape target exists) is dominated by the
  daily job, not the monthly ones — do not assume "roughly once a month":
  - `lifecycle-expurgo-working`: **daily**, `"0 3 * * *"` (`deploy/helm/maezo-tenant/values.yaml:559`)
  - `lifecycle-verify-erasure`: monthly, `"0 4 1 * *"` (`deploy/helm/maezo-tenant/values.yaml:565`)
  - `lifecycle-audit-retention`: monthly, `"0 5 1 * *"` (`deploy/helm/maezo-tenant/values.yaml:592`)

  Given every invocation of all three fails by design today (§3), expect this alert to fire
  **daily** (from `expurgo-working` alone), with two additional monthly firings (`verify-erasure`
  on the 1st at 04:00, `audit-retention` on the 1st at 05:00) landing on top of that — do not
  treat every firing as needing fresh investigation; confirm the message still matches §3's
  table (a quick log read, checking `job_name` to know which of the three fired) and move on if
  so.
- The one thing that WOULD change this from "expected, low-effort" to "needs attention": any of
  the three refusal messages changing shape, or a subcommand starting to exit 0 (which would mean
  someone shipped an implementation without going through this runbook — worth a deliberate
  review of what shipped and whether the human ratification gates were actually cleared first).
