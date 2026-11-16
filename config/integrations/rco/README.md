# Pin do pacote de regras K1 do rco

O repositório `regras-contratos-operadora` (rco) é o único dono das tabelas DMN de "o quê"
(cobertura, DUT, carência, autorização, limites, pacotes, OPME, glosa) — ADR-0075, seção (b).
O Maezo recebe o pacote K1 byte a byte (contrato `docs/programa/contrato-k1.md` do rco) e guarda o
pin `rules.lock.json`, no formato de `config/integrations/amh/contracts.lock.json`:

- os DMN em `spec/processes/dmn/rco/<arquivo>.dmn`, pasta plana (`decisoes[].caminho_consumidor`);
- o manifesto e os anexos em `config/integrations/rco/k1/` (pasta plana): `manifesto.json`,
  `vocabulario.json`, `entradas.json` e `k6-filas.json`, com os nomes que o manifesto der;
- **os exemplos do pacote (`exemplos/<id>.json`) ficam fora do lock e fora da árvore**: são material de
  teste do rco (contrato §3). Os testes do leitor usam um pacote sintético
  (`tests/unit/platform/rco_k1_sintetico.py`).

O gate é `make verify-rco-rules-pin` (`scripts/ci/verify_rco_rules_pin.py`): sem rede, fail-closed.
Saída 0 = passa; 1 = falha; 2 = uso inválido da linha de comando. O manifesto é lido por
`src/maezo/platform/rco_manifesto.py` (só biblioteca padrão), o MESMO leitor do runtime
(`src/maezo/tools/workers/rco_pin.py`), para que gate e runtime não divirjam de formato.

## Campos

| Campo | Conteúdo |
|---|---|
| `lock_format` | `name` = `rco-rules-lock`, `version` = `2.0.0`, `rules_dir` = `spec/processes/dmn/rco`, ADR que governa. |
| `estado` | `sem_pacote` (o rco ainda não publicou o K1) ou `fixado` (há pacote pinado). |
| `provenance` | `rco_repo`, `rco_commit_sha` (40 hex) e `status` da publicação no rco. Em `fixado`, iguais a `origem` do manifesto. |
| `manifest_pin` | `path` = `config/integrations/rco/k1/manifesto.json` e `sha256` = o conteúdo de `manifesto.sha256` do pacote. |
| `artifacts` | Lista de `{path, sha256}`, uma por `decisoes[]` do manifesto (`caminho_consumidor`, `sha256`). |
| `exemplos_sinteticos` | Lista de `{path, sha256}` de DMNs sintéticos **do Maezo** (sem regra real), aceitos em qualquer estado. Não confundir com os exemplos do pacote. |

**Digest:** todo `sha256` do pin está na notação do pacote, `sha256:<64 hex minúsculos>` (contrato
§1; mesma notação do K2, HEL-3a). O hex cru dispara o detector de segredos (gitleaks) e é recusado
pelo gate e pelo runtime. `lock_format.version` `2.0.0` marca essa notação.

## Estado `sem_pacote` (o estado comitado hoje)

- `provenance.rco_commit_sha` = `null`, `manifest_pin` = `null`, `artifacts` = `[]`.
- `spec/processes/dmn/rco/` pode estar ausente ou conter só `README.md` e os arquivos listados em
  `exemplos_sinteticos`, cada um com o sha256 certo; `config/integrations/rco/k1/` ausente ou vazia.
- Qualquer outro arquivo nas pastas (um `.dmn` "de verdade" solto, por exemplo) reprova.

## Estado `fixado`

O gate e o runtime (`rco_pin.load_rco_rules_pin`) recusam o pin inteiro quando:

- o sha256 do manifesto diverge de `manifest_pin`, ou os bytes não são os canônicos (contrato §3);
- `formato_manifesto` não tem MAJOR 1 ou 2 ou tem MINOR maior que o que o leitor conhece (hoje `1.0.x` e
  `2.0.x`); o leitor é atualizado antes de pinar (contrato §5). No 2.0.0 (PROG-RCO-8, contrato §3 e §7)
  cada regra tem exatamente `idRegra`, `versaoRegra`, `situacao`, `validacao_status`, `classe_adverso`,
  `ativacao` ∈ {`plena`, `conferida`, `em_correcao`}, `exige_aditivo` (booleano) e `sha256_yaml` — sem
  `ratificavel_automatico`;
- (2.0.0) `vocabulario.json` sem o bloco `ativacao`, com `ativacao.valores` diferente do conjunto
  fechado, com regra cuja `ativacao` não está em `ativacao.valores`, ou com `ativacao.parametros` sem
  exatamente `revisao_inicial_decisoes`, `revisao_inicial_prazo_dias`, `amostra_posterior_pct`,
  `limiar_reversao_pct`, `limiar_reversao_min_decisoes` e `conferencia_negativa_admin_decisoes`, cada um
  número finito ≥ 0. Os números ficam expostos em `RcoRulesPin.parametros_ativacao` e vêm sempre do
  pacote, nunca do código; quem os aplica (D1/D2, amostra, `em_correcao`, aditivo) é unidade posterior;
- existe `origem.sujo` (pacote de workspace sujo);
- falta ou sobra campo no topo, em `origem`, nos anexos, em cada decisão, entrada, saída, regra ou
  exemplo (contrato §4 item 7);
- id de decisão sem o prefixo `rco_`, diferente do `<decision>` do arquivo ou repetido; `idRegra`
  fora de `^[A-Za-z0-9._-]{1,64}$`; caminho não plano;
- `vocabulario.json`, `entradas.json` ou `k6-filas.json` ausente ou com sha256 diferente do manifesto;
- a tabela não tem as saídas obrigatórias de `vocabulario.json` (`saidas_obrigatorias`) ou tem
  coluna de saída fora do `saidas[]` daquela decisão (saída extra listada é aceita, contrato §6);
- `artifacts` difere de `decisoes[]`, ou `provenance` difere de `origem`;
- arquivo listado ausente, digest divergente, `.dmn` em `rco/` fora da lista, arquivo em `k1/` que não
  é o manifesto nem anexo dele, subpasta, link simbólico, caminho absoluto ou com `..`, entrada
  duplicada.

Na auditoria (`dmn_versions`, ADR-0075 (f)) a tabela do pacote leva `shaDmn` = `decisoes[].sha256`
e, quando a linha casada emite `idRegra`, `versaoRegra` = `regras[].versaoRegra` dessa regra no
manifesto (idRegra que o manifesto não lista para a decisão é recusado, `ERR_DMN_RCO_PIN`).
`regras[].sha256_yaml` fica exposto em `RcoPinnedTable.regras`; gravá-lo em `dmn_versions` pede
emenda ao ADR-0075 (f), que define só `idRegra`, `versaoRegra` e `shaDmn`.

O `shaDmn` gravado é o do arquivo pinado na árvore local, re-hasheado na carga. Ele não vem do
deploy no motor: `version`/`deploymentId` do motor ainda não são cruzados com o pin (Onda 2).

## Como o pin muda

O pin não se edita à mão para "consertar" digest. Ele muda só quando: (1) o rco publica um K1 novo
— copiar `k1/dmn/*.dmn` byte a byte para `spec/processes/dmn/rco/` e `k1/manifesto.json` mais os
anexos para `config/integrations/rco/k1/` (nunca `exemplos/`), preencher `provenance` (de `origem`),
`manifest_pin` (o `sha256` é o conteúdo de `manifesto.sha256`) e `artifacts` (de `decisoes[]`) e passar
`estado` para `fixado`; ou (2) entra ou sai um exemplo sintético declarado. O sha256 de cada entrada é
`sha256:` seguido do hex de `sha256sum <arquivo>` na árvore.

## `vocabulario-saida.yaml` — vocabulário de saída das tabelas DMN (MZO-1b)

Dado versionado (`schema: maezo.dmn.vocabulario-saida`, `versao`, `estado`) lido por
`src/maezo/platform/validation/vocabulario_saida.py` dentro de `make validate-artifacts`:

- `maezo`: valores adversos que nenhuma tabela do primeiro nível de `spec/processes/dmn/` pode
  emitir (ADR-0018 parte 2, emendado por ADR-0040 D4), cada um com as fontes de onde veio, e as
  `permissoes` explícitas (arquivo, saída, valor, citação) — hoje nenhuma;
- `k1`: vocabulário fechado das tabelas em `spec/processes/dmn/rco/` (ADR-0075 (d)): `decisao`,
  `classeAdverso` (com `vazio` = `""`, o vazio canônico do pacote — contrato §6 —, ou `null`/célula
  vazia) e as regras de `classeAdverso`/`fila`.

A seção `k1` será congelada pela trilha rco (PR da unidade PROG-RCO-1 em
`regras-contratos-operadora`). Mudar o vocabulário é mudar este arquivo, não o código. Fica aqui, e
não em `spec/processes/dmn/`, porque `rco/` só aceita o que o pin lista e porque os `.yaml` do
primeiro nível são lidos pela cerca de perspectiva (ADR-0040 D7), que reprovaria a lista de tokens.
