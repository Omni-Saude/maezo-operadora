// Valores NAO secretos da fonte REAL de cobranca do Lucas (contratos billing-status /
// subject-resolution da AMH; ver `amh-interop.tf` e `docs/runbooks/ligar-fonte-amh-dev.md`).
//
// Este arquivo NAO liga nada: sem `lucas_fonte_cobranca = "amh"` os locals de `amh-interop.tf`
// ficam vazios e nenhuma env, segredo ou regra de rede entra no plano. Ligar e' UMA linha em
// `lucas.auto.tfvars`, no momento certo (manifest v1.1 publicado e pin XRG-3 mergeado).
//
// Valores lidos (somente leitura) na conta de dados da AMH em 06/10/2026. NUNCA colocar aqui o
// client secret nem a chave do hash do telefone: os dois vivem no Secrets Manager (nomes nos
// defaults de `variables.tf`: amh/cognito/dev/maezo-operadora-interop e amh/interop/phone-lookup-key).

// ALB INTERNO `amh-interop-maezo-dev` (porta 80 dentro da VPC; sem caminho na URL).
amh_interop_base_url = "http://internal-amh-interop-maezo-dev-885055285.sa-east-1.elb.amazonaws.com"

// SG do ALB (`amh-interop-maezo-dev-alb-sg`): regra de saida 80 dedicada a partir do SG das tasks.
// O ingress correspondente (do SG das tasks do Maezo) ja' existe no lado da AMH.
amh_interop_alb_security_group_id = "sg-06aecbfc06289c9cf"

// Cognito: pool sa-east-1_9oKv7gHOJ, dominio `amh-maezo-bpm-dev` (ACTIVE); app client
// `maezo-operadora-interop` (client_credentials; escopos interop/billing.read,
// interop/subject.resolve, interop/profile.read).
amh_interop_token_url = "https://amh-maezo-bpm-dev.auth.sa-east-1.amazoncognito.com/oauth2/token"
amh_interop_client_id = "3n2cmh41eslj5mi2lf0b577teb"

// OpenAPI publicados, DENTRO da imagem (`deploy/Dockerfile` copia `config/` para /app/config).
// Os bytes sao colocados no passo de pin (runbook, secao 2), com digest conferido contra o pin.
amh_billing_status_openapi_path     = "/app/config/integrations/amh/openapi/billing-status.openapi.yaml"
amh_subject_resolution_openapi_path = "/app/config/integrations/amh/openapi/subject-resolution.openapi.yaml"

// Identidade do beneficiario na Helena pelos mesmos contratos (DL-0077, #677). So contexto: nao entra
// no prompt nem decide triagem/escala. Ligada em 07/10/2026; voltar: false.
helena_identidade_amh = true

// Historico curto da conversa da Helena (DL-0080, #689): ultimas 12 mensagens, 6 h, so' como dado no
// classificador e na redacao; liga tambem o modo coleta (triage_sufficiency, ainda nao ratificada).
// So' dev. Base LGPD PENDENTE do DPO; a varredura dos checkpoints antigos ainda nao existe. Voltar: false.
helena_historico = true

// Nova triagem da Helena (DL-0104, 14/10/2026): a tabela `helena_triage_admissibility` decide a saida do
// turno depois dos portoes de seguranca; pergunta clinica sem sintoma deixa de abrir caso. EXPERIMENTO so'
// em dev, decisao do dono; conteudo clinico DRAFT (medico-auditor pendente). Voltar: false (runbook
// docs/runbooks/helena-triagem-v2.md). DESLIGADA em 15/10/2026 a pedido do dono: o teste mostrou respostas
// genericas que nao resolviam a necessidade; o desenho sera refeito (catalogo v2) antes de religar.
helena_triagem_v2 = false

// Helena v4 (DL-0115, 16/10/2026): catalogo de necessidades v4 do dono. Especialista e carteirinha com texto
// fixo; sintoma leve so' com perguntas de alerta antes do caminho. Com ela ligada a nova triagem (v2) fica
// inerte. EXPERIMENTO so' em dev, decisao do dono; conteudo clinico DRAFT (medico-auditor pendente) e prazo de
// 14 dias uteis a confirmar pelo juridico. Voltar: false (runbook docs/runbooks/helena-v4.md).
helena_v4 = true

// Vinculo ativo e carteirinha completa na Helena (decisao do dono de 17/10/2026, "modelo de teste" reversivel,
// so' dev): tipo de plano e carteirinha so' com os vinculos ATIVOS, e o numero COMPLETO da carteirinha do proprio
// beneficiario verificado (rota `tina-carteirinha`, manifest v1.12, escopo `interop/tina.carteirinha`). Exige o
// bloco `manifest_v1_12` no pin (sem ele o receptor recusa servir). Voltar: false.
helena_carteirinha_completa = true
// Cobertura por procedimento na Helena (decisao do dono D1 de 18/10/2026): a Helena diz se o procedimento consta
// no Rol para a segmentacao do plano (rota `tina-cobertura`, manifest v1.9, escopo `interop/tina.read`). Voltar: "".
amh_tina_cobertura_openapi_path = "/app/config/integrations/amh/openapi/tina-cobertura.openapi.yaml"
// Base de conhecimento de saude da Helena (ADR-0064, DL-0118, 17/10/2026): MODELO DE TESTE do dono, so' em dev.
// A Helena INFORMA (sem diagnosticar, sem conduta, sem dose) perguntas gerais de saude pela base curada
// `spec/conhecimento/saude`, sem abrir caso; dor sem alarme recebe perguntas de alerta (com a v4); recusa com
// caso aberto encerra o caso P2/P3 com confirmacao (msg.escalation.withdrawn, SP-OP-ESCALATION-001 v1.1.0 —
// deploy-processes ANTES de ligar). Revisao medica e juridica antes de producao. Voltar: false (runbook
// docs/runbooks/helena-base-saude.md).
helena_base_saude = true

// SAUDE GERATIVA da Helena (ADR-0065, DL-0119, 17/10/2026): o Claude Haiku 5.5 REDIGE a resposta a perguntas
// informativas de saude, com checagem antes de enviar, pela rota PROPRIA
// `experimento_global_autorizado_dpo_2026-10-17` (perfil GLOBAL do Bedrock: PODE SAIR DO BRASIL). O dono
// AUTORIZOU COMO DPO so' para a conversa DELE, a da Tania (adendo de 18/10/2026) e as conversas sinteticas do
// simulador (roteiro `helena_saude_gerativa`, telefones 5511900000790..799), so' em dev, como teste reversivel.
// Todos os outros: a Helena de antes. Desligar ("desligue a saude gerativa"): false + apply direcionado (runbook
// docs/runbooks/helena-base-saude.md §10).
helena_saude_gerativa = true
helena_saude_gerativa_conversas = [
  "wa:amh:hk1_34d934f10c9167de002295ee0412fe6cf4eb0ba9885aef3a5f9ab7029e53e8f6", // o dono
  // A Tania (beneficiaria real que consentiu com os testes): AUTORIZACAO DO DPO (o dono) de 18/10/2026, que
  // confirmou que ela concorda com o perfil global (DL-0119, adendo de 18/10/2026).
  "wa:amh:hk1_0f251a8e67a29606f4f20e7a1e81f27be3b927b8472560e434c066ff83e6219b", // a Tania
  "wa:amh:hk1_41a907864e3b318fe11d8124e2eed5d8a0ad8d076a8cf69449e60e28043290d7", // simulador ...790
  "wa:amh:hk1_c416ceac7076a3946bcacc8c0ca3c5c68083ee7d5c3d3cdbc0335bedf5f0d086", // simulador ...791
  "wa:amh:hk1_dbae14094dd97675d3564309f70861c3f644d3134704cbe25f80e1dbb95956e5", // simulador ...792
  "wa:amh:hk1_0909c2f1979a1326c2210f50c38ac2da42b9f1462c36fe6f5bddb268acedc69a", // simulador ...793
  "wa:amh:hk1_2c6d769d38c68af9df7dad00393e8caed88e3f852b1c68f3aac2105c11ce3514", // simulador ...794
  "wa:amh:hk1_779f711a77180fa0ec38b23f7fe83848344447e1ed131e4c6c2cff2af784dbed", // simulador ...795
  "wa:amh:hk1_e4e695ffef67a87c66dfb14c14a38a2a7db4258ee10a642133e05c8f0fb04d31", // simulador ...796
  "wa:amh:hk1_c9a42214bf91e285a511878d57f169a2af5a67cbd964f194b6b1e7811698f9dd", // simulador ...797
  "wa:amh:hk1_298020f13ddf83ff5cc1e695591075729db2e5614775c824e192cbe1359363a0", // simulador ...798
  "wa:amh:hk1_1af788a6ccaa52b4135ae61ac2e816608024fc6307b04c2caf5303a1f606581f", // simulador ...799
]

# Acesso do beneficiario (DL-0083/DL-0084) LIGADO no dev em 08/10/2026 23:10 UTC, decisao do dono; testado
# ponta a ponta no mesmo dia (aceite, CPF, Helena -> Lucas, revogacao, consentimento gravado no lago).
// BUSCA POR SIGNIFICADO na base de saude (fase 3, decisao do dono de 18/10/2026): embeddings REGIONAIS em sa-east-1
// (sem perfil global) escolhem o apoio da saude gerativa, so' para a allowlist acima. Desligar ("desligue a busca por
// significado"): false + apply direcionado (runbook docs/runbooks/helena-base-saude.md §13).
helena_busca_semantica = true
// Calibrado em dev em 18/10/2026 (13/13 casos de aceite; o Cohere multilingue nao respondeu na conta):
// Titan v2, menor nota certa 0,365, maior nota fora da base 0,205.
helena_busca_semantica_modelo = "amazon.titan-embed-text-v2:0"
helena_busca_semantica_limiar = 0.33

acesso_beneficiario = true

// Fatos do plano na Helena pelo contrato TINA da AMH (DL-0081): elegibilidade, carteirinha, carencia e
// autorizacao do proprio beneficiario. Ligado so' no dev em 13/10/2026, decisao do dono, depois da
// publicacao do manifest v1.2 pela AMH (XRG-2 run 38346972222, AMH #232) e do pin `manifest_v1_2`.
// Base legal LGPD para fora do dev segue PENDENTE de ratificacao do DPO (DL-0081). Voltar: false.
helena_consultas_amh = true

// Lucas v4 (DL-0114; manifests v1.10 e v1.11 PUBLISHED em 16/10/2026): os OpenAPI do detalhamento do pagador e da
// 2a via (digest conferido contra o pin no boot). O executor so' e' composto quando `amh_interop_boleto_client_id`
// (o cliente Cognito DEDICADO da 2a via, criado pela AMH) estiver preenchido: ate' la o segundo sinal fica desligado.
// `maezo-operadora-interop-boleto` (unico escopo interop/billing.boleto), criado em 16/10/2026 (AMH #255); o client id e'
// publico por desenho, o segredo vive em `amh/cognito/dev/maezo-operadora-interop-boleto` (Secrets Manager).
// Voltar: "" (o Lucas v4 segue sem o segundo sinal).
amh_interop_boleto_client_id                 = "558ecnfomm33qbj6cd030rmlbh"
amh_billing_status_detalhamento_openapi_path = "/app/config/integrations/amh/openapi/billing-status-detalhamento.openapi.yaml"
amh_billing_segunda_via_openapi_path         = "/app/config/integrations/amh/openapi/billing-segunda-via.openapi.yaml"
