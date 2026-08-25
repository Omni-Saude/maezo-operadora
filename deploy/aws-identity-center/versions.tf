# Identidade humana de acesso ao ambiente maezo — permission sets e grupos do AWS
# Identity Center.
#
# POR QUE UMA RAIZ SEPARADA E NAO O ROOT ECS
#
# O Identity Center vive na conta de GESTAO (169446931765); todo o resto do maezo vive
# na conta de DADOS (203312548462). Um unico state com duas contas exigiria dois
# providers e removeria a trava `allowed_account_ids`, que e' exatamente o que impede um
# apply com o profile errado de criar recurso na conta errada. Duas raizes, duas travas.
#
# POR QUE NESTE REPO E NAO NUM REPO DE ORGANIZACAO
#
# Nao existe raiz de identidade em nenhum lugar hoje (medido 18/08/2026: 1 permission
# set, 0 grupos). Enquanto o unico consumidor for o maezo, a permissao mora ao lado do
# que ela libera — quem muda o service do agente e' quem responde pela permissao que
# deixa mudar. No dia em que um segundo produto precisar de permission set, esta raiz se
# muda para um repo de organizacao; states diferentes podem gerir permission sets
# diferentes na MESMA instancia sem conflito, o que faz a migracao ser um `state mv` e
# nao uma reescrita.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # O state mora no bucket da conta de DADOS — e' o unico bucket de state que existe
  # (medido: a conta de gestao nao tem nenhum). O provider desta raiz aponta para a
  # conta de gestao, entao o backend precisa assumir explicitamente o papel na conta de
  # dados para escrever o state. Chave fora de `envs/` de proposito: isto nao e' um
  # ambiente, e' identidade da organizacao.
  backend "s3" {
    bucket       = "amh-tfstate-dev"
    key          = "org/identity-center/maezo/terraform.tfstate"
    region       = "sa-east-1"
    use_lockfile = true
    encrypt      = true

    assume_role = {
      role_arn = "arn:aws:iam::203312548462:role/OrganizationAccountAccessRole"
    }
  }
}
