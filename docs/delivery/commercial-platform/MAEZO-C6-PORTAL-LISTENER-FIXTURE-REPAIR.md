# Maezo C6 — sincronização da fixture de revalidação automática

2026-10-22. Reparo terceiro do caso existente `revalidação automática anuncia o estado sem focar o destino`, em `src/maezo/portal/web/src/App.test.tsx`. Baseline ROOT: `ddb7116223067b32c123afcd24f3cb5a0bdd3c27`; CI original `39851012097`, job `120076853709`. **Congelado para rechecagem do diagnostician original; não constitui self-approval ou CI do HEAD sucessor.** O escopo é a fixture de sessão do portal humano, sob o comportamento de sessão/auth já existente (ADR-0049), sem nova regra, NR/CAP, endpoint ou autoridade comercial.

O diagnóstico original continua preservado em `reviews/maezo-portal-ci-diagnosis.{md,json}`. O CI reportou 669 PASS / 1 FAIL de 670 testes, com DOM autenticado e ausência do heading de revalidação após o evento. O código anterior aguardava o botão de logout, mas não estabelecia explicitamente que o listener de visibility havia sido instalado pelo efeito passivo. Isso sustenta a correção da precondição da fixture. **A causa da falha original continua NOT_PROVEN**: o CI não contém trace do registro/callback/fetch no momento do evento. O preimage também passou uma vez localmente com as versões exatas do lockfile; esse PASS não é reprodução nem explicação da falha original.

A única alteração de código está nesse último caso de `App.test.tsx`; todo o conteúdo anterior ao caso permanece byte-igual. A resposta inicial passa a ser deferred e resolvida dentro de `await act`. Um spy local de `document.addEventListener` delega ao método nativo capturado, e somente após a chamada real marca o registro de `visibilitychange`. O teste exige essa precondição antes do evento original, verifica um fetch antes e exatamente dois imediatamente após o evento, e restaura o spy em `finally`. A resposta da segunda consulta permanece pendente durante as verificações de loading.

Foram preservados o evento real, visibility visible, foco inicial no logout, loading sem foco, ausência de “Sessão ativa” durante a pendência, resolução explícita da segunda resposta, destino sem foco e anúncio exato “Sessão confirmada. Área de colaboradores.”. Não houve alteração de App, timeouts, budgets, configuração global, retries, sleeps, skips ou número de casos. O denominador permanece 670.

Um **experimento sintético privado**, separado da fonte final, reteve apenas a delegação do registro de `visibilitychange`. O caso falhou na nova asserção de readiness, com `false` antes de disparar o evento. A mutação foi removida e os bytes finais restaurados/conferidos antes do verify completo. Esse controle demonstra que a barreira não aceita a ausência de registro; não reproduz o agendamento do CI original e não é usado como evidência causal daquela falha.

O checkout não tinha `.venv` nem `portal/web/node_modules`, justificando a cópia privada equivalente autorizada por ROOT. Foram copiados e hashados os 91 arquivos versionados do portal; somente o arquivo de teste difere do baseline, e a cópia privada corresponde aos bytes finais dos 91 arquivos. Três dependências de contexto lidas por testes existentes — BPMN AUTH, DMN escalation e contrato ESCALATION — também foram copiadas byte a byte e registradas. Scripts e `.venv-commercial` foram usados por symlinks de leitura, com `PYTHONPATH` explícito e `PYTHONDONTWRITEBYTECODE=1`. Nenhum symlink de dependência aponta ao cache canônico.

A clonagem APFS inicial das dependências canônicas foi byte-igual, mas revelou Vitest 5.0.0 / Vite 8.2.2, anteriores ao lockfile. O PASS inicial nesse grafo é **UNQUALIFIED**, não é evidência causal e não qualifica a suíte final. `npm ci --ignore-scripts --no-audit --no-fund`, somente na área privada, alinhou 138 pacotes ao lockfile `6494fe65234b32e1cbf0c94055d185db335eb0c474acb079f3b0ed875e3fce39`. Validação final: Node 24.21.0, npm 11.19.0, Vitest 5.0.5 e Vite 8.3.4; caches e dist próprios.

| Verificação | Resultado real |
|---|---|
| Preimage com dependências qualificadas, caso delimitado | 1 PASS, 24 casos não selecionados pelo filtro |
| Sucessor qualificado, mesmo caso delimitado | 1 PASS, mesmos 24 casos não selecionados |
| Mutante privado com registro retido | 1 FAIL esperado na precondição, antes do evento; controle sintético |
| `npm run verify`: check:generated | PASS, paridade dos artefatos público e PHI |
| `npm run verify`: typecheck | PASS |
| `npm run verify`: suíte completa | **670/670 PASS em 31/31 arquivos, zero skips**, 3,97 s |
| `npm run verify`: Vite build | PASS, 48 módulos, 278 ms; aviso de chunk >500 kB preservado |
| Integridade pós-run | 91 arquivos do portal + 3 dependências de contexto correspondem à cópia final; App e diagnósticos originais inalterados |

A primeira tentativa de verify na cópia privada teve 666 PASS / 4 FAIL por ENOENT das três dependências de contexto acima. O log está preservado como `private-layout-missing-context.log`; foram acrescentadas somente as cópias exatas dos arquivos existentes, sem mudar testes, e o verify completo foi executado novamente. Não se trata da falha original nem de reparo em produção.

Comandos de validação: `npm run test -- src/App.test.tsx -t 'revalidação automática anuncia o estado sem focar o destino'` para cada candidato/controle explicitamente identificado, e o script normal `npm run verify` para o sucessor final. Não houve instalação/configuração global, Git mutation, porta, servidor, E2E, `.next` ou build Sales. O build Vite ocorreu somente em `workspace/src/maezo/portal/web/dist` privado. O arquivo histórico restrito de provider não foi lido, hashado, copiado, importado ou executado.

[MAEZO-C6-PORTAL-LISTENER-FIXTURE-REPAIR-MANIFEST.json](MAEZO-C6-PORTAL-LISTENER-FIXTURE-REPAIR-MANIFEST.json) registra hashes, baseline, versões, contexto e resultados. Teste final: **`d4ef473b81699558210df90488eb8c2b6c115a2376bd5081651777f8da9698ca`**. Payload `path + espaço + sha256 + newline`: **`72b138bad2fce040bbc98117144d4e210dcd72a2d255c9e20b4b35388dd1a22c`**. App inalterado: `9ec73770b0fcce826b9637b630ffac0372094063efeb7620962ff77058861b7c`.

Custódia privada ignorada: `docs/delivery/commercial-platform/repair-preimages.local/c6-portal-listener-fixture-r1/`. Diretórios 0700, preimages/logs/manifests 0600; executáveis privados mantêm execução somente pelo owner. Inclui o log CI original exato de 92.540 bytes, SHA `2fd84b791bd8a0a9cc61e4eb062dfbb360db785249dd95abfe4382e5f6b800cc`, preimage, diagnósticos, grafos e freezes de equivalência, mutante e todos os resultados. Owner de custódia após handoff: ROOT; a cópia de execução permanece até a rechecagem original e substituição por evidência do HEAD final, quando ROOT poderá avaliar sua aposentadoria conservadora. Não publicar essa área.

Lane devolvida a ROOT. Próximo gate: diagnostician original reavalia a fixture e seu freeze; ROOT integra e renova CI do HEAD final exato. Qualificação de fonte, humana, operacional e produção permanece separada.
