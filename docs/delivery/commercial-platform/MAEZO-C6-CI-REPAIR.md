# Maezo C6 — reparo terceiro de CI e transporte HTTP

2026-10-22. Baseline publicado informado por ROOT e conferido: `13846c85900f29fa671ea9d29e9b8432b3c6cf1a`; falha CI `39837960315`. Papel: reparador terceiro, distinto do autor original e dos revisores. **Congelado para rechecagem independente; não constitui self-approval, CI do HEAD sucessor ou aceite operacional.** ROOT mantém Git, CI, integração, revisão final e recursos compartilhados.

Rastreabilidade herdada do pacote: S03–S10 / NR-05/09/10 / CAP-06–19,24–29 e ADR-0037 XRD-03/04/05/09. O contrato candidato C6-4 continua `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea`, com 42 comandos e 41 recuperações. Nenhum pin publicado, regra de domínio, autoridade, factory, endpoint ou capacidade de fonte foi alterado; continuam as 35 exigências e 122 subchecks anteriores.

O scanner identificava dois construtores `httpx.AsyncClient` fora dos seams sancionados. Os clientes sancionados de engine/FHIR são específicos desses domínios, sem factory HTTP genérica reutilizável. Após inspeção, ROOT autorizou o padrão público de transporte já existente em `gateway/amh.py` e `gateway/amh_interop.py`: `AsyncHTTPTransport(retries=0, trust_env=False)`, `Request` e `handle_async_request`. `commercial_http.py` agora usa esse padrão explicitamente, sem esconder/renomear o construtor e sem editar o scanner, suas allowlists ou seus contadores. TLS mantém a verificação padrão do HTTPX; origem HTTPS fixa, credencial/connection atuais, ausência de cookies/redirects/proxy do ambiente/retry e limites de tempo/tamanho permanecem. O timeout externo cobre setup/envio/leitura/fechamento; os timeouts connect/read/write/pool seguem explícitos na request. Response e transporte fecham antes da última consulta de autoridade e da divulgação, inclusive nas recusas e erros de leitura.

ROOT encontrou uma janela adicional durante o `await __aenter__` do transporte. O reparo verifica novamente os deadlines puros de connection, scope, preparation e payment choice imediatamente antes do POST; o GET repete `_connection` no mesmo ponto. Não há novo IO de autoridade entre esse check e o envio. Seis regressões avançam o relógio no transporte efetivo e comprovam zero requests após a expiração. O código também envia `Accept-Encoding: identity` e recusa qualquer representação codificada diferente de identity antes de ler o stream; dois streams gzip válidos comprovam zero leituras/decodificações. As representações JSON válidas sem encoding ou com identity conservam o comportamento anterior. Os guards de admissão antes do body e de última autoridade/custódia não foram removidos ou deslocados.

A fronteira arquitetural agora declara os dois módulos C6 como sucessores explícitos, não como consumer de broker ou liquidação MZO-060. `jsonschema` é permitido adicionalmente somente em `commercial`; `httpx`, somente em `commercial_http`. Todos os módulos antigos conservam a proibição Phase-A de HTTP/broker/cloud/SQL, e CDC/ERP e inversão de dependência continuam proibidos em todos. Quinze controles negativos percorrem módulos existentes e um nome futuro não registrado, impedindo vazamento das permissões. A fronteira ports inclui o módulo comercial puro, mantém stdlib-only e aplica a mesma sonda PHI aos 62 tipos, incluindo os 15 tipos comerciais; o denominador de Protocols permanece 17.

O código real já documentava tanto POST `/api/v1/sales/commercial` quanto GET `/api/v1/sales/public/catalog`; a hipótese inicial de que o POST tinha `include_in_schema=False` estava incorreta. Não foi ocultada nenhuma rota. O exporter normal e openapi-typescript 7.13.0 regeneraram `src/maezo/portal/web/openapi.json` e `src/maezo/portal/web/src/generated/api.ts`, usando Node 24.21.0. A diferença JSON consiste exatamente nessas duas rotas: todos os paths anteriores e componentes são idênticos. Os testes passam a exigir essas duas rotas explícitas e seus métodos, mantendo a exclusão do catch-all e o conjunto anterior de paths. Uma segunda geração em área privada conferiu igualdade byte a byte dos dois artefatos.

Limite documental: essas rotas retornam `Response` genérico; o OpenAPI gerado não tipa o wire comercial fechado. A validação fechada continua no codec e no contrato C6-4 separado, exato e proposto; schema gerado, mirror TEST-only e igualdade de digest não são publicação AMH, qualificação de fonte ou admissão REAL.

Evidência preservada, antes e depois:

| Verificação | Antes | Sucessor final |
|---|---|---|
| Fence sem alterações, §§8.1–8.5 | 2 violações de construtor | PASS, 621 arquivos; todos os contadores de não vacuidade idênticos |
| Quatro suites arquiteturais/OpenAPI existentes | 6 FAIL / 60 PASS | Incluídas no conjunto final |
| Dez regressões iniciais do transporte efetivo | 10 FAIL | PASS |
| Deadline após entrada + gzip, fixtures corrigidas | 8 FAIL / 10 PASS | 18/18 testes novos PASS |
| Conjunto final delimitado: 13 arquivos | 237 comerciais anteriores + 66 anteriores de arquitetura/portal | **336 PASS, zero skips, 38,81 s**; os 33 casos adicionais são 18 de transporte + 15 de isolamento arquitetural |
| Ruff check e format --check | — | PASS, seis paths próprios |
| Mypy strict, follow-imports=silent | — | PASS, módulo HTTP próprio |
| Exporter + gerador TS repetidos | Artefatos divergentes | Igualdade de bytes; somente as duas rotas reais acrescentadas |

Uma primeira tentativa da fixture nova de relógio tinha shadowing de `Clock.now`, gerando NameError em seis casos. Esse log está preservado e não é evidência do defeito de produção; o log `before-entry-encoding-valid-tests.log`, após corrigir somente a fixture e antes de editar o código, contém as oito falhas válidas acima. As falhas originais e os manifests anteriores também foram preservados.

Python 3.12.13 de `.venv-commercial`, `PYTHONDONTWRITEBYTECODE=1`, `PYTHONPATH=src:.`, autoload de plugins desativado, `--noconftest`, plugin asyncio explícito, cache pytest desativado e TMP/basetemp/mypy privados. O run final executou somente:

```text
tests/unit/test_commercial_gateway.py
tests/unit/test_commercial_http.py
tests/unit/test_commercial_api.py
tests/unit/test_commercial_c5.py
tests/unit/test_commercial_recovery.py
tests/unit/test_commercial_disclosure.py
tests/unit/test_commercial_c6.py
tests/unit/test_commercial_prebody_deadline_repair.py
tests/unit/test_commercial_ci_transport_repair.py
tests/unit/adapters/amh/test_adapter_purity.py
tests/unit/ports/test_ports_purity.py
tests/unit/portal/test_openapi_export.py
tests/unit/portal/test_portal_web_bundle.py
```

Não houve descoberta ampla de testes, conftest, engine, integração REAL, cloud, porta/servidor, build, `.next`, E2E, Git mutation ou execução de CI por este reparador. `npm run verify` completo permanece com ROOT, pois inclui build; a paridade OpenAPI foi executada com os mesmos exporter e gerador do script normal, sem modificar scripts. O teste histórico restrito de provider não foi lido, hashado, importado, executado, copiado ou incluído na varredura; a fence percorreu somente source e suas dependências explícitas seguras. A lane Java, seus arquivos e os trackers centrais pertencem a ROOT/outros especialistas e não foram editados aqui.

Freeze: [MAEZO-C6-CI-REPAIR-MANIFEST.json](MAEZO-C6-CI-REPAIR-MANIFEST.json) registra os oito paths finais e doze dependências byte-iguais ao baseline, incluindo codec, ports, gateway/admission, router/app, scanner/exporter, mirror/provenance C6-4 e artefatos PHI. Digest da sequência ordenada `path + espaço + sha256 + newline`: **`f08af170abecf15793fde97f8c001ae65c2fe2bff8b475bfc1d8ae3654854e7d`**. Handoff e manifesto não fazem parte desse digest para evitar autorreferência. A integridade do freeze foi conferida após os checks finais.

Custódia privada ignorada: `docs/delivery/commercial-platform/repair-preimages.local/maezo-c6-ci-third/`, diretórios 0700 e arquivos 0600, contendo preimages, falhas originais, logs antes/depois/finais, gerações independentes e manifests predecessores. Não publicar essa área. Lane devolvida a ROOT; próximo gate: revisor ARCH original reavalia todo o transporte e a extensão arquitetural explícita; ROOT congela o sucessor integrado e executa CI do HEAD final exato. Tasy/PSP/fonte/humano/operação/produção continuam NOT_RUN/NO_GO conforme seus gates próprios.
