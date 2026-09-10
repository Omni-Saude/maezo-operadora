# ADR-0049 D8/D11 + ADR-0006/0017: dedicated SG, deny by default, concrete egress.
resource "aws_security_group" "portal" {
  for_each    = local.portal_config
  name        = "${local.portal_name}-tasks"
  description = "Dedicated portal identity BFF; no peer agent/engine/FHIR ingress"
  vpc_id      = data.aws_vpc.this.id
  tags        = local.portal_tags
}

resource "aws_security_group" "portal_ingress" {
  for_each    = local.portal_config
  name        = "${local.portal_name}-tls"
  description = "Dedicated public TLS ingress for the human portal"
  vpc_id      = data.aws_vpc.this.id
  tags        = local.portal_tags
}

resource "aws_vpc_security_group_ingress_rule" "portal_tls" {
  for_each          = local.portal_config
  security_group_id = aws_security_group.portal_ingress[each.key].id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0" # Public human login; only the TLS NLB, never tasks.
}

resource "aws_vpc_security_group_egress_rule" "portal_ingress_to_bff" {
  for_each                     = local.portal_config
  security_group_id            = aws_security_group.portal_ingress[each.key].id
  referenced_security_group_id = aws_security_group.portal[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_ingress_rule" "portal_from_ingress" {
  for_each                     = local.portal_config
  security_group_id            = aws_security_group.portal[each.key].id
  referenced_security_group_id = aws_security_group.portal_ingress[each.key].id
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
}

resource "aws_vpc_security_group_egress_rule" "portal_https" {
  for_each          = var.portal == null ? toset([]) : var.portal.https_egress_ipv4_cidrs
  security_group_id = aws_security_group.portal["this"].id
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = each.value
  description       = "Owner-approved fixed identity/AWS endpoint address; no FQDN enforcement claim"
}

resource "aws_vpc_security_group_egress_rule" "portal_postgres" {
  for_each                     = local.portal_config
  security_group_id            = aws_security_group.portal[each.key].id
  referenced_security_group_id = local.aurora_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = local.aurora_port
  to_port                      = local.aurora_port
  # Counterpart ingress belongs ONLY to the AMH Aurora Terraform state.
}

data "aws_subnet" "portal_public" {
  for_each = var.portal == null ? toset([]) : var.portal.public_subnet_ids
  id       = each.value
}

# TLS NLB preserves HTTP Host and does not inspect/log URL query or cookies.
# A standard ALB health probe uses an IP Host rejected by the unchanged BFF.
# TCP health is deliberately liveness only; ECS additionally probes exact session401.
resource "aws_lb" "portal" {
  for_each                         = local.portal_config
  name                             = "portal-${substr(sha256(local.portal_name), 0, 16)}"
  internal                         = false
  load_balancer_type               = "network"
  ip_address_type                  = "ipv4"
  subnets                          = each.value.public_subnet_ids
  security_groups                  = [aws_security_group.portal_ingress[each.key].id]
  enable_cross_zone_load_balancing = true
  tags                             = local.portal_tags
  lifecycle {
    precondition {
      condition = (
        alltrue([for subnet in data.aws_subnet.portal_public : subnet.vpc_id == data.aws_vpc.this.id]) &&
        length(toset([for subnet in data.aws_subnet.portal_public : subnet.availability_zone])) >= 2
      )
      error_message = "Portal public subnets must belong to this VPC in at least two availability zones."
    }
  }
}

resource "aws_lb_target_group" "portal" {
  for_each             = local.portal_config
  name                 = "portal-${substr(sha256(local.portal_name), 0, 16)}"
  vpc_id               = data.aws_vpc.this.id
  port                 = 8080
  protocol             = "TCP"
  target_type          = "ip"
  preserve_client_ip   = false
  proxy_protocol_v2    = false
  deregistration_delay = 30
  health_check {
    protocol = "TCP"
    port     = "traffic-port"
  }
  tags = local.portal_tags
}

resource "aws_lb_listener" "portal" {
  for_each          = local.portal_config
  load_balancer_arn = aws_lb.portal[each.key].arn
  port              = 443
  protocol          = "TLS"
  certificate_arn   = each.value.certificate_arn
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.portal[each.key].arn
  }
}

output "portal_prerequisites" {
  description = "Hand off the SG to the AMH Aurora owner and the NLB alias to the DNS owner; no readiness claim."
  value = { for key, config in local.portal_config : key => {
    task_security_group_id = aws_security_group.portal[key].id
    dns_name               = aws_lb.portal[key].dns_name
    dns_zone_id            = aws_lb.portal[key].zone_id
    public_origin          = config.public_origin
    callback_url           = "${config.public_origin}/api/v1/portal/auth/callback"
    activated              = var.portal_enabled
  } }
}

# Exact native target metadata only. The external owner retains counterpart ingress.
data "aws_security_group" "portal_staff_native_https" {
  for_each = local.portal_staff_config
  id       = each.value.native_https_security_group_id
}
data "aws_security_group" "portal_staff_native_database" {
  for_each = local.portal_staff_config
  id       = each.value.native_database_security_group_id
}
resource "aws_vpc_security_group_egress_rule" "portal_staff_native_https" {
  # Reuse the existing Aurora rule if its complete destination tuple matches.
  for_each = { for key, staff in local.portal_staff_config : key => staff
    if staff.native_https_security_group_id != local.aurora_security_group_id || local.aurora_port != 443
  }
  security_group_id            = aws_security_group.portal[each.key].id
  referenced_security_group_id = each.value.native_https_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = 443
  to_port                      = 443
  description                  = "Qualified staff native mTLS destination; no raw engine authority"
  lifecycle {
    precondition {
      condition = (data.aws_security_group.portal_staff_native_https[each.key].owner_id == var.aws_account_id &&
      data.aws_security_group.portal_staff_native_https[each.key].vpc_id == data.aws_vpc.this.id)
      error_message = "Staff native HTTPS SG must be explicitly owner-qualified in this account/VPC."
    }
  }
}
resource "aws_vpc_security_group_egress_rule" "portal_staff_native_database" {
  # One effective rule per SG/TCP/port tuple: Aurora first, then HTTPS, then DB.
  # Target metadata is still independently qualified by the task definition.
  for_each = { for key, staff in local.portal_staff_config : key => staff
    if(staff.native_database_security_group_id != local.aurora_security_group_id || staff.native_database_port != local.aurora_port) &&
    (staff.native_database_security_group_id != staff.native_https_security_group_id || staff.native_database_port != 443)
  }
  security_group_id            = aws_security_group.portal[each.key].id
  referenced_security_group_id = each.value.native_database_security_group_id
  ip_protocol                  = "tcp"
  from_port                    = each.value.native_database_port
  to_port                      = each.value.native_database_port
  description                  = "Qualified staff native SELECT-only witness DB destination"
  lifecycle {
    precondition {
      condition = (data.aws_security_group.portal_staff_native_database[each.key].owner_id == var.aws_account_id &&
      data.aws_security_group.portal_staff_native_database[each.key].vpc_id == data.aws_vpc.this.id)
      error_message = "Staff native witness DB SG must be explicitly owner-qualified in this account/VPC."
    }
  }
}
