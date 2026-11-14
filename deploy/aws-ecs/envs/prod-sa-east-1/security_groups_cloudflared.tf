# SG PROPRIO do tunel Cloudflare (review de seguranca do PR #1204).
#
# No dev o cloudflared usa o SG das tasks e a saida 7844 foi aberta NESSE SG — ou seja, todo
# processo do maezo (receptor, agentes, engine) ganhou saida para a internet na 7844. Em prod:
#   * o cloudflared tem SG proprio, SEM o SG-marcador do Aurora (a borda nao fala com banco);
#   * saida: 7844 UDP/TCP (QUIC e fallback HTTP/2 do tunel), 443 TCP (APIs da Cloudflare, ECR,
#     Logs, Secrets Manager via NAT) e 8080 SO' para o SG das tasks (receptor e cockpit);
#   * nenhuma regra de ENTRADA: a conexao e' de dentro para fora, a borda nao tem porta;
#   * o SG das tasks ganha UMA entrada 8080 vinda deste SG, e nada mais.
# A porta e' 7844, nao 443 — ver o cabecalho de service-cloudflared.tf (o tunel nao registrou
# so' com 443 no dev).

resource "aws_security_group" "cloudflared" {
  name        = "${local.name}-cloudflared"
  description = "Tunel Cloudflare do maezo-operadora (${local.env}) - so saida"
  vpc_id      = data.aws_vpc.this.id
  tags        = merge(local.base_tags, { Name = "${local.name}-cloudflared" })
}

# AWS-0104 (trivy) — IGNORE, mesmo despacho do dev (pacote trivy/IaC D2, 19/09/2026): a borda anycast
# da Cloudflare muda de IP sem aviso e o tunel nao registra sem a 7844 aberta. Mesma validade do dev.
# trivy:ignore:AWS-0104 exp:2026-12-19
resource "aws_vpc_security_group_egress_rule" "cloudflared_quic" {
  security_group_id = aws_security_group.cloudflared.id
  description       = "Tunel Cloudflare: QUIC (protocolo preferido)"
  ip_protocol       = "udp"
  from_port         = 7844
  to_port           = 7844
  cidr_ipv4         = "0.0.0.0/0"
}

# AWS-0104 (trivy) — IGNORE: fallback HTTP/2 do MESMO tunel (mesmo motivo e validade do QUIC acima).
# trivy:ignore:AWS-0104 exp:2026-12-19
resource "aws_vpc_security_group_egress_rule" "cloudflared_http2" {
  security_group_id = aws_security_group.cloudflared.id
  description       = "Tunel Cloudflare: fallback HTTP/2 sobre TCP"
  ip_protocol       = "tcp"
  from_port         = 7844
  to_port           = 7844
  cidr_ipv4         = "0.0.0.0/0"
}

# AWS-0104 (trivy) — IGNORE: 443 para APIs da Cloudflare (IP anycast, sem lista estavel) e AWS via NAT —
# mesma regra que o SG das tasks ja' tem com o mesmo ignore (security_groups.tf). Validade do dev.
# trivy:ignore:AWS-0104 exp:2026-12-19
resource "aws_vpc_security_group_egress_rule" "cloudflared_https" {
  security_group_id = aws_security_group.cloudflared.id
  description       = "APIs da Cloudflare e AWS (ECR, Logs, Secrets Manager) via NAT"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

resource "aws_vpc_security_group_egress_rule" "cloudflared_para_tasks" {
  security_group_id            = aws_security_group.cloudflared.id
  description                  = "Origem do tunel: receptor e cockpit na 8080 (SG das tasks)"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_ingress_rule" "tasks_8080_do_cloudflared" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Receptor/cockpit a partir do tunel Cloudflare"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.cloudflared.id
}
