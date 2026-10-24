# Portal-mirror — pin do contrato C6-4 congelado (`C6-4-frozen@3c619d91`)

Este diretório guarda o pin espelhado do congelamento BRDG-3.2 (D-15) para consumo pelo **portal externo `omni_sales_platform`** (repositório separado, **fora do write-scope deste orquestrador** — somente leitura; a cópia é executada pelo dono).

## Origem (maezo-operadora)

- Registro: `docs/delivery/commercial-platform/CONTRACT-FREEZE-C6-4-REGISTER.{md,json}` (schema `omni-commercial-contract-freeze.v1`)
- Contrato: `tests/candidate_artifacts/commercial/contract-c6-4.json` — SHA-256 `d1d796b8fd4546bd8d684baa9dd58c4536c3745dc2dce748909f69384eebd1ea` (320.163 bytes)
- Versão citável: **`C6-4-frozen@3c619d91`** (main `3c619d919738878331c21dadda4b112993f430ef`; merge #907 = `7b2dfb86f2680f0287bc37e3b4b31c23ca4bc98b`)

## Formato escolhido e por quê

O portal **não tem** loader runtime de pin (o admission layer `src/server/admission/sales.ts` é um bloqueio constante `blocked/qualification_required` e não lê arquivo algum; não há diretório `pins/` nem lock de contrato no portal). Os únicos formatos de pin que o portal já consome hoje são documentais:

1. `versionedSourceBaseline` — bloco usado por `docs/compatibility/maezo-interface-matrix-p1-successor.json` (matriz de 51 interfaces + 8 lacunas, `PLANNING_ONLY_UNQUALIFIED`), com campos `repository`, `ref`, `commit`, `observedAtUtc`, `observationMode`, `productionQualification`;
2. `omni-sales-p1-source-snapshot.v1` — manifesto de snapshot com `origin_repository`, `origin_main_sha`, `captured_utc`, `method`, `files[]{source_path, snapshot_path, sha256, bytes}` (`docs/audits/P1-SOURCE-SNAPSHOT-2026-10/SNAPSHOT-MANIFEST.json`).

O pin espelhado (`PORTAL-MIRROR-PIN-C6-4.json`, schema `omni-sales-commercial-contract-freeze-mirror.v1`) segue o formato 1 (`versioned_source_baseline` + `contract_pin` + `amh_pin`) e carrega a `lineage`/`prior_freezes` no estilo 2, mais o bloco `admission_relationship`, que repete as regras do portal (`mapping: at_admission`; dossiê `pending` nunca aparece em código/matrizes; registro de admissão só pelo ato externo verificável do owner) e declara **congelamento ≠ admissão** (`runtime_admission: UNBOUND`).

## Destino exato no portal (dono executa)

**Destino:** `/Users/familia/code/omni_sales_platform/docs/delivery/commercial-platform/PORTAL-MIRROR-PIN-C6-4.json`

Justificativa: `docs/delivery/commercial-platform/` já é a casa dos artefatos de custódia comercial do portal (`LANES.local.json`, `PREEXISTING-CUSTODY.local.json`, `reviews/`); o pin fica lado a lado com eles e é citável pela matriz de compatibilidade (BRDG-3.4) e pelos dossiês A1–A10 (BRDG-3.3) sem tocar `src/**` (regra do portal: dossiê `pending` nunca aparece em `src/**`).

```sh
# a partir da raiz do checkout maezo (ou do worktree), dono executa:
cp docs/delivery/commercial-platform/portal-mirror/PORTAL-MIRROR-PIN-C6-4.json \
   /Users/familia/code/omni_sales_platform/docs/delivery/commercial-platform/PORTAL-MIRROR-PIN-C6-4.json

# conferência: os dois shas devem ser iguais
shasum -a 256 docs/delivery/commercial-platform/portal-mirror/PORTAL-MIRROR-PIN-C6-4.json \
             /Users/familia/code/omni_sales_platform/docs/delivery/commercial-platform/PORTAL-MIRROR-PIN-C6-4.json
```

Observações:

- O portal é repo git separado; **o dono** decide se e quando commitar lá (este pacote não comita no portal e não altera `src/**` do portal).
- Conferência pós-cópia sugerida: `git -C /Users/familia/code/omni_sales_platform status --porcelain` deve mostrar apenas o arquivo novo.
- A aceitação W3 "sha do contrato nos 2 repos" só se completa com esta cópia (a metade maezo-side está em `CONTRACT-FREEZE-C6-4-REGISTER.json`).

## O que este pin NÃO faz

- Não admite nada em runtime (`runtime_admission: UNBOUND`; admission layer do portal segue bloqueado).
- Não move linha nenhuma da matriz de 51 interfaces (`PLANNING_ONLY_UNQUALIFIED`, 51/51 NOT_RUN).
- Não ergue A1–A10 (BRDG-3.3/D-16, incremental, teste negativo por admissão).
- Não preenche bloco de decisão humana (ADR-0007).
