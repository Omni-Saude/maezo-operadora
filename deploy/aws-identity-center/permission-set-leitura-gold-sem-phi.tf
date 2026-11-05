# LeituraPlataformaGoldSemPHI — leitura da AWS + Gold agregada sem dado pessoal (05/11/2026, pedido do Leonardo).
#
# ESCOPO DECIDIDO ("infra + Gold sem PHI"):
#   - em TODAS as contas: ver o que existe e como esta configurado (ViewOnlyAccess + SecurityAudit, igual a
#     AvaliacaoArquiteturaLeitura) — sem dado, sem log, sem segredo;
#   - SO em PROD: consultar pelo Athena as Gold AGREGADAS do Actio (`amh_actio_gold.actio_indicadores` e
#     `actio_indicadores_semanal`: indicador x periodo x valor, nenhuma coluna de pessoa).
# FORA, de proposito: `amh_hcentrix_gold.envio` (o JSON tem dado de beneficiario), Bronze, Silver, MPI, HAPI,
# dev (dado de saude real) e qualquer prefixo de resultado do Athena que nao seja o deste set.
#
# COMO A LINHA E' GARANTIDA (o bucket Gold de prod nao e' registrado no Lake Formation, entao o Athena le o S3
# com a credencial de quem consulta):
#   - S3: GetObject/ListBucket SO no prefixo das duas tabelas; a CMK `_shared` tambem cifra outras camadas, mas
#     sem permissao de S3 nelas o Decrypt nao alcanca nada;
#   - Lake Formation: SELECT so' nas duas tabelas (amh-data-platform, envs/prod-sa-east-1, lake_formation_leitura_gold.tf);
#   - Athena: SO o workgroup `amh-leitura-gold-prod`, com resultado em prefixo proprio (o `_shared/` guarda
#     resultado de consulta de outras pessoas, que pode ter PHI).
# Revogar: delete-group-membership do grupo `leitura-plataforma-gold-sem-phi` (README).
resource "aws_ssoadmin_permission_set" "leitura_gold_sem_phi" {
  name        = "LeituraPlataformaGoldSemPHI"
  description = "Leitura da AWS (ViewOnly + SecurityAudit) + Athena so nas Gold agregadas do Actio em prod. Sem PHI."

  instance_arn     = local.instance_arn
  session_duration = var.session_duration

  tags = {
    DataClass = "agregado-sem-pessoa"
    Proposito = "leitura-plataforma-gold-sem-phi"
  }
}

resource "aws_ssoadmin_managed_policy_attachment" "leitura_gold_view_only" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.leitura_gold_sem_phi.arn
  managed_policy_arn = "arn:${local.partition}:iam::aws:policy/job-function/ViewOnlyAccess"
}

resource "aws_ssoadmin_managed_policy_attachment" "leitura_gold_security_audit" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.leitura_gold_sem_phi.arn
  managed_policy_arn = "arn:${local.partition}:iam::aws:policy/SecurityAudit"
}

locals {
  _lg_conta      = "441395443640"
  _lg_regiao     = "sa-east-1"
  _lg_gold       = "amh-lake-gold-prod-sa-east-1"
  _lg_prefixos   = ["iceberg/amh_actio_gold.db/actio_indicadores/", "iceberg/amh_actio_gold.db/actio_indicadores_semanal/"]
  _lg_resultados = "amh-athena-results-prod-sa-east-1"
  _lg_cmk        = "arn:${local.partition}:kms:sa-east-1:441395443640:key/5a9bb66d-4fee-4350-a67c-46529e2bc606" # alias/amh-lake-_shared
}

data "aws_iam_policy_document" "leitura_gold_sem_phi" {
  statement {
    sid       = "AthenaSoNoWorkgroupProprio"
    actions   = ["athena:StartQueryExecution", "athena:StopQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:GetWorkGroup", "athena:ListQueryExecutions"]
    resources = ["arn:${local.partition}:athena:${local._lg_regiao}:${local._lg_conta}:workgroup/amh-leitura-gold-prod"]
  }

  statement {
    sid     = "CatalogoSoDasDuasTabelas"
    actions = ["glue:GetDatabase", "glue:GetTable", "glue:GetTables", "glue:GetPartitions"]
    resources = [
      "arn:${local.partition}:glue:${local._lg_regiao}:${local._lg_conta}:catalog",
      "arn:${local.partition}:glue:${local._lg_regiao}:${local._lg_conta}:database/amh_actio_gold",
      "arn:${local.partition}:glue:${local._lg_regiao}:${local._lg_conta}:table/amh_actio_gold/actio_indicadores",
      "arn:${local.partition}:glue:${local._lg_regiao}:${local._lg_conta}:table/amh_actio_gold/actio_indicadores_semanal",
    ]
  }

  statement {
    sid       = "LakeFormationVendeSoOQueFoiConcedido"
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }

  statement {
    sid       = "ListarSoOsPrefixosDasDuasTabelas"
    actions   = ["s3:ListBucket"]
    resources = ["arn:${local.partition}:s3:::${local._lg_gold}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in local._lg_prefixos : "${p}*"]
    }
  }

  statement {
    sid       = "LerSoAsDuasTabelas"
    actions   = ["s3:GetObject"]
    resources = [for p in local._lg_prefixos : "arn:${local.partition}:s3:::${local._lg_gold}/${p}*"]
  }

  statement {
    sid       = "ResultadoDoAthenaSoNoPrefixoProprio"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    resources = ["arn:${local.partition}:s3:::${local._lg_resultados}/leitura-gold/*"]
  }

  statement {
    sid       = "ListarResultadoSoNoPrefixoProprio"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:${local.partition}:s3:::${local._lg_resultados}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["leitura-gold/*"]
    }
  }

  statement {
    sid       = "CmkSoViaS3"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local._lg_cmk]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${local._lg_regiao}.amazonaws.com"]
    }
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "leitura_gold_sem_phi" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.leitura_gold_sem_phi.arn
  inline_policy      = data.aws_iam_policy_document.leitura_gold_sem_phi.json
}

resource "aws_identitystore_group" "leitura_gold_sem_phi" {
  identity_store_id = local.identity_store_id

  display_name = "leitura-plataforma-gold-sem-phi"
  description  = "Leitura da AWS em todas as contas + Gold agregada sem PHI em prod (LeituraPlataformaGoldSemPHI). Sem dado pessoal."
}

# Prod recebe o set com a Gold; as outras contas, so' a leitura de configuracao (o set da avaliacao, sem dado).
resource "aws_ssoadmin_account_assignment" "leitura_gold_prod" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.leitura_gold_sem_phi.arn
  principal_id       = aws_identitystore_group.leitura_gold_sem_phi.group_id
  principal_type     = "GROUP"
  target_id          = "441395443640"
  target_type        = "AWS_ACCOUNT"
}

resource "aws_ssoadmin_account_assignment" "leitura_gold_demais_contas" {
  for_each = { for k, v in var.contas_avaliacao_arquitetura : k => v if v != "441395443640" }

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.avaliacao_arquitetura.arn
  principal_id       = aws_identitystore_group.leitura_gold_sem_phi.group_id
  principal_type     = "GROUP"
  target_id          = each.value
  target_type        = "AWS_ACCOUNT"
}

output "grupo_leitura_gold_sem_phi" {
  description = "group-id para create-group-membership."
  value       = aws_identitystore_group.leitura_gold_sem_phi.group_id
}
