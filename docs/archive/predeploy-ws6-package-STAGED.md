# WS-6 User-Decision Package — facts gathered (for report + final message)

## 6.1 Host port 5432 (blocks nothing in-repo; a local-dev convenience conflict)
Observed 2026-07-04:
- `intensicare-postgres` (Up 8 days) — publishes `0.0.0.0:5432->5432/tcp` (the host-port holder; unrelated project)
- `austa-postgres` (Up 7 days) — no host binding (harmless)
- native `postgres` PID on 127.0.0.1:5432 + an ssh `*:5432` forward (earlier lsof)
- stale wave containers: `maezo-w2-cancel-postgres-1` (Up 34h), + `maezo-pd-idem/w3-nip6/w2-arc/w2-xproc3-postgres-1` (Created, not running)
DECISION for user: which Postgres should own host 5432 for local Maezo dev. Stopping intensicare needs explicit user authorization.
Commands (HAND TO USER, never executed):
```
# free 5432 for Maezo local dev (ONLY with your authorization — intensicare is another project):
docker stop intensicare-postgres
# clean up stale predeploy/wave scratch containers (safe — all Created/idle):
docker rm maezo-pd-idem-postgres-1 maezo-w3-nip6-postgres-1 maezo-w2-arc-postgres-1 maezo-w2-xproc3-postgres-1
docker stop maezo-w2-cancel-postgres-1 && docker rm maezo-w2-cancel-postgres-1
```

## 6.2 Branch protection + merge queue → apply AFTER the WS-1 workflow PR (#183) merges
Exact commands are in docs/adr/0023-merge-union-green-protected-main.md Apendice. Summary:
- `gh api -X PUT /repos/Omni-Saude/Maezo-Healthcare-Plan/branches/main/protection` with required_status_checks{strict:true, contexts: the 6 verified strings}, enforce_admins:true, required_pull_request_reviews:null, restrictions:null.
- Merge queue REJECTED (verified): private repo + org plan=team ⇒ needs Enterprise Cloud.
- Tradeoff to decide: docs-direct-push (DL-0003) → Opcao 1 docs-as-PRs (RECOMMENDED; classic protection suffices) vs Opcao 2 bypass_actors (forces rulesets). Stop-the-line hotfix = surgical enforce_admins lift + DL row.

## 6.3 Local-only worktrees (no remotes)
- **maezo-p3** @5911468 (feat/phase3-foundations): 1 commit — Phase-3 DRAFT foundations docs (6 SP-OP contract sheets + ADR-0019/0020 + review-queue). CORRECTION: its ADR-0019/0020 are BYTE-IDENTICAL to main's (duplication, not collision). → safe to DELETE, or push to an archive branch if the review-queue notes have value.
- **maezo-p3-we** @75d4f81 (wave/p3-we): 10 commits, top says "PRESERVE — do not auto-merge". Its int32→Long serializer fix is ALREADY on main (verified ×3 files). Contains Phase-3 feature quadruples (CRED/PAGTO/PROGRAMA/INADIMPLENCIA/ADEQUACAO) that are separate future work. → push to `archive/phase3-we` for preservation OR keep the worktree; do NOT merge.
DECISION for user: delete / archive-branch / keep each.
Commands (HAND TO USER):
```
git -C /Users/familia/code/maezo-p3 push origin feat/phase3-foundations:archive/phase3-foundations  # if keeping
git worktree remove /Users/familia/code/maezo-p3   # if discarding
git -C /Users/familia/code/maezo-p3-we push origin wave/p3-we:archive/phase3-we                     # preserve
```

## 6.4 Product-judgment findings (post-deploy, need domain owner)
- **matricula PHI**: adjudicated post-deploy (pseudonymized at intake) BUT pseudo-status is a CONTRACT not a MECHANISM. ACTION: confirm with amh-data-platform CDC owners that `matricula_beneficiario` is de-identified at the Tasy CDC source (if any producer ever ships raw matricula, unscrubbed cancel/inadimplencia publishers re-emit it → would flip to deploy-blocking). Structural fix brief ledgered (drop matricula from internal envelopes, GAP-XPHI-1 pattern).
- **phone-hash-keyless-brute-forceable-reidentification**: phone_number_hash on the general-zone bus is keyless SHA-256 of low-entropy phone → brute-forceable reidentification. Needs product decision: HMAC with the phi-hmac key (already provisioned) vs accept the risk.

## 6.5 §6.2 secret-population prerequisites (pods fail-closed until populated)
4 ExternalSecrets carry `maezo.io/blocked: "true"` (lines 51/80/131/156) + the new ones this session:
- maezo-llm-keys, maezo-whatsapp-config (waba-token), maezo-phi-hmac (phi-hmac-key — NOW REQUIRED by #177), tasy-oracle — pre-existing blocked
- NEW (this session): whatsapp app-secret + verify-token (#185), aurora database_url composed at ESO sync (#186)
- **ESO IRSA grant** (DB-4 residue, out-of-repo): the ESO pod role needs `secretsmanager:GetSecretValue` on the `rds!cluster-…` ARN (outside the `maezo/${env}/*` wildcard). Populate `aurora.{masterSecretArn,endpoint,port,database}` in values-<env>.yaml from `terraform output aurora_*`.

## 6.5b DEPLOY-PROCESS CHANGE (PR #188, DB-7 fix) — awareness item
The migrations-Job-before-secret Helm hook race is fixed by making the deploy TWO-PHASE (cd.yml + devops-stack.md runbook changed):
1. Phase 1: `helm upgrade --install --set migrations.enabled=false` — creates + syncs the aurora ExternalSecret (no migrations hook yet).
2. Barrier: `kubectl wait --for=condition=Ready externalsecret/aurora-master-credentials --timeout=180s`.
3. Phase 2: full `helm upgrade --install ... --atomic --wait` (migrations enabled) — pre-upgrade hook runs migrations against the now-existing secret.
PREREQUISITE (§6.2-class): External Secrets Operator must be running cluster-wide BEFORE deploy (out-of-chart operator). First production deploy MUST follow the two-phase runbook — a single `helm upgrade --install --atomic` on a fresh release would still race. Defense-in-depth: an init container also waits on the secret volume, so a single-phase attempt fails cleanly (bounded 300s) rather than crash-looping.

## 6.6 Deploy blocker #1: GitHub Actions spending limit
Org spending limit exhausted (~$30 July cap, 8k Linux min). Raise it: Omni-Saude org → Settings → Billing & plans → Spending limits → Actions. ~250-300 CI min needed to land the queue.
