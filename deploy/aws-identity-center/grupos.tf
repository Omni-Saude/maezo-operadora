# Grupos e atribuicoes.
#
# O DESENHO QUE FAZ ISTO FICAR "PRONTO" SEM OS NOMES:
#
# A atribuicao (`account_assignment`) liga GRUPO -> permission set -> conta. Ela existe e
# funciona com o grupo VAZIO. Quando os nomes chegarem, adicionar alguem ao grupo e' uma
# operacao de identidade — nao um `terraform apply`, nao uma mudanca de politica, nao
# uma revisao de seguranca. Isso e' o oposto de atribuir permission set direto a pessoa,
# que faria cada contratacao virar um commit.
#
# Consequencia pratica: a permissao pode ser revisada e aprovada AGORA, com o time ainda
# indefinido, porque o que se aprova e' o grupo — nao quem esta nele.

resource "aws_identitystore_group" "agent_engineers" {
  identity_store_id = local.identity_store_id

  display_name = "maezo-agent-engineers"
  description  = "Engenharia de agentes maezo. Membro deste grupo recebe MaezoAgentEngineer na conta ${var.data_account_id}."
}

resource "aws_identitystore_group" "leitura" {
  identity_store_id = local.identity_store_id

  display_name = "maezo-leitura"
  description  = "Leitura do ambiente maezo (forma e saude, sem log e sem conteudo). Recebe MaezoOperadoraLeitura na conta ${var.data_account_id}."
}

resource "aws_ssoadmin_account_assignment" "agent_engineers" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.agent_engineer.arn

  principal_id   = aws_identitystore_group.agent_engineers.group_id
  principal_type = "GROUP"

  target_id   = var.data_account_id
  target_type = "AWS_ACCOUNT"
}

resource "aws_ssoadmin_account_assignment" "leitura" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.leitura.arn

  principal_id   = aws_identitystore_group.leitura.group_id
  principal_type = "GROUP"

  target_id   = var.data_account_id
  target_type = "AWS_ACCOUNT"
}
