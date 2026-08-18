provider "cloudflare" {
  # Lido de CLOUDFLARE_API_TOKEN. NUNCA em variavel do Terraform: credencial de
  # provider nao entra no state, valor de variavel entra.
  #
  # Permissoes minimas do token:
  #   Account -> Cloudflare Tunnel : Edit   (config de ingress do tunel)
  #   Account -> Access: Apps and Policies : Edit
  #   Zone    -> DNS : Edit   (na zona austa.com.br)
}
