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
Saída 0 = passa; 1 = falha; 2 = uso inválido da linha de comando. O manifesto e as duas pastas são
lidos por `src/maezo/platform/rco_manifesto.py`, o MESMO leitor do runtime
(`src/maezo/tools/workers/rco_pin.py`), para que gate e runtime não divirjam de formato nem de
varredura. O módulo não importa nada de `maezo` e o gate o carrega pelo caminho do arquivo, então o gate
segue só biblioteca padrão.

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
  `2.0.x` a `2.3.x`: `provas[]` desde 2.1, `composicoes[]` desde 2.2 — DL-0173 —, `mais_restritiva`,
  `ordem_restricao`, `sinalAuditoria` e `avisos` do K6 desde 2.3 — DL-0183); o leitor é atualizado antes de
  pinar (contrato §5). O 2.0.0 segue o contrato estável da
  branch `prog/rco/ativacao-k1` do rco no commit 75015304 (PROG-RCO-8, contrato §3 e §7), **ainda
  não em `main` do rco**. No 2.0.0
  cada regra tem exatamente `idRegra`, `versaoRegra`, `situacao`, `validacao_status`, `classe_adverso`,
  `ativacao` ∈ {`plena`, `conferida`, `em_correcao`}, `exige_aditivo` (booleano) e `sha256_yaml` — sem
  `ratificavel_automatico`;
- (2.0.0) `vocabulario.json` sem o bloco `ativacao`, com `ativacao.valores` diferente do conjunto
  fechado, com regra cuja `ativacao` não está em `ativacao.valores`, ou com `ativacao.parametros` sem
  exatamente `revisao_inicial_decisoes`, `revisao_inicial_prazo_dias`, `amostra_posterior_pct`,
  `limiar_reversao_pct`, `limiar_reversao_min_decisoes` e `conferencia_negativa_admin_decisoes`, cada um
  número finito ≥ 0. Os números ficam expostos em `RcoRulesPin.parametros_ativacao` e vêm sempre do
  pacote, nunca do código; quem os aplica (D1/D2, amostra, `em_correcao`, aditivo) é unidade posterior;
- (2.0.0) alguma regra com `ativacao` fora do conjunto fechado (`plena`, `conferida`, `em_correcao`) ou com
  `exige_aditivo` que não é booleano (`ERR_DMN_RCO_PIN` no runtime, violação `ativacao-nao-suportada` no
  gate; o leitor já recusa os dois). Desde a unidade 5e-0 (DL-0181) as três ativações do contrato, com ou
  sem `exige_aditivo`, passam nesta checagem e ficam em sombra: com `efeito_k1_habilitado` desligado o
  worker só registra (`rco_ativacao.filtrar_saida`). Passar nesta checagem não basta para pinar: valem
  todas as outras recusas desta lista (provas, anexos, dicionário de entradas etc.);
- `formato_manifesto` abaixo de 2.1.0 (1.x e 2.0 não têm `provas[]`) ou `provas[]` vazia: o pin real exige
  as provas do motor executadas com sucesso no CIB Seven (contrato §11 item 12; decisão da ROOT de
  16/11/2026). O leitor ainda lê 1.x/2.0 para diagnóstico; a recusa é no pin (`ERR_DMN_RCO_PIN` no
  runtime, violação `pin-sem-provas` no gate; a lista vazia já é `manifesto-invalido` no leitor). As
  provas rodam em `tests/integration/dmn/test_rco_provas_engine.py`
  (`rco_provas.executar_provas_do_pin`), no shard de integração do PR que muda o pin;
- existe `origem.sujo` (pacote de workspace sujo);
- falta ou sobra campo no topo, em `origem`, nos anexos, em cada decisão, entrada, saída, regra ou
  exemplo (contrato §4 item 7);
- id de decisão sem o prefixo `rco_`, diferente do `<decision>` do arquivo ou repetido; `idRegra`
  fora de `^[A-Za-z0-9._-]{1,64}$`; caminho não plano;
- `vocabulario.json`, `entradas.json` ou `k6-filas.json` ausente ou com sha256 diferente do manifesto;
- a tabela não tem as saídas obrigatórias de `vocabulario.json` (`saidas_obrigatorias`) ou tem
  coluna de saída fora do `saidas[]` daquela decisão (saída extra listada é aceita, contrato §6);
- o conjunto de `idRegra` literais não vazios que a tabela emite difere das chaves de `regras[]` da
  decisão (contrato §3: uma entrada por `idRegra` emitido); `""` em `idRegra` é o vazio canônico e só
  vale em linha não adversa: linha com decisão em `combinacao.adversas` tem de preencher os campos de
  `combinacao.adversa_exige_preenchidos` de `vocabulario.json` (`idRegra`, `fila`, `classeAdverso`,
  `motivoTab38`; contrato §6);
- (2.3.0, DL-0183) `vocabulario.json` `ordem_restricao` que não é permutação exata de `decisao`
  (`ordem-restricao-invalida`); `sinalAuditoria` que não é lista de textos sem repetição com o vazio `""`
  (`sinal-auditoria-invalido`) ou que aparece em `saidas_obrigatorias` (`sinal-auditoria-obrigatorio`);
  `k6-filas.json` sem `avisos` com exatamente um item por sinal não vazio (o bloco só pode faltar quando o
  vocabulário não tem sinal não vazio), cada um com as chaves exatas
  `filas` (filas existentes no K6) e `prazo_parametro` (`avisos-k6-invalido`); célula de `sinalAuditoria`
  fora do vocabulário ou não literal (`sinal-auditoria-fora-do-vocabulario`); linha de decisão de grupo
  `mais_restritiva` com `decisao` fora da ordem (`composicao-decisao-fora-da-ordem`); `mais_restritiva` em
  manifesto 2.2 (`composicao-regra-fora-do-formato`); a saída `sinalAuditoria` antes de 2.3
  (`sinal-auditoria-antes-de-2.3`). O pin expõe `ordem_restricao`, `sinais_auditoria` e `avisos`
  (`RcoRulesPin`); a composição está em `rco_composicao` e a notificação dos avisos é de unidade posterior;
- `artifacts` difere de `decisoes[]`, ou `provenance` difere de `origem`;
- arquivo listado ausente, digest divergente, `.dmn` em `rco/` fora da lista, arquivo em `k1/` que não
  é o manifesto nem anexo dele (inclusive `k1/exemplos/`), subpasta, link simbólico (inclusive a própria
  `k1/`), caminho absoluto ou com `..`, entrada duplicada, chave JSON repetida ou `NaN`/`Infinity` no
  pin ou no manifesto, `.dmn` listado (artefato ou exemplo sintético) com id de decisão sem `rco_` ou
  repetido. A varredura das pastas é `rco_manifesto.verificar_pastas`, chamada pelo gate e pelo
  runtime (que a aplica também em `sem_pacote`).

Na auditoria (`dmn_versions`, ADR-0075 (f)) a tabela do pacote leva `shaDmn` = `decisoes[].sha256`
e, quando a linha casada emite `idRegra` não vazio, `versaoRegra` = `regras[].versaoRegra` dessa regra
no manifesto (idRegra que o manifesto não lista para a decisão é recusado, `ERR_DMN_RCO_PIN`). Linha com
`idRegra` `""` grava só `shaDmn`. Com manifesto MAJOR 2 a mesma linha leva também `ativacaoPacote` =
`regras[].ativacao` e `exigeAditivo` = `regras[].exige_aditivo` como texto `"true"`/`"false"` (ADR-0077 §4);
no MAJOR 1 esses dois campos ficam ausentes. Os parâmetros de `ativacao.parametros` têm faixa checada na
leitura (gate e runtime): `revisao_inicial_decisoes`, `revisao_inicial_prazo_dias`,
`limiar_reversao_min_decisoes` e `conferencia_negativa_admin_decisoes` são inteiros JSON > 0;
`amostra_posterior_pct` e `limiar_reversao_pct` são números em (0, 100] com no máximo 4 casas decimais.

**Item em aberto:** `regras[].sha256_yaml` fica exposto em `RcoPinnedTable.regras`, mas não entra em
`dmn_versions`. Gravá-lo, como o contrato §3 pede ("o consumidor guarda os dois na auditoria"), depende
da emenda de 16/11/2026 ao ADR-0075 (f) (campo `sha256Yaml`), que segue Proposed até o aceite do dono.

O `shaDmn` gravado é o do arquivo pinado na árvore local, re-hasheado na carga. Ele não vem do
deploy no motor: `version`/`deploymentId` do motor ainda não são cruzados com o pin (Onda 2).

## Como o pin muda

O pin não se edita à mão para "consertar" digest. Ele muda só quando: (1) o rco publica um K1 novo
— copiar `k1/dmn/*.dmn` byte a byte para `spec/processes/dmn/rco/` e `k1/manifesto.json` mais os
anexos para `config/integrations/rco/k1/` (nunca `exemplos/`), preencher `provenance` (de `origem`),
`manifest_pin` (o `sha256` é o conteúdo de `manifesto.sha256`) e `artifacts` (de `decisoes[]`) e passar
`estado` para `fixado`; ou (2) entra ou sai um exemplo sintético declarado. O sha256 de cada entrada é
`sha256:` seguido do hex de `sha256sum <arquivo>` na árvore.

## `entradas.json` — dicionário de entradas e o montador (MZO-2b)

`entradas.json` (o anexo `dicionario_entradas` do manifesto) é lido por
`src/maezo/platform/rco_entradas.py`, depois de `rco_manifesto.anexo_pinado` conferir o sha256. O
leitor só aceita as versões do dicionário que o montador conhece (`VERSOES_SUPORTADAS`, hoje `1.1.0`,
`1.2.0` e `1.4.0`; a 1.4.0 traz o campo opcional `formula.regimes`, DL-0183)
e só a política de nulo `nunca_negar`. Pinar um K1 com dicionário de versão nova exige rever antes o
montador (`src/maezo/tools/workers/rco_montador.py`): entrada nova sem mapeamento, origem diferente ou
fórmula publicada diferente da implementada fazem o montador recusar a decisão.
`rco_entradas.carregar_dicionario_entradas` ainda cruza o arquivo com o manifesto: versão e
`sha256_conteudo` iguais a `dicionario_entradas.versao`/`.sha256`, e `origem`/`nome_maezo` de cada
entrada das decisões iguais aos do dicionário.

`carater-tuss23.json` guarda o de-para do caráter do atendimento para a TUSS tabela 23 que o rco
declara (`valores_maezo` de `caraterAtendimento` no dicionário de dados dele), com repo, commit e
linhas da origem; um teste pina o conteúdo. **Pedido ao rco:** publicar `valores_maezo` no K1
(`entradas.json`), para que este arquivo deixe de existir.

## `vocabulario-saida.yaml` — vocabulário de saída das tabelas DMN (MZO-1b)

Dado versionado (`schema: maezo.dmn.vocabulario-saida`, `versao`, `estado`) lido por
`src/maezo/platform/validation/vocabulario_saida.py` dentro de `make validate-artifacts`:

- `maezo`: valores adversos que nenhuma tabela do primeiro nível de `spec/processes/dmn/` pode
  emitir (ADR-0018 parte 2, emendado por ADR-0040 D4), cada um com as fontes de onde veio, e as
  `permissoes` explícitas (arquivo, saída, valor, citação) — hoje nenhuma;
- `k1`: vocabulário fechado das tabelas em `spec/processes/dmn/rco/` (ADR-0075 (d)): `decisao`,
  `classeAdverso` (com `vazio` = `""`, o vazio canônico do pacote — contrato §6 —, ou `null`/célula
  vazia) e as regras de `classeAdverso`/`fila`; e `sinalAuditoria` (K1 2.3.0, DL-0183), o vocabulário
  fechado da saída opcional de mesmo nome (`""` = sem aviso): tabela com a coluna reprova se o valor estiver
  fora dele ou se o arquivo de dados não o declarar. Com pacote pinado que declara `sinalAuditoria`
  (`k1/vocabulario.json`), os dois vocabulários do sinal têm de ser o mesmo conjunto.

A seção `k1` será congelada pela trilha rco (PR da unidade PROG-RCO-1 em
`regras-contratos-operadora`). Mudar o vocabulário é mudar este arquivo, não o código. Fica aqui, e
não em `spec/processes/dmn/`, porque `rco/` só aceita o que o pin lista e porque os `.yaml` do
primeiro nível são lidos pela cerca de perspectiva (ADR-0040 D7), que reprovaria a lista de tokens.

## Pin do pacote K2 (Manual do Beneficiário)

O K2 (contrato `docs/programa/contrato-k2.md` do rco) é o conteúdo do Manual do Beneficiário, do mesmo
commit do K1. O pin é `k2.lock.json` (`lock_format` = `rco-k2-lock` `1.0.0`) e os bytes ficam em
`config/integrations/rco/k2/`, pasta plana. O gate é `scripts/ci/verify_rco_k2_pin.py`, chamado por
`make verify-rco-rules-pin` junto do gate K1: só biblioteca padrão, sem rede, fail-closed, com os
auxiliares do leitor K1 carregados pelo caminho do arquivo.

| Campo do lock | Conteúdo |
|---|---|
| `estado` | `sem_pacote` (comitado hoje) ou `fixado`. |
| `provenance` | `rco_repo` = `Omni-Saude/regras-contratos-operadora` e `rco_commit_sha` (40 hex; `null` sem pacote). |
| `manifest_pin` | `path` = `config/integrations/rco/k2/manifesto.json` e `sha256` (`sha256:<hex>`); `null` sem pacote. |
| `versao` | igual a `versao` do manifesto; `null` sem pacote. O leitor da Helena lê `versao` e `manifest_pin.sha256`. |

- `sem_pacote`: `k2/` ausente ou só com `README.md`.
- `fixado`: `k2/` tem exatamente 7 arquivos: `manifesto.json`, `manifesto.sha256` (`sha256:<hex>` e LF,
  igual ao hash recomputado do manifesto) e os 5 de dados (`search-index.json`, `capitulo-geral.yaml`,
  `sinonimos.json`, `sinonimos-por-termo.json`, `avaliacoes.yaml`). O manifesto tem bytes canônicos e
  exatamente `formato` (`rco-k2-v1`), `versao`, `commit_rco`, `gerado_em` (UTC `AAAA-MM-DDTHH:MM:SSZ`),
  `ratificado` e `arquivos` (os 5 de dados, cada um com o digest). Lock e manifesto concordam em hash,
  `versao` e commit.
- K2 `fixado` exige K1 `fixado` (contrato-k2 §2: mesma versão e mesmo commit do K1): com o lock K1 ausente,
  ilegível ou `sem_pacote`, ou com o manifesto K1 ausente ou ilegível, o gate reprova (`cruzamento-k1`). Com
  os dois `fixado`: `versao` do K2 = `versao_pacote` do K1 e `commit_rco` = `origem.commit_sha` do K1.
- K1 `fixado` com K2 `sem_pacote` é permitido: o runtime das regras não precisa do K2.
- `ratificado: false` passa no gate: recusá-lo em produção é do leitor, não da integridade dos bytes.
- Recusados: arquivo fora do manifesto, subpasta, link simbólico, chave JSON repetida, `NaN`/`Infinity`,
  hex cru sem `sha256:`.

O pin muda só quando o rco publica um K2 novo: copiar os 7 arquivos byte a byte para `k2/` e preencher o
lock a partir do manifesto.

## Estado manual por regra e indicador de ambiente (ADR-0077 §1-§2, unidade 5c-1)

Só lógica pura, dado e indicador. **Nada disso está ligado a worker ou processo**: a ligação é a unidade
5c-2 (e 5d/5e). Código: `src/maezo/platform/rco_ativacao.py`.

- **Arquivo de estado:** `spec/processes/dmn/rco-regras-estado.yaml`, irmão de
  `auth-criteria-ratification.yaml`. Chave (`idRegra`, `versaoRegra`), estados `sombra | ativa_l2 |
  desligada`. Comitado **sem entradas**: toda regra está em `sombra`. Chave ausente = `sombra`; arquivo
  ausente, ilegível ou inválido (YAML ruim, chave repetida, estado fora do conjunto, campo a mais ou a
  menos) = **todas** as regras `desligada`, com alerta (`rco_regras_estado_ilegivel`). Uma `versaoRegra`
  nova é regra nova e não herda estado. Mudar o arquivo é PR humano.
- **Indicador `efeito_k1_habilitado`:** chave `MAEZO_RCO_EFEITO_K1` (ou configuração do grafo) **e**
  `MAEZO_ENV` vivo em `AMBIENTES_QUE_PODEM_LIGAR`, que hoje só tem `dev`. Falso por padrão; produção,
  ambiente vazio e desconhecido nunca ligam (chave ligada ali gera o aviso
  `rco_efeito_k1_recusado_ambiente`). **Nenhum ambiente liga a chave**: nada em `deploy/` a define.
  Com o indicador falso, todo estado manual se comporta como `sombra`.
- `ativacao_efetiva`, `filtrar_saida` e a amostragem HMAC (`amostrar`, `amostra_inicial`) são funções
  puras; o registro em banco, as User Tasks e o monitor de reversão ficam para 5c-2, 5d e 5e.
