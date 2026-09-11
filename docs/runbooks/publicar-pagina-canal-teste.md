# Publicar ou alterar uma página do Canal de Teste

Quem tem `AdministratorAccess` na conta de dev (`203312548462`) consegue fazer isto sozinho.
São cinco passos e nenhum deles é adivinhável, por isso este arquivo existe.

**O que você precisa saber antes:** as páginas **viajam dentro da imagem do container**. O canal
roda como módulo Python desde 19/08/2026 e resolve `paginas/` adjacente ao pacote, então trocar
um HTML não é copiar um arquivo para um servidor — é construir uma imagem nova e apontar o
serviço para ela. É mais cerimônia do que a tarefa merece, e está registrado como tal no final.

## Antes de começar

```bash
aws sso login --profile adm-dev      # abre o navegador; a sessão dura algumas horas
export AWS_PROFILE=adm-dev
aws sts get-caller-identity          # tem de dizer 203312548462
```

Se o perfil não existir na sua máquina, ele é declarado em `~/.aws/config` apontando para a
sso-session `amh` (start URL `https://d-94676c2a0d.awsapps.com/start`, conta `203312548462`,
role `AdministratorAccess`).

## 1. Edite o arquivo

```
src/maezo/platform/testchannel/paginas/<sua-pagina>.html
```

Sem passo de configuração: `server.py` lista o diretório em tempo de requisição, então um
arquivo novo aparece sozinho em `/p/<nome>.html`. Extensões servidas: `.html`, `.css`, `.js`.

A página só pode chamar caminhos de **mesma origem** — `/engine/...`, `/agente/...` e
`/receptor/simular`. Ver `paginas/README.md` para o que cada um faz e para a cerca do telefone.

**Confira a sintaxe antes de construir uma imagem de 133 MB:**

```bash
node --check <(python -c "import re,sys;print('\n'.join(re.findall(r'<script[^>]*>(.*?)</script>', open(sys.argv[1],encoding='utf-8').read(), re.S|re.I)))" src/maezo/platform/testchannel/paginas/<sua-pagina>.html)
```

## 2. Empacote a fonte e mande para o S3

```bash
git archive --format=zip -o /tmp/fonte.zip HEAD
aws s3 cp /tmp/fonte.zip s3://amh-pipeline-artifacts-dev-sa-east-1/maezo-operadora/fonte.zip \
  --sse AES256 --region sa-east-1
```

`--sse AES256` não é opcional: sem ele a SCP `deny-unencrypted-resources` recusa o upload.

`git archive HEAD` empacota **o commit**, não a árvore de trabalho. Commite antes, ou sua
alteração não entra na imagem — é o erro mais fácil de cometer aqui.

## 3. Construa a imagem

```bash
aws codebuild start-build --region sa-east-1 \
  --project-name maezo-operadora-dev-imagem \
  --environment-variables-override name=TAG_IMAGEM,value=$(git rev-parse --short=8 HEAD),type=PLAINTEXT
```

`TAG_IMAGEM` não tem default de propósito. Use o SHA curto do commit: a tag passa a dizer o que
está rodando, e `latest` tornaria impossível saber. Leva de dois a três minutos; acompanhe com
`aws codebuild batch-get-builds --ids <id>` até `SUCCEEDED`.

Confirme que a imagem chegou:

```bash
aws ecr describe-images --region sa-east-1 --repository-name amh/maezo-operadora \
  --image-ids imageTag=<sua-tag> --query 'imageDetails[0].imagePushedAt'
```

## 4. Aponte o canal para a imagem nova

Em `deploy/aws-ecs/envs/dev-sa-east-1/variables.tf`, troque o default de
`canal_teste_image_tag` pela sua tag. **O default descreve o que roda** — se ele ficar para trás,
o próximo `apply` de outra pessoa devolve o canal à página antiga sem erro nenhum, e alguém vai
perder uma tarde entendendo por que a correção "sumiu".

## 5. Aplique, só no canal

```bash
cd deploy/aws-ecs/envs/dev-sa-east-1
terraform plan -target=aws_ecs_task_definition.canal_teste -target=aws_ecs_service.canal_teste \
  -var-file=<atestação PHI local> \
  -var helena_zona_phi=true \
  -var diagnostics_image_digest=<digest de uma imagem app qualificada> \
  -out=canal.tfplan
terraform apply canal.tfplan
aws ecs wait services-stable --cluster maezo-operadora-dev --services canal-teste
```

**Os `-target` não são zelo excessivo, são obrigatórios.** Um apply sem eles mexe em serviços que
sua entrega não pede. E os dois `-var` extras também não são opcionais: sem o arquivo da
atestação PHI, `rafael` e `marina` caem em `phi_zone_mock` no mesmo apply; sem
`helena_zona_phi=true`, a Helena volta a `bedrock_br` desligada e toda triagem volta a morrer em
`falha_tecnica`. Confira no `plan` que só as duas linhas do canal aparecem.

O arquivo da atestação vive **fora do repo**, de propósito — peça a quem já o tem.

## 6. Confirme

```bash
aws logs tail /ecs/maezo-operadora-dev/canal-teste --since 5m
```

A linha de boot diz quais páginas existem e se `/receptor/simular` está ligada. Depois abra
`https://maezo-teste-dev.austa.com.br/p/<sua-pagina>.html` — o Cloudflare Access manda um código
de uso único para qualquer e-mail `@austa.com.br` ou `@americashealth.co`.

## Reverter

Volte `canal_teste_image_tag` para a tag anterior e repita o passo 5. A imagem antiga continua no
ECR; não é preciso reconstruir nada.

## Isto devia ser mais simples, e um dia será

Cinco passos e uma imagem de 133 MB para trocar um arquivo de tela é caro. O caminho barato é
servir `paginas/` de um prefixo S3 em tempo de requisição, do mesmo jeito que hoje se serve do
disco: alterar a página viraria um `aws s3 cp`, sem build, sem Terraform e sem ECS, e quem edita
telas precisaria apenas de escrita num prefixo em vez de administrador da conta. Proposto em
11/09/2026 e **não** escolhido — fica registrado para quando o custo deste ciclo incomodar.
