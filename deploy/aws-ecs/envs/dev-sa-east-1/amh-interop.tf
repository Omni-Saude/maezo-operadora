# Fonte REAL de cobranca do Lucas: contratos `billing-status` / `subject-resolution` da AMH
# (ADR-0037; decisao do Diretor de Tecnologia de 06/10/2026, docs/decisions-log.md).
#
# DESLIGADO POR PADRAO. `var.lucas_fonte_cobranca` continua `simulada` (default de `variables.tf`;
# `lucas.auto.tfvars` NAO o muda), e com ela NADA deste arquivo existe no plano: nenhum segredo e'
# procurado, nenhuma env nova entra na task definition, nenhuma regra de rede e' criada. O motivo:
# os contratos ainda nao estao publicados nem pinados (XRG-2/XRG-3) — com `amh` o receptor RECUSA
# subir (`service.py::_build_lucas_turno`, fail-closed), entao ligar aqui antes disso so' derruba o
# despacho. Ligar e' um PR que troca `lucas_fonte_cobranca` para `amh` (so' em dev-sa-east-1 — a
# cerca `scripts/ci/check_roteador_lucas.py` reprova a variavel em qualquer outro ambiente) junto
# com a URL do ALB, o client id e os caminhos dos dois OpenAPI publicados.
#
# OS DOIS SEGREDOS sao criados e rotacionados pela AMH (amh-data-platform); aqui sao LIDOS, nunca
# criados — a mesma postura do segredo FHIR (`data.tf`). Os data sources so' existem com `amh`:
# procura-los com a fonte desligada quebraria o `plan` no dia em que um deles ainda nao existir.
#   * `amh/cognito/dev/maezo-operadora-interop` — segredo do app client `client_credentials`;
#   * `amh/interop/phone-lookup-key`            — chave DEDICADA do hash `amh-phone-lookup-v1`
#                                                 (nao e' o PHI_HMAC_KEY do Maezo).
# Ambos sao injetados INTEIROS (o valor do segredo e' a string), como `FHIR_CLIENT_SECRET`.

locals {
  lucas_fonte_amh = var.lucas_fonte_cobranca == "amh"

  # DL-0077: a identidade da Helena reusa o MESMO interop (env, segredos, egress); liga com qualquer um.
  # DL-0083: o acesso do beneficiario tambem usa o mesmo interop (env, segredos, egress).
  amh_interop_ligado = local.lucas_fonte_amh || var.helena_identidade_amh || var.acesso_beneficiario

  # Acesso do beneficiario (08/10/2026, DL-0083): SO' com `acesso_beneficiario` o caminho dos dois OpenAPI e a
  # chave do hash de verificacao entram na task definition. Desligado, a env e' a de antes (listas vazias).
  amh_acesso_env = local.amh_interop_ligado && var.acesso_beneficiario ? [
    { name = "MAEZO_AMH_SUBJECT_VERIFICATION_OPENAPI_PATH", value = var.amh_subject_verification_openapi_path },
    { name = "MAEZO_AMH_CONSENT_RECORD_OPENAPI_PATH", value = var.amh_consent_record_openapi_path },
  ] : []
  amh_acesso_secrets = var.acesso_beneficiario ? [
    { name = "MAEZO_AMH_SUBJECT_VERIFY_KEY", valueFrom = data.aws_secretsmanager_secret.amh_subject_verify_key[0].arn },
  ] : []
  amh_acesso_secret_arns = var.acesso_beneficiario ? [data.aws_secretsmanager_secret.amh_subject_verify_key[0].arn] : []
  amh_acesso_kms_key_ids = var.acesso_beneficiario ? [data.aws_secretsmanager_secret.amh_subject_verify_key[0].kms_key_id] : []

  # Fatos do plano na Helena (07/10/2026): SO' com as consultas ligadas o escopo `interop/tina.read`
  # entra no pedido de token e o caminho do OpenAPI TINA entra na env. Desligadas, a env e' a de antes.
  # Acesso do beneficiario (DL-0083, revisao do #700 P1-a): `interop/subject.verify` e `interop/consent.write`
  # SO' com `acesso_beneficiario` — o Cognito recusa o token INTEIRO se o client nao tiver um escopo pedido, e
  # pedi-los sempre derrubaria identidade, cobranca e TINA. Desligadas as duas flags, o valor e' o de antes.
  amh_interop_scopes_efetivos = join(" ", concat(
    [var.amh_interop_scopes],
    var.helena_consultas_amh ? ["interop/tina.read"] : [],
    var.acesso_beneficiario ? ["interop/subject.verify", "interop/consent.write"] : [],
  ))
  amh_tina_env = local.amh_interop_ligado && var.helena_consultas_amh ? [
    { name = "MAEZO_AMH_TINA_OPENAPI_PATH", value = var.amh_tina_openapi_path },
  ] : []

  # Env do receptor so' com a fonte AMH ligada (lista vazia = task definition intacta).
  amh_interop_env = local.amh_interop_ligado ? [
    { name = "MAEZO_AMH_INTEROP_BASE_URL", value = var.amh_interop_base_url },
    { name = "MAEZO_AMH_INTEROP_TOKEN_URL", value = var.amh_interop_token_url },
    { name = "MAEZO_AMH_INTEROP_CLIENT_ID", value = var.amh_interop_client_id },
    { name = "MAEZO_AMH_INTEROP_SCOPES", value = local.amh_interop_scopes_efetivos },
    { name = "MAEZO_AMH_INTEROP_TENANT", value = var.amh_interop_tenant },
    { name = "MAEZO_AMH_INTEROP_PURPOSE_OF_USE", value = var.amh_interop_purpose_of_use },
    { name = "MAEZO_AMH_BILLING_STATUS_OPENAPI_PATH", value = var.amh_billing_status_openapi_path },
    { name = "MAEZO_AMH_SUBJECT_RESOLUTION_OPENAPI_PATH", value = var.amh_subject_resolution_openapi_path },
  ] : []

  amh_interop_secrets = concat(local.amh_interop_ligado ? [
    { name = "MAEZO_AMH_INTEROP_CLIENT_SECRET", valueFrom = data.aws_secretsmanager_secret.amh_interop_cognito[0].arn },
    { name = "MAEZO_AMH_PHONE_LOOKUP_KEY", valueFrom = data.aws_secretsmanager_secret.amh_phone_lookup_key[0].arn },
  ] : [], local.amh_acesso_secrets)

  amh_interop_secret_arns = concat(local.amh_interop_ligado ? [
    data.aws_secretsmanager_secret.amh_interop_cognito[0].arn,
    data.aws_secretsmanager_secret.amh_phone_lookup_key[0].arn,
  ] : [], local.amh_acesso_secret_arns)

  # Os segredos da AMH podem estar sob a CMK compartilhada do env de dados (como o do Cognito FHIR):
  # sem `kms:Decrypt` o GetSecretValue falha com AccessDenied. Vazio com a chave padrao.
  amh_interop_secret_kms_key_ids = concat(local.amh_interop_ligado ? [
    data.aws_secretsmanager_secret.amh_interop_cognito[0].kms_key_id,
    data.aws_secretsmanager_secret.amh_phone_lookup_key[0].kms_key_id,
  ] : [], local.amh_acesso_kms_key_ids)
}

data "aws_secretsmanager_secret" "amh_interop_cognito" {
  count = local.amh_interop_ligado ? 1 : 0
  name  = var.amh_interop_cognito_secret_name
}

data "aws_secretsmanager_secret" "amh_phone_lookup_key" {
  count = local.amh_interop_ligado ? 1 : 0
  name  = var.amh_phone_lookup_secret_name
}

# Chave DEDICADA do hash `amh-subject-verify-v1` (acesso do beneficiario, DL-0083): segredo criado pela AMH
# (`amh/interop/subject-verify-key`), LIDO aqui so' com `acesso_beneficiario`; injetado inteiro como
# `MAEZO_AMH_SUBJECT_VERIFY_KEY`. Leitura IAM e `kms:Decrypt` entram pelas listas `amh_interop_secret_arns` /
# `amh_interop_secret_kms_key_ids` acima.
data "aws_secretsmanager_secret" "amh_subject_verify_key" {
  count = var.acesso_beneficiario ? 1 : 0
  name  = var.amh_subject_verify_secret_name
}

# Saida 80 do SG das tasks para o ALB INTERNO do servico interop da AMH (`http://` dentro da VPC,
# decisao do dono). Por REFERENCIA ao SG do ALB, nao por CIDR: a regra `hapi_internal`
# (`security_groups.tf`) ja' abre 80 para o CIDR inteiro da VPC, e uma segunda regra com o mesmo
# CIDR/porta falharia no apply como duplicata. Se o ALB da AMH estiver nesta VPC, aquela regra ja'
# cobre o trafego; esta existe para o dia em que a regra larga sair, e para o destino ficar legivel.
# So' e' criada com a fonte AMH ligada E o SG do ALB informado.
resource "aws_vpc_security_group_egress_rule" "amh_interop_alb" {
  count = local.amh_interop_ligado && var.amh_interop_alb_security_group_id != "" ? 1 : 0

  security_group_id            = aws_security_group.tasks.id
  description                  = "Servico interop da AMH (billing-status/subject-resolution) pelo ALB interno"
  ip_protocol                  = "tcp"
  from_port                    = 80
  to_port                      = 80
  referenced_security_group_id = var.amh_interop_alb_security_group_id
}
