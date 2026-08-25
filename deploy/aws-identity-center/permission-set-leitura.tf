# MaezoOperadoraLeitura — ver o ambiente sem poder toca-lo nem ler conteudo.
#
# Existe porque a maior parte do time precisa OLHAR: qual imagem esta rodando, quantas
# tasks estao de pe, o que o painel diz. Hoje a unica forma de olhar e' o
# `AdministratorAccess` — que e' o unico permission set que existe (medido 18/08/2026) e
# que da o lake inteiro junto.
#
# A LINHA QUE ESTE SET NAO ATRAVESSA: nenhum log. Nem um. Log de agente carrega prompt e
# narrativa; log do worker carrega variavel de processo do fluxo AUTH. Num ambiente que
# um dia tera dado real, "ver log" e' acesso a conteudo clinico, nao observabilidade de
# infraestrutura. Quem precisa de log de agente entra em `MaezoAgentEngineer`, onde essa
# concessao esta escrita e datada.
#
# O que sobra e' FORMA e SAUDE: o desenho do ambiente e se ele esta de pe. Isso responde
# a maioria das perguntas do dia sem entregar nada de paciente.

resource "aws_ssoadmin_permission_set" "leitura" {
  name        = "MaezoOperadoraLeitura"
  description = "Ver forma e saude do ambiente maezo (ECS, metricas, imagens). Sem log, sem secret, sem lake, sem escrita."

  instance_arn     = local.instance_arn
  session_duration = var.session_duration

  relay_state = "https://${var.aws_region}.console.aws.amazon.com/ecs/v2/clusters/${var.cluster_name}/services?region=${var.aws_region}"

  tags = {
    DataClass = "none"
  }
}

data "aws_iam_policy_document" "leitura" {
  statement {
    sid = "VerOCluster"

    actions = [
      "ecs:DescribeClusters",
      "ecs:ListServices",
      "ecs:ListTasks",
      "ecs:ListTaskDefinitions",
    ]

    resources = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:cluster/${var.cluster_name}"]
  }

  statement {
    sid = "VerServicesETasks"

    actions = [
      "ecs:DescribeServices",
      "ecs:DescribeTasks",
    ]

    # Todos os services deste cluster, nao apenas `agent-*`: o objetivo aqui e'
    # justamente ver o ambiente COMO UM TODO.
    resources = [
      "arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:service/${var.cluster_name}/*",
      "arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:task/${var.cluster_name}/*",
    ]
  }

  statement {
    sid = "LerTaskDefinition"

    # Sem ARN possivel (limitacao da API). Mostra imagem e nome de variavel; o VALOR de
    # secret nunca aparece aqui — ele e' resolvido pela execution role no momento do
    # start da task.
    actions   = ["ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }

  statement {
    sid = "VerImagens"

    actions = [
      "ecr:DescribeRepositories",
      "ecr:DescribeImages",
      "ecr:ListImages",
      "ecr:DescribeImageScanFindings",
    ]

    # So os dois repositorios do maezo. O `ecr:GetDownloadUrlForLayer` NAO esta aqui:
    # ver a tag de uma imagem e' informacao de deploy; baixar o conteudo da imagem e'
    # obter o codigo da aplicacao.
    resources = [
      "arn:${local.partition}:ecr:${var.aws_region}:${local.conta}:repository/amh/maezo-operadora",
      "arn:${local.partition}:ecr:${var.aws_region}:${local.conta}:repository/amh/cibseven-maezo",
    ]
  }

  statement {
    sid = "VerSaudeEPaineis"

    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "cloudwatch:GetDashboard",
      "cloudwatch:ListDashboards",
      "cloudwatch:DescribeAlarms",
      "cloudwatch:DescribeAlarmHistory",
    ]

    resources = ["*"]
  }

  # Tripwire, pelo mesmo motivo do outro set: sobrevive a alguem anexar uma managed
  # policy larga aqui depois. `ReadOnlyAccess` da AWS seria exatamente esse erro — ela
  # inclui `s3:GetObject`, o que transformaria "ver o ambiente" em ler o lake.
  statement {
    sid    = "NegarConteudoEEscrita"
    effect = "Deny"

    actions = [
      "s3:*",
      "rds:*",
      "rds-data:*",
      "glue:*",
      "athena:*",
      "lakeformation:*",
      "dms:*",
      "secretsmanager:GetSecretValue",
      # Nenhum log, de propriedade: ver o cabecalho deste arquivo.
      "logs:GetLogEvents",
      "logs:FilterLogEvents",
      "logs:StartQuery",
      "logs:GetQueryResults",
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
      "iam:*",
      "sso:*",
      "identitystore:*",
      "organizations:*",
      "ecs:UpdateService",
      "ecs:RegisterTaskDefinition",
      "ecs:ExecuteCommand",
      "ecs:RunTask",
    ]

    resources = ["*"]
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "leitura" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.leitura.arn
  inline_policy      = data.aws_iam_policy_document.leitura.json
}
