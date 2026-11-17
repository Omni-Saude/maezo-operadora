# Governança de proveniência omni → Maezo (E-SF-33)

Épico E-SF-33 do plano do Funil de Vendas (registros CORE-09 e CORE-10). Três peças, cada uma com guarda executável:

| Peça | Artefato | Verificador | Guarda (pytest) |
|---|---|---|---|
| EP-CORE-09a — proveniência | `config/provenance/omni-sales-b3de4b4.blobs.json` (sha256 bruto e normalizado de todo arquivo rastreado em `omni_sales_platform@b3de4b447cdf2e6f5440716b4c79d7f49555f939`; só caminhos e hashes) e `config/provenance/omni-sales-b3de4b4.shared-blobs.json` (blobs que também existem no Maezo, com a primeira aparição de cada lado) | `scripts/docs/omni_provenance.py` | `tests/governance/test_omni_provenance_guard.py` |
| EP-CORE-09b — sala limpa | `docs/governance/cleanroom/` | `scripts/docs/cleanroom_records.py` | `tests/governance/test_cleanroom_records.py` |
| EP-CORE-10a — recibo T2 | `.github/workflows/omni-trust-receipt.yml` (só `workflow_dispatch`) e `docs/governance/omni-trust/` | `scripts/docs/omni_trust_receipt.py` | `tests/governance/test_omni_trust_receipt.py` |

## Estado fechado por construção

- `docs/governance/licensing/` **não existe** (nenhum arquivo). É ali que o resolvedor de `License-Act` procura o registro do
  ato (`<id>.json` com `act_id == <id>`). O ato de titularidade/licença do omni é EP-CORE-09d: ato do dono, forma do
  instrumento RATIFY-LATER-jurídico (CORE-09; CA-03). Agente não redige nem commita licença. Enquanto o diretório estiver
  vazio, todo cabeçalho de proveniência é recusado com `LICENSE_ACT_NOT_FOUND`.
- Registros de sala limpa commitados (`docs/governance/cleanroom/`): `CLEANROOM-OMNI-V-02.json` (binding TS do C6-4:
  `src/maezo/portal/web/codegen/c6-4/generate_c64_binding.py` e `commercial-c6-4.generated.ts`) e `CLEANROOM-OMNI-V-10.json`
  (guardas anti-PII: `tests/governance/test_c64_ts_binding.py`). #3/#8/#12 não têm registro (GAP: a atestação exige o
  implementador do código pré-existente).
- Nenhum recibo de confiança foi produzido: o workflow depende do segredo `OMNI_SALES_READ_TOKEN`, ainda não provisionado.
- Lacuna de CI (declarada): as guardas rodam no shard `restante` do job `unit`, que só roda na trilha plataforma (`scripts/ci/trilha.py`). PR só de lógica/pacote de agente, e o push do seu merge, não as rodam; a cópia é pega no run noturno (`schedule`). Fechar exige job próprio ligado ao `portao` em `ci.yml` (editor único W-MZO-DOC, CD-11 item 8).

## Passivo existente (não é licença)

Nove arquivos rastreados do Maezo são byte-idênticos a blobs que apareceram **primeiro** no omni (classe
`omni_first_pending_adjudication` em `config/provenance/omni-sales-b3de4b4.shared-blobs.json`: os contratos C4, C5,
C6-1..C6-4 em `tests/candidate_artifacts/commercial/` e três documentos de `docs/delivery/commercial-platform/`). A guarda
os **recusa** (`UNLICENSED_OMNI_ORIGIN`); o teste da árvore real fixa exatamente esse conjunto, de modo que qualquer cópia
nova falha e a adjudicação (dono/jurídico; para os contratos, editor canônico AMH segundo CA-01) só pode encolhê-lo.

## Cabeçalho de proveniência (formato)

Nas primeiras 40 linhas do arquivo, em linha própria (prefixo de comentário opcional):

    Provenance: Omni-Saude/omni_sales_platform@<sha completo, 40 hex>:<caminho no omni>
    License-Act: <id do ato em docs/governance/licensing/>

Arquivo com cabeçalho também exige recibo T2 válido do mesmo sha (EP-CORE-10a).

## Limites declarados

Hash não prova sala limpa contra paráfrase (por isso 09b). Blobs triviais (menos de 32 bytes após remover espaços) não
geram recusa por hash. O registro de sala limpa é atestação. A data de primeira aparição usa a data de autoria do commit.
