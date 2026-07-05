#!/usr/bin/env bash
# Seed the LocalStack substrate so the staging-sa-east-1 env's data sources
# resolve. Each stub's NAME/TAGS must MATCH the corresponding data-source
# filter in deploy/terraform/envs/staging-sa-east-1/main.tf and
# deploy/terraform/modules/secrets/main.tf. If you change a tag here, change
# the matching filter there (and vice versa).
#
# This script is idempotent-ish: it tolerates "already exists" errors so a
# re-run against a warm LocalStack does not fail the smoke job.
#
# Invoked by docker-compose.localstack.yml (service `seed`) and indirectly by
# `make tf-smoke`. Not meant to be run by hand against real AWS — it targets
# the LocalStack edge endpoint only.
set -euo pipefail

ENDPOINT="${LOCALSTACK_ENDPOINT:-http://localstack:4566}"
REGION="${AWS_DEFAULT_REGION:-sa-east-1}"

# Names/tags that MUST match the staging env (keep in sync — see header).
SHARED_VPC_NAME="${SHARED_VPC_NAME:-amh-data-platform-vpc-staging}"
EKS_CLUSTER_NAME="${EKS_CLUSTER_NAME:-amh-data-platform-eks-staging}"
MSK_SECRET_NAME="${MSK_SECRET_NAME:-debezium/msk-bootstrap-servers}"
TF_STATE_BUCKET="${TF_STATE_BUCKET:-amh-terraform-state-sa-east-1}"
TF_LOCK_TABLE="${TF_LOCK_TABLE:-amh-terraform-locks}"

# awslocal ships in the localstack image; fall back to `aws --endpoint-url`.
if command -v awslocal >/dev/null 2>&1; then
  aws_cmd() { awslocal --region "$REGION" "$@"; }
else
  aws_cmd() { aws --endpoint-url "$ENDPOINT" --region "$REGION" "$@"; }
fi

log()  { echo "[seed] $*"; }
# Treat "already exists" / "duplicate" as success so re-runs are safe.
soft() { "$@" || log "  (non-fatal: command returned $? — likely already exists)"; }

log "waiting for LocalStack edge at ${ENDPOINT} ..."
for _ in $(seq 1 30); do
  if curl -sf "${ENDPOINT}/_localstack/health" >/dev/null 2>&1; then
    break
  fi
  sleep 2
done

# ---------------------------------------------------------------------------
# VPC + subnets — matches:
#   data.aws_vpc.shared            { tags = { Name = var.shared_vpc_name } }
#   data.aws_subnets.private_app   { tags = { Tier = "private-app" } }
#   data.aws_subnets.private_data  { tags = { Tier = "private-data" } }
# ---------------------------------------------------------------------------
log "creating shared VPC '${SHARED_VPC_NAME}' ..."
VPC_ID=$(aws_cmd ec2 create-vpc \
  --cidr-block "10.42.0.0/16" \
  --tag-specifications "ResourceType=vpc,Tags=[{Key=Name,Value=${SHARED_VPC_NAME}}]" \
  --query 'Vpc.VpcId' --output text)
log "  VPC_ID=${VPC_ID}"

# Two AZs per tier so the data sources return >1 subnet (mirrors real multi-AZ).
log "creating private-app subnets ..."
i=0
for az_suffix in a b; do
  aws_cmd ec2 create-subnet \
    --vpc-id "${VPC_ID}" \
    --cidr-block "10.42.1${i}.0/24" \
    --availability-zone "${REGION}${az_suffix}" \
    --tag-specifications "ResourceType=subnet,Tags=[{Key=Tier,Value=private-app},{Key=Name,Value=${SHARED_VPC_NAME}-app-${az_suffix}}]" \
    >/dev/null
  i=$((i + 1))
done

log "creating private-data subnets ..."
i=0
for az_suffix in a b; do
  aws_cmd ec2 create-subnet \
    --vpc-id "${VPC_ID}" \
    --cidr-block "10.42.2${i}.0/24" \
    --availability-zone "${REGION}${az_suffix}" \
    --tag-specifications "ResourceType=subnet,Tags=[{Key=Tier,Value=private-data},{Key=Name,Value=${SHARED_VPC_NAME}-data-${az_suffix}}]" \
    >/dev/null
  i=$((i + 1))
done

# ---------------------------------------------------------------------------
# EKS cluster — matches:
#   data.aws_eks_cluster.existing       { name = var.existing_cluster_name }
#   data.aws_eks_cluster_auth.existing  { name = var.existing_cluster_name }
# NOTE: LocalStack mocks the EKS API surface; this is NOT a real cluster.
# It exists so the data-source read returns a value during `plan`.
# ---------------------------------------------------------------------------
log "creating EKS cluster stub '${EKS_CLUSTER_NAME}' ..."
# A real cluster needs a role ARN + subnets; LocalStack accepts placeholders.
SUBNET_IDS_CSV=$(aws_cmd ec2 describe-subnets \
  --filters "Name=vpc-id,Values=${VPC_ID}" "Name=tag:Tier,Values=private-app" \
  --query 'Subnets[].SubnetId' --output text | tr '\t' ',')
soft aws_cmd eks create-cluster \
  --name "${EKS_CLUSTER_NAME}" \
  --role-arn "arn:aws:iam::000000000000:role/${EKS_CLUSTER_NAME}-role" \
  --resources-vpc-config "subnetIds=${SUBNET_IDS_CSV}" \
  --kubernetes-version "1.31" \
  >/dev/null

# ---------------------------------------------------------------------------
# GitHub OIDC provider — matches:
#   data.aws_iam_openid_connect_provider.github_existing
#     { url = "https://token.actions.githubusercontent.com" }
# (env sets create_oidc_provider=false, so the env READS this provider.)
# ---------------------------------------------------------------------------
log "creating GitHub OIDC provider stub ..."
soft aws_cmd iam create-open-id-connect-provider \
  --url "https://token.actions.githubusercontent.com" \
  --client-id-list "sts.amazonaws.com" \
  --thumbprint-list "6938fd4d98bab03faadb97b34396831e3780aea1" \
  >/dev/null

# ---------------------------------------------------------------------------
# MSK bootstrap secret — matches:
#   data.aws_secretsmanager_secret.msk_bootstrap         { name = "debezium/msk-bootstrap-servers" }
#   data.aws_secretsmanager_secret_version.msk_bootstrap { secret_id = ... }
# amh-data-platform convention; we only READ it. A placeholder string value is
# enough for the *_version data source to resolve.
# ---------------------------------------------------------------------------
log "creating MSK bootstrap secret stub '${MSK_SECRET_NAME}' ..."
soft aws_cmd secretsmanager create-secret \
  --name "${MSK_SECRET_NAME}" \
  --description "STUB (localstack smoke): amh-data-platform MSK Serverless bootstrap brokers" \
  --secret-string "b-1.amh-stub.kafka.sa-east-1.amazonaws.com:9098,b-2.amh-stub.kafka.sa-east-1.amazonaws.com:9098" \
  >/dev/null

# ---------------------------------------------------------------------------
# Terraform remote-state backend — S3 bucket + DynamoDB lock table.
# Matches the (commented) backend block in
#   deploy/terraform/envs/staging-sa-east-1/versions.tf
# The smoke target uses -backend=false by default, so these are only needed if
# a developer opts into exercising the real backend wiring against LocalStack.
# ---------------------------------------------------------------------------
log "creating terraform state bucket '${TF_STATE_BUCKET}' ..."
# sa-east-1 requires LocationConstraint on bucket creation.
soft aws_cmd s3api create-bucket \
  --bucket "${TF_STATE_BUCKET}" \
  --create-bucket-configuration "LocationConstraint=${REGION}" \
  >/dev/null
soft aws_cmd s3api put-bucket-versioning \
  --bucket "${TF_STATE_BUCKET}" \
  --versioning-configuration "Status=Enabled" \
  >/dev/null

log "creating terraform lock table '${TF_LOCK_TABLE}' ..."
soft aws_cmd dynamodb create-table \
  --table-name "${TF_LOCK_TABLE}" \
  --attribute-definitions "AttributeName=LockID,AttributeType=S" \
  --key-schema "AttributeName=LockID,KeyType=HASH" \
  --billing-mode "PAY_PER_REQUEST" \
  >/dev/null

log "seed complete. Substrate stubs ready for: terraform plan (staging-sa-east-1)."
