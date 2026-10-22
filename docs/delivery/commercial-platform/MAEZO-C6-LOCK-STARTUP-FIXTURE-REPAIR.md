# Maezo C6 — reparo do fixture de startup do lock

Status: entrega THIRD de fixture; rechecagem independente e CI final pendentes. Baseline informado por ROOT: `ddb7116223067b32c123afcd24f3cb5a0bdd3c27`.

O [diagnóstico original](reviews/maezo-ci-lock-diagnosis.md) demonstrou uma corrida de prontidão no teste: o filho publica `child.pid` antes de o supervisor concluir `_record_pending`. No [CI original 39851012097, job 120076853860](https://github.com/Omni-Saude/maezo-operadora/actions/runs/39851012097/job/120076853860), a mesma identidade passou de `subprocess_spawning` para `subprocess_running` durante a execução do contender. O resultado histórico 1 FAIL / 17 PASS permanece preservado. Não foi identificado um heartbeat periódico nem estabelecido defeito de ownership/cleanup de produção.

## Alteração delimitada

Somente `tests/unit/dev/test_engine_integration_runner.py::test_lock_contention_never_grants_cleanup_and_owner_releases_on_interrupt` mudou. O fixture ancora PID/token do primeiro owner publicado; após ler o PID do filho, uma barreira monotônica de até 10 segundos exige supervisor vivo, mesmo PID/token, checkpoint `subprocess_running`, `pending_pgids == [child_pid]` e `subprocess_quiescent is False`. Troca de identidade, saída ou deadline falham. Cada predicado usa o JSON decodificado dos mesmos bytes lidos; a publicação que satisfaz a barreira fornece os bytes exatos de `original_owner`.

As oito asserções originais permanecem idênticas por AST, incluindo igualdade integral dos bytes após o contender, rc73, eventos exatos `[lock_busy]`, SIGTERM/rc130 do owner, filho ausente, log, lock removido e sequência integral dos eventos do owner. Foram acrescentadas quatro verificações de prontidão/identidade. Todas as funções externas ao caso e os decorators/parâmetros permanecem idênticos; o arquivo conserva 16 funções e o denominador de 18 casos parametrizados. Nenhum timeout existente foi aumentado.

- Preimage do teste: `dcec1a521698f6d09900d25bff20e7fd55515c203892da6a910a3d1e3aab987d`.
- Teste final: `20c25346a0167b56d98ef61030d2ae83d46bd48b31cd39eb9db875d8d9f8ae1b`.
- Runner de produção, intocado: `5b27e471bdf85443457564634f79cafb7fc28f9294214687e9bd909b44c88281`.

## Evidência e limites

O caso local sem atraso passou antes da alteração; isso não reproduziu a corrida. O caso final, usando o CLI real de `child-probe`/`lock-probe` e subprocessos sintéticos próprios, passou: 1 PASS, zero FAIL/skip, 0.21s. Ruff check e format check: PASS.

Um experimento privado separado atrasou somente a publicação pós-Popen numa cópia controlada do runner e iniciou o contender após essa publicação. A versão original falhou na igualdade integral dos bytes; a versão reparada passou com todas as asserções. O experimento apenas controla scheduling; não altera a fonte de produção nem constitui integração com engine. Todos os filhos próprios foram confirmados ausentes.

**Arquivo completo de 18 casos local: NOT_RUN**, por restrição explícita de ROOT após leitura dos efeitos. Outros casos efetuam discovery amplo, probes de arquivos no checkout e bind efêmero; esses efeitos não pertencem à lane local concedida. CI do HEAD final continua obrigado a executar os 18 casos, sem skips, supressão de casos ou redução de denominador.

Não houve execução de engine, PG, Docker, porta, discovery amplo, operação Git ou qualificação operacional/de produção. O módulo alvo foi carregado explicitamente; o arquivo restrito não foi lido, importado, executado ou incluído em evidência.

## Custódia e próximo gate

Preimage, diagnóstico CI RED, resultados naturais, experimento controlado e prova AST estão sob `repair-preimages.local/c6-ci-lock-startup-fixture-r1/`, diretórios 0700 e arquivos 0600. São 47 arquivos incluindo recibo; originais mantidos, owner ROOT. Manifesto canônico de conteúdo: `f5ec3362543e9173596930126e68735a953b77b010c457129e0cefc9a680c075`; recibo privado: `d40a9cd0b077f62ed1a2263ebac5325b731c47567363b82519f0bc160f6b4149`.

O [manifesto público](MAEZO-C6-LOCK-STARTUP-FIXTURE-REPAIR-MANIFEST.json) congela hashes e limites. Lane liberada, sem comando próprio em execução. Próximo gate: rechecagem do diagnostician original, integração ROOT e CI exato do HEAD final. Esta entrega não é self-approval.
