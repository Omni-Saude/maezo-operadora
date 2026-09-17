# Briefing para o agente do Filipe — alterar e publicar páginas do Canal de Teste

Você vai mexer no `maezo-operadora`, ambiente **dev** da AMH. Leia isto inteiro antes do primeiro
comando: três dos passos abaixo têm um jeito errado que não dá erro, só um resultado
silenciosamente diferente do esperado.

## Antes de tudo: a página ainda não está na `main`

A página, a rota `/receptor/simular` e o runbook estão em **pull requests abertas**, não na `main`.
Se você clonar a `main` não vai encontrar `escalonamento.html`. Comece assim:

```bash
git clone https://github.com/Omni-Saude/maezo-operadora.git
cd maezo-operadora
git checkout feat/canal-rota-simular-receptor     # PR #371 — a página e a rota
```

PRs abertas relevantes: **#371** (rota + página), **#372** (este procedimento no repo), **#370**
(por que a triagem usa Mistral em sa-east-1). Quando o Leonardo fizer o merge, trabalhe a partir
da `main` normalmente.

## Quem você é aqui

- Identidade AWS: `filipe@americashealth.co`, via AWS Identity Center.
- Portal: `https://d-94676c2a0d.awsapps.com/start`
- Acesso: `AdministratorAccess` na conta **203312548462** (dev), região **sa-east-1**. Só nessa
  conta, nenhuma outra da organização, e fora de todos os grupos.
- GitHub: `filipecarmo81` é membro da org `Omni-Saude` com **leitura** em `maezo-operadora`.
  **Você não consegue dar push.** Trabalhe num clone local; quem publica no repo é o Leonardo.

Perfil da CLI (crie em `~/.aws/config` se não existir):

```ini
[sso-session amh]
sso_start_url = https://d-94676c2a0d.awsapps.com/start
sso_region = sa-east-1
sso_registration_scopes = sso:account:access

[profile adm-dev]
sso_session = amh
sso_account_id = 203312548462
sso_role_name = AdministratorAccess
region = sa-east-1
output = json
```

```bash
aws sso login --profile adm-dev
export AWS_PROFILE=adm-dev
aws sts get-caller-identity          # tem de dizer 203312548462
```

## O que é o Canal de Teste

Um serviço ECS que serve páginas HTML e faz proxy de mesma origem para o motor de processos. Fica
atrás do Cloudflare Access em `https://maezo-teste-dev.austa.com.br`. Qualquer e-mail
`@austa.com.br` ou `@americashealth.co` entra com código de uso único.

A página do momento é `escalonamento.html`: ela manda uma mensagem sintética de WhatsApp, a
triagem clínica decide, e a tela mostra o rastro até a tarefa humana.

**O fato que governa todo o resto: as páginas viajam DENTRO da imagem do container.** Trocar um
HTML não é copiar arquivo para um servidor. É construir imagem e reapontar o serviço.

## O ciclo, em cinco passos

### 1. Edite

```
src/maezo/platform/testchannel/paginas/escalonamento.html
```

A página só pode chamar caminhos de mesma origem: `/engine/...` (motor), `/agente/...` e
`/receptor/simular` (dispara um turno da Helena). Nada de host externo, nada de segredo no
JavaScript. Confira a sintaxe antes de gastar um build — extraia o bloco `<script>` e rode
`node --check` nele.

### 2. Empacote e suba a fonte

```bash
git add -A && git commit -m "..."        # OBRIGATÓRIO antes do archive
git archive --format=zip -o /tmp/fonte.zip HEAD
aws s3 cp /tmp/fonte.zip \
  s3://amh-pipeline-artifacts-dev-sa-east-1/maezo-operadora/fonte.zip \
  --sse AES256 --region sa-east-1
```

**Armadilha 1:** `git archive HEAD` empacota o **commit**, não a árvore de trabalho. Se esquecer
de commitar, você constrói uma imagem sem a sua alteração e passa meia hora procurando o que não
mudou.

**Armadilha 2:** sem `--sse AES256` a política da organização (`deny-unencrypted-resources`)
recusa o upload.

### 3. Construa a imagem

```bash
TAG=$(git rev-parse --short=8 HEAD)
aws codebuild start-build --region sa-east-1 \
  --project-name maezo-operadora-dev-imagem \
  --environment-variables-override name=TAG_IMAGEM,value=$TAG,type=PLAINTEXT
```

Dois a três minutos. Acompanhe com `aws codebuild batch-get-builds --ids <id>` até `SUCCEEDED`, e
confirme que a imagem chegou:

```bash
aws ecr describe-images --region sa-east-1 --repository-name amh/maezo-operadora \
  --image-ids imageTag=$TAG --query 'imageDetails[0].imagePushedAt'
```

`TAG_IMAGEM` não tem default de propósito. Use sempre o SHA curto; `latest` tornaria impossível
saber o que está rodando.

### 4. Aponte o canal para a imagem

Em `deploy/aws-ecs/envs/dev-sa-east-1/variables.tf`, troque o default de `canal_teste_image_tag`.
Hoje ele é `048e234e`.

O default **descreve o que roda**. Se ficar para trás, o próximo apply de outra pessoa devolve o
canal à página antiga sem erro nenhum.

### 5. Aplique, só no canal

```bash
cd deploy/aws-ecs/envs/dev-sa-east-1
terraform plan \
  -target=aws_ecs_task_definition.canal_teste \
  -target=aws_ecs_service.canal_teste \
  -var-file=<arquivo da atestação PHI, peça ao Leonardo> \
  -var helena_zona_phi=true \
  -var diagnostics_image_digest=sha256:451d7e6be004815988f3e0ff464c6da16c4013dc42f512a64ee5e3d3b4337080 \
  -out=canal.tfplan
terraform apply canal.tfplan
aws ecs wait services-stable --cluster maezo-operadora-dev --services canal-teste
```

**Armadilha 3, a mais cara.** Os `-target` e os dois `-var` não são zelo excessivo:

- sem os `-target`, o apply mexe em serviços que a sua entrega não pede;
- sem o arquivo da atestação PHI, `rafael` e `marina` caem no provedor simulado;
- sem `helena_zona_phi=true`, a Helena sai do provedor de zona de saúde e **toda a triagem volta a
  morrer em falha técnica** — no mesmo comando, sem aviso.

Confira no `plan` que aparecem só as duas linhas do canal. Se aparecer mais, pare.

**Nunca commite o arquivo da atestação.** Ele vive fora do repo de propósito: é uma afirmação de
quem tem autoridade contratual, não uma linha de código. Apague a cópia local ao terminar.

### 6. Confirme

```bash
aws logs tail /ecs/maezo-operadora-dev/canal-teste --since 5m
```

A linha de boot lista as páginas e diz se `/receptor/simular` está ligada. Depois abra a página no
navegador.

### Reverter

Volte `canal_teste_image_tag` para a tag anterior e repita o passo 5. A imagem antiga continua no
ECR, não precisa reconstruir.

## Como testar a página depois de publicar

Mande *"estou com uma dor muito forte no peito e falta de ar"*. Deve produzir, em segundos:
bandeira vermelha clínica, prioridade **P1**, fila `plantao-clinico`, prazos de 5 e 30 minutos, uma
tarefa esperando e dois relógios armados.

Duas regras da página:

1. **O telefone tem de estar na faixa `55119000000xx`** — dois dígitos no fim, exatamente. É a
   cerca que impede o canal de fabricar mensagem em nome de um número real, e ela é verificada nas
   duas pontas. A faixa como prefixo de um número mais longo é recusada.
2. **Use um número novo a cada rodada.** A chave do processo vem da conversa, que vem do telefone.
   Um número com escalonamento já aberto **não abre outro** — o turno se prende ao que existe. É o
   comportamento correto, mas engana num teste. A página já incrementa o número sozinha; a caixa
   "manter o número" existe para testar de propósito a segunda mensagem.

## O que você NÃO consegue, e o que fazer

| Limite | Caminho |
|---|---|
| Push no repositório | Você tem leitura. Mande o diff ou o arquivo para o Leonardo publicar. |
| Criar ou atualizar segredo | A política da organização isenta uma lista fechada de papéis, e o administrador do SSO não está nela. Peça a quem tem o papel da organização. |
| Arquivo da atestação PHI | Não está no repo. Peça ao Leonardo; sem ele o passo 5 quebra a triagem. |
| Terraform do Identity Center | O backend assume um papel que você não tem. Não é seu trabalho. |
| Cloudflare | Outra conta e outro token. Não é necessário para publicar página. |

## Regras que não se negociam

- **Nunca** rode `terraform apply` sem `-target` neste ambiente.
- **Nunca** ponha segredo, token ou o valor da atestação num arquivo servido, num commit ou num log.
- A conta de dev contém dados FHIR reais no lake e no Aurora. Publicar página não toca nisso, e
  nada do que está aqui pede que você toque.
- Se o `plan` mostrar algo que você não pediu, pare e pergunte. O circuit breaker do ECS faz
  rollback de um deploy ruim, mas não desfaz um recurso destruído.

## Onde está o resto

- Procedimento no repo: `docs/runbooks/publicar-pagina-canal-teste.md` (PR #372)
- Como as páginas funcionam: `src/maezo/platform/testchannel/paginas/README.md`
- A rota que assina o webhook e por que ela é cercada: `testchannel/server.py::_simular_whatsapp`
  e `scripts/ci/check_canal_simular.py`
- A entrega original: PR #371

**Pendência conhecida, não é defeito seu:** o envio da resposta ao beneficiário dá `401` na API da
Meta porque a credencial do WhatsApp em dev é um valor de preenchimento. A decisão clínica, o
escalonamento e os relógios são reais; só a resposta não chega ao WhatsApp.
