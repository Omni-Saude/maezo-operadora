# Runbook: CD Rollback — Helm Release Recovery

**Audience:** Platform engineers, SRE, on-call
**Last updated:** 2026-06-14
**Applies to:** Maezo Healthcare Plan — staging & production (chart `deploy/helm/maezo-tenant`)

This runbook covers rolling back a bad deploy made by `.github/workflows/cd.yml`
(`helm upgrade --install`). The CD workflow deploys with `--atomic`, so a failed
*upgrade* auto-rolls-back to the prior revision. This runbook is for the case where
an upgrade **succeeded** but the new revision is **misbehaving** in service.

---

## Table of Contents

1. [Pre-flight: confirm the bad release](#1-pre-flight-confirm-the-bad-release)
2. [Roll back with Helm](#2-roll-back-with-helm)
3. [Verify the rollback](#3-verify-the-rollback)
4. [Roll forward (pin a known-good image tag)](#4-roll-forward-pin-a-known-good-image-tag)
5. [When `--atomic` already rolled back for you](#5-when---atomic-already-rolled-back-for-you)
6. [Escalation](#6-escalation)

---

## 0. Conventions

| Environment | Release name    | Namespace       | Values file (`$ENV` suffix)                    | EKS cluster     |
|-------------|-----------------|-----------------|------------------------------------------------|-----------------|
| staging     | `maezo-staging` | `maezo-staging` | `deploy/helm/maezo-tenant/values-staging.yaml` | `maezo-staging` |
| production  | `maezo-amh`     | `maezo-amh`     | `deploy/helm/maezo-tenant/values-amh.yaml`     | see note below  |

> **Production EKS cluster name — verify before use, do not assume.**
> `.github/workflows/cd.yml` resolves the prod cluster via
> `${{ vars.EKS_CLUSTER_PROD || 'maezo-prod' }}` — `maezo-prod` is only the fallback
> used when the `EKS_CLUSTER_PROD` repository variable is unset. Separately,
> `deploy/terraform/envs/prod-amh-sa-east-1` (`main.tf:12`, `local.env = "prod-amh"`)
> defaults `create_eks_cluster=true` (dedicated cluster per ADR-0004), and
> `deploy/terraform/modules/eks-cluster/main.tf:13` names that cluster
> `${name_prefix}-eks-${environment}` = **`maezo-eks-prod-amh`**. These two names do
> not match. Before pointing `kubectl`/`helm` at production, confirm the actual live
> cluster name (`aws eks list-clusters --region sa-east-1`) rather than hardcoding
> either value.

Set these once per shell (staging shown — swap for prod):

```bash
export REL=maezo-staging
export NS=maezo-staging
export CLUSTER=maezo-staging        # production: verify against the note above first
export ENV=staging                  # values-<ENV>.yaml suffix — staging|amh (see table)
export AWS_REGION=sa-east-1

aws eks update-kubeconfig --name "$CLUSTER" --region "$AWS_REGION"
```

> The deploy role (`maezo-github-deploy-<env>`, from
> `deploy/terraform/modules/github-oidc`) is assumed by CD via OIDC. For a manual
> rollback an operator uses their own credentials with equivalent EKS access — CD
> does **not** run rollbacks.

---

## 1. Pre-flight: confirm the bad release

```bash
# Current + historical revisions (note the FROM revision you want to return to).
helm history "$REL" -n "$NS"

# What's actually running and why it's unhappy.
# NOTE: there is no `deployment/gateway` — the gateway is an in-process library
# (`gateway.enabled: false`; the orphaned standalone Deployment was disabled).
# The chart's real workloads: agent-{name} per enabled agent (helena/rafael/marina
# on AMH), webhook-receiver, worker-daemon, fhir-sync, bridges.
kubectl get pods -n "$NS" -o wide
kubectl rollout status deployment/webhook-receiver -n "$NS" --timeout=30s || true
for a in helena rafael marina; do
  kubectl rollout status deployment/agent-$a -n "$NS" --timeout=30s || true
done
kubectl describe deployment/agent-helena -n "$NS" | sed -n '/Conditions/,/Events/p'
```

Capture the **revision number** you intend to roll back to (the last `deployed`
revision before the bad one). Example `helm history` output:

```
REVISION  UPDATED                   STATUS      CHART                APP VERSION  DESCRIPTION
3         Sat Jun 14 09:00:00 2026  deployed    maezo-tenant-0.1.0   0.1.0        Upgrade complete   <- BAD
2         Fri Jun 13 18:30:00 2026  superseded  maezo-tenant-0.1.0   0.1.0        Upgrade complete   <- GOOD (target)
```

---

## 2. Roll back with Helm

```bash
# Roll back to the last-known-good revision (2 in the example above).
helm rollback "$REL" 2 -n "$NS" --wait --timeout 5m

# Omit the revision number to go back exactly one revision:
#   helm rollback "$REL" -n "$NS" --wait --timeout 5m
```

`helm rollback` re-applies the stored manifest of the target revision (including the
image tag that was live then), creating a NEW revision that is a copy of the good one.
`--wait` blocks until pods are Ready or the timeout trips.

---

## 3. Verify the rollback

```bash
# New revision should be `deployed` and reference the good chart/values.
helm history "$REL" -n "$NS" | tail -5

# Pods healthy (no deployment/gateway exists — see §1 note).
for a in helena rafael marina; do
  kubectl rollout status deployment/agent-$a -n "$NS" --timeout=180s
done
kubectl rollout status deployment/webhook-receiver -n "$NS" --timeout=180s
kubectl get pods -n "$NS"

# Smoke the public edge service (webhook-receiver Service :8080). The app serves
# only GET/POST /webhook (no /healthz route yet — the chart's probes reference
# /healthz but whatsapp/app.py does not implement it; see escalation in the
# predeploy sweep). An unauthenticated GET /webhook returning HTTP 403 proves the
# service is up AND enforcing verification:
# CAUTION: .github/workflows/cd.yml still smokes `http://gateway:8000` — that step
# predates the gateway Deployment being disabled and will need updating; do NOT
# copy it here.
kubectl run rollback-smoke --rm -i --restart=Never -n "$NS" \
  --image=curlimages/curl:8.8.0 --quiet -- \
  sh -c '[ "$(curl -s -o /dev/null -w "%{http_code}" http://webhook-receiver:8080/webhook)" = 403 ] && echo edge-up'
```

Confirm the image tag now in service is the expected good one (all workloads share
the same chart image):

```bash
kubectl get deployment/agent-helena -n "$NS" \
  -o jsonpath='{.spec.template.spec.containers[0].image}{"\n"}'
```

---

## 4. Roll forward (pin a known-good image tag)

If you prefer to deploy a specific good image tag instead of reverting to a prior
Helm revision (e.g. the values changed and you only want to revert the image), run
the same command CD runs, pinning `image.tag`:

```bash
# Uses $ENV from §0 Conventions — NEVER hardcode values-staging.yaml here.
# Applying production with staging's values file is a real incident (wrong replica
# counts, wrong ESO secret prefix, wrong resource limits/CIDRs in prod).
helm upgrade --install "$REL" deploy/helm/maezo-tenant/ \
  --namespace "$NS" --create-namespace \
  -f "deploy/helm/maezo-tenant/values-${ENV}.yaml" \
  --set image.tag=<known-good-sha> \
  --atomic --timeout 5m --wait
```

`--atomic` means if THIS upgrade fails it auto-rolls-back, so a roll-forward attempt
can never leave you worse off than the current state.

---

## 5. When `--atomic` already rolled back for you

CD uses `--atomic`. If an `helm upgrade` in CI **fails** (pods never became Ready
within `--timeout`), Helm has *already* rolled the release back to the previous
revision before the job exits non-zero. In that case:

- The cluster is still on the **previous good revision** — no manual rollback needed.
- Confirm with `helm history "$REL" -n "$NS"` (the failed upgrade shows `failed`,
  and the prior revision is `deployed`).
- Investigate the failure (image, config, secrets) before re-running CD. Re-running
  CD with the same bad tag will fail `--atomic` again — fix forward, do not retry blind.

---

## 6. Escalation

- **PHI-zone agents (rafael, marina) degraded** → page the on-call clinical-platform
  lead immediately; PHI-path outages are SEV-2 minimum (ADR-0006).
- **Rollback does not recover** (good revision also unhealthy) → likely an external
  dependency (Aurora, MSK, ESO secret rotation, CIB Seven). Check
  `docs/runbooks/devops-stack.md` §6 alert runbooks and the gateway/agent runbooks.
- **Production**: any prod rollback must be logged in the deploy channel with the
  `helm history` before/after and the triggering CD run URL.
