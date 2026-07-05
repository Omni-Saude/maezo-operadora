# Module: secrets
# Secrets Manager secret shells for Maezo.
#
# IMPORTANT — these are SHELLS ONLY (names/ARNs) for consumption by External
# Secrets Operator. No actual secret values are written here.
# The following secrets are BLOCKED pending credential delivery:
#   - whatsapp/waba-token       (blocked: WABA credentials pending)
#   - whatsapp/app-secret       (blocked: WABA credentials pending — predeploy DB-2)
#   - whatsapp/verify-token     (blocked: WABA credentials pending — predeploy DB-2)
#   - llm/api-keys              (blocked: LLM API keys pending)
#   - tasy/oracle               (blocked: Tasy Oracle credentials pending)
#   - phi/hmac-key              (blocked: PHI HMAC key population pending — §6.2)
#
# References from amh-data-platform (data source, not resource):
#   - debezium/msk-bootstrap-servers  (their MSK Serverless; their convention)

locals {
  name_prefix = "${var.name_prefix}/${var.environment}"
  common_tags = merge(var.tags, {
    Module      = "secrets"
    ManagedBy   = "terraform"
    Platform    = "maezo"
    environment = var.environment
  })
}

# ---------------------------------------------------------------------------
# BLOCKED secrets — shells only; values set out-of-band
# ---------------------------------------------------------------------------

resource "aws_secretsmanager_secret" "whatsapp_waba_token" {
  name        = "${local.name_prefix}/whatsapp/waba-token"
  description = "BLOCKED: WhatsApp Business Account (WABA) token. Value set out-of-band by ops team."
  kms_key_id  = var.kms_key_arn

  # Prevent accidental deletion during plan/apply while awaiting credentials.
  recovery_window_in_days = 30

  tags = merge(local.common_tags, {
    Name    = "whatsapp-waba-token"
    Status  = "BLOCKED-pending-credentials"
    Blocker = "WABA-onboarding"
  })
}

# Meta App Secret — HMAC-SHA256 validation of X-Hub-Signature-256 on the
# webhook-receiver (predeploy DB-2). Consumed by the helm ExternalSecret
# `whatsapp-config` (remoteRef .../whatsapp/app-secret, property app_secret).
resource "aws_secretsmanager_secret" "whatsapp_app_secret" {
  name        = "${local.name_prefix}/whatsapp/app-secret"
  description = "BLOCKED: Meta App Secret for WhatsApp webhook HMAC signature validation. Value set out-of-band by ops team."
  kms_key_id  = var.kms_key_arn

  # Prevent accidental deletion during plan/apply while awaiting credentials.
  recovery_window_in_days = 30

  tags = merge(local.common_tags, {
    Name    = "whatsapp-app-secret"
    Status  = "BLOCKED-pending-credentials"
    Blocker = "WABA-onboarding"
  })
}

# WhatsApp webhook Verify Token — GET /webhook handshake with the Meta panel
# (predeploy DB-2). Consumed by the helm ExternalSecret `whatsapp-config`
# (remoteRef .../whatsapp/verify-token, property verify_token).
resource "aws_secretsmanager_secret" "whatsapp_verify_token" {
  name        = "${local.name_prefix}/whatsapp/verify-token"
  description = "BLOCKED: WhatsApp webhook verify token (Meta panel GET /webhook handshake). Value set out-of-band by ops team."
  kms_key_id  = var.kms_key_arn

  # Prevent accidental deletion during plan/apply while awaiting credentials.
  recovery_window_in_days = 30

  tags = merge(local.common_tags, {
    Name    = "whatsapp-verify-token"
    Status  = "BLOCKED-pending-credentials"
    Blocker = "WABA-onboarding"
  })
}

resource "aws_secretsmanager_secret" "llm_api_keys" {
  name        = "${local.name_prefix}/llm/api-keys"
  description = "BLOCKED: LLM provider API keys (Anthropic + PHI endpoint). Value set out-of-band."
  kms_key_id  = var.kms_key_arn

  recovery_window_in_days = 30

  tags = merge(local.common_tags, {
    Name    = "llm-api-keys"
    Status  = "BLOCKED-pending-credentials"
    Blocker = "LLM-contract"
  })
}

resource "aws_secretsmanager_secret" "tasy_oracle" {
  name        = "${local.name_prefix}/tasy/oracle"
  description = "BLOCKED: Tasy Oracle DB credentials for FHIR sync. Value set out-of-band."
  kms_key_id  = var.kms_key_arn

  recovery_window_in_days = 30

  tags = merge(local.common_tags, {
    Name    = "tasy-oracle"
    Status  = "BLOCKED-pending-credentials"
    Blocker = "Tasy-integration-agreement"
  })
}

# PHI pseudonymizer HMAC key (ADR-0006). Required at runtime since PR #177
# (res-phi-hmac-env-injection) made PHI_HMAC_KEY a mandatory env on
# agent-runtime + worker-daemon. Consumed by the helm ExternalSecret
# `phi-hmac-key` (remoteRef .../phi/hmac-key, property hmac_key). Populating
# the real key is a manual §6.2 step — shell only, same as the others.
resource "aws_secretsmanager_secret" "phi_hmac_key" {
  name        = "${local.name_prefix}/phi/hmac-key"
  description = "BLOCKED: PHI pseudonymizer HMAC key (ADR-0006). Value set out-of-band by ops team (§6.2)."
  kms_key_id  = var.kms_key_arn

  # Prevent accidental deletion during plan/apply while awaiting credentials.
  recovery_window_in_days = 30

  tags = merge(local.common_tags, {
    Name    = "phi-hmac-key"
    Status  = "BLOCKED-pending-credentials"
    Blocker = "PHI-HMAC-key-population"
  })
}

# ---------------------------------------------------------------------------
# MSK bootstrap servers — data source from amh-data-platform (not a resource)
# Name follows amh-data-platform convention: debezium/msk-bootstrap-servers
# External Secrets Operator reads this and injects into pod env.
# ---------------------------------------------------------------------------
data "aws_secretsmanager_secret" "msk_bootstrap" {
  name = "debezium/msk-bootstrap-servers"
}

data "aws_secretsmanager_secret_version" "msk_bootstrap" {
  secret_id = data.aws_secretsmanager_secret.msk_bootstrap.id
}
