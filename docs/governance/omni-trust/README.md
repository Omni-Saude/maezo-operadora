# Recibos de confiança do omni, degrau T2 (EP-CORE-10a)

Produtor: `.github/workflows/omni-trust-receipt.yml` (só `workflow_dispatch`; checkout read-only do sha pedido; Node
24.21.0 / npm 11.19.0). O recibo sai como **artefato do run**; entra aqui só por PR humano, com o nome
`OMNI-TRUST-RECEIPT-<sha40>.json`. Hoje **não há recibo**: nenhum run foi feito (o segredo `OMNI_SALES_READ_TOKEN`, leitura
do repo privado omni_sales_platform, não está provisionado).

Consumidor: `scripts/docs/omni_trust_receipt.py`. Exigido só para consumo por cópia (A) ou in-place (C), isto é, para todo
arquivo com cabeçalho de proveniência (EP-CORE-09a). O caminho B (sala limpa) não exige recibo (CORE-10, regra 2).

Forma do recibo:

```json
{
  "schema_version": "1.0.0",
  "omni_repository": "Omni-Saude/omni_sales_platform",
  "omni_sha": "<40 hex>",
  "generated_at": "<ISO 8601 UTC>",
  "runtime": {"node": "v24.21.0", "npm": "11.19.0"},
  "steps": {"npm_ci": {"exit": 0}, "audit": {"exit": 0}, "lint": {"exit": 0}, "typecheck": {"exit": 0},
            "test": {"exit": 0}, "server_only_proof": {"exit": 0}, "build": {"exit": 0}},
  "tests_executed": 0,
  "producer": {"workflow": ".github/workflows/omni-trust-receipt.yml", "maezo_sha": "<sha>", "run_url": "<url>"}
}
```

| Condição | Recusa |
|---|---|
| Arquivo com cabeçalho de proveniência sem recibo do sha | `TrustRefusal.RECEIPT_MISSING` |
| `steps.audit.exit != 0` | `TrustRefusal.AUDIT_FAILED` |
| `runtime` ≠ `v24.21.0` / `11.19.0` | `TrustRefusal.RUNTIME_NOT_PINNED` |
| Recibo de um sha usado para outro | `TrustRefusal.SHA_MISMATCH` |
| Recibo com mais de N dias (N = 7, **PROVISÓRIO**: sugestão do plano, não decidido) | `TrustRefusal.RECEIPT_STALE` |
| Outro passo obrigatório com exit ≠ 0 ou ausente | `TrustRefusal.STEP_FAILED` |
| Recibo fora da forma acima | `TrustRefusal.RECEIPT_MALFORMED` |
