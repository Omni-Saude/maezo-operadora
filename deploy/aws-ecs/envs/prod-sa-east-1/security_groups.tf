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
#
# AWS-0104 (trivy) — INTERIM, decidido pelo dono no pacote trivy/IaC D2 (19/09/2026):
# o conserto de verdade NAO e' estreitar este CIDR, e' VPC endpoint (gateway S3 +
# interface endpoints para ECR API/DKR, Secrets Manager, Logs, SSM e Bedrock) no VPC
# compartilhado da plataforma — trabalho cross-repo, fora deste state. Enquanto isso
# nao pousa, a saida /0 pela NAT e' o que mantem as tasks vivas. Precedente de egress
# exato no mesmo env: portal-network.tf (lista /32 validada fail-closed).
# EXPIRA 2026-12-19 (o dono pode emendar): quando o endpoint pousar, a regra /0 some
# e este ignore sai junto — a data esta aqui para o ignore nao virar permanente.
# trivy:ignore:AWS-0104 exp:2026-12-19
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

# Porta 8000 dos agentes — a rota de ingresso que EXECUTA um turno
# (`runtime/agent_runtime/ingress.py`). Antes desta regra a 8000 era so' o health check
# LOCAL do container: nada na VPC alcancava a porta, entao o Canal de Teste nao tinha como
# pedir um turno ao Rafael. Medido em 19/08/2026: o SG admitia 8080, 8500 e 9092, e nada mais.
#
# O par ingress/egress e' necessario porque as tasks compartilham UM security group: a mesma
# regra que deixa o Canal chegar ao agente tambem precisa deixar o Canal SAIR para ele.
resource "aws_vpc_security_group_ingress_rule" "interno_8000" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Ingresso dos agentes a partir dos demais componentes do maezo"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
  referenced_security_group_id = aws_security_group.tasks.id
}

resource "aws_vpc_security_group_egress_rule" "interno_8000" {
  security_group_id            = aws_security_group.tasks.id
  description                  = "Saida para o ingresso dos agentes na 8000"
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
  referenced_security_group_id = aws_security_group.tasks.id
}
