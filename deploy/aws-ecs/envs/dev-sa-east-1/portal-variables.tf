# ADR-0049 D3/D4/D9, DL-0048. Null creates no portal resources. Supplying
# configuration prepares infrastructure at zero tasks; activation is a second step.
variable "portal_enabled" {
  type        = bool
  default     = false
  description = "Activate only after the AMH-owned DB/IdP/DNS prerequisites in portal.md are verified."
  validation {
    condition     = !var.portal_enabled || var.portal != null
    error_message = "Activating the portal requires the complete portal configuration."
  }
}

variable "portal" {
  description = "Owner-supplied non-secret deployment inputs. No fabricated live defaults; see portal.md."
  type = object({
    tenant                  = string
    issuer                  = string
    cognito_origin          = string
    human_client_id         = string
    human_client_purpose    = string
    machine_client_id       = string
    public_origin           = string
    image_digest            = string
    database_secret_arn     = string
    certificate_arn         = string
    public_subnet_ids       = set(string)
    https_egress_ipv4_cidrs = set(string)
  })
  default = null

  validation {
    condition = var.portal == null ? true : (
      can(regex("^[A-Za-z0-9_-]{1,80}$", var.portal.tenant)) &&
      var.portal.tenant == var.tenant_id && var.aws_region == "sa-east-1"
    )
    error_message = "Portal tenant must equal the server-owned environment tenant, in sa-east-1."
  }
  validation {
    condition = var.portal == null ? true : (
      can(regex("^https://cognito-idp\\.sa-east-1\\.amazonaws\\.com/sa-east-1_[A-Za-z0-9]+$", var.portal.issuer)) &&
      alltrue([for origin in [var.portal.public_origin, var.portal.cognito_origin] :
        can(regex("^https://[a-z0-9]([a-z0-9-]*[a-z0-9])?(\\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$", origin))
      ])
    )
    error_message = "Use an exact regional Cognito issuer and approved HTTPS DNS origins without path, port, credentials, query or fragment."
  }
  validation {
    condition = var.portal == null ? true : (
      var.portal.human_client_purpose == "dedicated-human-code-pkce" &&
      can(regex("^[a-z0-9]{1,128}$", var.portal.human_client_id)) &&
      can(regex("^[a-z0-9]{1,128}$", var.portal.machine_client_id)) &&
      var.portal.human_client_id != var.portal.machine_client_id &&
      var.portal.machine_client_id == var.fhir_cognito_client_id
    )
    error_message = "Require a dedicated public human code/PKCE client distinct from the actual configured M2M client."
  }
  validation {
    condition = var.portal == null ? true : (
      can(regex("^sha256:[0-9a-f]{64}$", var.portal.image_digest)) &&
      startswith(var.portal.database_secret_arn, "arn:aws:secretsmanager:sa-east-1:${var.aws_account_id}:secret:maezo-operadora/dev/portal/${var.portal.tenant}/session-dsn-") &&
      can(regex("^arn:aws:secretsmanager:sa-east-1:[0-9]{12}:secret:maezo-operadora/dev/portal/[A-Za-z0-9_-]+/session-dsn-[A-Za-z0-9]{6}$", var.portal.database_secret_arn)) &&
      startswith(var.portal.certificate_arn, "arn:aws:acm:sa-east-1:${var.aws_account_id}:certificate/") &&
      can(regex("^arn:aws:acm:sa-east-1:[0-9]{12}:certificate/[0-9a-f-]{36}$", var.portal.certificate_arn))
    )
    error_message = "Require an immutable application image digest, tenant-dedicated session secret ARN and regional account-owned ACM certificate ARN."
  }
  validation {
    condition = var.portal == null ? true : (
      length(var.portal.public_subnet_ids) >= 2 &&
      alltrue([for id in var.portal.public_subnet_ids : can(regex("^subnet-[0-9a-f]+$", id))]) &&
      length(var.portal.https_egress_ipv4_cidrs) > 0 &&
      alltrue([for cidr in var.portal.https_egress_ipv4_cidrs :
        can(cidrnetmask(cidr)) && can(regex("/32$", cidr))
      ])
    )
    error_message = "Require at least two public subnets and explicit approved IPv4 /32 HTTPS destinations; empty or universal egress is forbidden."
  }
}
