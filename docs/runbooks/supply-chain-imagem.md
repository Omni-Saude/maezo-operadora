# Cadeia de suprimentos da imagem — SBOM e assinatura (o portão 1.4 do portal)

O item 1.4 de `deploy/aws-ecs/envs/dev-sa-east-1/portal.md` exige **SBOM e assinatura
verificados do digest exato** antes de aceitar uma imagem para o portal. Este runbook diz
**com o que se assina**, **onde os artefatos moram**, **o comando exato de verificação** e
**quando ele é obrigatório**.

Vale para o repositório ECR `amh/maezo-operadora` (conta `203312548462`, `sa-east-1`).

## 1. O que o portão significa, em concreto

Um digest é aceitável quando, e só quando, as duas verificações saem verdes:

| O quê | Como é provado |
|---|---|
| **Assinatura** | `cosign verify --key awskms:///alias/maezo-operadora-dev-image-signing <repo>@sha256:…` |
| **SBOM** | `cosign verify-attestation --type spdxjson --key <a mesma chave> <o MESMO digest>` |

Verde significa: existe no registro uma assinatura produzida pela **chave KMS assimétrica da
própria conta**, e um atestado SPDX ligado ao mesmo digest. **Não** significa que a imagem
não tem CVE (isso é o portão L3, `.github/workflows/security.yml`) nem que alguém leu o que
há dentro dela. Assinatura prova **origem e integridade**, não qualidade.

## 2. Com o que se assina

| | Valor |
|---|---|
| Raiz de confiança | **Chave KMS assimétrica** `ECC_NIST_P256`, `SIGN_VERIFY`, na mesma conta/região |
| Ponto estável | o **alias** `alias/maezo-operadora-dev-image-signing` (nunca o key id) |
| Quem pode **assinar** | só a role `maezo-operadora-dev-github-supply-chain` — a policy da chave tem `Deny` explícito de `kms:Sign` para qualquer outro principal, inclusive administrador |
| Quem pode **verificar** | qualquer principal da conta com `kms:Verify` (as roles de deploy, quem opera o Terraform, o job de PR) |
| Quem publica | `.github/workflows/supply-chain.yml`, job `assinar` |
| Quem confere | o mesmo workflow, job `verificar`, com a role **verify-only** `maezo-operadora-dev-github-verify` (sem `kms:Sign`, sem `ecr:PutImage`) |
| Onde ficam os artefatos | no próprio ECR, como tags `sha256-<digest sem prefixo>.sig` e `.att` |
| Declaração | `deploy/aws-ecs/envs/dev-sa-east-1/kms-image-signing.tf` e `iam-github-supply-chain.tf` |

**Por que chave e não keyless (Sigstore/Fulcio), que é o que o `cd.yml` declara:** porque o
portão é **verificar no deploy**, e a verificação keyless precisa alcançar Fulcio/Rekor **no
minuto da verificação**. O egresso deste ambiente é allowlist de `/32` — o SG do portal tem
14 regras nominais e não existe internet genérica. Com KMS, verificar é uma chamada
`kms:Verify` na própria conta. Além disso o caminho keyless do `cd.yml` **nunca rodou**: está
atrás do gate `AWS_ENABLED`, ausente por desenho; declarado ali é aspiração, não operação.

Consequência assumida: assina-se com `--tlog-upload=false` e verifica-se com
`--insecure-ignore-tlog`. **Não há log de transparência público neste desenho** — o nome da
flag sugere que se está ignorando uma checagem existente, e não é isso: não existe Rekor
nesta cadeia. O preço é real e fica registrado: sem tlog não há prova pública de *quando* a
assinatura foi feita, então "assinatura válida" aqui quer dizer "feita por quem tem a chave",
não "feita antes de tal instante".

**Quem controla a chave controla o portão.** O `Deny` da policy fecha o resto da conta, mas
um administrador pode alterar a policy — por PR neste repositório, que é o registro que se
quer.

## 3. Verificar um digest (o comando exato)

Precisa de `cosign`, de `kms:Verify` e de leitura no ECR. **Não** precisa da role de
assinatura e **não** precisa de rede fora da AWS.

```bash
DIGEST=sha256:d29ce828272cb6b34ab5f2ec7c452fed1663d903f5cb3c958b02e13354d16fb6
REPO=203312548462.dkr.ecr.sa-east-1.amazonaws.com/amh/maezo-operadora

aws ecr get-login-password --region sa-east-1 --profile adm-dev \
  | cosign login 203312548462.dkr.ecr.sa-east-1.amazonaws.com -u AWS --password-stdin

./scripts/ci/verify_image_signature.sh "${REPO}@${DIGEST}"
```

O script é **o mesmo** que o CI roda (`scripts/ci/verify_image_signature.sh`) — de propósito:
portão conferido por um caminho e documentado por outro vira dois portões diferentes, e um
deles apodrece. Ele roda, em sequência:

```bash
cosign verify --key awskms:///alias/maezo-operadora-dev-image-signing \
  --insecure-ignore-tlog=true --registry-referrers-mode=legacy "${REPO}@${DIGEST}"

cosign verify-attestation --type spdxjson \
  --key awskms:///alias/maezo-operadora-dev-image-signing \
  --insecure-ignore-tlog=true --registry-referrers-mode=legacy "${REPO}@${DIGEST}"
```

Saída esperada de cada um: `Verification for … --` seguido de
`The following checks were performed on each of these signatures:`. Qualquer outra coisa —
em especial `no matching signatures` — significa **portão não satisfeito**; não interprete,
não contorne.

**O veredito é o marcador, não o código de saída:** o `cosign` às vezes termina a verificação
e demora minutos para encerrar o processo (bug conhecido dele; medido aqui em uma execução
que ficou 20+ minutos pendurada). O script aplica um timeout e decide pelo bloco de
verificação no stderr; se ele não apareceu, reprova.

Conferência independente, sem cosign, de que os artefatos existem:

```bash
aws ecr describe-images --repository-name amh/maezo-operadora --region sa-east-1 \
  --profile adm-dev --query "imageDetails[].imageTags[]" --output text \
  | tr '\t' '\n' | grep "^sha256-${DIGEST#sha256:}"
# esperado: sha256-<...>.sig  e  sha256-<...>.att
```

## 4. Quando verificar é obrigatório

1. **No PR** — o job `verificar` roda em `pull_request` que toque
   `portal.auto.tfvars` e confere o `image_digest` declarado ali. Um PR que aponta o portal
   para um artefato não assinado **reprova**. É este job que faz a assinatura virar portão em
   vez de enfeite.
2. **Antes de `terraform apply`** que mude o digest de qualquer serviço — rode o script acima
   com o digest novo. Leva segundos.
3. **Ao investigar incidente** — "que imagem é essa, e quem a produziu" se responde pelo par
   assinatura + SBOM, não pela tag.

## 5. Assinar um digest novo

1. A imagem já tem de estar no ECR. **Quem constrói é o CodeBuild**
   (`deploy/aws-ecs/envs/dev-sa-east-1/codebuild.tf`); este workflow não constrói nem
   implanta nada, só acrescenta e confere prova.
2. `gh workflow run supply-chain.yml --ref main -f image=sha256:<64 hex>` (aceita tag também;
   o workflow resolve para o digest antes de assinar — assinar por tag é assinar alvo móvel).
3. Sem `-f image`, o alvo é o `image_digest` do `portal.auto.tfvars`.
4. O job só fica verde depois do `verify` **e** do `verify-attestation`. Assinar sem
   verificar é metade do portão.
5. **Engine (imagem human, Onda 2):** construir com
   `aws codebuild start-build --project-name maezo-operadora-dev-imagem --environment-variables-override
   name=TAG_IMAGEM,value=<tag> name=DOCKERFILE,value=deploy/cibseven/Dockerfile.human
   name=REPOSITORIO,value=amh/cibseven-maezo "name=BUILD_ARGS,value=INSTALL_STAFF_COMPOSITION=true INSTALL_PORTAL_READ=true"`
   (DOCKERFILE, REPOSITORIO e cada token de BUILD_ARGS são conferidos contra allowlist
   fechada no buildspec) e assinar com
   `gh workflow run supply-chain.yml --ref main -f repository=amh/cibseven-maezo -f image=<digest|tag>`
   — no engine `image` é obrigatório. Pré-requisito: `terraform apply` deste PR (IAM das
   roles de supply-chain/verify e regra de retenção `sha256-*` no lifecycle do engine).

## 6. Rotação da chave (ato declarado, não automático)

KMS **assimétrica não tem rotação automática**. Rotacionar é:

1. criar a chave nova (Terraform, mesmo arquivo) e aplicar;
2. **repontar o alias** para ela — o alias é o que workflow, script e runbook citam;
3. **re-assinar os digests ainda em uso** (`gh workflow run … -f image=<digest>` para cada
   um). Assinatura antiga **não migra**: ela continua válida só contra a chave antiga, então
   quem não re-assinar descobre no primeiro `verificar` vermelho;
4. só então agendar a exclusão da chave antiga (`deletion_window_in_days = 30`).

Fazer na ordem inversa deixa artefato em produção sem assinatura verificável.

## 7. Detalhes que já custaram tempo

- **Referrers vs tag.** O cosign 3.x publica por padrão pela API de referrers do OCI 1.1, e
  no ECR isso vira **imagem sem tag** — que a regra 1 do lifecycle (`expira sem tag em 1 dia`)
  apagaria em 24h. Por isso tudo aqui usa `--registry-referrers-mode=legacy`: os artefatos
  são tags `sha256-*.sig`/`.att`, protegidas pela regra de retenção dedicada em `ecs.tf`.
  **Uma assinatura que se apaga sozinha não é portão.**
- **Variável de ambiente `COSIGN_*` vira flag.** O cosign lê `COSIGN_<FLAG>` do ambiente. Um
  `COSIGN_OIDC_ISSUER` definido "só para a verificação" virou `--oidc-issuer` no `sign` e
  derrubou o job com `cannot specify service URLs and use signing config`.
- **Repositório IMMUTABLE.** As tags de assinatura são novas, então a imutabilidade não
  atrapalha — mas **re-assinar o mesmo digest falha**, porque reescreveria a mesma tag
  `.sig`. Re-assinar exige apagar a tag antiga (decisão de dono; a role do CI não tem
  `ecr:DeleteImage`, de propósito).
- **`AWS_ENABLED` continua ausente.** Ligá-lo para obter SBOM habilitaria o caminho de deploy
  inteiro do `cd.yml`. Este workflow não lê nem define essa variável.
- **As roles de OIDC já existentes na conta** (`github-actions-ecr-push`,
  `github-actions-deploy`) confiam em `Omni-Saude/amh-data-platform` e **não** foram
  alargadas: elas têm permissão de deploy, que este trabalho não precisa.

## 8. Histórico

- **2026-09-21** — portão fechado. Chave, roles, workflow, script e este runbook criados após
  review P1 do dono em `Omni-Saude/maezo-operadora#454` (o portal fora ligado com o item 1.4
  não satisfeito; funcionar não satisfaz o portão). Primeira tentativa foi keyless e foi
  revertida por decisão da diretoria: verificação tem de funcionar dentro do egresso
  fechado deste ambiente. Digest assinado e verificado:
  `sha256:d29ce828272cb6b34ab5f2ec7c452fed1663d903f5cb3c958b02e13354d16fb6` (tag `e857e284`),
  que passou a ser também o digest do portal (antes `685ddb6d`, de 20/09).
