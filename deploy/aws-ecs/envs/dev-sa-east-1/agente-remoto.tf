# ---------------------------------------------------------------------------
# RUNTIME REMOTO DO AGENTE DE ENTRADA (ADR-0067 §6, fase 4b)
# ---------------------------------------------------------------------------
# Ate' aqui o `agent-helena` (service-agents.tf) subia e NAO executava turno: o grafo da Helena roda
# DENTRO do receptor. Com a chave `helena_agente_remoto` ligada:
#
#   * a task do `agent-helena` (so' ela; rafael e marina ficam byte a byte iguais) troca o comando para
#     `python -m maezo.platform.agente_remoto` (FastAPI interna, porta 8000, `/healthz`) e passa a
#     compor o turno com o MESMO contrato de AMBIENTE do receptor (`local.webhook_receiver_contrato`),
#     mas com segredos e role PROPRIOS e menores (revisao de seguranca da 4b, abaixo). A imagem continua
#     `var.agents_image_digest` — e' isso que separa o deploy da Helena do deploy do receptor;
#   * o receptor recebe `MAEZO_AGENTE_DE_ENTRADA_URL` e passa a chamar o agente pela porta;
#   * o agente recebe `MAEZO_RECEPTOR_URL` para o callback de envio: quem envia WhatsApp continua
#     sendo o receptor. Por isso o agente NAO recebe `secrets_envio` (credenciais da Meta) nem a
#     custodia do telefone (`local.recipient_vault_env`): ele nunca envia e nunca ve o numero.
#
# Desligada (default) nenhum recurso muda: as listas abaixo ficam vazias e os ternarios de
# service-agents.tf devolvem o que ja' estava la'.
#
# PRE-REQUISITO DO APPLY: o digest em `agents_image_digest` precisa conter `maezo.platform.agente_remoto`.
# Com uma imagem anterior o container sai no boot, o health check reprova e o circuit breaker do
# servico volta a task definition anterior — o receptor, porem, ja' teria a URL nova.
#
# REDE: nada novo. Receptor e agentes usam o MESMO security group (`aws_security_group.tasks`) e as
# regras auto-referenciadas de security_groups.tf ja' cobrem os dois sentidos:
#   receptor -> agent-helena:8000  `interno_8000` (ingress + egress)
#   agent-helena -> receptor:8080  `interno_8080` (ingress + egress)
# Declarar outro par aqui duplicaria regra e o apply falharia por regra repetida.
#
# DESCOBERTA: os dois ja' tem registro no namespace privado (`aws_service_discovery_service.agente`
# e `aws_service_discovery_service.webhook_receiver`). Os nomes sao DERIVADOS desses recursos,
# nunca IP fixo — o IP da task muda a cada deploy.

variable "helena_agente_remoto" {
  description = "Liga o runtime remoto da Helena (ADR-0067 §6): agent-helena executa o turno em `maezo.platform.agente_remoto` (porta 8000) e o receptor o chama por HTTP interno (o simulador segue em processo). Desligada, nada muda."
  type        = bool
  default     = false
}

locals {
  # Portas reais de cada lado (as mesmas dos `portMappings` e dos health checks).
  agente_remoto_porta = 8000
  # O receptor escuta na 8080 (service-webhook-receiver.tf), NAO na 8000.
  receptor_porta = 8080

  # `<servico>.<namespace>`: o nome A que o Cloud Map publica para cada servico.
  agente_remoto_dns = "${aws_service_discovery_service.agente["helena"].name}.${aws_service_discovery_private_dns_namespace.this.name}"
  receptor_dns      = "${aws_service_discovery_service.webhook_receiver.name}.${aws_service_discovery_private_dns_namespace.this.name}"

  agente_remoto_url = "http://${local.agente_remoto_dns}:${local.agente_remoto_porta}"
  receptor_url      = "http://${local.receptor_dns}:${local.receptor_porta}"

  # Por agente: so' a helena e so' com a chave. service-agents.tf le' este mapa.
  agente_remoto_em = { for id in keys(local.agentes) : id => id == "helena" && var.helena_agente_remoto }

  agente_remoto_comando = ["sh", "-c", "set -eu\n${local.dsn_export_app}\nexec python -m maezo.platform.agente_remoto"]

  # Lado CLIENTE: SO' o receptor. Fora do `webhook_receiver_contrato` de proposito — o contrato tambem
  # alimenta o proprio agente remoto, que nao pode receber a URL de si mesmo.
  #
  # O SIMULADOR NAO chama o agente remoto (decisao de 23/10/2026): o callback de envio do agente vai para
  # o RECEPTOR (`MAEZO_RECEPTOR_URL`), que nao conhece o turno simulado — o envio do simulador e' capturado
  # dentro do proprio processo dele. O simulador segue rodando a Helena EM PROCESSO, com a mesma imagem.
  agente_remoto_env_cliente = var.helena_agente_remoto ? [
    { name = "MAEZO_AGENTE_DE_ENTRADA_URL", value = local.agente_remoto_url },
  ] : []

  # Lado SERVIDOR: o contrato do receptor, com tres trocas:
  #   HEALTH_PORT  8000 (o contrato traz a 8080 do receptor);
  #   AGENT_ID     `helena` (o contrato ja' traz; repetido aqui para nao depender disso);
  #   MAEZO_RECEPTOR_URL  o callback de envio.
  agente_remoto_environment = concat(
    [for e in local.webhook_receiver_contrato.environment_app : e if !contains(["HEALTH_PORT", "AGENT_ID"], e.name)],
    [
      { name = "AGENT_ID", value = "helena" },
      { name = "HEALTH_PORT", value = tostring(local.agente_remoto_porta) },
      { name = "MAEZO_RECEPTOR_URL", value = local.receptor_url },
    ],
    local.webhook_receiver_contrato.environment_amh,
  )

  # Contrato de segredos PROPRIO do agente (revisao de seguranca da 4b): banco, PHI_HMAC_KEY e AMH.
  # FORA de proposito, e nao por esquecimento:
  #   WHATSAPP_APP_SECRET   com ele quem compromete o agente forja webhook valido no /webhook publico;
  #   WHATSAPP_VERIFY_TOKEN idem para o handshake da Meta;
  #   chave de assinatura A2A (MAEZO_A2A_CARD_SIGNING_KEY__*) o agente remoto nao publica Card;
  #   `secrets_envio`        o agente nao envia (o callback e' do receptor).
  # PHI_HMAC_KEY vem do MESMO segredo do receptor: o pseudonimo tem de ser igual dos dois lados.
  agente_remoto_secrets = concat(
    local.db_secrets_maezo,
    [
      { name = "PHI_HMAC_KEY", valueFrom = "${aws_secretsmanager_secret.phi_hmac_key.arn}:key::" },
    ],
    local.amh_interop_secrets,
  )
}

# ---------------------------------------------------------------------------
# ROLE PROPRIA DO AGENTE REMOTO (revisao de seguranca da 4b)
# ---------------------------------------------------------------------------
# Antes ele herdava a role do receptor, que com a custodia ligada tem `CustodiaSoCifra` (kms:Encrypt na
# chave da custodia, recipient-vault.tf): quem comprometesse o agente plantaria custodia com o numero
# do atacante. Esta role tem SO' o que o turno usa:
#   Bedrock     os MESMOS documentos do receptor (`task_bedrock`, busca semantica, saude gerativa e o
#               modelo proprio do roteador) — mesma lista de modelos e a mesma excecao regional do DPO;
#   pacotes     leitura do prefixo dos pacotes (pacotes-dos-agentes.tf), quando ligados;
#   ECS Exec    o servico `agent-helena` tem `enable_execute_command = true`.
# Segredos (`valueFrom`) e logs (`awslogs`) sao da role de EXECUCAO (`task_execution`), nao desta.
# SEM kms da custodia. Desligada a chave, nada disto existe.
locals {
  agente_remoto_role = var.helena_agente_remoto ? 1 : 0
}

resource "aws_iam_role" "agente_remoto_task" {
  count              = local.agente_remoto_role
  name               = "${local.name}-agent-helena-remoto-task"
  description        = "Runtime remoto da Helena (ADR-0067 fase 4b): Bedrock, pacotes e ECS Exec. Sem custodia e sem segredos do webhook."
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
  tags               = merge(local.base_tags, { agente = "helena" })
}

resource "aws_iam_role_policy" "agente_remoto_bedrock" {
  count  = local.agente_remoto_role
  name   = "${local.name}-bedrock"
  role   = aws_iam_role.agente_remoto_task[0].id
  policy = data.aws_iam_policy_document.task_bedrock.json
}

resource "aws_iam_role_policy" "agente_remoto_busca_semantica" {
  count  = local.agente_remoto_role
  name   = "${local.name}-busca-semantica-saude"
  role   = aws_iam_role.agente_remoto_task[0].id
  policy = data.aws_iam_policy_document.busca_semantica_saude.json
}

resource "aws_iam_role_policy" "agente_remoto_saude_gerativa" {
  count  = local.agente_remoto_role
  name   = "${local.name}-saude-gerativa-experimental"
  role   = aws_iam_role.agente_remoto_task[0].id
  policy = data.aws_iam_policy_document.saude_gerativa_experimental.json
}

resource "aws_iam_role_policy" "agente_remoto_roteador_modelo" {
  count  = local.agente_remoto_role * local.roteador_modelo_ativo
  name   = "${local.name}-roteador-modelo"
  role   = aws_iam_role.agente_remoto_task[0].id
  policy = data.aws_iam_policy_document.roteador_modelo.json
}

resource "aws_iam_role_policy" "agente_remoto_pacotes" {
  count  = var.pacotes_dos_agentes ? local.agente_remoto_role : 0
  name   = "pacotes-dos-agentes-leitura"
  role   = aws_iam_role.agente_remoto_task[0].id
  policy = data.aws_iam_policy_document.pacotes_leitura[0].json
}

resource "aws_iam_role_policy" "agente_remoto_exec_ssm" {
  count  = local.agente_remoto_role
  name   = "${local.name}-ecs-exec"
  role   = aws_iam_role.agente_remoto_task[0].id
  policy = data.aws_iam_policy_document.task_exec_ssm.json
}

output "helena_agente_remoto" {
  description = "Nomes internos do runtime remoto da Helena e do callback do receptor (null com a chave desligada)."
  value = var.helena_agente_remoto ? {
    agente_url   = local.agente_remoto_url
    receptor_url = local.receptor_url
  } : null
}
