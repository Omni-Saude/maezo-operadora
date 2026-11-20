# Leitura da Gold de confirmacao de agendamento pelo time do CEIA (20/11/2026), para o envio do WhatsApp.
# A Gold tem NOME e TELEFONE do paciente: uso aprovado pelo DPO (confirmado pelo Leonardo em 20/11/2026).
# Set e grupo PROPRIOS, como manda o README: dado pessoal nunca entra no `LeituraPlataformaGoldSemPHI`.
#
# So' a conta de DEV (a Gold so' existe la': o Oracle so' e' lido do dev). Nada alem de Athena num workgroup proprio
# e S3 nos prefixos das duas tabelas. Sem ViewOnly/SecurityAudit: quem precisa ler configuracao tem o set da
# avaliacao. Lado do lago (workgroup + Lake Formation): amh-data-platform,
# infrastructure/envs/dev-sa-east-1/leitura_ceia.tf.

resource "aws_ssoadmin_permission_set" "leitura_ceia_agendamento" {
  name        = "LeituraCeiaAgendamentoPHI"
  description = "Athena so na Gold de confirmacao de agendamento (nome e telefone, aval do DPO) no dev, para o CEIA."

  instance_arn     = local.instance_arn
  session_duration = var.session_duration

  tags = {
    DataClass = "pessoal-aval-dpo"
    Proposito = "leitura-ceia-confirmacao-agendamento"
  }
}

locals {
  _lc_conta      = "203312548462"
  _lc_regiao     = "sa-east-1"
  _lc_gold       = "amh-lake-gold-dev-sa-east-1"
  _lc_dbs        = ["amh_austa_hospital_gold", "amh_imc_gold"]
  _lc_tabela     = "confirmacao_agendamento_pendente"
  _lc_prefixos   = [for d in local._lc_dbs : "iceberg/${d}.db/${local._lc_tabela}/"]
  _lc_resultados = "amh-athena-results-dev-sa-east-1"
  _lc_cmk        = "arn:${local.partition}:kms:sa-east-1:203312548462:key/b38f235c-ab63-42a6-8521-729380096cb3" # alias/amh-lake-_shared (dev)
}

data "aws_iam_policy_document" "leitura_ceia_agendamento" {
  statement {
    sid       = "AthenaSoNoWorkgroupProprio"
    actions   = ["athena:StartQueryExecution", "athena:StopQueryExecution", "athena:GetQueryExecution", "athena:GetQueryResults", "athena:GetWorkGroup", "athena:ListQueryExecutions"]
    resources = ["arn:${local.partition}:athena:${local._lc_regiao}:${local._lc_conta}:workgroup/amh-leitura-ceia-dev"]
  }

  statement {
    sid       = "AthenaListarWorkgroups"
    actions   = ["athena:ListWorkGroups", "athena:ListDataCatalogs", "athena:ListDatabases", "athena:ListTableMetadata", "athena:GetTableMetadata", "athena:GetDatabase", "athena:GetDataCatalog"]
    resources = ["*"]
  }

  statement {
    sid     = "CatalogoSoDasDuasTabelas"
    actions = ["glue:GetDatabase", "glue:GetDatabases", "glue:GetTable", "glue:GetTables", "glue:GetPartitions"]
    resources = concat(
      ["arn:${local.partition}:glue:${local._lc_regiao}:${local._lc_conta}:catalog"],
      [for d in local._lc_dbs : "arn:${local.partition}:glue:${local._lc_regiao}:${local._lc_conta}:database/${d}"],
      [for d in local._lc_dbs : "arn:${local.partition}:glue:${local._lc_regiao}:${local._lc_conta}:table/${d}/${local._lc_tabela}"],
    )
  }

  statement {
    sid       = "LakeFormationVendeSoOQueFoiConcedido"
    actions   = ["lakeformation:GetDataAccess"]
    resources = ["*"]
  }

  statement {
    sid       = "ListarSoOsPrefixosDasDuasTabelas"
    actions   = ["s3:ListBucket"]
    resources = ["arn:${local.partition}:s3:::${local._lc_gold}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = [for p in local._lc_prefixos : "${p}*"]
    }
  }

  statement {
    sid       = "LerSoAsDuasTabelas"
    actions   = ["s3:GetObject"]
    resources = [for p in local._lc_prefixos : "arn:${local.partition}:s3:::${local._lc_gold}/${p}*"]
  }

  statement {
    sid       = "ResultadoDoAthenaSoNoPrefixoProprio"
    actions   = ["s3:GetObject", "s3:PutObject", "s3:AbortMultipartUpload", "s3:ListMultipartUploadParts"]
    resources = ["arn:${local.partition}:s3:::${local._lc_resultados}/leitura-ceia/*"]
  }

  statement {
    sid       = "ListarResultadoSoNoPrefixoProprio"
    actions   = ["s3:ListBucket", "s3:GetBucketLocation"]
    resources = ["arn:${local.partition}:s3:::${local._lc_resultados}"]
    condition {
      test     = "StringLike"
      variable = "s3:prefix"
      values   = ["leitura-ceia/*"]
    }
  }

  statement {
    sid       = "CmkSoViaS3"
    actions   = ["kms:Decrypt", "kms:GenerateDataKey"]
    resources = [local._lc_cmk]
    condition {
      test     = "StringEquals"
      variable = "kms:ViaService"
      values   = ["s3.${local._lc_regiao}.amazonaws.com"]
    }
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "leitura_ceia_agendamento" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.leitura_ceia_agendamento.arn
  inline_policy      = data.aws_iam_policy_document.leitura_ceia_agendamento.json
}

resource "aws_identitystore_group" "leitura_ceia_agendamento" {
  identity_store_id = local.identity_store_id

  display_name = "leitura-ceia-confirmacao-agendamento"
  description  = "CEIA: Gold de confirmacao de agendamento no dev (nome e telefone, aval do DPO em 20/11/2026)."
}

resource "aws_ssoadmin_account_assignment" "leitura_ceia_agendamento_dev" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.leitura_ceia_agendamento.arn
  principal_id       = aws_identitystore_group.leitura_ceia_agendamento.group_id
  principal_type     = "GROUP"
  target_id          = local._lc_conta
  target_type        = "AWS_ACCOUNT"
}

output "grupo_leitura_ceia_agendamento" {
  description = "group-id para create-group-membership (CEIA)."
  value       = aws_identitystore_group.leitura_ceia_agendamento.group_id
}
