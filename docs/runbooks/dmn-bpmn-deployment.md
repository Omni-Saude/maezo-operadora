# Runbook: DMN/BPMN Artifact Deployment — validate, deploy, rollback posture, ratification

**Audience:** Process engineers, compliance, platform, on-call
**Last updated:** 2026-08-11
**Applies to:** Maezo Healthcare Plan, all environments

> **Scope vs. `engine-processes.md`.** [`engine-processes.md`](engine-processes.md) covers
> process **instance** management (business keys, HITL task completion, stuck-instance
> diagnosis, SLA timers) — that content is not duplicated here. This runbook covers the
> **artifact pipeline**: how `spec/processes/{bpmn,dmn}/**` gets validated and pushed to the
> engine, what rollback means for that pipeline, and the human-ratification constraints that
> apply to a subset of DMN tables. See §5 for a known discrepancy between this runbook and
> `engine-processes.md`'s existing §2 that a reviewer should be aware of.

---

## Table of Contents

1. [Validation — `make validate-artifacts`](#1-validation--make-validate-artifacts)
2. [Deployment — `make deploy-artifacts`](#2-deployment--make-deploy-artifacts)
3. [Rollback posture](#3-rollback-posture)
4. [Ratification constraints (M-4..M-7 and beyond)](#4-ratification-constraints-m-4m-7-and-beyond)
5. [Known discrepancy with `engine-processes.md`](#5-known-discrepancy-with-engine-processesmd)

---

## 1. Validation — `make validate-artifacts`

**Code:** `src/maezo/platform/validation/` (`cli.py`, `bpmn.py`, `dmn.py`, `policy.py`,
`agent_def.py`, `crossref.py`)

```bash
make validate-artifacts
# = uv run python -m maezo.platform.validation.cli validate spec/processes spec/policies spec/agents
```

This is a **CI blocker** (`.github/workflows/ci.yml` job `artifact-validation` runs it; also a
`.PHONY` `Makefile` target). Fail-closed, real parsing — not a stub: BPMN/DMN XML well-formedness,
YAML policy/agent-definition parsing, BPMN↔DMN cross-references, the DMN orphan allowlist
(`spec/processes/dmn/orphans-allowlist.yaml`), and `agent.yaml` schema + MCP-server allowlist
checks. There is no "greenfield exemption" and no warn-then-pass path
(`src/maezo/platform/validation/cli.py` module docstring). Each directory argument is classified
by its on-disk **structure** (a `bpmn/`/`dmn/` subdirectory, an `autonomy/` subdirectory, or
per-item `agent.yaml` files) rather than by name, so the gate does not silently go blind if a
path is renamed.

A sibling, separately-gated check for **process contracts** (not BPMN/DMN files themselves):

```bash
make validate-signoff
# = uv run python -m maezo.platform.validation.cli signoff
```

Requires a valid, current-version, human-approved `docs/processes/contracts/signoffs/<ID>.signoff.yaml`
for every contract under `docs/processes/contracts/*.md` marked `**Status:** FINAL`
(`src/maezo/platform/validation/signoff.py`). Also runs in CI, in its own job
(`.github/workflows/ci.yml`, job `content-signoff-gate`, step "Content sign-off gate (promotable
artifacts require human sign-off)" → `make validate-signoff`) — separate from the
`artifact-validation` job. No agent — including this validation module itself — ever creates,
edits, or infers a signoff record; it is an exclusively human act.

## 2. Deployment — `make deploy-artifacts`

**Code:** `src/maezo/platform/deploy/` (`cli.py`, `engine_deploy.py`, `__init__.py`)

```bash
# Requires `make dev-stack` (or equivalent) up first — deploys against a running engine.
make dev-stack
make deploy-artifacts
# = uv run python -m maezo.platform.deploy

# List what the engine currently holds, without deploying anything:
uv run python -m maezo.platform.deploy --list

# Override the target engine (default: $ENGINE_REST_URL or http://localhost:8080/engine-rest,
# matching the `cibseven` compose service's published port):
ENGINE_REST_URL=http://cibseven.staging.internal:8080/engine-rest uv run python -m maezo.platform.deploy
```

Mechanics (`EngineDeployClient.deploy`, `src/maezo/platform/deploy/engine_deploy.py`):
one multipart `POST /deployment/create` per run, submitting **every** `.bpmn`/`.dmn` file under
`spec/processes/{bpmn,dmn}/` (via `collect_artifacts` — globs `*.bpmn`/`*.dmn` only; it does
**not** sweep `orphans-allowlist.yaml` or any `*-ratification.yaml` manifest — those are never
deployed to the engine, see §4), with `enable-duplicate-filtering=true` and
`deploy-changed-only=true`. This is the CIB Seven/Camunda 7 REST convention for idempotent,
versioned deployment: an unchanged resource is skipped (not redeployed, and does not appear in
the response's `deployedXDefinitions` maps) purely by comparing against the most recent
deployment sharing the same `deployment-name` (default `DEFAULT_DEPLOYMENT_NAME =
"maezo-spec-processes"`, kept **stable** across runs on purpose — a different name every run
would defeat the idempotency check entirely).

Fail-closed throughout: any non-2xx response, transport error, or unparseable success body raises
`EngineDeployError` carrying the engine's **verbatim** error body — a BPMN/DMN rejection at
deploy time is treated as a real engine-side defect, never downgraded to a warning or silently
retried into a false "OK". `spec/` resolution honors `MAEZO_SPEC_DIR` (same override as
`maezo.agents.resolve_spec_dir()`), and deploys **directly** from `spec/processes/`; nothing is
copied elsewhere first — `spec/` is the single source of truth.

**Verified: not currently CI-automated.** `grep -n "deploy-artifacts" .github/workflows/*.yml`
returns zero hits — unlike `validate-artifacts`/`validate-signoff`, `make deploy-artifacts` is a
manual, human-run CLI step today, not part of any GitHub Actions workflow. See §5 for how this
differs from what `engine-processes.md` currently describes.

## 3. Rollback posture

**Code:** `src/maezo/platform/deploy/cli.py`, `engine_deploy.py`

There is **no undeploy/delete/rollback command** in this tool (`grep -n
"rollback\|undeploy\|delete" src/maezo/platform/deploy/*.py` — zero hits; the CLI's only two
modes are deploy and `--list`). Rollback posture for the artifact pipeline is therefore:

- **Camunda 7 deployments are additive and versioned.** Redeploying a corrected `.bpmn`/`.dmn`
  file creates a **new version** of that process/decision definition; the engine does not
  overwrite or delete the prior version. Running instances continue on the version they started
  with — there is no forced migration.
- **To "roll back" a bad artifact change:** `git revert` (or otherwise restore) the prior content
  of the affected file(s) under `spec/processes/`, then run `make deploy-artifacts` again. Because
  the prior content's hash differs from what is currently in the deployment named
  `maezo-spec-processes`, the engine's duplicate-filtering will treat it as a change and deploy it
  as a new version — i.e. a rollback is itself a forward deploy of old content, not a deletion of
  the bad version. The bad version remains on the engine (any instances that started under it keep
  running under it) unless a human separately uses the raw engine REST API (`DELETE
  /process-definition/{id}` et al. — outside this tool's scope) to remove it, which this repo
  does not wrap in a script.
- **This is distinct from an app-container rollback.** If the problem is a bad `worker-daemon`/
  `agent-runtime`/etc. image or Helm values (not a bad BPMN/DMN artifact), use
  [`cd-rollback.md`](cd-rollback.md) (`helm rollback`) instead — that runbook is not duplicated
  here.

## 4. Ratification constraints (M-4..M-7 and beyond)

**Code:** `spec/processes/dmn/auth-criteria-ratification.yaml`, `docs/review-queue.md` (§"W4 —
shadow candidates para as quatro tabelas irmas conhecidas-erradas (M-4..M-7)")

A subset of DMN decision tables carry **clinical, regulatory, or contractual content that is
synthetic/DRAFT and requires human ratification before its output can drive an automatic
approval.** `docs/review-queue.md` documents four such tables under active review — informally
labeled M-4 through M-7 in that document — each with a proposed "shadow candidate" fix
(`spec/processes/dmn/*-shadow-candidate.yaml` manifests) that is explicitly **DRAFT, not
ratified**, pending the relevant SME (auditoria-contas/compliance for M-4, juridico+medico for
M-5, medico-auditor+financas for M-6, medico obstetra/pediatra for M-7).

**The ratification mechanism is a separate, human-edited YAML manifest — deployment never
touches it.** `spec/processes/dmn/auth-criteria-ratification.yaml` is the concrete example: a
criterion (`tecnico`/`financeiro`/`regulatorio`/`contratual`) may contribute a PASS only if its
DMN-table source is marked ratified there, via three fields that must **all** be set —
`ratificado: true`, `revisor: "<who>"`, `ratificado_em: "YYYY-MM-DD"`. Any missing/blank/non-true
field means "not ratified" (fail-closed); the loader (`maezo.tools.workers.auth_criteria`) never
defaults a source to ratified and swallows every load error into "nothing is ratified" — a
manifest problem can only route to human review, never open an automatic approval. Unratified
tables are still **evaluated** and their would-be verdict recorded in a bounded, non-PHI
`auto_criteria_shadow` variable for the reviewer's benefit; shadow output never influences the
real verdict. This is a **data change, not a code or deployment change** — ratifying a table does
not require running `make deploy-artifacts` or touching Python.

**Why `make deploy-artifacts` cannot silently change a ratified table's status:**
`collect_artifacts()` (§2) only globs `*.bpmn`/`*.dmn` — it never touches
`*-ratification.yaml` or `*-shadow-candidate.yaml` files, so pushing artifacts to the engine
cannot flip a ratification flag. The other direction is the real hazard to watch for: **editing
the bytes of an already-ratified `.dmn` file and then deploying it is a live content change to
what a ratified table computes**, and several shadow-candidate manifests pin their applicability
to the exact bytes of the live table on disk (e.g., "Ratificar agora TAMBEM exige que o
`tabela_viva.sha256` deste manifesto ainda bata com os bytes de `glosa_triage.dmn` no disco" —
`docs/review-queue.md`) — an edit invalidates any pending/granted ratification for that table
until re-review. **Operationally: never deploy a change to a ratified `.dmn` table's rule content
without a corresponding, explicit re-ratification decision from the accountable SME first** —
`make deploy-artifacts` itself has no concept of "ratified" and will not stop you.

## 5. Known discrepancy with `engine-processes.md`

`docs/runbooks/engine-processes.md` §2 ("Manual deployment (dev)" / "Automated deployment
(staging/prod)") currently documents deployment via a raw `curl -X POST
http://localhost:8080/engine-rest/deployment` and a `.github/workflows/deploy.yml` GitHub Actions
workflow. As of this writing, **`.github/workflows/deploy.yml` does not exist** (`ls
.github/workflows/` — no match), and the actual `EngineDeployClient.deploy()` implementation
posts to `/deployment/create` (not the bare `/deployment` path shown there) with
`enable-duplicate-filtering`/`deploy-changed-only` multipart fields that the curl example omits.
The `deploy()` method's own docstring in `src/maezo/platform/deploy/engine_deploy.py` in fact
cites `docs/runbooks/engine-processes.md` as "the" doc for this convention, which suggests §2 was
written before (or was never updated after) `make deploy-artifacts`/`EngineDeployClient` landed.
This runbook was written and re-verified against the current tree; §2 of `engine-processes.md`
was not edited as part of this change (out of scope for this leg) — flagged here, and in the
final report, rather than silently fixed.
