# ADR-0075: Regras de negócio da operadora vêm do `regras-contratos-operadora` (pacote K1); o Maezo mantém as tabelas de processo

**Status:** Proposed — rascunho escrito por agente (sub-orquestradora T-MZO-A, Programa Circuito Fechado, unidade MZO-0c) com base nas decisões do dono de 14/11/2026. Aceitar este ADR é ato separado do dono, porque `/docs/adr/` é caminho de CODEOWNERS. Esta unidade não muda código, DMN, worker nem política de autonomia.
**Aceite parcial:** os itens implementados na Onda 1 (§(b) pasta, varredura, pin e regras por pasta; §(c) erro determinístico permanente; §(d) só o lint estático de vocabulário) foram aceitos pelo ADR-0076, por ordem do dono de 15/11/2026 (Programa Circuito Fechado, Onda 1). O ADR-0076 também corrige o §(c): a sobreposição `UNIQUE` não é mais transiente. O restante deste ADR segue Proposed.
**Data:** 2026-11-14
**Área:** Integração / Regras de negócio / Motor de processos / Autonomia

## Contexto

### Decisões do dono que este ADR transforma em arquitetura (14/11/2026)

1. O repositório `regras-contratos-operadora` (rco) decide **"o quê"**: cobertura, DUT, carência, autorização, limites, pacotes, OPME e glosa. Ele tem 867 regras e 233 tabelas DMN `UNIQUE` para Camunda 7, derivadas do Rol, da DUT e da TUSS, e ainda não tem consumidor. O Maezo decide **"como"**: SLA, triagem, roteamento, alçada e escalonamento.
2. A negativa por critério clínico continua com o médico: `authorization_denial` é L0 hard e não muda. A negativa administrativa (carência simples, exclusão a pedido) e a glosa determinística de conta poderão passar a ser automáticas em L2, com amostragem, interruptor e desligamento por regra, e só para regra que passou no backtest. Isso exige, em onda futura, uma emenda ao ADR-0018 e um adendo ao ADR-0040. **Este ADR não faz essa emenda.** Ele só descreve o que ela vai precisar.
3. A ratificação de regras é feita pelo dono mais um agente auditor neutro. A revisão médica, jurídica e do DPO é assíncrona e não bloqueia.
4. Contratos entre repositórios. Interface entre repositórios é sempre um artefato versionado com hash: o fornecedor publica e o consumidor fixa (pin). O padrão é `config/integrations/amh/contracts.lock.json`.
   - **K1** é o pacote de regras (rco→mzo): as tabelas DMN mais um manifesto com `versaoRegra` e `shaDmn`. As saídas são `decisao`, `classeAdverso` ∈ {`clinico`, `administrativo`, `contas`}, `motivoTab38`, `idRegra` e `fila`.
   - **K6** é a correspondência entre as filas do rco e os `candidateGroups` do mzo.

### O que o código do Maezo tem hoje

- **70 arquivos DMN, todos no primeiro nível** de `spec/processes/dmn/`. Não existe subpasta. A contagem é pinada por `scripts/docs/contagens_canonicas.py:117` contra `README.md:90,133`.
- Hit policy: 68 arquivos `FIRST`; `programa_sla.dmn:23` é `UNIQUE`; `prestador_trust_scorecard.dmn:54,254` tem duas decisões `COLLECT`.
- O ADR-0040 D4 fixa "Hit policy `FIRST` com row catch-all conservadora em toda tabela" (`docs/adr/0040-perspectiva-operadora-contas-recurso.md:178`).
- O ADR-0018, parte 2, proíbe saída adversa em DMN: "Nenhuma `decisionTable` do processo possui saida que confirme/aceite/negue o efeito adverso. A DMN apenas *sinaliza candidata e roteia*" (`docs/adr/0018-no-denial-structural-replication.md:77-78`).
- O ADR-0040 D4 repete a regra: "Nenhuma DMN emite o adverso; o unico desfecho automatico e o favoravel" (`docs/adr/0040-perspectiva-operadora-contas-recurso.md:167`).

## Decisão

### (a) Fronteira "o quê" × "como", por arquivo de `spec/processes/dmn/`

**Lado rco ("o quê"): serão substituídas pelas tabelas do K1 em ondas futuras.** Nenhuma é removida por este ADR.

| Tabela | Consumidor hoje |
|---|---|
| `dut_rol_coverage.dmn` | worker `auth.py:279` (`ValidateAutoCriteriaWorker`, critério técnico); órfã listada em `orphans-allowlist.yaml:81` |
| `dut_criteria_bariatrica.dmn`, `dut_criteria_oncologia_pet_ct.dmn`, `dut_criteria_terapias_especiais.dmn` | `src/maezo/tools/workers/auth.py:878,886,893`; órfãs (`orphans-allowlist.yaml:58,65,72`) |
| `carencia_check.dmn` | `auth.py:280` (critério regulatório); órfã (`orphans-allowlist.yaml:50`); tem `carencia-check-shadow-candidate.yaml` |
| `auth_criteria_contratual.dmn` | `auth.py:281`; hoje tem uma única linha catch-all que devolve `false,"SEM_REGRA_RATIFICADA"` (`tests/unit/sec/test_auto_approval_criteria_fence.py:220-227`) |
| `glosa_classification.dmn` | BPMN `SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:169`; `src/maezo/agents/marina/graph.py:226` |
| `glosa_reason_normalization.dmn` | BPMN `SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:159`; `src/maezo/tools/workers/contas.py:384` |

**Lado Maezo ("como"): ficam no Maezo.**
- SLA: `adequacao_sla`, `ans_sla`, `auth_sla`, `cancel_sla`, `contas_sla`, `cred_sla`, `fraude_sla`, `inadimplencia_sla`, `nip_sla`, `pagto_sla`, `programa_sla`, `recurso_sla`, `reembolso_sla`, `ans_calendar`, `ans_retry_policy`.
- Admissibilidade e roteamento: `adequacao_remediation_routing`, `ans_submission_admissibility`, `auth_junta_admissibility`, `cancel_admissibility`, `cancel_routing`, `cred_admissibility`, `cred_route`, `escalation_routing`, `fraude_routing`, `lgpd_dsr_routing`, `nip_classification`, `nip_routing`, `pagto_admissibility`, `programa_routing`, `recurso_admissibility`, `suppression_routing`, `lucas_billing_admissibility`, `lucas_escalation_routing`, `helena_triage_admissibility`.
- Triagem: `glosa_triage` (domínio `{PAGAR, ANALISE_HUMANA}`, ADR-0040 D4), `triage_redflag_adult`, `triage_redflag_gestante`, `triage_redflag_mental_health`, `triage_redflag_pediatric`, `triage_sufficiency`.
- Alçada: `pagto_alcada`, `pagto_comite_coordenacao`.
- Investigação: `fraude_indicadores`.

São regras de domínio que o K1 não cobre (rede, pagamento e fraude) e continuam no Maezo: `adequacao_gap`, `cred_prior_notice`, `pagto_duplicidade`, `prestador_tier` e `prestador_trust_scorecard`.

**Casos ambíguos.** Cada um exige uma decisão explícita na onda que tocar a tabela.

| Tabela | Por que é ambígua | Proposta |
|---|---|---|
| `auth_auto_approval` (BPMN `SP-OP-AUTH-001_Autorizacao_Previa.bpmn:273`) | É agregador e porta de alçada L2 ("como"), mas suas entradas `criterio_{tecnico,financeiro,regulatorio,contratual}_ok` são veredictos "o quê". | Fica no Maezo. As entradas passam a vir das saídas do K1. |
| `reembolso_auto_approval` (BPMN `SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:565`, faixa dormente, DL-0164) | Mistura limite contratual e tabela (`limite_contratual_cents`, `valor_tabela_dmn_cents`: "o quê") com teto e orçamento de alçada (`teto_l2_cents`, `orcamento_auto_tenant_dia_cents`: "como"). | Dividir: o limite vem do K1; a alçada fica no Maezo. |
| `reembolso_prazo` (BPMN `SP-OP-REEMBOLSO-001_Reembolso_Beneficiario.bpmn:80`) | O prazo de pedido de reembolso é cláusula contratual, mas a tabela hoje só devolve `NAO_AVALIADO`. | Vai para o rco se o K1 trouxer prazo contratual; senão fica como está. |
| `recurso_eligibility` (BPMN `SP-OP-RECURSO-001_Recurso_Glosa.bpmn:249`) | A saída é roteamento, mas a chave é a classificação da glosa ("o quê"). | Fica no Maezo; passa a consumir o `idRegra`/`motivoTab38` da glosa do K1. |
| `inadimplencia_status`, `inadimplencia_purga` (BPMN `SP-OP-INADIMPLENCIA-001_Suspensao_Rescisao.bpmn:117,136`) | Regra legal (Lei 9.656, art. 13) fora da lista do K1. A saída é roteamento ou prazo regulatório, e prazo regulatório é timer/DMN do Maezo (AGENTS.md, regra 5). | Ficam no Maezo enquanto o rco não publicar a regra. |
| `auth_admissibility` (BPMN `SP-OP-AUTH-001_Autorizacao_Previa.bpmn:110`) | Roteia, mas consome `requer_autorizacao` e `carencia_cumprida`, que são fatos "o quê". | Fica no Maezo; as entradas passam a vir do K1. |
| 7 tabelas de indicador de fraude (órfãs, `orphans-allowlist.yaml:140-230`) | `unbundling_partial_bundles` se sobrepõe aos "pacotes" do rco; `upcoding_complexity_ceiling`, à glosa técnica. | Ficam no Maezo (SP-OP-FRAUDE). Revisar a sobreposição quando o K1 trouxer pacotes. |

### (b) Onde o pacote K1 entra

- **Pasta:** `spec/processes/dmn/rco/`. Recebe só os arquivos do K1, byte a byte, e é escrita só pela atualização do pin.
- **Pin:** `config/integrations/rco/rules.lock.json`, no formato de `config/integrations/amh/contracts.lock.json`:
  - `lock_format` (`config/integrations/amh/contracts.lock.json:2`);
  - `provenance` com o commit do rco (`:29`);
  - `manifest_pin` com `sha256` (`:39`);
  - `artifacts` com `path`+`sha256` por arquivo (`:89`).
- **Verificador novo:** `verify-rco-rules-pin`, espelho de `scripts/ci/verify_amh_contract_pin.py`. Roda só com a biblioteca padrão, sem rede, recalcula o sha256 de cada `rco/*.dmn` e recusa arquivo fora da lista. Entra no Makefile e no CI no mesmo lugar do AMH (`Makefile:157-166`, `ci.yml:922` (`.github/workflows/`)).
- **Namespacing obrigatório dos ids de decisão.** No motor, a chave da decisão é global por tenant. Um `carencia_check` do rco, publicado ao lado do `carencia_check` do Maezo, viraria uma *nova versão* da mesma chave. Além disso, o deploy dá nome ao recurso pelo nome do arquivo (`src/maezo/platform/deploy/engine_deploy.py:328`), o que causa colisão de nome. Regra: toda decisão do K1 tem id com prefixo `rco_` (ou outro prefixo que o manifesto declare). O nome do recurso no deploy passa a ser o caminho relativo (`rco/<arquivo>.dmn`). A validação já recusa id de decisão repetido em toda a árvore varrida (`src/maezo/platform/validation/cli.py:117-126`) e passa a varrer `rco/` também.

**Pontos que hoje varrem DMN só no primeiro nível.** Todos foram conferidos; `glob("*.dmn")` não enxerga `rco/`.

| # | Ponto | Mudança mínima proposta |
|---|---|---|
| 1 | `src/maezo/platform/deploy/engine_deploy.py:172` (`collect_artifacts`). Consumido por `platform/deploy/cli.py:139,173,180`, `tests/integration/dmn/conftest.py:37`, `tests/unit/spec/test_shadow_candidates_common.py:199-222` e `tests/unit/ci/test_check_aprovacao_de_regras.py:131`. O caminho `--shared dmn` (`cli.py:139`) publica sem tenant, o que amplia a colisão de chave descrita acima. | Segunda varredura explícita `sorted((dmn_dir / "rco").glob("*.dmn"))`, condicionada ao pin verificado. Não usar `rglob` geral, para que subpasta solta continue fora. Nome do recurso em `:328` = caminho relativo. |
| 2 | `src/maezo/platform/validation/cli.py:104` (schema, crossref e unicidade de id) e `:138` (perspectiva) | Incluir `rco/` na validação de schema e na unicidade de id; isentar `rco/` da regra de órfã (ver gates abaixo). |
| 3 | `src/maezo/platform/validation/perspective.py:948` (`check_processes_root`) | Decidir no adendo ao ADR-0040 se o vocabulário da perspectiva (D7) vale para `rco/`. O gate não tem mecanismo de exceção por desenho (`perspective.py:42`); a escolha é estender o vocabulário, não isentar. |
| 4 | `scripts/ci/check_aprovacao_de_regras.py:73` (`artefatos_publicaveis`) | Mudar junto com o ponto 1. O teste de paridade `tests/unit/ci/test_check_aprovacao_de_regras.py:132` obriga isso. |
| 5 | `scripts/docs/contagens_canonicas.py:117` | Manter a contagem do primeiro nível (tabelas do Maezo) e acrescentar uma contagem `dmn_rco` separada, com frase própria no README. |
| 6 | `scripts/dev/run_engine_integration.py:1484-1485` (`glob("*/*.dmn")` a partir de `spec/processes`, que acha `dmn/*.dmn` mas não `dmn/rco/*.dmn`) | `glob("dmn/**/*.dmn")` ou varredura explícita de `rco/`. |
| 7 | `tests/support/engine_tenant_deploy.py:38` (`artifacts_for`; `LookupError` em `:49`) | Incluir `rco/` quando um `decisionRef` de BPMN apontar para decisão do K1. |
| 8 | `tests/unit/spec/test_dmn_dead_inputs_fence.py:221,272,347` | Ver os gates abaixo. |
| 9 | `tests/unit/platform/test_validation_perspective.py:1288,1293` | Segue a decisão do ponto 3. |
| 9b | `tests/unit/spec/test_programa_population_aggregate_fence.py:167` (`glob("programa_*.dmn")`) | Nenhuma: escopo por nome, só tabelas `programa_*` do Maezo. |
| 10 | `tests/support/dmn_first_hit.py:75,490-500`: o manifesto de sombra só aceita "a bare `.dmn` filename directly inside" `spec/processes/dmn` | Aceitar `spec/processes/dmn/rco` se tabelas do K1 ganharem manifesto de sombra. |
| 11 | **Já recursivo:** `src/maezo/platform/validation/phi_completeness.py:1527` (`rglob("*")`) | Hoje ele já pegaria `rco/`: toda variável do K1 precisaria de classificação PHI, e `tests/unit/platform/test_validation_phi_completeness.py:147` (`== 70`) quebraria. Proposta: classificar o vocabulário do K1 (entradas e saídas são poucas e repetidas), sem isentar a pasta. Também já recursivo: o teste Java `src/maezo/portal/engine/java/src/test/java/br/com/maezo/human/PortalReadResourceBoundTest.java:34-43` (`Files.walk(spec/processes)`) aplica o limite de 1 MiB a todo `.dmn` e pegaria `rco/`; as tabelas do K1 têm de caber nele. |
| 12 | Empacotamento: `pyproject.toml:158` e `deploy/Dockerfile:79` copiam `spec/processes` inteiro | Nada a mudar: `rco/` vai junto. O comentário de `Dockerfile:69-71` ("16 BPMN + 62 DMN") já está desatualizado. |
| 13 | `.github/CODEOWNERS`: não há dono para a pasta inteira de DMN (comentário em `:98-99`) | Acrescentar `/spec/processes/dmn/rco/` e `/config/integrations/rco/`. |

**Contagens `== 70` que mudam quando `rco/` for publicado:** `tests/unit/platform/test_engine_deploy.py:144`, `tests/unit/platform/test_engine_deploy_cli.py:274` (`"17 BPMN, 70 DMN"`), `tests/unit/spec/test_shadow_candidates_common.py:222` e `tests/unit/platform/test_validation_phi_completeness.py:147`.

**Gates que precisam de isenção ou regra por pasta:**
- **Órfãs/crossref.** `src/maezo/platform/validation/crossref.py:30,97-110` trata órfã fora de `spec/processes/dmn/orphans-allowlist.yaml` como ERRO. As 233 decisões do K1 são consumidas por worker, sem `decisionRef`. Proposta: regra por pasta ("decisão sob `rco/` é governada pelo `rules.lock.json`"), validada contra o manifesto, em vez de 233 linhas na allowlist. Isso altera a afirmação "orphans-allowlist.yaml tem delta ZERO" do ADR-0040 (`:176`) e precisa entrar no adendo.
- **Entradas mortas.** `tests/unit/spec/test_dmn_dead_inputs_fence.py:365-372` fixa a lista exata de tabelas não-FIRST. Hoje `rco/` escapa da cerca por acidente, porque a varredura é de primeiro nível. Proposta: estender a varredura a `rco/` e aceitar `UNIQUE` sob `rco/` (o "catch-all" de UNIQUE não existe; ver (c)).
- **Aprovações.** `spec/aprovacoes-de-regras.yaml` e o seu checker (`scripts/ci/check_aprovacao_de_regras.py:173`) reprovam arquivo publicável fora do registro. Ver (g).

### (c) Hit policy: as tabelas do rco continuam `UNIQUE`

As tabelas do K1 entram como o rco publica: `UNIQUE`, sem conversão para `FIRST`. Converter seria reescrever regra do fornecedor e quebraria o `shaDmn` do pin.

- **No motor:** em `UNIQUE`, duas linhas que casam geram `DmnHitPolicyException`. Pelo transporte do Maezo, isso vira `DmnEvaluationError`, classificado como TRANSIENTE e repetível (`src/maezo/tools/workers/dmn_transport.py:94-103,378-383`). Isso está errado para este caso: sobreposição de regras não se resolve com nova tentativa. A onda de integração precisa:
  - distinguir o erro de hit policy, mapeando-o para erro permanente que roteia para a fila humana da regra e gera incidente, não retry;
  - tratar a ausência de linha casada (`DmnNoResultError`, `dmn_transport.py:509-510`) também como encaminhamento humano, porque UNIQUE não tem catch-all obrigatório.

  Fica aberta a questão Q-1 do ADR-0028 (política de resultado vazio por hit policy, `docs/adr/0028-dmn-evaluation-engine-side.md:306-309`).
- **No leitor de testes:** `tests/support/dmn_first_hit.py:305-307` recusa qualquer tabela que não seja `FIRST`, e `:299-303` exige exatamente uma decisão por arquivo. O leitor precisa de um modo `UNIQUE`: avaliar todas as linhas, falhar se mais de uma casar (espelhando o motor) e encaminhar para humano se nenhuma casar. O modo `FIRST` atual fica intacto para as tabelas do Maezo. Também é preciso ler arquivos do K1 com mais de uma decisão, se o rco publicar assim.
- **No ADR-0040:** o D4 ("FIRST com catch-all em toda tabela", `:178`) passa a valer só para tabelas do Maezo. Isso vai no adendo.

### (d) Vocabulário de saída do K1 e a emenda que o ADR-0018, parte 2, vai precisar

As saídas do K1 são `decisao`, `classeAdverso` ∈ {`clinico`, `administrativo`, `contas`}, `motivoTab38`, `idRegra` e `fila`. Uma `decisao` adversa contraria literalmente o ADR-0018, parte 2 (`:77-78`), e o ADR-0040 D4 (`:167`).

A emenda proposta (onda futura, decisão do dono 2) diz que tabela sob `rco/` pode emitir saída adversa, mas essa saída só produz efeito por um **worker guardado**:
- `classeAdverso = clinico` vai sempre para User Task do médico auditor (`authorization_denial` L0, `ERR_DENIAL_NOT_HUMAN`).
- `administrativo` e `contas` só são executadas em L2 quando a regra (`idRegra`) passou no backtest, está ligada no interruptor e não está amostrada para revisão. Caso contrário, vão para a fila humana da regra (`fila`, via K6).
- Nenhum `businessRuleTask` de BPMN termina em efeito adverso sem passar por esse worker.

Testes que hoje garantem "nenhuma saída adversa". Precisam ser **reescritos para o novo invariante, nunca apagados**. Cada um fixa uma tabela ou processo do Maezo e não varre `rco/`.

| Teste | O que garante hoje |
|---|---|
| `tests/unit/spec/test_glosa_triage_shadow_candidate.py:381-394` | domínio de `glosa_triage` == `{PAGAR, ANALISE_HUMANA}` (`:389`); candidata sem "GLOSAR"/"PAGAR_PARCIAL" (`:392`) |
| `tests/integration/processes/test_sp_op_contas_001.py:292-294,803-847` | nenhum dos 48 casos chega a `End_GlosaAplicadaHumano`/`End_PagamentoParcialHumano` sem User Task |
| `tests/unit/sec/test_auto_approval_criteria_fence.py:203-227` | `auth_auto_approval` só emite `{AUTO_APROVAR, ANALISE_HUMANA}`; FIRST; catch-all humano |
| `tests/unit/agents/test_agent_fleet_autonomy_fences.py:101-290` | nenhum agente declara ação hard; nenhuma operação adversa alcançável pelo PEP |
| `tests/unit/tools/workers/test_auth_denial_guard.py:172-194,849-871,1030` | `ERR_DENIAL_NOT_HUMAN`; `analyze_request` nunca nega |
| `tests/unit/gateway/test_hard_items_guard_map.py:48-73,124-166` | todo item hard tem `ERR_*_NOT_HUMAN` no worker e no BPMN |
| Outros encontrados: `tests/unit/tools/workers/test_auth_auto_criteria.py:524-530`, `tests/unit/tools/workers/test_adequacao.py:1062-1068` e as varreduras de integração da parte 5 do ADR-0018 (`tests/integration/processes/test_sp_op_recurso_001.py:613`, `test_sp_op_cred_001.py`, `test_sp_op_adequacao_001.py:1437`) | nenhuma saída `NEGAR`/`NEGADO` automática |

Também falta uma **cerca genérica nova**, porque hoje nenhuma existe: toda saída `decisao` adversa de tabela sob `rco/` tem `classeAdverso` preenchida, e todo consumidor dessa saída é o worker guardado.

### (e) Sombra: por que `auto_criteria_shadow` não basta e o registro por `idRegra`

O `auto_criteria_shadow` (`src/maezo/tools/workers/auth.py:904-940`, montado em `:943-986`) tem quatro limites para 233 decisões:
- grava **um token por critério** (`*_SOMBRA_APROVARIA|REPROVARIA|INDETERMINADO`), sem id de regra, versão de tabela ou entradas;
- só existe na autorização automática;
- fica como variável de processo e linha de log; só `motivo_bloqueio_criterios` chega à trilha do ADR-0007 (`auth.py:982-985`);
- a ratificação é tudo ou nada por conjunto de tabelas consultadas (`auth.py:930`).

Não mede concordância com a decisão humana, que é o que o backtest e o desligamento por regra exigem.

**Desenho proposto: registro de sombra por `idRegra`.** É uma linha append-only por avaliação, com:
- `idRegra`, `versaoRegra`, `shaDmn`;
- `decisao`, `classeAdverso`, `motivoTab38`, `fila`;
- o caso (chave de negócio pseudonimizada, Zona Geral);
- a decisão humana que de fato encerrou o caso, ligada depois pelo desfecho da User Task;
- `concordou` ∈ {sim, não, sem desfecho}.

O registro alimenta o K5 (desfechos mzo→adp→rco) e o painel de concordância por regra.

**Interruptor:** `spec/processes/dmn/auth-criteria-ratification.yaml` já tem a semântica certa:
- liga só com `ratificado: true` literal, mais `revisor` e `ratificado_em` (`src/maezo/tools/workers/auth_criteria.py:78-80,220-236`);
- falha de carga = nada ratificado (`auth_criteria.py:32-44`).

Ele é estendido (ou ganha um irmão com o mesmo leitor) para chave por `idRegra` e `versaoRegra`, com os estados `sombra` | `ativa_l2` | `desligada`. Mudar o estado de uma regra é mudança de dado, como já é hoje (`auth_criteria.py:14-15`).

### (f) Trilha de auditoria: estender `dmn_versions`

Hoje, `DmnVersion.to_audit_dict` grava `{version, id, deploymentId}` por tabela consultada (`src/maezo/tools/workers/dmn_transport.py:157-172`), em `AuditRecord.dmn_versions` (`src/maezo/gateway/audit.py:222`). Não grava a linha casada, as saídas nem o hash da origem.

Para decisão do K1, a entrada do `dmn_versions` ganha três campos opcionais:
- `idRegra`: da saída da tabela;
- `versaoRegra` e `shaDmn`: do `rules.lock.json` verificado no deploy, não do motor;
- `sha256Yaml`: o `regras[].sha256_yaml` do manifesto K1 pinado para o `idRegra` da linha casada, no formato `sha256:<hex>` do pacote. É o hash da fonte YAML da regra no rco (contrato-k1 §3) e responde "qual texto de regra decidiu" mesmo quando duas versões da regra compilam no mesmo DMN. Só aparece junto com `idRegra`; linha sem `idRegra` (por exemplo, o `liberar` padrão com `idRegra = ""`) grava só `shaDmn`.

**Emenda de 16/11/2026 (Programa Circuito Fechado, Onda 2, pedido do contrato-k1 §11 item 2).** O campo `sha256Yaml` acima foi acrescentado depois da redação original, que previa só três campos. O leitor do manifesto (MZO-2a, #1244) já expõe `sha256_yaml` por regra em `RcoPinnedTable.regras`, mas não o grava em `dmn_versions` enquanto esta seção não for aceita. A gravação é a mesma regra das outras chaves: opcional, só para decisão de `rco/`, sem mudar as chaves atuais.

As chaves atuais ficam como estão, para não quebrar leitores. Os campos só aparecem quando a decisão vem de `rco/`. Mudar o formato do registro exige revisar o verificador de cadeia (ADR-0007) e os testes de forma de `AuditRecord`.

### (g) Aprovação: o que teria de mudar

Hoje, dois arquivos pedem aprovações que contradizem a decisão 3 do dono (ratificação = dono + auditor neutro; médico, jurídico e DPO assíncronos):
- `spec/aprovacoes-de-regras.yaml` (DL-0148): aprovação por artefato, presa ao sha256, com papéis `medico-auditor|juridico|dpo|regulatorio|financas|po|dono`. Pendente não bloqueia o deploy, mas um arquivo fora do registro bloqueia (`scripts/ci/check_aprovacao_de_regras.py:173`). Com 233 arquivos, isso vira 233 entradas.
- `spec/policies/autonomy/action-approvals.yaml:526-550` (`avaliacao_dmn`): `dominios_exigidos: [medica, ans, seguranca]` (`:546`), todos `aprovado: false`. O arquivo está em `status: DRAFT` (`:73`) e `modo: shadow` (`:99`).

O que teria de mudar:
1. **Aprovação em lote por versão do manifesto do K1.** Uma entrada por `sha256` do `rules.lock.json`, com papéis `dono` + `auditor-neutro`. O checker passa a aceitar essa entrada como cobertura de todos os arquivos listados no pin.
2. Médico, jurídico e DPO viram campos de revisão assíncrona, registrados sem bloquear.
3. Em `avaliacao_dmn`, os domínios exigidos para tabela sob `rco/` seguem a mesma regra.

**Este ADR não altera esses arquivos.** É decisão pendente do dono (P7). `spec/policies/autonomy/` está fora do escopo desta onda.

### (h) K6: filas do rco × `camunda:candidateGroups` do Maezo

Os nomes nunca coincidem literalmente: o rco usa `snake_case` e o Maezo usa `kebab-case`. O registro canônico do Maezo está em `src/maezo/platform/engine_bootstrap/bootstrap.py:41-98`.

| Fila rco | Grupo(s) Maezo | Exemplo | Par |
|---|---|---|---|
| `auditoria_medica` | `medico-auditor` (+ `coordenacao-auditoria-medica`) | `SP-OP-AUTH-001_Autorizacao_Previa.bpmn:193` | sim |
| `auditoria_enfermagem` | — (o mais próximo, `enfermagem-triagem` em `bootstrap.py:43`, é fila de triagem/escalonamento) | — | **não** |
| `junta_medica` | `junta-medica` | `SP-OP-AUTH-001_Autorizacao_Previa.bpmn:573` | sim |
| `regulacao` | — (`regulatorio-ans`/`coordenacao-regulatorio` são regulatório ANS, não regulação assistencial) | — | **não** (falso amigo) |
| `cadastro` | `cadastro-beneficiarios` | `SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:143` | sim |
| `faturamento` | `auditoria-contas`/`coordenacao-contas` (glosa); `analise-contas-pagar` (pagamento) | `SP-OP-CONTAS-001_Processamento_Contas_Glosa.bpmn:190` | parcial (1→n) |
| `juridico` | `juridico-contratos`/`-rede`/`-fraude`/`-regulatorio`/`-privacidade` | `SP-OP-CANCEL-001_Cancelamento_Contrato.bpmn:384` | parcial (1→5, por domínio) |

Grupos do Maezo sem fila do rco (são "como" puro):
- reembolso: `analise-reembolso`, `coordenacao-reembolso`;
- recurso de glosa: `analista-recurso-glosa`, `coordenacao-recurso`;
- coordenação: `coordenacao-clinica`, `coordenacao-cobranca`, `coordenacao-contratos`, `gestao-contratos`, `coordenacao-financeira`;
- financeiro: `aprovacao-financeira-l1/l2/l3`, `comite-financeiro`;
- fraude: `coordenacao-investigacao`, `investigacao-fraude`;
- rede: `coordenacao-rede`, `gestao-rede`;
- regulatório: `coordenacao-regulatorio`, `gerencia-regulatoria`, `regulatorio-ans`;
- atendimento e privacidade: `ouvidoria`, `dpo`, `plantao-clinico`, `enfermagem-triagem`, `atendimento-humano`, `supervisao-atendimento`.

Proposta:
- O K6 é um arquivo versionado de correspondência fila→grupo, publicado pelo rco e fixado pelo Maezo.
- As duas filas sem par (`auditoria_enfermagem`, `regulacao`) exigem decisão do dono: criar grupo novo no Maezo ou mapear para um grupo existente.
- As correspondências 1→n precisam de uma chave de desempate (domínio ou processo) no próprio K6.

### (i) O que NÃO muda

- Os 5 itens hard de `spec/policies/autonomy/_hard_frozen.yaml:17-31`: `clinical_decision`, `authorization_denial`, `nip_manter_negativa`, `fraud_accusation` e `contract_termination`. A cópia congelada no código fica em `src/maezo/gateway/pep.py:71-79`.
- `authorization_denial` continua L0 hard (`spec/policies/autonomy/L0-core.yaml:7`).
- "Negativa clínica = médico auditor": `classeAdverso = clinico` nunca é executada por máquina, em nenhum nível.
- O ADR-0018 e o ADR-0040 continuam em vigor sem mudança até a emenda e o adendo próprios serem aceitos. Até lá, nenhuma saída adversa do K1 produz efeito.

## Consequências

**Positivas:**
- Uma única fonte de regra "o quê", versionada e com hash, fixada pelo consumidor.
- Cada decisão auditada passa a apontar para a regra exata (`idRegra`, `versaoRegra`, `shaDmn`).
- A fronteira e a lista de pontos a mudar ficam fixadas antes do código, com o custo conhecido.

**Negativas (aceitas):**
- A chegada do K1 mexe em pelo menos 13 pontos de varredura, 4 contagens `== 70`, 3 gates de pasta e 6+ testes de invariante, que precisam ser reescritos com cuidado.
- Duas hit policies convivem (FIRST no Maezo, UNIQUE no rco), com tratamento de erro diferente.
- O interruptor por regra e o registro de sombra são componentes novos.
- As filas `auditoria_enfermagem` e `regulacao` ficam sem destino até decisão do dono.

**Pendências do dono:**
- aceitar este ADR;
- P7 (aprovação em lote, item g);
- grupos para as filas sem par (item h);
- emenda ao ADR-0018 e adendo ao ADR-0040 (decisão 2).

## Supersedes

—. Este ADR não substitui nenhum. A emenda ao ADR-0018 e o adendo ao ADR-0040 serão ADRs próprios.
