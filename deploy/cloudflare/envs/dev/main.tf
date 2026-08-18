# Borda do maezo no Cloudflare: DNS + rota do túnel + Access com login por e-mail.
#
# O que este state NÃO faz, de propósito:
#
#   - não cria o túnel. Criar o túnel devolve o TOKEN, e o token é credencial de
#     borda: quem o tem publica hostname na zona da empresa. Se o Terraform criasse o
#     túnel, o token ficaria no state — e o state é um arquivo que muita gente lê.
#     O túnel é criado por API uma vez e o token vai direto para o Secrets Manager.
#   - não gerencia o provedor de identidade. O One-time PIN é embutido na organização
#     Zero Trust; declará-lo aqui daria conflito com o que já existe na conta.
#
# O login é por CÓDIGO NO E-MAIL (One-time PIN): a pessoa digita o e-mail, recebe um
# código de uso único e entra. Sem SSO, sem senha para vazar, sem usuário para
# provisionar — e a lista de quem pode está versionada em `terraform.tfvars`.

# ---------------------------------------------------------------------------
# DNS: o hostname público aponta para o túnel, não para um IP
# ---------------------------------------------------------------------------
# `<tunnel-id>.cfargotunnel.com` só é roteável por dentro da rede do Cloudflare —
# não há endereço público para alcançar diretamente. `proxied = true` é obrigatório:
# sem o proxy, o Cloudflare não teria onde interceptar a requisição e o Access não
# existiria no caminho.
resource "cloudflare_dns_record" "hostname" {
  zone_id = var.zone_id
  name    = var.hostname
  type    = "CNAME"
  content = "${var.tunnel_id}.cfargotunnel.com"
  proxied = true
  ttl     = 1 # 1 = automático, exigido quando proxied

  comment = "maezo-operadora dev — borda por tunel, sem porta aberta (gerenciado por Terraform)"
}

# ---------------------------------------------------------------------------
# Rota do túnel: qual hostname entrega em qual serviço interno
# ---------------------------------------------------------------------------
# Isto substitui o "Public Hostname" que se preenche à mão no painel. Ficar aqui
# significa que o mapeamento hostname -> serviço é revisável em pull request, e não
# um clique que ninguém lembra de ter dado.
resource "cloudflare_zero_trust_tunnel_cloudflared_config" "maezo" {
  account_id = var.account_id
  tunnel_id  = var.tunnel_id

  config = {
    ingress = [
      {
        hostname = var.hostname
        service  = var.destino_interno
      },
      # Regra final obrigatória: tudo que não casar acima recebe 404 em vez de vazar
      # para algum destino padrão.
      {
        service = "http_status:404"
      },
    ]
  }
}

# ---------------------------------------------------------------------------
# Access: a aplicação e quem pode entrar
# ---------------------------------------------------------------------------
resource "cloudflare_zero_trust_access_application" "cockpit" {
  account_id = var.account_id
  name       = var.nome_aplicacao
  domain     = var.hostname
  type       = "self_hosted"

  session_duration = var.sessao_duracao

  # Sem `allowed_idps`: a organização oferece o One-time PIN e a pessoa entra com
  # código no e-mail. Fixar uma lista aqui esconderia o OTP da tela de login.

  # A pessoa vê o e-mail dela na tela após entrar — ajuda a perceber na hora quando
  # alguém está logado com a identidade errada.
  app_launcher_visible = true

  # HttpOnly sim; SameSite = `lax`, NÃO `strict`.
  #
  # Eu pus `strict` "por segurança" e quebrei o login: ERR_TOO_MANY_REDIRECTS, medido
  # no navegador em 18/08/2026. O fluxo do Access termina numa navegação CROSS-SITE
  # (`austa.cloudflareaccess.com` -> `maezo-dev.austa.com.br`), e com `strict` o
  # navegador se recusa a enviar o cookie `CF_Authorization` justamente nessa volta.
  # O Access então vê uma requisição anônima e redireciona para o login de novo — em
  # loop, para sempre.
  #
  # `lax` envia o cookie em navegação de TOPO (o retorno do login) e não em
  # sub-requisição iniciada por terceiro, que é a proteção que interessa aqui. Trocar
  # por `strict` de novo reintroduz o loop; se alguém tentar, é isto que vai acontecer.
  http_only_cookie_attribute = true
  same_site_cookie_attribute = "lax"

  # A política é referenciada AQUI. Na v5 do provider não existe recurso de
  # attachment separado — verificado contra o schema, não suposto: o `validate`
  # recusou `cloudflare_zero_trust_access_application_policy_attachment` e o
  # `terraform providers schema` mostrou `policies` como lista aninhada com `id` e
  # `precedence`.
  #
  # Uma aplicação sem nenhuma política não é "aberta com aviso": o Access nega tudo.
  # Mas nós não dependemos disso — a validação em `dominios_autorizados` já impede
  # aplicar sem nenhuma regra de inclusão.
  policies = [
    {
      id         = cloudflare_zero_trust_access_policy.por_email.id
      precedence = 1
    }
  ]
}

resource "cloudflare_zero_trust_access_policy" "por_email" {
  account_id = var.account_id
  name       = "Dominios corporativos autorizados (dev)"
  decision   = "allow"

  # `include` é OU: basta casar uma regra. Domínios primeiro, e-mails nominais depois.
  # A forma (`email_domain = { domain = ... }`, nesting single) veio do
  # `terraform providers schema`, não de memória — na v4 era lista plana.
  include = concat(
    [for dominio in var.dominios_autorizados : { email_domain = { domain = dominio } }],
    [for email in var.emails_autorizados : { email = { email = email } }],
  )
}
