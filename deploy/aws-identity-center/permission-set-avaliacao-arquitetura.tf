# AvaliacaoArquiteturaLeitura — avaliacao externa da arquitetura (ITCore, 14/10/2026).
#
# PARA QUE: os avaliadores precisam ENXERGAR tudo o que existe e como esta configurado em todas
# as contas da organizacao (rede, ECS, EMR, Glue, MWAA, IAM, KMS, politicas de bucket, SCPs),
# para avaliar a arquitetura de dados de ponta a ponta. Pedido do Leonardo.
#
# A LINHA QUE ESTE SET NAO ATRAVESSA: CONTEUDO. Sao duas politicas gerenciadas da AWS:
#   - ViewOnlyAccess: List/Describe dos servicos — o inventario, sem ler dado;
#   - SecurityAudit: leitura de CONFIGURACAO de seguranca (politicas IAM/KMS/bucket, SCP, trilhas).
# Nenhuma das duas concede s3:GetObject, secretsmanager:GetSecretValue, leitura de log
# (logs:GetLogEvents/FilterLogEvents) nem athena:StartQueryExecution; e o Lake Formation nao da
# grant a este set, entao o lago nao e' consultavel. O ambiente de dev tem dado de saude REAL e os
# avaliadores sao terceiros: forma e configuracao, sim; dado, nao.
#
# Ressalva conhecida: o ecs:DescribeTaskDefinition (ViewOnly) mostra VALORES de variavel de
# ambiente nao secreta das task definitions; segredo de verdade vai por `secrets`/valueFrom e nao
# aparece. Revogar ao fim da avaliacao: delete-group-membership (README).
resource "aws_ssoadmin_permission_set" "avaliacao_arquitetura" {
  name        = "AvaliacaoArquiteturaLeitura"
  description = "Avaliacao externa da arquitetura: ver o que existe e como esta configurado (ViewOnly + SecurityAudit). Sem dado, sem log, sem secret."

  instance_arn     = local.instance_arn
  session_duration = var.session_duration

  tags = {
    DataClass = "none"
    Proposito = "avaliacao-arquitetura-externa"
  }
}

resource "aws_ssoadmin_managed_policy_attachment" "avaliacao_view_only" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.avaliacao_arquitetura.arn
  managed_policy_arn = "arn:${local.partition}:iam::aws:policy/job-function/ViewOnlyAccess"
}

resource "aws_ssoadmin_managed_policy_attachment" "avaliacao_security_audit" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.avaliacao_arquitetura.arn
  managed_policy_arn = "arn:${local.partition}:iam::aws:policy/SecurityAudit"
}

resource "aws_identitystore_group" "avaliacao_arquitetura" {
  identity_store_id = local.identity_store_id

  display_name = "avaliacao-arquitetura"
  description  = "Avaliacao externa da arquitetura (somente configuracao, sem dado). Recebe AvaliacaoArquiteturaLeitura em todas as contas da organizacao."
}

# Todas as contas da organizacao: avaliar a arquitetura inteira inclui rede, log, auditoria e prod.
variable "contas_avaliacao_arquitetura" {
  description = "Contas da organizacao em que o grupo avaliacao-arquitetura recebe AvaliacaoArquiteturaLeitura."
  type        = map(string)
  default = {
    gestao      = "169446931765"
    dev         = "203312548462"
    stg         = "506953262361"
    prod        = "441395443640"
    dr          = "278029334974"
    networking  = "829182231954"
    audit       = "970227532033"
    log_archive = "578125593630"
  }
}

resource "aws_ssoadmin_account_assignment" "avaliacao_arquitetura" {
  for_each = var.contas_avaliacao_arquitetura

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.avaliacao_arquitetura.arn

  principal_id   = aws_identitystore_group.avaliacao_arquitetura.group_id
  principal_type = "GROUP"

  target_id   = each.value
  target_type = "AWS_ACCOUNT"
}

output "grupo_avaliacao_arquitetura" {
  description = "group-id para create-group-membership."
  value       = aws_identitystore_group.avaliacao_arquitetura.group_id
}
