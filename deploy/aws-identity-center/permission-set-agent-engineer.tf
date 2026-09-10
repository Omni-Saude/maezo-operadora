# MaezoAgentEngineer — o time de engenharia de agentes.
#
# O teste do desenho, e a unica frase que importa aqui: este time consegue trocar
# modelo, criar guardrail, medir custo e reiniciar um agente — e NAO consegue ler dado
# de paciente nem se auto-promover.
#
# Escrito por RECURSO, nao por servico. A conta 203312548462 guarda 11,45M de recursos
# FHIR; `bedrock:*` ou `logs:*` em `Resource: "*"` seria conceder o ambiente inteiro
# para liberar quatro tarefas.

resource "aws_ssoadmin_permission_set" "agent_engineer" {
  name        = "MaezoAgentEngineer"
  description = "Engenharia de agentes maezo: invocar/gerir Bedrock, medir custo, reiniciar agente. Sem dado de paciente; PassRole somente para diagnostico sem secrets."

  instance_arn     = local.instance_arn
  session_duration = var.session_duration

  # A URL de relay leva a pessoa direto ao console do Bedrock em sa-east-1 depois do
  # login, em vez de despeja-la no console generico da conta que guarda o lake.
  relay_state = "https://${var.aws_region}.console.aws.amazon.com/bedrock/home?region=${var.aws_region}"

  tags = {
    Time      = "engenharia-de-agentes"
    DataClass = "none"
  }
}

data "aws_iam_policy_document" "agent_engineer" {
  # -------------------------------------------------------------------------
  # Bedrock — plano de dados
  # -------------------------------------------------------------------------
  statement {
    sid = "InvocarModelosAnthropic"

    actions = [
      "bedrock:InvokeModel",
      "bedrock:InvokeModelWithResponseStream",
      "bedrock:Converse",
      "bedrock:ConverseStream",
    ]

    # Tres familias de ARN, e todas as tres sao necessarias juntas:
    #
    # 1. foundation-model — sem conta e com regiao `*` de proposito: o ARN de foundation
    #    model NAO tem conta, e um perfil `global.*` roteia a chamada para a regiao que
    #    tiver capacidade. Restringir a regiao aqui quebra a invocacao de forma
    #    intermitente, que e' o pior modo de falha possivel.
    # 2. inference-profile — o `global.anthropic.*`, que e' o que existe nesta conta
    #    (medido: nao ha perfil BR-resident para Claude em sa-east-1).
    # 3. application-inference-profile — os perfis por agente da Onda 4, que existem para
    #    atribuir custo a `rafael`/`helena`/`marina` separadamente.
    #
    # `anthropic.*` e nao `claude-opus-5*` como na task role: o time PRECISA testar
    # haiku e sonnet para decidir o tier `fast`. Continua fechado a Anthropic — outro
    # fornecedor de modelo e' decisao de arquitetura, nao de tuning.
    resources = [
      "arn:${local.partition}:bedrock:*::foundation-model/anthropic.*",
      "arn:${local.partition}:bedrock:*:${local.conta}:inference-profile/global.anthropic.*",
      "arn:${local.partition}:bedrock:*:${local.conta}:application-inference-profile/*",
    ]
  }

  # -------------------------------------------------------------------------
  # Bedrock — plano de controle, leitura
  # -------------------------------------------------------------------------
  statement {
    sid = "LerCatalogoDeModelos"

    actions = [
      "bedrock:ListFoundationModels",
      "bedrock:GetFoundationModel",
      "bedrock:ListInferenceProfiles",
      "bedrock:GetInferenceProfile",
      # LEITURA da configuracao de logging. Ligar continua negado abaixo — mas quem
      # opera precisa poder VER se o log de invocacao esta ligado, porque a resposta
      # muda o que pode ser dito num incidente.
      "bedrock:GetModelInvocationLoggingConfiguration",
    ]

    # Estas APIs nao aceitam recurso: sao chamadas de catalogo do servico. O `*` aqui
    # e' o escopo real da API, nao afrouxamento.
    resources = ["*"]
  }

  # -------------------------------------------------------------------------
  # Bedrock — guardrails
  # -------------------------------------------------------------------------
  statement {
    sid = "CriarEListarGuardrails"

    # Create e List nao tem recurso pre-existente para nomear.
    actions = [
      "bedrock:CreateGuardrail",
      "bedrock:ListGuardrails",
    ]

    resources = ["*"]
  }

  statement {
    sid = "GerirGuardrailsExistentes"

    actions = [
      "bedrock:GetGuardrail",
      "bedrock:UpdateGuardrail",
      "bedrock:CreateGuardrailVersion",
      "bedrock:DeleteGuardrail",
      "bedrock:ApplyGuardrail",
    ]

    resources = ["arn:${local.partition}:bedrock:${var.aws_region}:${local.conta}:guardrail/*"]
  }

  # -------------------------------------------------------------------------
  # Bedrock — prompts versionados
  # -------------------------------------------------------------------------
  statement {
    sid = "CriarEListarPrompts"

    actions = [
      "bedrock:CreatePrompt",
      "bedrock:ListPrompts",
    ]

    resources = ["*"]
  }

  statement {
    sid = "GerirPromptsExistentes"

    actions = [
      "bedrock:GetPrompt",
      "bedrock:UpdatePrompt",
      "bedrock:CreatePromptVersion",
      "bedrock:DeletePrompt",
    ]

    resources = ["arn:${local.partition}:bedrock:${var.aws_region}:${local.conta}:prompt/*"]
  }

  # -------------------------------------------------------------------------
  # Bedrock — perfis de aplicacao (custo por agente, Onda 4)
  # -------------------------------------------------------------------------
  statement {
    sid = "CriarPerfilDeAplicacao"

    actions   = ["bedrock:CreateInferenceProfile"]
    resources = ["*"]
  }

  statement {
    sid = "GerirPerfisDeAplicacao"

    actions = [
      "bedrock:DeleteInferenceProfile",
      "bedrock:TagResource",
      "bedrock:UntagResource",
      "bedrock:ListTagsForResource",
    ]

    resources = ["arn:${local.partition}:bedrock:${var.aws_region}:${local.conta}:application-inference-profile/*"]
  }

  # -------------------------------------------------------------------------
  # Observabilidade — metricas
  # -------------------------------------------------------------------------
  statement {
    sid = "LerMetricasEPaineis"

    actions = [
      "cloudwatch:GetMetricData",
      "cloudwatch:GetMetricStatistics",
      "cloudwatch:ListMetrics",
      "cloudwatch:GetDashboard",
      "cloudwatch:ListDashboards",
      "cloudwatch:DescribeAlarms",
    ]

    # DITO EXPLICITAMENTE PARA NAO PARECER DESCUIDO: metrica do CloudWatch nao tem ARN
    # por namespace. A condicao `cloudwatch:namespace` existe para GetMetricStatistics e
    # ListMetrics, mas NAO para GetMetricData — usa-la aqui negaria silenciosamente a
    # unica API que serve para somar token por periodo. Metrica e' contador agregado,
    # nao conteudo: o que vaza no maximo e' volume de uso.
    resources = ["*"]
  }

  # -------------------------------------------------------------------------
  # Observabilidade — CUSTO
  # -------------------------------------------------------------------------
  # Adicionado em 02/09/2026. O permission set prometia "medir custo" no README desde 18/08 e
  # NAO entregava: dava metrica do CloudWatch, que mostra USO (token, invocacao) e nao
  # DINHEIRO. Trocar modelo sem ver a conta e' escolher no escuro — um perfil global e um
  # regional podem diferir mais no preco que na latencia.
  #
  # POR QUE AQUI E NAO NUM PERMISSION SET DA CONTA DE GESTAO
  #
  # O dado de custo e' consolidado no payer, e o instinto e' pedir acesso la'. Medido em
  # 02/09/2026: a conta de dados VE O PROPRIO custo pelo Cost Explorer — `get-cost-and-usage`
  # de dentro de 203312548462 devolveu US$ 4.487,33 para julho, exatamente o valor que o payer
  # atribui a ela. O acesso de conta vinculada esta' habilitado.
  #
  # Entao este statement da' o que ele precisa sem nenhuma pegada na conta de gestao — que a
  # AWS isenta de SCP por desenho e e' o unico lugar da organizacao sem rede de protecao. Ele
  # ve o custo da conta onde os agentes rodam, e NAO ve o das outras sete (prod, auditoria,
  # arquivo). Para um engenheiro de agentes isso nao e' limitacao: e' o escopo.
  statement {
    sid = "LerCusto"

    actions = [
      # O basico: quanto se gastou, por servico, por periodo.
      "ce:GetCostAndUsage",
      "ce:GetDimensionValues",
      "ce:GetTags",
      "ce:GetCostCategories",
      # Projecao — "se eu deixar este modelo ligado, quanto fecha o mes".
      "ce:GetCostForecast",
      "ce:GetUsageForecast",
      # Custo por RECURSO, e e' a que mais serve aqui: e' como se separa o gasto de um
      # modelo do de outro em vez de ver "Bedrock" como uma linha so'. Depende de o
      # payer ter ligado dado em nivel de recurso; sem isso a API responde vazia, nao
      # nega — e vazio e' resposta honesta.
      "ce:GetCostAndUsageWithResources",
      # Anomalia: e' o que avisa que um agente entrou em loop antes da fatura.
      "ce:GetAnomalies",
      "ce:GetAnomalyMonitors",
      "ce:GetAnomalySubscriptions",
      # Orcamento, somente leitura. `budgets:ModifyBudget` fica FORA de proposito: mudar o
      # teto de alerta e' o mesmo que desligar o alarme, e nao e' papel de quem e' medido
      # por ele.
      "budgets:ViewBudget",
      "budgets:DescribeBudgets",
      # Preco de tabela, para comparar modelo ANTES de trocar. E' dado publico da AWS.
      "pricing:GetProducts",
      "pricing:DescribeServices",
      "pricing:GetAttributeValues",
    ]

    # `*` porque nenhuma destas APIs tem ARN de recurso — o escopo delas e' a CONTA, e a
    # conta ja' e' a fronteira: este permission set esta' atribuido somente a 203312548462.
    # Uma condicao aqui nao teria o que restringir.
    resources = ["*"]
  }

  # -------------------------------------------------------------------------
  # Observabilidade — logs dos agentes
  # -------------------------------------------------------------------------
  statement {
    sid = "LerLogsDosAgentes"

    actions = [
      "logs:FilterLogEvents",
      "logs:GetLogEvents",
      "logs:DescribeLogStreams",
      # Insights: e' como se soma `llm_token_usage` por agente e por dia sem exportar
      # nada. StartQuery e' autorizado no log group, por isso esta neste statement.
      "logs:StartQuery",
    ]

    # SO' os log groups dos agentes. `/ecs/maezo-operadora-dev/worker-runtime` e o do
    # engine ficam de fora: eles carregam variavel de processo do fluxo AUTH, que num
    # ambiente com dado real seria clinico.
    #
    # ATENCAO, E ISTO E' UMA FRONTEIRA E NAO UMA NOTA DE RODAPE: o log do agente contem
    # o prompt e a narrativa gerada. Hoje isso e' aceitavel porque o ambiente roda com
    # `MAEZO_DOSSIER_NARRATIVE_GENERAL_ZONE_SYNTHETIC_ONLY=1` — ou seja, afirma nao ter
    # dado real de paciente. No dia em que tiver, LER ESTE LOG passa a ser acesso a PHI
    # e este statement precisa de decisao do DPO, nao de um `terraform apply`.
    resources = [
      "arn:${local.partition}:logs:${var.aws_region}:${local.conta}:log-group:/ecs/${var.cluster_name}/agent-*",
      "arn:${local.partition}:logs:${var.aws_region}:${local.conta}:log-group:/ecs/${var.cluster_name}/agent-*:*",
      # 09/09: receptor e ponte sao onde a Helena de fato EXECUTA — sem estes, o engenheiro de
      # agentes veria o daemon `agent-helena` (que nao executa turno) e nao o turno.
      "arn:${local.partition}:logs:${var.aws_region}:${local.conta}:log-group:/ecs/${var.cluster_name}/webhook-receiver",
      "arn:${local.partition}:logs:${var.aws_region}:${local.conta}:log-group:/ecs/${var.cluster_name}/webhook-receiver:*",
      "arn:${local.partition}:logs:${var.aws_region}:${local.conta}:log-group:/ecs/${var.cluster_name}/notifications-bridge",
      "arn:${local.partition}:logs:${var.aws_region}:${local.conta}:log-group:/ecs/${var.cluster_name}/notifications-bridge:*",
    ]
  }

  statement {
    sid = "NavegarNosLogs"

    # DescribeLogGroups nao aceita filtro por recurso de forma util e GetQueryResults e'
    # autorizado na consulta, nao no grupo. O que estas duas expoem e' NOME de log group
    # e resultado de consulta que a pessoa mesma disparou — nao conteudo novo.
    actions = [
      "logs:DescribeLogGroups",
      "logs:GetQueryResults",
      "logs:StopQuery",
      "logs:DescribeQueries",
    ]

    resources = ["*"]
  }

  # -------------------------------------------------------------------------
  # Operacao minima dos agentes
  # -------------------------------------------------------------------------
  statement {
    sid = "VerOAmbiente"

    actions = [
      "ecs:DescribeClusters",
      "ecs:ListServices",
      "ecs:ListTasks",
    ]

    resources = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:cluster/${var.cluster_name}"]
  }

  statement {
    sid = "VerTasks"

    actions = ["ecs:DescribeTasks"]

    resources = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:task/${var.cluster_name}/*"]
  }

  statement {
    sid = "LerTaskDefinition"

    # DescribeTaskDefinition nao e' autorizavel por ARN (a API nao suporta). E' leitura
    # de configuracao — mostra imagem, variavel de ambiente e ARN de secret, nunca o
    # VALOR do secret.
    actions   = ["ecs:DescribeTaskDefinition"]
    resources = ["*"]
  }

  # -------------------------------------------------------------------------
  # Diagnostico VPC delimitado — ADR-0006/0007/0049, sucessor PR357 F1-F4
  # -------------------------------------------------------------------------
  # Uma unica familia tem entrypoint fixo DNS/TCP, sem payload, task role ou secrets.
  # RunTask nao autoriza por ARN de cluster: a fronteira e' ecs:cluster. Exec segue
  # explicitamente negado; a identidade humana nao recebe canais SSM.
  # O operador envia MaezoPurpose=diagnostics e MaezoOwner=<STS UserId> em --tags.
  # &{aws:userid} e' expandido pelo IAM, nao pelo Terraform. O UserId inclui a role
  # e o nome da sessao federada; outra identidade nao pode escolher o dono da task.
  statement {
    sid       = "ExecutarDiagnosticoVpc"
    actions   = ["ecs:RunTask"]
    resources = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:task-definition/${var.cluster_name}-diagnostics:*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:cluster/${var.cluster_name}"]
    }
    condition {
      test     = "StringEquals"
      variable = "ecs:enable-execute-command"
      values   = ["false"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/MaezoPurpose"
      values   = ["diagnostics"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/MaezoOwner"
      values   = ["&{aws:userid}"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["MaezoPurpose", "MaezoOwner"]
    }
  }

  # Tag-on-create exige autorizacao adicional. ecs:CreateAction e' contexto AWS,
  # nao um parametro que TagResource direto consiga fornecer. Nao permite marcar
  # Kafka/engine como diagnostico, nem alterar o dono depois de criar a task.
  statement {
    sid       = "MarcarSomenteNovoDiagnostico"
    actions   = ["ecs:TagResource"]
    resources = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:task/${var.cluster_name}/*"]
    condition {
      test     = "StringEquals"
      variable = "ecs:CreateAction"
      values   = ["RunTask"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/MaezoPurpose"
      values   = ["diagnostics"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:RequestTag/MaezoOwner"
      values   = ["&{aws:userid}"]
    }
    condition {
      test     = "ForAllValues:StringEquals"
      variable = "aws:TagKeys"
      values   = ["MaezoPurpose", "MaezoOwner"]
    }
  }

  statement {
    sid       = "EncerrarSomenteDiagnosticoProprio"
    actions   = ["ecs:StopTask"]
    resources = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:task/${var.cluster_name}/*"]
    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:cluster/${var.cluster_name}"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/MaezoPurpose"
      values   = ["diagnostics"]
    }
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/MaezoOwner"
      values   = ["&{aws:userid}"]
    }
  }

  statement {
    sid       = "PassarSomenteExecutionRoleDeDiagnostico"
    actions   = ["iam:PassRole"]
    resources = ["arn:${local.partition}:iam::${local.conta}:role/${var.cluster_name}-diagnostics-execution"]
    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  statement {
    sid       = "LerResultadoDeDiagnostico"
    actions   = ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:DescribeLogStreams"]
    resources = ["arn:${local.partition}:logs:${var.aws_region}:${local.conta}:log-group:/ecs/${var.cluster_name}/diagnostics:*"]
  }

  # iam:* permanece explicitamente negado. A chave iam:PassedToService so' existe
  # na autorizacao PassRole: ausente em CreateRole/PutRolePolicy/AttachRolePolicy,
  # etc., StringNotEqualsIfExists faz o Deny aplicar. A excecao ECS ainda depende
  # do Allow na role EXATA e do segundo Deny, que fecha todas as outras roles.
  statement {
    sid       = "NegarIamExcetoPassRoleParaEcs"
    effect    = "Deny"
    actions   = ["iam:*"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }
  statement {
    sid           = "NegarPassRoleForaDoDiagnostico"
    effect        = "Deny"
    actions       = ["iam:PassRole"]
    not_resources = ["arn:${local.partition}:iam::${local.conta}:role/${var.cluster_name}-diagnostics-execution"]
  }
  statement {
    sid           = "NegarRunTaskForaDoDiagnostico"
    effect        = "Deny"
    actions       = ["ecs:RunTask"]
    not_resources = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:task-definition/${var.cluster_name}-diagnostics:*"]
  }
  statement {
    sid       = "NegarDiagnosticoForaDoCluster"
    effect    = "Deny"
    actions   = ["ecs:RunTask", "ecs:StopTask"]
    resources = ["*"]
    condition {
      test     = "ArnNotEquals"
      variable = "ecs:cluster"
      values   = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:cluster/${var.cluster_name}"]
    }
  }
  statement {
    sid       = "NegarStopDeWorkload"
    effect    = "Deny"
    actions   = ["ecs:StopTask"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "aws:ResourceTag/MaezoPurpose"
      values   = ["diagnostics"]
    }
  }
  statement {
    sid       = "NegarStopDeOutroDono"
    effect    = "Deny"
    actions   = ["ecs:StopTask"]
    resources = ["*"]
    condition {
      test     = "StringNotEquals"
      variable = "aws:ResourceTag/MaezoOwner"
      values   = ["&{aws:userid}"]
    }
  }
  statement {
    sid       = "NegarRemarcacaoDeTasksExistentes"
    effect    = "Deny"
    actions   = ["ecs:TagResource"]
    resources = ["*"]
    condition {
      test     = "StringNotEqualsIfExists"
      variable = "ecs:CreateAction"
      values   = ["RunTask"]
    }
  }

  statement {
    sid = "OperarServicesDosAgentes"

    actions = [
      "ecs:DescribeServices",
      "ecs:UpdateService",
    ]

    # `agent-*` apenas: reiniciar o proprio agente depois de mudar prompt ou tier e' a
    # tarefa. O engine, o worker, o canal e o tunel nao entram.
    #
    # A permissao preexistente e' preservada, mas roles iguais NAO provam secrets
    # iguais: bootstrap-db injeta o segredo mestre. PassRole nas roles de workload
    # continua explicitamente negado; este pacote nao qualifica deploy de services.
    resources = ["arn:${local.partition}:ecs:${var.aws_region}:${local.conta}:service/${var.cluster_name}/agent-*"]
  }

  # -------------------------------------------------------------------------
  # Negacoes explicitas — o tripwire
  # -------------------------------------------------------------------------
  # Nada abaixo esta concedido acima; o Deny e' redundante HOJE. Existe porque um Deny
  # dentro do proprio permission set sobrevive a alguem anexar uma managed policy larga
  # a este mesmo permission set no futuro — que e' exatamente como um acesso estreito
  # vira acesso amplo sem ninguem decidir isso.
  statement {
    sid    = "NegarDadoDePacienteEAutoPromocao"
    effect = "Deny"

    actions = [
      # O lake e o banco: onde os 11,45M de recursos FHIR realmente vivem
      # (amh-lake-raw/bronze/silver/gold-dev-sa-east-1, amh-aurora-hapi-dev).
      "s3:*",
      "rds:*",
      "rds-data:*",
      "glue:*",
      "athena:*",
      "lakeformation:*",
      "dms:*",
      # As credenciais — inclusive `amh/mpi/pepper`, que e' a chave de
      # reidentificacao do indice de pacientes.
      "secretsmanager:GetSecretValue",
      "secretsmanager:PutSecretValue",
      # Quem pode escrever politica pode se conceder qualquer coisa.
      # IAM permanece no Deny especifico acima, com a unica excecao PassRole.
      "sso:*",
      "sso-directory:*",
      "identitystore:*",
      "organizations:*",
      # Deploy: task definition nova e' o caminho para rodar container com outra role.
      "ecs:RegisterTaskDefinition",
      "ecs:UntagResource",
      "ssm:StartSession",
      "ecs:ExecuteCommand",
      # Ligar log de invocacao captura narrativa clinica — decisao de DPO (§1 item 6 do
      # plano), nao tarefa de engenharia.
      "bedrock:PutModelInvocationLoggingConfiguration",
      "bedrock:DeleteModelInvocationLoggingConfiguration",
      # Treinar/copiar modelo com dado desta conta e' outra ordem de decisao.
      "bedrock:CreateModelCustomizationJob",
      "bedrock:CreateModelCopyJob",
      # Throughput provisionado e' compromisso financeiro mensal, nao tuning.
      "bedrock:CreateProvisionedModelThroughput",
      "bedrock:UpdateProvisionedModelThroughput",
    ]

    resources = ["*"]
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "agent_engineer" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.agent_engineer.arn
  inline_policy      = data.aws_iam_policy_document.agent_engineer.json
}
