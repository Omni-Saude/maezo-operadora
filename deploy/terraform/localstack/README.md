# LocalStack terraform smoke scaffold

Author-only scaffold that brings up [LocalStack](https://localstack.cloud/),
seeds stub copies of the **amh-data-platform** substrate, and runs
`terraform plan` for the `staging-sa-east-1` environment against it.

It exists to catch a specific class of regression in CI **without touching real
AWS**: the env's `data` sources silently breaking (a renamed tag, a dropped
secret, a filter that no longer matches). It is wired into the root `Makefile`
as `make tf-smoke` and is **CI-only** — terraform is not installed locally in
this repo, and nothing here runs on a default `docker compose up` or a default
`terraform plan`.

> [!IMPORTANT]
> **This proves data-source resolution and plan-ability — NOT provisioning.**
> See [Honest limitations](#honest-limitations). A green `tf-smoke` does **not**
> mean a real `terraform apply` will succeed; real apply is owned by S1 and is
> out of scope (stop-condition #16).

## What it does

1. **`docker-compose.localstack.yml`** brings up `localstack/localstack` with the
   AWS services the staging env touches (`ec2`, `iam`, `sts`, `secretsmanager`,
   `s3`, `dynamodb`, `ecr`, `rds`, `eks`, `kms`), pinned to region `sa-east-1`.
2. **`seed.sh`** pre-creates the amh-data-platform substrate **stubs** with
   names/tags that **match** the data-source filters in the staging env:

   | Stub created by seed.sh | Matches data source | Match key |
   | --- | --- | --- |
   | VPC tagged `Name=amh-data-platform-vpc-staging` | `data.aws_vpc.shared` | `tags = { Name = var.shared_vpc_name }` |
   | Subnets tagged `Tier=private-app` (×2 AZ) | `data.aws_subnets.private_app` | `tags = { Tier = "private-app" }` |
   | Subnets tagged `Tier=private-data` (×2 AZ) | `data.aws_subnets.private_data` | `tags = { Tier = "private-data" }` |
   | EKS cluster `amh-data-platform-eks-staging` | `data.aws_eks_cluster[_auth].existing` | `name = var.existing_cluster_name` |
   | OIDC provider `token.actions.githubusercontent.com` | `data.aws_iam_openid_connect_provider.github_existing` | `url = "https://token.actions.githubusercontent.com"` |
   | Secret `debezium/msk-bootstrap-servers` (+ version) | `data.aws_secretsmanager_secret[_version].msk_bootstrap` | `name = "debezium/msk-bootstrap-servers"` |
   | S3 bucket `amh-terraform-state-sa-east-1` + DynamoDB table `amh-terraform-locks` | remote-state backend (commented in `versions.tf`) | only used if you opt into the real backend |

3. **`provider_override.tf.tmpl`** is copied into the env dir as
   `localstack_override.tf` for the duration of the plan (terraform auto-merges
   `*_override.tf` files), repointing the AWS provider at the LocalStack edge
   (`:4566`) with dummy creds. It is removed again after the plan, so it never
   affects a real `init`/`plan`/`apply`.
4. **`smoke.staging.tfvars`** supplies the no-default variables
   (`aws_account_id`, `owner_email`, `eks_cluster_name`, …) with fake values
   that match what `seed.sh` creates.

## Usage (CI, or a local box with docker + terraform)

```bash
# one shot: up + seed + plan + teardown
make tf-smoke

# or, just bring up a seeded LocalStack to poke at by hand:
make localstack-up
#   ... awslocal --region sa-east-1 ec2 describe-vpcs   etc.
docker compose -f deploy/terraform/localstack/docker-compose.localstack.yml down -v
```

`make tf-smoke`:

- brings up `localstack` and waits for health;
- runs `seed.sh` (one-shot, exits 0);
- copies the provider override into `deploy/terraform/envs/staging-sa-east-1/`;
- `terraform init -backend=false` + `terraform plan` (no apply, `-lock=false`);
- removes the override and tears LocalStack down (`down -v`);
- exits with the plan's status.

### Why `staging-sa-east-1` and not `prod-amh`

Staging uses `create_cluster=false` / `create_eks_cluster` defaults that make it
**read** the existing-cluster + OIDC + MSK data sources — exactly the lookups
this scaffold validates. The prod env's `create_eks_cluster=true` path provisions
a real cluster (and an IRSA OIDC provider, node groups, etc.) that LocalStack
does **not** emulate faithfully; plan-testing that path here would prove nothing.
Module-level + prod static checks stay covered by `make tf-validate`.

## Honest limitations

LocalStack does **not** fully emulate every AWS service. This scaffold is
deliberately scoped to **data-source resolution and plan-ability**, not real
provisioning:

- **EKS** is mocked at the API surface. `seed.sh` "creates" a cluster object so
  `data.aws_eks_cluster` returns a value, but there is **no real control plane,
  no nodes, no kubeconfig that works**. IRSA, the cluster OIDC issuer, and node
  groups are not real.
- **MSK** is not provisioned at all. We only seed the `debezium/...` Secrets
  Manager **secret** that the env reads; the brokers it points at are fake.
- **Aurora / RDS, ECR, KMS, IAM roles/policies** are created in the `plan`
  graph but not actually stood up; LocalStack Community's fidelity for RDS
  Serverless v2 and KMS-backed ECR is partial. A successful plan here is **not**
  a guarantee that apply produces working infrastructure.
- **Networking** (real route tables, NAT, SG reachability) is not exercised.

What a green `tf-smoke` **does** tell you:

- every `data` source in `staging-sa-east-1` still resolves against a substrate
  shaped like amh-data-platform (no renamed tag / dropped secret / broken
  filter);
- the module + env graph is internally consistent enough to produce a plan
  (variable wiring, output references, `for_each`/`count` shapes, type
  agreement).

For anything beyond that — real provisioning, apply-time validation, drift —
you need a real AWS account (S1, stop-condition #16).

## Keeping it in sync

The stub names/tags in `seed.sh` are **coupled** to the data-source filters in
`deploy/terraform/envs/staging-sa-east-1/main.tf` and
`deploy/terraform/modules/secrets/main.tf`. If you change a filter there (a tag
key, a secret name, the VPC `Name`), update `seed.sh` and `smoke.staging.tfvars`
to match — otherwise `tf-smoke` will fail with an "no matching … found"
data-source error (which is exactly the regression this scaffold is meant to
surface).

## Files

| File | Purpose |
| --- | --- |
| `docker-compose.localstack.yml` | LocalStack edge + one-shot `seed` service |
| `seed.sh` | Pre-creates amh substrate stubs (matching tags/names) |
| `provider_override.tf.tmpl` | AWS-provider → LocalStack override (copied in for the plan, removed after) |
| `smoke.staging.tfvars` | Fake var values for the staging smoke plan |
| `README.md` | This file |
