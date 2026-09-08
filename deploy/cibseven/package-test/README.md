# Prova local descartável do pacote D5

Contrato: ADR-0049 D5/D7, `docs/plan.md:231–244`, DL-0048. Esta prova cobre
carregamento do plugin na imagem CIB Seven 2.1.0 e autenticação TLS direta.
Não implementa D7 (migração de todos os callers/REST), deployment produtivo ou
transação adicional; as provas PostgreSQL de atomicidade permanecem separadas.

Estado observado no SHA `060139c08b485c1754238b12b6b2b84837ccdc63`: o build
real da imagem passou, mas a primeira execução parou no bootstrap PostgreSQL,
antes de Tomcat e dos cinco testes. A comparação pública posterior mostrou que
o daemon Colima não via o bind sob o temporary directory do macOS (`rc=125`),
enquanto leu os mesmos 61 bytes sob `~/.cache` (`rc=0`, digest igual). Esta
receita corrige a interface da fixture; não transforma aquela execução em PASS.

## Execução pelo ROOT, dono exclusivo dos serviços

O ROOT deve adquirir e manter o `EngineLock` canônico durante **toda** a sequência,
registrar inventário antes/depois e garantir portas 15433, 18080 e 18443 livres.
Não executar com outra suíte engine, PostgreSQL/Kafka ou full-unit concorrente.
Não usar a stack existente. O projeto Compose deve ser exclusivo desta execução.
`prepare.py` e `check.py` não adquirem locks nem iniciam/encerram serviços.

1. Criar worktree **nova**, destacada no SHA integral aprovado; verificar
   `git status --porcelain --untracked-files=all` vazio e `git rev-parse HEAD`.
   Instalar `uv sync --frozen --extra dev` nessa checkout. Executar os próximos
   comandos com cwd nessa checkout. Capturar argv, cwd, SHA, UTC início/fim, rc,
   stdout/stderr privados para cada comando; erros originais não são sobrescritos.
2. `docker build --progress=plain -f deploy/cibseven/Dockerfile.human --iidfile IMAGE_ID -t IMAGE .`
   Usar tag exclusiva. `IMAGE_ID` é artefato público sem segredo. Capturar também
   `docker image inspect IMAGE` e os dois manifests de base fixados no Dockerfile.
3. Criar container **parado** com `docker create --name EXTRACTION IMAGE`; copiar
   `docker cp EXTRACTION:/camunda/conf/server.xml ORIGINAL_XML`, depois remover
   somente esse container (`docker rm EXTRACTION`). Não substituir o descriptor
   BPM original: o plugin é registrado pelo Dockerfile na distribuição verdadeira.
4. Criar `PRIVATE_ROOT` como diretório **novo**, absoluto e canônico, externo à
   checkout e a `EVIDENCE`, pertencente ao usuário executor e mode `0700`, sob
   uma raiz que o daemon realmente compartilha (neste host, `~/.cache`). Não usar
   symlink, `..`, `TMPDIR`, diretório da evidência ou diretório da checkout.
   `PRIVATE` deve ser um caminho novo que seja filho direto de `PRIVATE_ROOT`;
   `prepare.py` o cria mode `0700`, recusa overwrite/escape e não inicia serviço.

   Antes de subir PostgreSQL, o ROOT deve executar dois preflights sob o mesmo
   lock e projeto exclusivo: criar em `PRIVATE_ROOT` um marcador **público**
   aleatório mode `0600`, registrar seus bytes/digest esperados e exigir leitura
   byte-exata por um container descartável; depois apontar outro Compose mínimo
   para uma fonte pública inexistente e exigir `rc != 0`, mantendo a fonte
   inexistente. A montagem negativa deve usar a forma longa abaixo:

   ```json
   {
     "type": "bind",
     "source": "/ABS/PRIVATE_ROOT/missing-public-marker",
     "target": "/probe",
     "read_only": true,
     "bind": {"create_host_path": false}
   }
   ```

   Se leitura, tamanho, digest, falha negativa ou ausência final divergirem,
   abortar **antes** de `postgres up`. O marcador não contém credencial e fica
   co-localizado apenas para provar a visibilidade da raiz; não copiar à
   evidência. Todos os oito binds gerados em `compose.json` declaram
   `bind.create_host_path=false`.

5. Gerar a fixture e executar PostgreSQL, Tomcat e a suíte:

   ```sh
   .venv/bin/python deploy/cibseven/package-test/prepare.py --checkout "$PWD" --sha "$SHA" --base-server-xml "$ORIGINAL_XML" --private-root "$PRIVATE_ROOT" --evidence-root "$EVIDENCE" --output "$PRIVATE" --image "$IMAGE"
   docker compose -p "$PROJECT" -f "$PRIVATE/compose.json" up -d --wait postgres
   docker compose -p "$PROJECT" -f "$PRIVATE/compose.json" exec -T postgres psql -U maezo -d maezo -v ON_ERROR_STOP=1 -c "SELECT tenant_, rev_ FROM mzo_human_tenant"
   docker compose -p "$PROJECT" -f "$PRIVATE/compose.json" up -d engine
   .venv/bin/python deploy/cibseven/package-test/check.py --fixture "$PRIVATE" --junit "$EVIDENCE/package-junit.xml"
   ```

6. Esperar **5 PASS**, zero skips/errors: os três testes originais preservados
   (`mTLS + {}` → 400 INVALID_COMMAND/no-store, sem certificado recusado,
   HTTP + forwarded headers → 403) e dois novos testes que exigem alertas da
   camada TLS: certificado ausente e certificado de CA não confiável. Resposta
   HTTP 403 isolada não satisfaz os dois novos oráculos. 400 exige plugin carregado;
   503 indica que trust/schema/bootstrap do engine falhou e é uma falha real.
7. Capturar `docker compose ... logs --no-color engine postgres` em arquivo privado
   para revisão; não publicar logs brutos automaticamente. Capturar
   `docker compose ... exec -T engine java -version`,
   `docker compose ... exec -T engine /camunda/bin/version.sh`,
   `docker compose ... exec -T engine sha256sum /camunda/lib/maezo-human-command.jar`,
   e query PostgreSQL `SELECT version(); SELECT COUNT(*) FROM mzo_human_tenant;`.
   Registrar nomes/versões dos JARs `cibseven-engine-*.jar`, `postgresql-*.jar` no
   runtime. Copiar `public-receipt.json` (único arquivo gerado publicável) à evidência.
8. **Em finally, inclusive falha:** `docker compose -p "$PROJECT" -f "$PRIVATE/compose.json" down -v --remove-orphans`.
   Verificar ausência dos containers/volumes/rede desse projeto; não apagar recursos
   de outro projeto. Apagar apenas a fixture privada gerada após teardown confirmado;
   nunca copiar chaves, certificados, XML com senhas, compose ou env a Git/evidência.
   Confirmar SHA/status da checkout novamente; só então liberar o lock canônico.

A fixture usa certificados EC emitidos por CA efêmera (2h), dois pares Ed25519 novos
para propósitos distintos, dois SPKIs de cliente diferentes, audience/tenant/workload
explícitos, sem fixture sintética ativada e truststore Java PKCS12. O XML deriva do
original pinado, mantém listeners/JNDI e adiciona JSSE TLS1.3 com
`certificateVerification=required`; nenhum RemoteIpValve/forwarded-cert é aceito.
HTTP existe apenas em loopback para o teste negativo. O CIB **descartável** executa
como UID0 para ler mounts individuais mode0600; isso não é configuração produtiva.
Os SQLs públicos são0444 pois PostgreSQL reduz seu UID antes do bootstrap. Todos os
arquivos privados são0600 sob diretório0700. Senhas aleatórias só entram em arquivos
privados. Nenhum arquivo gerado é aceito por symlink ou por caminho fora da raiz
privada validada.

## Proposta de classificação e CI (sem alteração compartilhada neste pacote)

`pytestmark = integration` permanece. A suíte global atual iniciará engine comum e
não terá os sete `MAEZO_HUMAN_PACKAGE_*` valores: integrar este pacote exige wiring
explícito antes de CI/merge. Criar lane serializada `portal-engine-package`, construir
imagem do SHA real, executar receita acima e publicar os cinco resultados/versões/
digests verificados. A coleção global deve atribuir estes cinco nodeids exatamente
uma vez a essa lane, contabilizar o conjunto global sem perda e nunca transformar
configuração ausente em skip. Atualizar descoberta/dependências do runner canônico,
manifestos e CI em pacote coordenado separado; não usar o engine comum como falso
substituto. Nenhuma aprovação/execução de CI é alegada por este diretório.

Fontes verificadas: [plugins CIB2.1](https://docs.cibseven.org/manual/2.1/user-guide/process-engine/process-engine-plugins/),
[distribuição Docker](https://github.com/cibseven/cibseven-docker),
[Tomcat TLS required](https://tomcat.apache.org/tomcat-9.0-doc/config/http.html).
As configurações efetivas do CIB foram lidas das layers do digest pinado, não
inferidas da documentação da versão mais recente.
