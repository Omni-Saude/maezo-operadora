# Maezo C6-4 — reparo terceiro do deadline antes do body

2026-10-22. Autor: `amh_c5_disclosure_residual_repair`, distinto do autor Maezo e do revisor ARCH original. **REPARO CONGELADO PARA RECHECAGEM INDEPENDENTE; REAL NOT_RUN; PRODUCTION NO_GO.** ROOT concedeu somente o final de `CommercialGateway.admit`, um teste novo e este handoff. AMH Work permanece sob custódia congelada e não foi editado neste reparo.

Este sucessor responde a **AS-MAEZO-C6-PREBODY-01, P1**, registrado em Sales `reviews/architecture-security-maezo-c6-final.{md,json}`. Preserva integralmente o review REQUEST_CHANGES, o freeze original de 30 paths `MAEZO-C6-4-SOURCE-FREEZE.json` SHA-256 `b820c563cca651dab60f66453e1daf925a8f7b10f73c865530b9f532d9694d9b` e o relatório predecessor de 231 UNIT. O commit atual comunicado por ROOT é `a7785fb37d0fe68bb60c0c0e95ddc743ad2defce`; não houve uso de Git por este autor para verificá-lo.

Rastreabilidade: contrato C6-4 `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea`, 42 comandos/41 recuperações, S03–S10 / NR-05/09/10 / CAP-06–19,24–29 e ADR-0037 XRD-03/04/05/09, conforme a fronteira comercial já estabelecida. Trata-se da aplicação do deadline de admissão existente antes da aquisição de dados; não há novo prazo, TTL, grant, endpoint ou regra de domínio. Os 35 requisitos e 122 subchecks anteriores permanecem.

## Delta mínimo

O escopo podia expirar durante o último `await self.source.admit(scope)` enquanto a publicação independente continuava válida. A rota então consumia e interpretava body antes da recusa posterior de execute. O reparo acrescenta somente `self._scope(scope)` depois de `_publication(...)`, imediatamente antes de `return scope` em `CommercialGateway.admit`.

O diff completo do módulo foi comparado ao preimage: exatamente essa linha foi acrescentada. O check é puro e reusa os guards existentes de escopo/deadline. Nenhuma autoridade/source IO ocorre após ele antes de devolver a admissão à rota. Publicação, timeout, recusa categórica e defaults fechados foram preservados. Nenhum router, app, outro gateway, teste anterior, schema, pin ou configuração foi editado.

## Evidência antes e depois

O teste novo monta o `commercial_router` efetivo numa aplicação ASGI, chama a aplicação com receive/send spies e usa o `CommercialGateway` e o codec C6-4 reais. Admission/source são explicitamente sintéticos; o mirror TEST-only é injetado apenas pelo teste. O relógio avança dentro da última consulta assíncrona de publicação; sua prova continua futura enquanto o escopo atinge ou ultrapassa o deadline.

- Antes do reparo: **2 FAIL / 4 PASS**, 1,28 s. Os dois casos de deadline consumiram um receive quando deveriam consumir zero; os controles já passaram.
- Após o reparo: **6/6 PASS**, 1,27 s. Deadline exato e ultrapassado recusam antes de receive, parse, prepare ou execute. O controle fresco permite um receive e uma execução sintética; expiração inicial, expiração da publicação e RuntimeError/TimeoutError mantêm a recusa sem body e sem expor erro estrangeiro.
- Target relacionado completo: **237/237 UNIT PASS**, zero skips, 33,72 s — 231 casos originais sem alterações mais os seis novos.
- Ruff check/format --check: **PASS**, somente os dois paths próprios. Mypy strict `--follow-imports=silent`: **PASS**, módulo gateway próprio.

Python 3.12.13 da `.venv-commercial`; TMP/basetemp/cache mypy privados; `PYTHONDONTWRITEBYTECODE=1`, `--noconftest`, plugin asyncio explícito, autoload de plugins desativado e cache pytest desabilitado. Os testes foram exclusivamente os oito arquivos explicitamente permitidos. Não houve importação de conftest, descoberta ampla, engine, Local, provider HTTP, cloud, servidor/porta, CI ou build.

Os 30 paths curados conferiram antes da edição. O run relacionado registrou hashes antes/depois: zero drift durante a execução. Todos os 29 paths predecessores fora do gateway continuam nos hashes originais, incluindo os sete arquivos de testes anteriores. O arquivo histórico restrito de provider não foi lido, hashado, importado, copiado ou executado.

```text
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src:. PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 TMPDIR=<PRIVATE_TMP> .venv-commercial/bin/python -m pytest --noconftest -p pytest_asyncio.plugin -p no:cacheprovider --basetemp=<PRIVATE_TMP>/related -q tests/unit/test_commercial_gateway.py tests/unit/test_commercial_http.py tests/unit/test_commercial_api.py tests/unit/test_commercial_c5.py tests/unit/test_commercial_recovery.py tests/unit/test_commercial_disclosure.py tests/unit/test_commercial_c6.py tests/unit/test_commercial_prebody_deadline_repair.py --tb=short
.venv-commercial/bin/ruff check --no-cache src/maezo/gateway/commercial.py tests/unit/test_commercial_prebody_deadline_repair.py
.venv-commercial/bin/ruff format --check --no-cache src/maezo/gateway/commercial.py tests/unit/test_commercial_prebody_deadline_repair.py
.venv-commercial/bin/mypy --strict --follow-imports=silent --cache-dir=<PRIVATE_TMP>/mypy src/maezo/gateway/commercial.py
```

## Freeze e custódia

| Path | SHA-256 | Bytes |
|---|---|---:|
| Gateway preimage | `5ddcbe8ddc29438e0f51d02a9d6e74d67044b44701ee912b701f8c289f487c28` | 22764 |
| `src/maezo/gateway/commercial.py` | `952bd672ec3f411897970cd5804549b231df3e8dabd050e8a86848ae9fa10877` | 22795 |
| `tests/unit/test_commercial_prebody_deadline_repair.py` | `7aa7b11ec4c3ff9dc2dea98354b5be9fb64da0c7b5a0f78f1a91d6d2bc9818eb` | 8649 |

Manifesto dos dois paths finais, ordenado `path + espaço + sha256 + newline`, excluindo este handoff: **`2a36a07295bb24f7f608337a53316db9e6f2fe681797bbfadee4664613075be0`**.

Custódia privada ignorada: `docs/delivery/commercial-platform/repair-preimages.local/maezo-c6-prebody-deadline-repair/`, com diretórios 0700 e arquivos 0600. Preserva preimage, cópias do finding/freeze/relatório 231 originais, logs before/after/related/Ruff/mypy, diff, manifestos e hashes de dependências. O diretório TMP próprio possui as mesmas permissões restritas. Não publicar essa área nem apagar a evidência original.

Lane devolvida a ROOT. Próximo gate: revisor ARCH original verifica os hashes sucessores e a regressão efetiva; ROOT renova o freeze integrado, CI do HEAD exato e gates finais. Este autor não fecha o próprio finding nem declara aceitação de fonte, humana, operacional ou produção.
