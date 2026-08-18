# A instancia do Identity Center NAO e' hardcoded: ha exatamente uma por organizacao, e
# ler e' mais honesto que fixar um ARN que ninguem consegue conferir de cabeca.
data "aws_ssoadmin_instances" "this" {}

data "aws_partition" "current" {}
data "aws_caller_identity" "current" {}

locals {
  instance_arn      = tolist(data.aws_ssoadmin_instances.this.arns)[0]
  identity_store_id = tolist(data.aws_ssoadmin_instances.this.identity_store_ids)[0]

  partition = data.aws_partition.current.partition
  conta     = var.data_account_id
}

# Duas instancias de Identity Center nao existem no mesmo AWS Organizations, mas
# `tolist(...)[0]` sobre um set sem ordem garantida merece uma afirmacao explicita: se
# um dia houver mais de uma, isto para o apply em vez de escolher a errada em silencio.
check "instancia_unica" {
  assert {
    condition     = length(data.aws_ssoadmin_instances.this.arns) == 1
    error_message = "Esperava exatamente 1 instancia de Identity Center; achei ${length(data.aws_ssoadmin_instances.this.arns)}."
  }
}
