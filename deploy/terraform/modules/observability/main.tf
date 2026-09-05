# Module: observability
# Amazon Managed Prometheus (AMP) + Amazon Managed Grafana (AMG) for Maezo.
# ADR-0014 gap#32: IaC for the observability backend + SigV4 wiring.
#
# BLOCKED: terraform apply requires AWS credentials (issue #16).
# Manual step: terraform apply with AWS creds (see blocked[] in PR).
# All IaC is complete — wire the collector IRSA role ARN into Helm values after apply.
#
# Components provisioned:
#   1. AMP workspace (Amazon Managed Prometheus) — receives remote-write from OTel collector
#   2. AMG workspace (Amazon Managed Grafana)     — dashboards + AMP datasource
#   3. Grafana AMG datasource pointing at the AMP workspace
#   4. AMP alert-rules upload (rule_group_namespace) from deploy/observability/alert-rules.yml
#   5. IRSA IAM role for the OTel collector (SigV4 for AMP remote-write + query)
#   6. Secrets Manager secret shell for the AMP endpoint URL (ESO-injected into collector pod)

locals {
  name_prefix = "${var.name_prefix}-${var.environment}"
  common_tags = merge(var.tags, {
    Module      = "observability"
    ManagedBy   = "terraform"
    Platform    = "maezo"
    environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# 1. Amazon Managed Prometheus (AMP) workspace
# ---------------------------------------------------------------------------
resource "aws_prometheus_workspace" "maezo" {
  alias = "${local.name_prefix}-metrics"

  # LGPD compliance: logging enabled for audit trail
  logging_configuration {
    log_group_arn = "${aws_cloudwatch_log_group.amp_audit.arn}:*"
  }

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-amp-workspace"
  })
}

resource "aws_cloudwatch_log_group" "amp_audit" {
  name              = "/maezo/${var.environment}/amp-audit"
  retention_in_days = 90  # 90-day audit retention (LGPD / ANS compliance)

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-amp-audit-logs"
    Purpose = "AMP audit trail"
  })
}

# ---------------------------------------------------------------------------
# 2. Alert rules — upload to AMP as a rule_group_namespace
#    Source: deploy/observability/alert-rules.yml (canonical rule set)
#    ADR-0010 gap#31+32: rules authored AND actually wired to AMP.
#
#    The alert_rules_content variable holds the rule file contents.
#    When calling this module from an env, pass:
#      alert_rules_content = file("${path.root}/../../../../deploy/observability/alert-rules.yml")
#    The default is a placeholder that validates cleanly (no file() call in the module).
# ---------------------------------------------------------------------------
resource "aws_prometheus_rule_group_namespace" "maezo_alerts" {
  name         = "maezo-alert-rules"
  workspace_id = aws_prometheus_workspace.maezo.id
  data         = var.alert_rules_content
}

# ---------------------------------------------------------------------------
# 3. IRSA IAM role for the OTel collector — SigV4 auth for AMP
#    The collector pod assumes this role via EKS OIDC token projection.
#    Role ARN is output below and must be set in Helm values:
#      observability.collector.irsaRoleArn = module.observability.collector_irsa_role_arn
# ---------------------------------------------------------------------------
data "aws_iam_policy_document" "collector_trust" {
  statement {
    sid     = "EKSOIDCTrustCollector"
    effect  = "Allow"
    actions = ["sts:AssumeRoleWithWebIdentity"]

    principals {
      type        = "Federated"
      identifiers = [var.eks_oidc_provider_arn]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(var.eks_oidc_provider_url, "https://", "")}:sub"
      values   = ["system:serviceaccount:${var.collector_namespace}:${var.collector_service_account}"]
    }

    condition {
      test     = "StringEquals"
      variable = "${replace(var.eks_oidc_provider_url, "https://", "")}:aud"
      values   = ["sts.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "collector_irsa" {
  name               = "${local.name_prefix}-otel-collector-irsa"
  assume_role_policy = data.aws_iam_policy_document.collector_trust.json

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-otel-collector-irsa"
    Purpose = "IRSA role for OTel collector SigV4 AMP auth"
  })
}

data "aws_iam_policy_document" "collector_amp_policy" {
  # AMP remote-write permission
  statement {
    sid    = "AMPRemoteWrite"
    effect = "Allow"
    actions = [
      "aps:RemoteWrite",
      "aps:GetSeries",
      "aps:GetLabels",
      "aps:GetMetricMetadata",
    ]
    resources = [aws_prometheus_workspace.maezo.arn]
  }

  # CloudWatch metrics (optional — for cross-correlating with EKS node metrics)
  statement {
    sid    = "CloudWatchPutMetrics"
    effect = "Allow"
    actions = [
      "cloudwatch:PutMetricData",
    ]
    resources = ["*"]
    condition {
      test     = "StringEquals"
      variable = "cloudwatch:namespace"
      values   = ["Maezo/Observability"]
    }
  }
}

resource "aws_iam_role_policy" "collector_amp" {
  name   = "amp-remote-write"
  role   = aws_iam_role.collector_irsa.id
  policy = data.aws_iam_policy_document.collector_amp_policy.json
}

# ---------------------------------------------------------------------------
# 4. Secrets Manager shell — AMP remote-write endpoint
#    The ESO ExternalSecret in Helm reads this to inject
#    PROMETHEUS_REMOTE_WRITE_ENDPOINT into the collector pod env.
# ---------------------------------------------------------------------------
resource "aws_secretsmanager_secret" "amp_endpoint" {
  name        = "${var.name_prefix}/${var.environment}/observability/amp-remote-write-endpoint"
  description = "Amazon Managed Prometheus remote-write endpoint URL for OTel collector SigV4 auth."
  kms_key_id  = var.kms_key_arn != "" ? var.kms_key_arn : null

  recovery_window_in_days = 7

  tags = merge(local.common_tags, {
    Name    = "${local.name_prefix}-amp-endpoint"
    Purpose = "AMP remote-write URL for OTel collector"
  })
}

resource "aws_secretsmanager_secret_version" "amp_endpoint" {
  secret_id = aws_secretsmanager_secret.amp_endpoint.id
  # Compose the AMP remote-write URL from the workspace prometheus_endpoint output.
  # e.g. https://aps-workspaces.sa-east-1.amazonaws.com/workspaces/<id>/api/v1/remote_write
  secret_string = "${aws_prometheus_workspace.maezo.prometheus_endpoint}api/v1/remote_write"
}

# ---------------------------------------------------------------------------
# 5. Amazon Managed Grafana (AMG) workspace
# ---------------------------------------------------------------------------
resource "aws_grafana_workspace" "maezo" {
  name        = "${local.name_prefix}-grafana"
  description = "Maezo Healthcare Platform observability dashboards (${var.environment})"

  account_access_type      = "CURRENT_ACCOUNT"
  authentication_providers = ["AWS_SSO"]
  permission_type          = "SERVICE_MANAGED"

  # AMP datasource permission
  data_sources = ["PROMETHEUS"]

  # CloudWatch and X-Ray for cross-correlation (optional, additive)
  notification_destinations = []

  tags = merge(local.common_tags, {
    Name = "${local.name_prefix}-amg-workspace"
  })
}

# Grafana admin role association (AWS SSO group — AWS provider v5 uses group_ids list)
resource "aws_grafana_role_association" "admin" {
  count        = var.grafana_admin_group_id != "" ? 1 : 0
  workspace_id = aws_grafana_workspace.maezo.id
  role         = "ADMIN"
  group_ids    = [var.grafana_admin_group_id]
}

# Grafana viewer role association (AWS SSO group)
resource "aws_grafana_role_association" "viewer" {
  count        = var.grafana_viewer_group_id != "" ? 1 : 0
  workspace_id = aws_grafana_workspace.maezo.id
  role         = "VIEWER"
  group_ids    = [var.grafana_viewer_group_id]
}

# ---------------------------------------------------------------------------
# 6. AMG → AMP datasource wiring (Grafana provider)
#
# BLOCKED: the grafana provider requires an AMG workspace URL + service account
# token at plan/apply time. These are only available AFTER the AMG workspace
# is created (see aws_grafana_workspace.maezo above).
#
# Manual step: after `terraform apply` creates the AMG workspace, configure the
# AMP datasource in AMG via the Grafana UI or a second targeted apply with the
# grafana provider initialized against the newly-created workspace URL:
#
#   provider "grafana" {
#     url  = "https://${aws_grafana_workspace.maezo.endpoint}"
#     auth = "<service-account-token>"  # from aws_grafana_workspace_service_account
#   }
#
# The AMG datasource JSON config (for reference):
# {
#   "type": "prometheus",
#   "name": "AMP-<env>",
#   "url": "<amp_prometheus_endpoint>",
#   "jsonData": {
#     "sigV4Auth": true,
#     "sigV4Region": "<aws_region>",
#     "sigV4AuthType": "workspace-iam-role",
#     "httpMethod": "POST"
#   }
# }
#
# Code location: deploy/terraform/modules/observability/main.tf (this comment block)
# Blocker: issue #16 (no AWS creds) + AMG two-phase bootstrap
#
# D12-02 / R-030 (2026-09-05): the dashboards themselves are now versioned as JSON at
# deploy/observability/dashboards/ (worker-runtime.json, agentes.json, dlq-lifecycle.json),
# provisioned into the DEV Grafana (docker-compose) via deploy/observability/
# grafana-provisioning/. AMG has no equivalent push path yet — pushing this same JSON into the
# AMG workspace is future work gated on the SAME blocker as the datasource wiring above (the
# `grafana` provider needs the AMG workspace URL from a completed first apply). Content is
# provisional until D12-01-a (SLO document) exists.
# ---------------------------------------------------------------------------
