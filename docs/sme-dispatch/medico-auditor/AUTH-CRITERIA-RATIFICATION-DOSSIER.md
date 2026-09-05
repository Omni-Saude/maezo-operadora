# Dossie de ratificacao — criterios de aprovacao automatica de SP-OP-AUTH-001

**Origem:** `OWNER-DECISIONS-REGISTER` **R-163** (gap `AUTH-CRITERIA-RATIFICACAO-MANIFESTO`,
P1). **Work package:** `WP-DMN-RATIFICACAO`. **Portao:** `GAP-AUTH-4`.

**Sessao:** conjunta — **medico auditor + juridico/regulatorio + financas**, a ser convocada
**ate 2026-09-19**. A decisao aprovada manteve a opcao **B** (ratificar as tabelas preenchidas e
deixar a contratual pendente) e a transformou num **ato unico datado**, com este dossie entregue
**antes** da data e o binding sha256 amarrando cada ratificacao ao texto lido.

**Manifesto que a sessao preenche:** `spec/processes/dmn/auth-criteria-ratification.yaml` — **CODEOWNED**.

> **Nenhum agente preenche `ratificado`, `revisor` ou `ratificado_em`.** Este dossie e insumo:
> ele le, cita e amarra: nao decide. Rotulo desta entrega: *recomendacao — pendente de
> assinatura de medico auditor + juridico/regulatorio + financas*.

> **O desfecho de hoje, sem exagero.** Enquanto `ratificado: false`, os criterios tecnico,
> regulatorio e contratual avaliam **FALSE** com `*_FONTE_NAO_RATIFICADA`, independentemente do
> que a tabela computa — e esse e **o unico cadeado hoje, nao dois**: o teto financeiro de
> `authorization_approval` **ja esta fechado em R$500** (`spec/policies/autonomy/tenants-amh.yaml`,
> decisao **D-07 FECHADA em 25/08/2026**), e `auth.py::_criterio_financeiro` ja devolve `ok=True`
> para um pedido precificado ate esse teto. Por isso **NADA auto-aprova ainda**: toda solicitacao
> de autorizacao vai a revisao humana, mas por falta de fonte ratificada — nao por teto zerado.
> Ratificar as fontes que um criterio consulta **abre**, sozinha e sem mudanca de codigo, a
> aprovacao automatica desse criterio ate R$500 (`auth.py::_gate_on_ratification`, gate
> conjuntivo: `all(sources.is_ratified(k) for k in consulted)`) — um caminho automatico que hoje
> esta fechado.

> **PHI.** Este documento nao contem dado de paciente. O conteudo citado das tabelas e **texto
> de politica** (limiares, categorias, prazos), nao dado clinico de individuo.

**Gemeo legivel por maquina:** `auth-criteria-ratification-dossier.yaml`.
`tests/unit/docs/test_sme_dispatch_dossiers.py` **re-deriva** cada `sha256` dos bytes em disco e
compara com o declarado aqui e no YAML, usando a mesma funcao que o repositorio ja usa para o
binding `tabela_viva.sha256` dos manifestos shadow-candidate
(`tests/support/dmn_first_hit.py::live_table_digest`). Se uma tabela for editada, o dossie fica
**vermelho** em vez de continuar descrevendo um texto que ja mudou.

---

## 0. Divergencia de contagem — item de pauta, nao erro de leitura

A linha aprovada fala em preencher "os tres campos das **4 tabelas**"; a pergunta original fala
em "as **5 tabelas** de criterio de AUTH"; `docs/review-queue.md` (linha do residuo de
`GAP-AUTH-4`) diz "ratificar as **5 tabelas** + popular a contratual". O manifesto no disco
declara **6 fontes**.

Reconciliacao contra o artefato real, que este dossie adota:

- **Grupo A — 5 fontes preenchidas**, tecnicamente ratificaveis nesta sessao:
  `dut_rol_coverage`, `dut_criteria_bariatrica`, `dut_criteria_oncologia_pet_ct`,
  `dut_criteria_terapias_especiais`, `carencia_check`.
- **Grupo B — 1 fonte**, `auth_criteria_contratual`, **costura vazia**: permanece pendente
  (opcao B mantida) e, alem disso, tem a ratificacao **suprimida por dados** (§4, item 1).

A leitura "4" e reconstruivel como as **4 tabelas de criterio clinico/regulatorio**
(`dut_criteria_bariatrica`, `dut_criteria_oncologia_pet_ct`, `dut_criteria_terapias_especiais`,
`carencia_check`), tratando `dut_rol_coverage` como tabela de **cobertura/mapeamento**. Essa
leitura, porem, **nao funciona operacionalmente**: sem `dut_rol_coverage` ratificada o criterio
TECNICO continua `false` (§4, item 3), de modo que ratificar so as 4 clinicas nao muda nada. A
sessao precisa dizer explicitamente **quais ids** assina.

---

## 1. Quadro-resumo

| Fonte (`id` no manifesto) | Criterio que ela porta | Arquivo | hitPolicy | Regras | `ratificado` | `revisor` | `ratificado_em` |
|---|---|---|---|---|---|---|---|
| `dut_rol_coverage` | TECNICO | `spec/processes/dmn/dut_rol_coverage.dmn` | FIRST | 25 | *(vazio)* | *(vazio)* | *(vazio)* |
| `dut_criteria_bariatrica` | TECNICO (via mapeamento DUT-BARIATRICA-001) | `spec/processes/dmn/dut_criteria_bariatrica.dmn` | FIRST | 7 | *(vazio)* | *(vazio)* | *(vazio)* |
| `dut_criteria_oncologia_pet_ct` | TECNICO (via mapeamento DUT-PET-CT-ONCOLOGIA-001) | `spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn` | FIRST | 9 | *(vazio)* | *(vazio)* | *(vazio)* |
| `dut_criteria_terapias_especiais` | TECNICO — HOJE INALCANCAVEL (ver §4, item 2) | `spec/processes/dmn/dut_criteria_terapias_especiais.dmn` | FIRST | 11 | *(vazio)* | *(vazio)* | *(vazio)* |
| `carencia_check` | REGULATORIO | `spec/processes/dmn/carencia_check.dmn` | FIRST | 12 | *(vazio)* | *(vazio)* | *(vazio)* |
| `auth_criteria_contratual` | CONTRATUAL | `spec/processes/dmn/auth_criteria_contratual.dmn` | FIRST | 1 | *(vazio)* | *(vazio)* | *(vazio)* |

Os tres campos de ratificacao aparecem **vazios** de proposito: sao o ato da sessao. O manifesto
no disco carrega hoje `ratificado: false`, `ratificado_em: null` e, em `revisor`, uma descricao
de **papel** (nao uma pessoa) — reproduzida na coluna "papel indicado" de cada ficha abaixo.
Ratificar exige os **tres** campos: qualquer um ausente, em branco ou diferente do literal
booleano `true` significa NAO ratificado (fail-closed).

## 2. Binding sha256 — o que cada assinatura cobre

Cada `sha256` abaixo e o digest dos **bytes do arquivo em disco neste commit**, re-derivado com
a mesma funcao que o repositorio ja usa para amarrar uma aprovacao humana a bytes (e nao a um
nome): `hashlib.sha256(path.read_bytes()).hexdigest()`, o padrao de
`amh_inbox.migration_digest()` / `tiss_schema_pin` gate 5 / `tabela_viva.sha256`. Nenhum digest
foi calculado a mao.

| Artefato | sha256 |
|---|---|
| `spec/processes/dmn/auth-criteria-ratification.yaml` (o proprio manifesto) | `899853c5cc078cf9563287cb02f6ca8b4edb215ca2674acfe28277fd19fc385c` |
| `spec/processes/dmn/dut_rol_coverage.dmn` | `0af3f7ba785b5e197e47222ba21652782a1a1934f7c71df1cf5fa2b257a1a551` |
| `spec/processes/dmn/dut_criteria_bariatrica.dmn` | `f4ee5adf9c3f70f16165238c266c05ba4575961de88bcfadc684c41d38c550f7` |
| `spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn` | `f401becbc611b9a0da5a51f0892abf2704428cb92c012b3ffdab142c36d646ef` |
| `spec/processes/dmn/dut_criteria_terapias_especiais.dmn` | `e1200d3c1208acaad64a44d635f1e54bf3fb189cf39d66879d3717e86ac9cd13` |
| `spec/processes/dmn/carencia_check.dmn` | `2fb3aafe2a5953ffea95b57e1a9aaf04bf4e48899896be790d0dcd921cc85a21` |
| `spec/processes/dmn/auth_criteria_contratual.dmn` | `266f22c00aae35c67493b7c394b0d9d4e36870c359c2d887e2c6359530094113` |

**O que isto compra.** A assinatura da sessao passa a cobrir um texto identificado, e nao um
nome de arquivo. Se qualquer uma das tabelas for editada depois da leitura e antes da
ratificacao, o digest muda, o teste de cerca fica vermelho e a ratificacao tem de ser refeita
contra o texto novo — exatamente a propriedade que `tabela_viva.sha256` ja da aos manifestos
shadow-candidate.

---

## 3. Fichas por tabela

### `dut_rol_coverage`

- **Grupo:** A — tabelas preenchidas, ratificaveis nesta sessao
- **Criterio que ela porta:** TECNICO
- **Arquivo:** `spec/processes/dmn/dut_rol_coverage.dmn` — hitPolicy `FIRST`, 25 regras, `historyTimeToLive=P180D`
- **Binding sha256 do texto lido:** `0af3f7ba785b5e197e47222ba21652782a1a1934f7c71df1cf5fa2b257a1a551`
- **Papel indicado no manifesto (campo `revisor` de hoje):** medico auditor + juridico/regulatorio
- **Campos de ratificacao:** `ratificado` *(vazio)* · `revisor` *(vazio)* · `ratificado_em` *(vazio)*

**Conteudo atual (regra/criterio, texto lido).** hitPolicy FIRST, 25 regras (`r01`..`r24` + catch-all `r99`). Entradas `codigo_procedimento_tuss` (string) e `categoria_procedimento` (string); saidas `no_rol` (boolean), `requer_dut` (boolean) e `dut_ref` (string). Todos os codigos TUSS das 24 regras sao declarados **SINTETICOS e representativos** pelo proprio cabecalho da tabela; cada `<description>` de regra comeca com a palavra `SINTETICO`. Invariante L0: a tabela NAO tem saida de negativa — fora do ROL ou com DUT obrigatoria roteia a analise humana.

**Fonte normativa/clinica a conferir.** Citado pelo repositorio (cabecalho da DMN, secao "Fontes regulatorias (DRAFT/verify)"): **ROL vigente RN 465/2021 + RN 473/2021 + RN 643/2025**; **DUTs** publicadas no portal ANS; **tabela TUSS** publicada pela ANS (`tabela_tuss_4_01`). O manifesto repete a mesma lista. **Nao ha, no repositorio, copia ou extrato dessas tabelas** — `docs/compliance/` contem `rn-currency-review.md`, `rn-639-primary-source.md`, `lgpd-topic-reconciliation.md`, `ripd-kickoff.md` e `ADR-0020-amendment-draft.md`, nenhum deles com o ROL ou a TUSS. A conferencia codigo-a-codigo dos 24 TUSS e **externa ao repositorio**.

**O que muda ao ratificar.** `sources.is_ratified("dut_rol_coverage")` passa a `True`. O criterio **TECNICO** deixa de sair `false` com `TECNICO_FONTE_NAO_RATIFICADA` e passa a **usar o veredito real** da tabela: `no_rol=true` -> `TECNICO_FORA_DO_ROL`; `requer_dut=false` -> criterio tecnico PASSA; `requer_dut=true` -> consulta a `dut_criteria_*` mapeada. **Porem o TECNICO so passa quando TODAS as tabelas consultadas estao ratificadas** (`_gate_on_ratification`, `consulted=[dut_rol_coverage] (+ a dut_criteria_* mapeada)`): ratificar esta sozinha libera apenas o caminho `requer_dut=false`. O token de sombra `TECNICO_SOMBRA_*` deixa de ser emitido para os caminhos ja cobertos.

### `dut_criteria_bariatrica`

- **Grupo:** A — tabelas preenchidas, ratificaveis nesta sessao
- **Criterio que ela porta:** TECNICO (via mapeamento DUT-BARIATRICA-001)
- **Arquivo:** `spec/processes/dmn/dut_criteria_bariatrica.dmn` — hitPolicy `FIRST`, 7 regras, `historyTimeToLive=P180D`
- **Binding sha256 do texto lido:** `f4ee5adf9c3f70f16165238c266c05ba4575961de88bcfadc684c41d38c550f7`
- **Papel indicado no manifesto (campo `revisor` de hoje):** medico auditor + cirurgiao bariatrico + juridico/regulatorio
- **Campos de ratificacao:** `ratificado` *(vazio)* · `revisor` *(vazio)* · `ratificado_em` *(vazio)*

**Conteudo atual (regra/criterio, texto lido).** hitPolicy FIRST, 7 regras (`r01`..`r06` + catch-all `r99`). Entradas `imc` (integer), `imc_acima_35_com_comorbidade`, `imc_acima_40`, `tentativas_previas_tratamento_clinico` (integer), `sem_contraindicacao_cirurgica`, `avaliacao_multidisciplinar_completa`; saidas `dut_atendida` (boolean) e `motivo` (string). Limiares declarados **SINTETICOS**: IMC >= 40 (grau III), IMC >= 35 com comorbidade, >= 1 tentativa previa documentada de tratamento clinico. Nenhuma regra nega: `dut_atendida=false` encaminha ao medico auditor; catch-all `r99` cobre combinacao nao mapeada ou dados ausentes.

**Fonte normativa/clinica a conferir.** Citado pelo repositorio (cabecalho da DMN e `observacao` do manifesto): **DUT ANS de cirurgia bariatrica vigente** (portal ANS) e **Resolucao CFM 2.131/2015** e atualizacoes. **A conferir sem fonte no repositorio:** a definicao exata de comorbidade qualificante (a DMN lista "DM2, HAS, apneia obstrutiva, dislipidemia" como DRAFT/verify), a lista completa de contraindicacoes cirurgicas, e o periodo/documentacao exigidos das tentativas previas (o cabecalho menciona "minimo 2 anos" mas a tabela conta apenas `>= 1` tentativa) — nenhuma dessas tres definicoes tem lastro em `docs/compliance/`.

**O que muda ao ratificar.** As 7 regras passam a decidir de fato para procedimentos cujo `dut_ref` resolva `DUT-BARIATRICA-001` (mapeamento declarado no manifesto). Com **dut_rol_coverage tambem ratificada**, `dut_atendida=true` faz o criterio TECNICO PASSAR e `dut_atendida=false` vira `TECNICO_DUT_NAO_ATENDIDA` (analise humana, nunca negativa automatica). Ratificada sozinha, sem `dut_rol_coverage`, **nao muda nada**: o criterio continua `TECNICO_FONTE_NAO_RATIFICADA`.

### `dut_criteria_oncologia_pet_ct`

- **Grupo:** A — tabelas preenchidas, ratificaveis nesta sessao
- **Criterio que ela porta:** TECNICO (via mapeamento DUT-PET-CT-ONCOLOGIA-001)
- **Arquivo:** `spec/processes/dmn/dut_criteria_oncologia_pet_ct.dmn` — hitPolicy `FIRST`, 9 regras, `historyTimeToLive=P180D`
- **Binding sha256 do texto lido:** `f401becbc611b9a0da5a51f0892abf2704428cb92c012b3ffdab142c36d646ef`
- **Papel indicado no manifesto (campo `revisor` de hoje):** medico auditor + oncologista/nucleo-medicina + juridico/regulatorio
- **Campos de ratificacao:** `ratificado` *(vazio)* · `revisor` *(vazio)* · `ratificado_em` *(vazio)*

**Conteudo atual (regra/criterio, texto lido).** hitPolicy FIRST, 9 regras (`r01`..`r08` + catch-all `r99`). Entradas `diagnostico_oncologico_confirmado`, `finalidade_pet_ct` (string: `estadiamento|reestadimento|avaliacao_resposta|deteccao_recorrencia|outras`), `tipo_neoplasia_elegivel`, `exames_convencionais_inconclusivos`, `solicita_oncologista_ou_nucleo`; saidas `dut_atendida` e `motivo`. Cobre **apenas a via oncologica**; indicacoes neurologicas/cardiologicas sao escopo separado, declarado pendente de SME adicional pela propria tabela.

**Fonte normativa/clinica a conferir.** Citado pelo repositorio: **DUT PET-CT oncologia** publicada pela ANS (o cabecalho diz "referencia inicial: DUT PET-CT oncologia publicada ANS circa 2014/emendas" — datacao aproximada, marcada DRAFT/verify). **A conferir sem fonte no repositorio:** a lista completa de neoplasias elegiveis (a DMN exemplifica "linfoma, melanoma, cancer pulm, colorretal, mama, etc."), quais biopsias contam como confirmacao histologica, quando a DUT exige exames convencionais inconclusivos, e a especialidade habilitada a solicitar.

**O que muda ao ratificar.** Identico em mecanica a `dut_criteria_bariatrica`, para `dut_ref = DUT-PET-CT-ONCOLOGIA-001`. Depende da ratificacao conjunta de `dut_rol_coverage` para produzir qualquer efeito.

### `dut_criteria_terapias_especiais`

- **Grupo:** A — tabelas preenchidas, ratificaveis nesta sessao
- **Criterio que ela porta:** TECNICO — HOJE INALCANCAVEL (ver §4, item 2)
- **Arquivo:** `spec/processes/dmn/dut_criteria_terapias_especiais.dmn` — hitPolicy `FIRST`, 11 regras, `historyTimeToLive=P180D`
- **Binding sha256 do texto lido:** `e1200d3c1208acaad64a44d635f1e54bf3fb189cf39d66879d3717e86ac9cd13`
- **Papel indicado no manifesto (campo `revisor` de hoje):** medico auditor + neuropediatra/psiquiatra infantil + juridico/regulatorio
- **Campos de ratificacao:** `ratificado` *(vazio)* · `revisor` *(vazio)* · `ratificado_em` *(vazio)*

**Conteudo atual (regra/criterio, texto lido).** hitPolicy FIRST, 11 regras (`r01`..`r10` + catch-all `r99`). Entradas `diagnostico_tea_ou_neurodesenvolvimento`, `tipo_terapia` (string: `aba|fonoaudiologia_especializada|terapia_ocupacional_sensorial|psicoterapia_especializada|outras`), `avaliacao_multidisciplinar_completa`, `solicitante_habilitado`, `sessoes_mensais_solicitadas` (integer), `plano_terapeutico_documentado`; saidas `dut_atendida` e `motivo`. Limites de sessoes declarados **SINTETICOS**: ABA <= 20/mes; fonoaudiologia especializada <= 8/mes; terapia ocupacional sensorial <= 8/mes.

**Fonte normativa/clinica a conferir.** Citado pelo repositorio: **DUT ANS de terapias especiais** (RN 465/2021 + emendas RN 473/2021 e RN 643/2025), **Lei 12.764/2012** (Berenice Piana), **Lei 14.254/2021** e **Resolucao CFM 2.294/2021**. **A conferir sem fonte no repositorio:** os limites de sessoes por modalidade (a propria tabela diz que sao SINTETICOS), os CIDs aceitos (o cabecalho cita F84.0 e as faixas F80-F89 / F90-F98 "parcialmente"), a composicao minima da equipe multidisciplinar e as especialidades habilitadas por tipo de terapia.

**O que muda ao ratificar.** **Nada, hoje.** Nenhum `dut_ref` emitido por `dut_rol_coverage` resolve para esta tabela: o `mapeamento_dut_criteria` do manifesto declara duas correspondencias (`DUT-BARIATRICA-001`, `DUT-PET-CT-ONCOLOGIA-001`) e nenhuma delas e esta. Um `dut_ref` com `requer_dut=true` sem entrada no mapeamento resolve FECHADO, com `TECNICO_PROCEDIMENTO_NAO_MAPEADO`. Ratificar esta tabela **sem** declarar o mapeamento e um ato sem efeito — e a decisao de mapeamento (`DUT-TERAPIA-OCUPACIONAL-001`, `DUT-FONOAUDIOLOGIA-001`, `DUT-PSICOTERAPIA-001`) e julgamento clinico do medico auditor, deliberadamente nao adivinhado pelo repositorio.

### `carencia_check`

- **Grupo:** A — tabelas preenchidas, ratificaveis nesta sessao
- **Criterio que ela porta:** REGULATORIO
- **Arquivo:** `spec/processes/dmn/carencia_check.dmn` — hitPolicy `FIRST`, 12 regras, `historyTimeToLive=P365D`
- **Binding sha256 do texto lido:** `2fb3aafe2a5953ffea95b57e1a9aaf04bf4e48899896be790d0dcd921cc85a21`
- **Papel indicado no manifesto (campo `revisor` de hoje):** juridico/regulatorio + medico auditor
- **Campos de ratificacao:** `ratificado` *(vazio)* · `revisor` *(vazio)* · `ratificado_em` *(vazio)*

**Conteudo atual (regra/criterio, texto lido).** hitPolicy FIRST, 12 regras (`r01`..`r11` + catch-all `r99`). Entradas `tipo_procedimento` (string: `urgencia_emergencia|parto|eletivo|alta_complexidade|outros`), `dias_desde_adesao` (integer) e `cpt_declarada` (boolean); saidas `carencia_cumprida` (boolean), `prazo_restante_dias` (integer) e `fonte` (string). Prazos declarados **SINTETICOS** pelo proprio cabecalho: urgencia/emergencia **24 horas (1 dia)**, parto **300 dias**, eletivo **180 dias**, CPT para doenca pre-existente **24 meses (730 dias)**. Portabilidade de carencia **nao esta implementada**; a tabela manda encaminhar esses casos ao medico auditor.

**Fonte normativa/clinica a conferir.** Citado pelo repositorio (cabecalho da DMN e `observacao` do manifesto): **Lei 9.656/1998** art. 11, art. 12 II "a" e art. 35-C; **RN 195/2009**; **RN 259/2011** (art. 7); **RN 162/2007** (CPT); **RN 465/2021**; **RN 186/2009** para portabilidade (nao implementada). `docs/review-queue.md` acrescenta a advertencia geral de que "RN 259 pode ter sido consolidada/substituida". **A conferir sem fonte no repositorio:** o mapeamento de `tipo_procedimento` para prazo (a DMN o marca DRAFT/verify) e o tratamento de portabilidade/migracao de plano.

**O que muda ao ratificar.** `sources.is_ratified("carencia_check")` passa a `True` e o criterio **REGULATORIO** deixa de sair `false` com `REGULATORIO_FONTE_NAO_RATIFICADA`. **Mas o criterio continua falhando fechado por outro motivo:** as tres entradas (`tipo_procedimento`, `dias_desde_adesao`, `cpt_declarada`) **nao existem no contrato de start de SP-OP-AUTH-001** — sao dado cadastral da fronteira AMH (MZO-050b, bloqueado). O criterio sai `false` com `REGULATORIO_ENTRADA_AUSENTE`, que e um problema de MECANISMO, distinto de `*_FONTE_NAO_RATIFICADA` e deliberadamente nao mascarado por ele. Ratificar esta tabela **nao** abre aprovacao automatica enquanto as entradas nao existirem.

### `auth_criteria_contratual`

- **Grupo:** B — costura vazia; PERMANECE PENDENTE (nao ratificar nesta sessao)
- **Criterio que ela porta:** CONTRATUAL
- **Arquivo:** `spec/processes/dmn/auth_criteria_contratual.dmn` — hitPolicy `FIRST`, 1 regras, `historyTimeToLive=P180D`
- **Binding sha256 do texto lido:** `266f22c00aae35c67493b7c394b0d9d4e36870c359c2d887e2c6359530094113`
- **Papel indicado no manifesto (campo `revisor` de hoje):** juridico/contratos + financas + medico auditor (criterios de KPI assistencial)
- **Campos de ratificacao:** `ratificado` *(vazio)* · `revisor` *(vazio)* · `ratificado_em` *(vazio)*

**Conteudo atual (regra/criterio, texto lido).** hitPolicy FIRST, **1 unica regra**: o catch-all `r99`, que devolve `criterio_contratual_ok=false` e `motivo="SEM_REGRA_RATIFICADA"`. Entradas `tenant_id` e `categoria_procedimento` (ambas ja existentes no contrato de SP-OP-AUTH-001 — nenhuma variavel foi inventada). **Nenhuma regra contratual, milestone ou KPI foi inventada:** a tabela e o lugar versionado onde essas regras vao aterrissar.

**Fonte normativa/clinica a conferir.** **Sem fonte no repositorio, por construcao.** Nao existe variavel de plano, produto ou termos contratuais em SP-OP-AUTH-001, e os KPIs declarados em `spec/agents/rafael/agent.yaml` (`auto_approval_rate`, `human_routing_precision`, `false_denial_rate`) sao prosa aspiracional sem codigo que os compute. A fonte tem de ser trazida de fora pelo SME contratual (juridico/contratos + financas). Pergunta aberta registrada em `docs/review-queue.md`: os criterios de KPI sao a nivel de prestador, de plano, ou ambos?

**O que muda ao ratificar.** **Nada — a ratificacao esta SUPRIMIDA por dados.** A secao `criterios_nao_cobertos` do manifesto declara `rede_credenciada` com `bloqueia_ratificacao_de: auth_criteria_contratual`, e o loader `maezo.tools.workers.auth_criteria` **le essa declaracao e suprime a ratificacao da fonte nomeada**: `is_ratified` devolve `false` por mais completos que estejam os tres campos, o criterio sai `false` com `CONTRATUAL_FONTE_NAO_RATIFICADA` e a supressao e logada em `auth_criteria_ratification_suppressed`. Preencher os tres campos aqui produziria uma ratificacao **inerte e enganosa**. Levantar o bloqueio exige uma fonte de credenciamento de rede legivel por maquina — que nao existe no repositorio — e a remocao da entrada, no mesmo commit CODEOWNED. Esta e exatamente a opcao **B** que a decisao aprovada manteve.

---

## 4. Quatro fatos que a sessao precisa ver antes de assinar

**1. `auth_criteria_contratual` tem a ratificacao SUPRIMIDA por dados, nao apenas pendente.**
A secao `criterios_nao_cobertos` do manifesto declara `rede_credenciada` com
`bloqueia_ratificacao_de: auth_criteria_contratual`, e o loader **le e obedece** essa declaracao
(ate 2026-08-09 ela era inerte — defeito M-3, corrigido). Preencher os tres campos dessa fonte
produz uma ratificacao **sem efeito**, registrada em log como `auth_criteria_ratification_suppressed`.
Levanta-la exige uma fonte de credenciamento de rede legivel por maquina, que **nao existe** no
repositorio, e a remocao da entrada no mesmo commit CODEOWNED.

**2. `dut_criteria_terapias_especiais` esta INALCANCAVEL.** Nenhum `dut_ref` emitido por
`dut_rol_coverage` resolve para ela: o `mapeamento_dut_criteria` declara **2 de 3**
correspondencias, deliberadamente. `DUT-TERAPIA-OCUPACIONAL-001`, `DUT-FONOAUDIOLOGIA-001` e
`DUT-PSICOTERAPIA-001` estao pendentes de julgamento clinico — o repositorio recusa-se a
adivinhar qual tabela de criterios se aplica a qual DUT. Ratificar a tabela **sem** declarar o
mapeamento e um ato sem efeito; declarar o mapeamento e decisao do medico auditor.

**3. Ratificacao e CONJUNTIVA por criterio.** `_gate_on_ratification` exige que **todas** as
tabelas consultadas por um criterio estejam ratificadas
(`all(sources.is_ratified(key) for key in consulted)`). Para o criterio TECNICO, `consulted` e
`[dut_rol_coverage]` mais a `dut_criteria_*` mapeada quando `requer_dut=true`. Consequencia
pratica: **ratificar as 4 tabelas clinicas sem `dut_rol_coverage` nao muda absolutamente nada**.

**4. Ratificar NAO abre aprovacao automatica sozinho — mas o criterio financeiro ja e diferente.**
Ao contrario do que uma leitura antiga sugeria, o teto financeiro **nao** esta zerado hoje:
`authorization_approval.max_value_brl = 500` (`spec/policies/autonomy/tenants-amh.yaml`, decisao
**D-07 FECHADA em 25/08/2026**), e `auth.py::_criterio_financeiro` ja devolve `ok=True` para
pedido precificado ate esse teto — esse criterio **nao e governado por este manifesto** (nao ha
tabela DMN de teto a ratificar aqui). Restam, fora do alcance desta assinatura: (a) o teto
`reembolso_auto_approval.max_value_brl = 0`, com D-07 **ainda em aberto** para essa outra acao
(nao afeta AUTH); (b) as entradas de `carencia_check` (`tipo_procedimento`, `dias_desde_adesao`,
`cpt_declarada`), que **nao existem** no contrato de start e sao dado cadastral da fronteira AMH
(MZO-050b, bloqueado); (c) a politica de **combinacao** da `auth_auto_approval.dmn` v0.2.0 —
notadamente que `carater_atendimento` e *don't-care* na regra `r1`, de modo que uma urgencia
auto-aprovaria sob os mesmos fatos de um eletivo (item proprio em `docs/review-queue.md`).

## 5. O que a sessao produz

Para cada `id` que decidir ratificar, em `spec/processes/dmn/auth-criteria-ratification.yaml` (CODEOWNED,
PR de owner-review, **mudanca de DADOS — nenhuma linha de codigo muda**):

```yaml
  <id>:
    ratificado: true            # o literal booleano true — nada mais conta
    revisor: "<quem ratificou>"  # nao-vazio; responsabilizacao ADR-0007
    ratificado_em: "AAAA-MM-DD"  # nao-vazio; quando
```

E, para os ids que **nao** ratificar, o registro do motivo em `docs/review-queue.md`. Nenhum
agente escreve qualquer um desses valores.
