# Acesso humano ao ambiente maezo

Permission sets e grupos do AWS Identity Center. Aplicado em 18/08/2026.

## O que existe, e por que está pronto sem os nomes

| Recurso | Estado |
|---|---|
| `MaezoAgentEngineer` | permission set, atribuído ao grupo `maezo-agent-engineers` na conta `203312548462` |
| `MaezoOperadoraLeitura` | permission set, atribuído ao grupo `maezo-leitura` na mesma conta |
| `maezo-agent-engineers` | grupo **vazio** |
| `maezo-leitura` | grupo **vazio** |

A atribuição liga **grupo → permission set → conta**, e funciona com o grupo vazio. Quando
os nomes chegarem, dar acesso é adicionar a pessoa ao grupo: não é `terraform apply`, não é
mudança de política, não é nova revisão de segurança. O que foi aprovado é o grupo — não
quem está nele.

O contrário disso — atribuir permission set direto à pessoa — faria cada contratação virar
um commit, e é assim que se acaba com permissão concedida "temporariamente" que ninguém
remove.

## Adicionar uma pessoa

```bash
export AWS_PROFILE=default   # conta de gestao 169446931765

# 1) criar a identidade
aws identitystore create-user --identity-store-id d-94676c2a0d \
  --user-name fulano@austa.com.br --display-name "Fulano de Tal" \
  --name '{"GivenName":"Fulano","FamilyName":"de Tal"}' \
  --emails '[{"Value":"fulano@austa.com.br","Type":"work","Primary":true}]' \
  --region sa-east-1

# 2) por no grupo — ISTO, e so' isto, concede o acesso
aws identitystore create-group-membership --identity-store-id d-94676c2a0d \
  --group-id <group-id do output `grupos`> \
  --member-id '{"UserId":"<user-id do passo 1>"}' --region sa-east-1
```

A pessoa entra em **https://d-94676c2a0d.awsapps.com/start**.

Remover o acesso é `delete-group-membership`. A identidade continua existindo, o acesso não.

## O que cada um pode

`MaezoAgentEngineer` — trocar modelo, criar guardrail, medir custo, reiniciar um agente.
`MaezoOperadoraLeitura` — ver a forma e a saúde do ambiente. **Nenhum log**, porque log de
agente carrega prompt e narrativa, e log de worker carrega variável de processo do fluxo
AUTH.

O detalhe de cada permissão, com o motivo, está nos comentários de
`permission-set-agent-engineer.tf` e `permission-set-leitura.tf` — é lá que a decisão vive,
não aqui.

### A fronteira, verificada e não apenas alegada

31 casos simulados com `aws iam simulate-principal-policy` contra as roles provisionadas,
todos conforme o desenho. Para repetir:

```bash
aws iam simulate-principal-policy \
  --policy-source-arn arn:aws:iam::203312548462:role/aws-reserved/sso.amazonaws.com/sa-east-1/AWSReservedSSO_MaezoAgentEngineer_216e6220a283bc3d \
  --action-names s3:GetObject --resource-arns arn:aws:s3:::amh-lake-gold-dev-sa-east-1/x \
  --context-entries ContextKeyName=aws:RequestedRegion,ContextKeyType=string,ContextKeyValues=sa-east-1 \
                    ContextKeyName=aws:SecureTransport,ContextKeyType=boolean,ContextKeyValues=true \
  --region sa-east-1
```

**As duas chaves de contexto não são detalhe.** Sem elas o simulador devolve `explicitDeny`
para **tudo**, inclusive para o que a política permite: a SCP `deny-region-outside-allowlist`
condiciona em `StringNotEquals aws:RequestedRegion`, e `StringNotEquals` sobre uma chave
ausente é verdadeiro. Uma simulação sem contexto parece uma política perfeita e não prova
nada — as negativas vêm da organização, não do que se escreveu.

Confira sempre `OrganizationsDecisionDetail` e `MatchedStatements` na resposta: eles dizem
QUEM negou.

## Como aplicar

```bash
export AWS_PROFILE=default   # a conta de GESTAO, nao a de dados
terraform init && terraform apply
```

O provider aponta para a conta de gestão (onde o Identity Center vive) e o backend assume o
papel na conta de dados (onde fica o único bucket de state). Por isso o `assume_role` no
bloco `backend` de `versions.tf` — não é enfeite, sem ele o `init` falha.

## O que continua fora daqui

- **MFA do Identity Center.** Não há recurso Terraform nem comando de CLI para configurá-lo
  (conferido em `aws sso-admin` e `aws identitystore`): é tela de console, em
  _Settings → Authentication_. **Antes de convidar a primeira pessoa**, confirme que o modo
  exige MFA — a conta guarda 11,45M de recursos FHIR e o portal é público.
- **Acesso ao Cockpit** (o motor BPMN) não passa por aqui: é identidade do próprio engine.
  O grupo `maezoleitura` já existe lá, vazio, com as autorizações de leitura — ver
  `src/maezo/platform/engine_bootstrap/`. Mesmo desenho, outro sistema de identidade.
- **Cloudflare Access**, que decide quem alcança os hostnames públicos, é outra camada
  ainda: hoje libera por domínio de e-mail (`@austa.com.br`, `@americashealth.co`).

Três portões diferentes, e uma pessoa precisa passar pelos três para operar de fato. Isso é
deliberado, mas significa que "dar acesso" é três passos — não um.
