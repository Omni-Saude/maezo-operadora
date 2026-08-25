output "portal_de_acesso" {
  description = "URL que a pessoa abre para entrar. Vale para os dois permission sets."
  value       = "https://${local.identity_store_id}.awsapps.com/start"
}

output "grupos" {
  description = "Grupos criados VAZIOS. Adicionar pessoa aqui e' o que concede o acesso."
  value = {
    (aws_identitystore_group.agent_engineers.display_name) = aws_identitystore_group.agent_engineers.group_id
    (aws_identitystore_group.leitura.display_name)         = aws_identitystore_group.leitura.group_id
  }
}

output "permission_sets" {
  description = "ARNs dos permission sets, para conferencia no console."
  value = {
    (aws_ssoadmin_permission_set.agent_engineer.name) = aws_ssoadmin_permission_set.agent_engineer.arn
    (aws_ssoadmin_permission_set.leitura.name)        = aws_ssoadmin_permission_set.leitura.arn
  }
}

output "adicionar_pessoa" {
  description = "O comando exato para o dia em que os nomes existirem."
  value       = <<-EOT
    1) criar a pessoa (ou deixar o IdP criar, se um dia houver federacao):
       aws identitystore create-user --identity-store-id ${local.identity_store_id} \
         --user-name <email> --display-name "<Nome>" \
         --name '{"GivenName":"<Nome>","FamilyName":"<Sobrenome>"}' \
         --emails '[{"Value":"<email>","Type":"work","Primary":true}]' --region ${var.aws_region}

    2) por no grupo (isto, e SO' isto, concede o acesso):
       aws identitystore create-group-membership --identity-store-id ${local.identity_store_id} \
         --group-id ${aws_identitystore_group.agent_engineers.group_id} \
         --member-id '{"UserId":"<user-id>"}' --region ${var.aws_region}

    3) a pessoa entra em https://${local.identity_store_id}.awsapps.com/start
  EOT
}
