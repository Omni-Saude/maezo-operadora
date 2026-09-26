"""Operacao in-VPC do plano staff (Ondas 4-7, dev), na imagem `deploy/ops/staff-install.Dockerfile`.

Tres pecas, cada uma com `python -m tools.staff_ops <comando>`:

* ``rows``      grava, como `maezo_native_schema_owner`, a designacao instalada (event + current) e a
                admissao Q2 assinada; idempotente e provado por releitura. Artefatos so do Secrets
                Manager (`STAFF_ROWS_SECRET_ARN`), nunca de env/override.
* ``job-init``  init container do job T1.5: materializa o pacote humano e os arquivos do job,
                vindos do segredo injetado pelo ECS, em volumes que o job monta READ-ONLY.
* ``job-run``   roda `membership_publication_job publish` com o ledger duravel em S3 versionado
                (baixa antes, sobe so se a rodada saiu 0) e imprime `T15_RESULT ok=<bool>`.
* ``syn``       runner da fixture `SYN-` (runbook §10): monta a configuracao a partir do segredo
                (`STAFF_SYN_SECRET_ARN`) em arquivos 0400 e chama `tools.dev_syn_fixture`.

Nada aqui imprime segredo, DSN ou chave: so contagens, digests publicos e o tipo da falha.
"""
