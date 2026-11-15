# Pin do pacote de regras K1 do rco

O repositório `regras-contratos-operadora` (rco) é o único dono das tabelas DMN de "o quê"
(cobertura, DUT, carência, autorização, limites, pacotes, OPME, glosa) — ADR-0075, seção (b).
O Maezo recebe o pacote K1 byte a byte em `spec/processes/dmn/rco/` e guarda só o pin
`rules.lock.json`, no formato de `config/integrations/amh/contracts.lock.json`.

O gate é `make verify-rco-rules-pin` (`scripts/ci/verify_rco_rules_pin.py`): só biblioteca padrão,
sem rede, fail-closed. Saída 0 = passa; 1 = falha; 2 = uso inválido da linha de comando.

## Campos

| Campo | Conteúdo |
|---|---|
| `lock_format` | `name` = `rco-rules-lock`, `version`, `rules_dir` = `spec/processes/dmn/rco`, ADR que governa. |
| `estado` | `sem_pacote` (o rco ainda não publicou o K1) ou `fixado` (há pacote pinado). |
| `provenance` | `rco_repo`, `rco_commit_sha` (40 hex) e `status` da publicação no rco. |
| `manifest_pin` | `path` (relativo à raiz do repo) e `sha256` do manifesto K1 vendorizado (`versaoRegra`, `shaDmn`). |
| `artifacts` | Lista de `{path, sha256}`, um por `.dmn` do K1, direto em `spec/processes/dmn/rco/`. |
| `exemplos_sinteticos` | Lista de `{path, sha256}` de DMNs sintéticos declarados (sem regra real), aceitos em qualquer estado. |

## Estado `sem_pacote` (o estado comitado hoje)

- `provenance.rco_commit_sha` = `null`, `manifest_pin` = `null`, `artifacts` = `[]`.
- `spec/processes/dmn/rco/` pode estar ausente ou conter só `README.md` e os arquivos listados em
  `exemplos_sinteticos`, cada um com o sha256 certo.
- Qualquer outro arquivo na pasta (um `.dmn` "de verdade" solto, por exemplo) reprova.

## Estado `fixado`

- Exige `rco_repo`, `rco_commit_sha`, `manifest_pin` e `artifacts` não vazio.
- O gate recalcula o sha256 do manifesto e de cada arquivo listado e recusa: arquivo listado
  ausente, digest divergente, `.dmn` em `rco/` fora da lista, subpasta, link simbólico, caminho
  absoluto ou com `..` e entrada duplicada.
- **O runtime ainda recusa `fixado`.** `rco_pin.load_rco_rules_pin`
  (`src/maezo/tools/workers/rco_pin.py`) só lê `sem_pacote`. A leitura de `versaoRegra` do manifesto K1
  chega na Onda 2. Passar o pin para `fixado` antes disso passa no CI, mas faz falhar em produção
  toda avaliação de decisão `rco_` (`ERR_DMN_RCO_PIN`, incidente sem retry). As tabelas Maezo não
  são afetadas.
- O `shaDmn` gravado em `dmn_versions` é o do arquivo pinado na árvore local, re-hasheado na carga.
  Ele não vem do deploy no motor: `version`/`deploymentId` do motor ainda não são cruzados com o pin
  (fica para a Onda 2).

## Como o pin muda

O pin não se edita à mão para "consertar" digest. Ele muda só quando: (1) o rco publica um K1 novo
— copiar os arquivos byte a byte para `spec/processes/dmn/rco/`, preencher `provenance`,
`manifest_pin` e `artifacts` e passar `estado` para `fixado`; ou (2) entra ou sai um exemplo
sintético declarado. O sha256 de cada entrada é o de `sha256sum <arquivo>` na árvore.

## `vocabulario-saida.yaml` — vocabulário de saída das tabelas DMN (MZO-1b)

Dado versionado (`schema: maezo.dmn.vocabulario-saida`, `versao`, `estado`) lido por
`src/maezo/platform/validation/vocabulario_saida.py` dentro de `make validate-artifacts`:

- `maezo`: valores adversos que nenhuma tabela do primeiro nível de `spec/processes/dmn/` pode
  emitir (ADR-0018 parte 2, emendado por ADR-0040 D4), cada um com as fontes de onde veio, e as
  `permissoes` explícitas (arquivo, saída, valor, citação) — hoje nenhuma;
- `k1`: vocabulário fechado das tabelas em `spec/processes/dmn/rco/` (ADR-0075 (d)): `decisao`,
  `classeAdverso` (com `vazio` = `null`/célula vazia) e as regras de `classeAdverso`/`fila`.

A seção `k1` será congelada pela trilha rco (PR da unidade PROG-RCO-1 em
`regras-contratos-operadora`). Mudar o vocabulário é mudar este arquivo, não o código. Fica aqui, e
não em `spec/processes/dmn/`, porque `rco/` só aceita o que o pin lista e porque os `.yaml` do
primeiro nível são lidos pela cerca de perspectiva (ADR-0040 D7), que reprovaria a lista de tokens.
