# Um SG para as tasks do maezo. Regras de EGRESS declaram para onde ESTE SG pode
# falar — elas nao escrevem nada no SG de destino, entao nao conflitam com o state
# da plataforma (mesma postura de envs/dev-sa-east-1-hapi/security_groups.tf).
#
# ATENCAO — a contrapartida NAO esta aqui e nao pode estar: o INGRESS 5432 no SG do
# Aurora (sg-0a8364a76c605d782) precisa ser adicionado no state da plataforma, via
# module.aurora_hapi.allowed_security_group_ids. O modulo aurora-cluster usa bloco
# `ingress` dynamic inline, que e' AUTORITATIVO: uma regra criada por este state
# seria apagada no proximo apply deles. Ver README, "dependencia cross-repo".

resource "aws_security_group" "tasks" {
  name        = "${local.name}-tasks-sg"
  description = "Tasks Fargate do maezo-operadora (${local.env})"
  vpc_id      = data.aws_vpc.this.id

  tags = merge(local.base_tags, { Name = "${local.name}-tasks-sg" })

  # O SG e' referenciado por servicos que sao substituidos com frequencia; criar o
  # novo antes de destruir o velho evita janela em que nenhuma task tem SG.
  lifecycle {
    create_before_destroy = true
  }
}

# Saida 443: ECR (puxar imagem), Secrets Manager, CloudWatch Logs, SSM (ECS Exec) e
# Bedrock — tudo pela NAT. Sem esta regra a task nem inicia: falha ao puxar a imagem.
resource "aws_vpc_security_group_egress_rule" "https" {
  security_group_id = aws_security_group.tasks.id
  description       = "APIs AWS (ECR, Secrets Manager, Logs, SSM, Bedrock) via NAT"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
  cidr_ipv4         = "0.0.0.0/0"
}

# Saida 5432 para o Aurora compartilhado.
resource "aws_vpc_security_group_egress_rule" "postgres" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Postgres do Aurora compartilhado (${var.aurora_cluster_identifier})"
  ip_protocol                  = "tcp"
  from_port                    = local.aurora_port
  to_port                      = local.aurora_port
  referenced_security_group_id = local.aurora_security_group_id
}

# Saida 80 para o ALB interno do HAPI (FHIR). O ALB e' da plataforma; so declaramos
# o destino a partir do nosso SG.
resource "aws_vpc_security_group_egress_rule" "hapi_internal" {
  security_group_id = aws_security_group.tasks.id
  description       = "HAPI FHIR pelo ALB interno da plataforma"
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
  cidr_ipv4         = data.aws_vpc.this.cidr_block
}

# Trafego entre componentes do proprio maezo (worker -> engine BPMN na 8080).
# Auto-referencia: origem e destino sao o mesmo SG.
resource "aws_vpc_security_group_ingress_rule" "interno_8080" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Engine BPMN (CIB Seven) a partir dos demais componentes do maezo"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_egress_rule" "interno_8080" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Saida para os demais componentes do maezo na 8080"
  ip_protocol                  = "tcp"
  from_port                    = 8080
  to_port                      = 8080
  referenced_security_group_id = aws_security_group.tasks.id
}
