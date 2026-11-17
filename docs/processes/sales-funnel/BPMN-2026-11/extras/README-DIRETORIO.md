# README — pasta de entrega: diagramas BPMN do funil de vendas (nível 1)

Missão §10 · 14 diagramas descritivos (E01–E09, F01–F04, E2E) · Sem rede · Sem nomes de pessoa/empresa.

## 1. COMO ABRIR

- **Camunda Modeler 5 ou superior**: menu *File → Open File* e escolha qualquer `diagrams/FUNIL-*.bpmn`. Os arquivos são BPMN 2.0 XML puro de colaboração (pools/raias), validados contra os XSD oficiais da OMG (cópia local em `tools/xsd/`).
- **bpmn.io demo**: abra https://demo.bpmn.io e arraste o arquivo `.bpmn` para a janela.
- **Sem instalar nada**: imagens já renderizadas em `render/<CODIGO>/` — `svg/FUNIL-*.svg` (vetorial) e `png/FUNIL-*.png` (2x; com `*-preview.jpg` ao lado em algumas pastas). Cada pasta tem também `render-report.json` com o resultado do harness.
- **Planilha**: `extras/MATRIZ-RASTREIO.csv` — UTF-8, separador `;`, abre em Excel/LibreOffice/Numbers.

## 2. O QUE SÃO

14 diagramas **descritivos de nível 1** (`isExecutable="false"`): modelagem de referência do funil, com portões de ratificação fechados e caminhos conservadores ativos. Nenhum processo aqui é executável por engine. Cada elemento relevante carrega `bpmn:documentation` com épico (E-SF-nn), norma com linha da baseline e portão — tudo extraído em `extras/MATRIZ-RASTREIO.csv` (484 elementos: 296 ATIVO, 109 FECHADO, 79 VARIANTE).

| Arquivo (`diagrams/`) | Cobertura (1 linha) |
|---|---|
| `FUNIL-E01-entrada-com-preco.bpmn` | Etapa 1 — vitrine pública com catálogo e preço (portão A1/RL-SF-001 fechado; registro ANS ativo como fato de fonte; vedação de "a partir de"). |
| `FUNIL-E02-qualificacao.bpmn` | Etapa 2 — formulário de schema fechado (≤5 campos), cerca "zero saúde" vigente, sessão de prospect sem conta sob A2/RL-SF-002; consulta por CPF fechada. |
| `FUNIL-E03-cotacao-e-comparador.bpmn` | Etapa 3 — comparador só orientação (sem ranking), composição de preço só da própria operadora (RL-SF-019), "preço válido até" como GAP-DMN-PV1. |
| `FUNIL-E04-proposta-e-termos.bpmn` | Etapa 4 — tela de proposta, recibo OP21 e protocolo por e-mail (EM-01/RL-SF-010/011/012 fechado); legitimidade de coletivo fechada (RL-SF-020/RL-REG-08). |
| `FUNIL-E05-aceite.bpmn` | Etapa 5 — aviso prévio (dever vigente), declarações como atos separados, assinatura na forma admitida (A3: RL-SF-027/028). |
| `FUNIL-E06-contrato-e-pagamento.bpmn` | Etapa 6 — execução na fonte, porta de pagamento com boundary de erro (pagamento incerto nunca recriado/trocado), relógio de 25 d com 26º dia automático (T0 fechado — RL-SF-038). |
| `FUNIL-E07-declaracao-de-saude.bpmn` | Etapa 7 — carta de orientação antes da DS, entrevista digital, custódia clínica isolada em raia própria; ponte CPT por decisão humana (médico auditor). |
| `FUNIL-E08-admissao-e-acompanhamento.bpmn` | Etapa 8 — rastreador de 7 estágios, catálogo de prazos (DMN), aviso de estouro; emite os links E08-ES1..7 para F1; medição A9 fechada. |
| `FUNIL-E09-pos-venda.bpmn` | Etapa 9 — arrependimento 7 d (contagem parametrizada RL-SF-015), cancelamento em 2 toques com protocolo, reembolso urgência/emergência P30D; nenhuma mensagem proativa (RT-D4). |
| `FUNIL-F01-mediacao.bpmn` | Frente 1 — pontos de medição dos 7 estágios; A9 admitida (OS-04) e NÃO executável (assinatura do encarregado pendente; nada emite evento). |
| `FUNIL-F02-recuperacao-e-mensagens.bpmn` | Frente 2 — captura do aceite único (OS-05) e recuperação de abandono; envio proativo fechado (RL-SF-066); encerra antes dos 25 dias; zero dado de saúde. |
| `FUNIL-F03-canal-de-vendas.bpmn` | Frente 3 — papel comercial humano, canal do corretor (link/QR, faixa neutra), comissão sem valor (GTM-02/J1+J2+D1); legitimidade coletiva = ratificação mais urgente. |
| `FUNIL-F04-retencao.bpmn` | Frente 4 — renovação e reajuste; avisos proativos fechados (RT-J1 — RL-SF-104/105); hoje o boleto é o veículo do valor; links com a etapa 9. |
| `FUNIL-E2E-ponta-a-ponta.bpmn` | Ponta a ponta — costura 1→2→3→4→7(DS)→5→6→8→9 com as fronteiras fechadas e os remetimentos por link às frentes F1–F4. |

## 3. LEGENDA DE CORES

Cores só em `bioc:stroke`/`bioc:fill`; a informação nunca está só na cor (anotação de texto acompanha).

- **Vermelho = portão fechado**: aguarda ratificação (jurídico / DPO / dono). O caminho conservador desenhado é o que roda — nenhum caminho ativo decide ponto jurídico, de DPO ou de deploy.
- **Azul = custódia clínica** (dado de saúde isolado, missão §6): `dataStoreReference` em raia própria, sem nenhuma associação ou message flow para raias comerciais, medição, mensagens/recuperação ou retenção.

## 4. LEGENDA DE PORTÕES

Formato dos ids citados na documentação dos elementos (e na coluna `portoes` do CSV):

| Família | Formato | Significado |
|---|---|---|
| Regra do funil | `RL-SF-nnn` | Regra ratificável do funil de vendas (ex.: RL-SF-001 = portão A1). |
| Registro regulatório | `RL-REG-nn` | Registro de apoio ao portão (ex.: RL-REG-08 com RL-SF-020). |
| Portão de decisão | `A1` … `A10` | Portões-A (A1 vitrine, A2 sessão, A3 forma do aceite, A4 cobertura, A9 medição…). |
| Ordem de serviço | `OS-nn` (ex.: OS-04, OS-05) | Decisão de escopo da missão; `Q-OS05(-n)` = questionário do OS-05. |
| Ratify-later | `RT-Jn` (jurídico), `RT-Dn` (dono) | Perguntas em ratificação (ex.: RT-D4 = nenhuma mensagem proativa a beneficiário). |
| Guardas | `GT-n`, `GTM(-nn)` | Guardas de teste / guarda GTM (ex.: GT-2, GTM-07). |
| Sucessor de contratos | `SC-nn` (com `.Dn`/`.Jn`/`.O1` quando por papel) | Pergunta ao sucessor do contrato C6-x (ex.: SC-03.D1). |
| E-mail | `EM-01` | Transporte de e-mail de protocolo (RL-SF-010/011/012). |
| Decisão | `DEC-15` | Decisão de provedor de pagamento (com RL-SF-029). |

**Quem abre cada portão (jurídico / DPO / dono-deploy), o fallback conservador e o estado oficial de cada um**: ver **`PORTOES.md`** nesta pasta (tabela completa com fonte e diagramas onde cada portão aparece).

## 5. LINKS ENTRE ARQUIVOS (contratos de link — convenção missão §5.2: a frente LIGA às etapas, não duplica tarefas)

Resultado do parse dos `linkEventDefinition` dos 14 arquivos (2026-11-17):

| Contrato | Origem (throw) | Destino (catch) | Estado do par |
|---|---|---|---|
| `E08-ES1` … `E08-ES7` | E08 `Event_EmiteES1..7` (rastreador, Lane_Cadastro) | F01 `Event_LinkES1..7` | **Par completo nos dois arquivos** (fato de conclusão de estágio; NÃO é emissão A9). O E2E reimite `E08-ES1` como amostra. |
| `F1-EMITE-ES1` … `F1-EMITE-ES7` | F01 `Event_Emite_ES1..7` | — | Throw sem catch (medição A9 fechada: nada consuma o evento). |
| `ABANDONO-E02` … `ABANDONO-E06` | — nos arquivos E02–E06 (abandono como fim nomeado); o E2E reimite `ABANDONO-E02` | F02 `Event_AbandonoE02..06` | Catch completo na frente; **lado origem ainda não declarado** nos arquivos das etapas (documentado na F02 como par previsto). |
| `E01-HUMANO`, `E02-HUMANO`, `E03-HUMANO`, `E05-HUMANO`, `E02-ORIGEM` | — (etapas) | F03 `Event_LinkE01Humano` etc. | Catch presente na F03; lado origem não declarado nos arquivos das etapas. |
| `E04-LEG-COLETIVA` | — em E04; o E2E reimite `E04-LEG-COLETIVA` (PJ × tela de legitimidade fechada) | F03 `Event_LinkE04Leg` | Catch presente; **lado origem ainda não declarado** em E04. |
| `F3-LEG-COLETIVA-VALIDA` | F03 `Event_F3LegValida` (recibo *valid* devolve à etapa 4) | — em E04 | Throw presente; catch não declarado (variante não ativa). |
| `F3-CORRETOR-ENTRADA` | — | F03 `Event_LinkCorretorEntrada` | Catch presente (variante não ativa). |
| `ETAPA9-CICLO-VIGENTE` | E09 `Event_ThrowE9CicloVigente` | F04 `Event_LinkE9Ciclo` | **Par completo** (ciclo vigente abre renovação/reajuste). O E2E reimite como amostra. |
| `F4-ETAPA9-RENOVACAO` | F04 `Event_ThrowRenovacaoE9` | E09 `Event_LinkF4Renovacao` | **Par completo** (renovação efetivada remetida à etapa 9). |
| `F4-ETAPA9-CANCELAMENTO` | F04 `Event_ThrowCancelE9` | E09 `Event_LinkF4Cancelamento` | **Par completo** (pedido protocolado remetido à etapa 9). |

## 6. LISTA DE GAPs

Consolidado das seções GAP de `REG-NOTAS/*.md` e dos achados de `REVISAO-CEGA/*-VEREDITO.md` — número, o que foi buscado/encontrado e onde está registrado:

| # | GAP | O que foi buscado / o que falta | Onde está registrado |
|---|---|---|---|
| 1 | GAP-1 (residual) | Norma ANS de faixas etárias ("7 faixas") não localizada; localizador "MP art. 5º XVIII–XX" refutado | Baseline L360; `REG-NOTAS/ETAPAS-01-02-03.md` §(d) |
| 2 | GAP-3 (residual) | Pin DOU da RN 593/2023 (inadimplência) ausente; padrões em `cancel_sla.dmn:60` e `SP-OP-CANCEL-001.md:9` | Baseline L286/L363; `REG-NOTAS/ETAPAS-07-08-09.md` §(d) |
| 3 | GAP-4 (residual) | RN 590/2023 (alteração reportada da RN 500/SIB) não pinada; leitura por RL-REG-28 | Baseline L302/L364; `REG-NOTAS/ETAPAS-07-08-09.md` §(d) |
| 4 | GAP-8 / NORM-GAP-8 (alias GAP-NORM-2) | Gatilho do relógio de 25 d (RN 413 art. 5º §3º) indefinido; DMN `evento_ancora_ref` vazio | Baseline L368; RL-SF-038; `REG-NOTAS/ETAPA-06.md` §(d) e `ETAPAS-07-08-09.md` §(d); vereditos E06/E08 |
| 5 | GAP-9 | Forma da Carta de DS (RN 558 Anexo): texto fechado no DOU; forma TNR 12/digital aberta | Baseline L369; RL-SF-039/RL-REG-33; `REG-NOTAS/ETAPAS-07-08-09.md` §(d); `REVISAO-CEGA/E07-VEREDITO.md` |
| 6 | GAP-10 | Guia de Orientação (RN 413 art. 18) não localizado; disclosure limitado aos incisos I–IX e §1º | Baseline L370 (ABERTO); `REG-NOTAS/ETAPAS-01-02-03.md` §(d); `REVISAO-CEGA/E01-VEREDITO.md` |
| 7 | GAP-2r | RN 558 art. 5º caput com duas redações oficiais divergentes (DOU 30/12/2022 × cópia SEI) | Baseline L161/L362; RL-REG-26; `REG-NOTAS/ETAPAS-07-08-09.md` §(d) |
| 8 | GAP-B1 | Texto de ciência do resumo dos termos (declaração separada do aceite) sem fonte localizada | `REVISAO-CEGA/E05-VEREDITO.md` |
| 9 | GAP-DMN-PV1 | Regra de prazo "preço válido até" inexistente no `funnel_deadline_catalog.dmn` (0 hits "validade"; regras em L47/L66/L85/L104) | `REG-NOTAS/ETAPAS-01-02-03.md` §(d); `REVISAO-CEGA/E03-VEREDITO.md` |
| 10 | GAP-E45-1 | CDC arts. 46–47 não pinados na baseline §2.6 (o par "CDC 39/46-49" não se sustenta) | `REG-NOTAS/ETAPAS-04-05.md` §(d) |
| 11 | GAP-E45-2 | E-SF-62 sem linha no `EPICS-PLAN.tsv` (fonte correta: ADDENDUM-01 + EXECUTION-LOG) | `REG-NOTAS/ETAPAS-04-05.md` §(d) |
| 12 | GAP-E45-3 | Momento do aviso prévio (RN 413 art. 7º §2º, L123) no funil digital sem âncora; RL-SF-028 e RL-SF-052/TB-06 em ratificação | `REG-NOTAS/ETAPAS-04-05.md` §(d); `REVISAO-CEGA/E05-VEREDITO.md` |
| 13 | GAP-WIRE | `funnel_deadline_catalog` sem consumidor nesta onda (campo novo no sucessor do C6-4 — CD-24); nenhum ETA emitido | DMN L23–24; `EXECUTION-LOG.md:126`; `REG-NOTAS/ETAPAS-07-08-09.md` §(d); `REVISAO-CEGA/E08-VEREDITO.md` |
| 14 | GAP-NORM-1 (PSP) | Nenhuma norma da baseline v2 sobre provedor de pagamento (2 hits, ambos não-PSP: L118/L120) | `REG-NOTAS/ETAPA-06.md` §(d) |
| 15 | GAP-NORM (RN 561 art. 15) | Incisos I e III–VI da RN 561 só em paráfrase na baseline (L293) | Registrado em `RT-J2.md` (adendo de ratificação); citado em `REG-NOTAS/ETAPAS-07-08-09.md` §(d) |
| 16 | GAP-NORM (RN 518/2022 disclosure) | Dispositivo de disclosure do canal/intermediário sem pin; âncora herdada é só o report operadora→ANS (L321; conclusão L323) | `REG-NOTAS/FRENTES-01-04.md` §(e) F3; RL-SF-082/083; `REVISAO-CEGA/F03-VEREDITO.md` |
| 17 | GAP-NORM (reajuste/SAC/LGPD) | LGPD arts. 5º XII, 8º e 14/15 não pinados; reajuste sem norma (GAP-RT-01..03); Decreto 11.034/2022 não capturado (GAP-RT-07) | `REG-NOTAS/FRENTES-01-04.md` §(e) F4; ADDENDUM-01 L422; RT-J5 |
| 18 | GAP-ENCARREGADO | Ato de indicação do encarregado não assinado; bloqueia registro A9, perna DPO do A6 e contra-assinaturas | `REG-NOTAS/FRENTES-01-04.md` §(e) F1; ADDENDUM-01 L397/L417 |
| 19 | GAP-DIM-A9 | Dimensões de medição (SC-10, RT-09, E-SF-55r) que OS-04 não cobre → Q-OS04-2 | `REG-NOTAS/FRENTES-01-04.md` §(e) F1; ADDENDUM-01 L419 |
| 20 | GAP-C6X-CLASSE | Classe do ato de publicar o sucessor C6-x indefinida | `REG-NOTAS/FRENTES-01-04.md` §(e) F1; ADDENDUM-01 L420 |
| 21 | GAP-OS-LOG | OS-04 a OS-08 fora do `MISSION-LOG.md`; valem como relatadas | `REG-NOTAS/FRENTES-01-04.md` §(e) F1; ADDENDUM-01 L414 |
| 22 | GAP-CONSENT-ADMISSAO | Termo de consentimento de contato colhido na admissão não existe (só `wa-consent-v2`, restrito a "nesta conversa"); fecha com a minuta E-SF-62 | `REG-NOTAS/FRENTES-01-04.md` §(e) F2; ADDENDUM-01 L415; RL-SF-093(ii)+OS05-J1 |
| 23 | GAP-BENEF-LEGADO | Beneficiários já admitidos sem consentimento de contato demonstrável; default Q-BEN-1 (sem contato proativo) | `REG-NOTAS/FRENTES-01-04.md` §(e) F2; ADDENDUM-01 L416/L391 |
| 24 | GAP-SC-SUCESSOR | `amh-data-platform` não clonado na sessão de derivação (SC §5); caminhos de arquivos podem ter derivado | `REG-NOTAS/FRENTES-01-04.md` §(e) F3; ADDENDUM-01 L421 |
| 25 | GAP-SC-OMNI | omni lido só pelo snapshot do pacote naquela sessão | `REG-NOTAS/FRENTES-01-04.md` §(e) F3; ADDENDUM-01 L421 |
| 26 | GAP-RT-06 | Casa da tela "Meu plano"; ADR-0049 não implementado; default Q-RT-1 (portal provisório) | `REG-NOTAS/FRENTES-01-04.md` §(e) F4; ADDENDUM-01 L418/L385 |
| 27 | GAP-LINK-E08 | Par de link E08-ES1..7 entre F1 e E8 — **RESOLVIDO**: throws e catches presentes nos dois arquivos (conferido por parse nesta entrega, ver §5) | Instrução de registro em `REVISAO-CEGA/F01-VEREDITO.md` |

## 7. VERIFICADOR

Todos os comandos a partir do diretório `tools/` (sem rede; node_modules e XSDs já são locais):

```bash
cd tools

# Verificação completa de TODOS os diagramas (XSD OMG + cobertura DI nas duas direções +
# geometria + domínio [custódia clínica, timers ISO, portão com default] + bpmnlint):
python3 di_verifier.py --dir ../diagrams --json ../render/di-report.json

# Um arquivo específico:
python3 di_verifier.py ../diagrams/FUNIL-E01-entrada-com-preco.bpmn

# Render REAL com bpmn-js + Chromium headless (SVG + PNG 2x + render-report.json):
node render_bpmn.mjs ../diagrams --out ../render
```

Exit codes do `di_verifier.py`: `0` = sem achado fail; `1` = há achado fail; `2` = erro de uso/interno. O lint (check 5) usa `npx --no-install bpmnlint`; se indisponível, o check marca UNVERIFIED (não é fail). Opções úteis: `--skip-lint`, `--fail-on-warn`. Requisitos: `python3` com `lxml` instalado.

## 8. AVISO

- Estes 14 diagramas são **descritivos** (`isExecutable="false"`): **nada aqui é executável por engine**. São modelo de referência para ratificação e para o nível 2.
- Nenhum caminho ativo decide ponto jurídico, de DPO ou de deploy: onde há portão, o default desenhado é o caminho conservador (funil parado, sem dado, sem envio, sem cobrança).
- **Processos nível 2 (SP-OP-\*)**: os 17 existentes em `maezo-main/spec/processes/bpmn/` são apenas **apontados** nos diagramas (call activity/anotação — ex.: SP-OP-AUTH-001 em E07, SP-OP-PAGTO-001 em E06, SP-OP-REEMBOLSO-001 e SP-OP-SUPP-001 em E09/F02/F04). Nenhum executável foi criado nesta entrega; os faltantes estão propostos em **`LISTA-SP-OP-FALTANTE.md`**.
- `MATRIZ-RASTREIO.csv`: 484 elementos (1 linha por elemento com `bpmn:documentation`; não por citação). Nota de leitura: a categoria GATED não ocorreu — neste corpus todos os ramos que dependem de ratificação estão declarados como "variante não ativa" nos `conditionExpression` e por isso figuram como VARIANTE.
