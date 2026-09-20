# Raízes públicas do RDS em sa-east-1

ADR-0049 D4/D8, ADR-0006 e DL-0048: o gateway humano continua usando
`ssl.create_default_context()` com validação de certificado e hostname. O build da
imagem instala somente as três raízes regionais abaixo no trust store Debian;
preserva as CAs de sistema existentes. Não inclui intermediárias, outras regiões,
chaves privadas ou download durante a inicialização da aplicação.

Fonte oficial: [bundle regional da AWS](https://truststore.pki.rds.amazonaws.com/sa-east-1/sa-east-1-bundle.pem),
obtido por HTTPS com validação em **2026-09-09 08:45:58 UTC**, HTTP 200.
O arquivo vendorizado preserva os **4.572 bytes** recebidos.
SHA-256: `c2f9255eadfa939dd6f965ede75d8e0d4168c9cbb7ca1e7baa9bff6d5e2c96e1`.
Revalidado por HTTPS em **2026-09-19** na mesma fonte: HTTP 200, mesmos **4.572 bytes**
e mesmo SHA-256 — o arquivo vendorizado continua byte-idêntico ao bundle público
(antes do gate de imagem, revalidar novamente na data do build).
A [documentação do Aurora](https://docs.aws.amazon.com/AmazonRDS/latest/AuroraUserGuide/UsingWithRDS.SSL.html)
vincula o bundle regional e orienta instalar as raízes, sem intermediárias.
Custódia da descoberta: `docs/audits/maezo-deep-audit/remediation/completion-portal-rds-ca-source-discovery/`.

| CA raiz | SHA-256 DER | Validade UTC |
|---|---|---|
| Amazon RDS sa-east-1 Root CA RSA2048 G1 | `376e82dd0d72b3f4234e1dad34f7bc086d51947f8b343a887e3ae122e0fd212b` | 2021-05-19 18:06:26 a 2061-05-19 19:06:26 |
| Amazon RDS sa-east-1 Root CA ECC384 G1 | `80ca8be42d38ea097fc733d79fc73d595f95f3834e04bb4efed15f1be53e8790` | 2021-05-19 18:16:01 a 2121-05-19 19:16:01 |
| Amazon RDS sa-east-1 Root CA RSA4096 G1 | `4d895d0786fd52b513554e73a07c2c0df47138e834a4338866e4d4bdc85778a3` | 2021-05-19 18:11:20 a 2121-05-19 19:11:20 |

`install_rds_roots.py` recusa bytes alterados antes de escrever qualquer certificado,
confere as três fingerprints e emite um `.crt` por raiz, como exigido por
[`update-ca-certificates`](https://manpages.debian.org/trixie/ca-certificates/update-ca-certificates.8.en.html). Erro na atualização ou raiz ausente no contexto padrão do
Python falha o build. O instalador e o bundle temporários são removidos nessa etapa;
os `.crt` públicos permanecem legíveis pelo usuário 1000. Não há novo parâmetro de
trust/SSL na aplicação. A adição afeta os consumidores do trust store de sistema
da imagem compartilhada; não altera consumidores que escolhem outro CA file.

Rotação exige nova obtenção HTTPS da fonte oficial, conferência independente das
autoassinaturas/CA/validades e diff, atualização revisada dos pins e desta tabela,
testes negativos, build da imagem completa e prova do digest exato antes da promoção.
Não atualizar somente o hash para aceitar um arquivo desconhecido. O bundle é
público e versionado; nenhuma busca de credenciais é necessária para revisá-lo.

Testes locais exercitam instalação em diretório temporário e o OpenSSL real do
Python, com trust store isolado por processo. TLS sintético em memória não comprova
Aurora, SQL, IAM, DNS ou staging. A imagem construída precisa demonstrar as três
raízes no contexto padrão como UID 1000 e recusa de hostname/cadeia inválidos;
depois, o endpoint Aurora real precisa passar TLS/hostname sob a role/DSN dedicada.
