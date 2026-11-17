# COORD-OUT — W2-MZO-CA (maezo-operadora)

Canal janela → coordenador. Só acréscimo. Horas em UTC.

## 2026-11-17 ~15:20Z — abertura

- Acesso de leitura ao pacote: OK (ramo do pacote lido; hashes dos seis arquivos conferem com o brief comum).
- Lidos: brief comum inteiro; CD-52 a CD-76; cédula conferida inteira (cópia de CD-62); CD-08, CD-44, CD-49 e CDs da W-MZO-CHAN.
- Base: main `208ec575f5c6c678881e5f43aba082452b11afae`.
- Fila (CD-76), em série: (1) telemetria do agente comercial + GTM-E-11; (2) ADD01-02; (3) E-SF-21 só GTM-E-63; (4) E-SF-23; (5) E-SF-15; (6) E-SF-22r; (7) E-SF-16 + 16r (depois de E-SF-07).

## 2026-11-17 ~15:45Z — Q-W2-MZO-CA-01 (linha 1, telemetria do agente comercial)

Conflito: a linha pede que o agente comercial emita o desfecho do turno; os limites mandam que nenhum evento de medição seja emitido (A9 não executável) e o próprio vocabulário do agente tem `saida_clinica` (sinal de saúde virando rótulo de métrica).
- (A) recomendada: ligar a emissão no grafo (nó terminal `finalize` chama `emit_turn_desfecho`, cerca de frota passa a cobrir o agente, rota com vocabulário fechado) e fechar por construção no ponto único de emissão: conjunto fechado de agentes sem medição admitida = {comercial}; o turno valida e normaliza e NÃO grava no contador. Teste afirma zero emissão. Abrir = PR próprio, depois da decisão do encarregado sobre o rótulo `saida_clinica` (classe ii).
- (B) emitir já a telemetria operacional. Descartada: contraria o limite.
Seguindo por (A), marcado `PROVISÓRIO-aguarda-coordenador`.

## 2026-11-17 ~15:45Z — Q-W2-MZO-CA-02 (linha 1, GTM-E-11, onde mora o módulo compartilhado)

Mover o consumidor administrativo para fora de `src/maezo/agents/lucas/` exige editar a baseline da catraca de fronteira (`scripts/ci/fronteira_agentes_baseline.txt`), a lista do SDK (`src/maezo/contratos/sdk.py`) e importadores fora da minha posse (`gateway/tool_registry.py`, `platform/integrations/agent_resume.py`).
- (A) recomendada: EXTRACT no lugar — a identidade (id do agente, prefixos de checkpoint, versões de grafo) e o conjunto de operações viram parâmetro (`AdministrativeIdentity`); o Lucas é a instância padrão, byte-idêntica; nenhum literal de identidade no módulo comum; nenhum importador muda.
- (B) mudança física de pacote. Fica para linha própria com `CAMINHO` dos arquivos acima.
Seguindo por (A), marcado `PROVISÓRIO-aguarda-coordenador`.

ACHADO: o catálogo GT-GTM-018/099 não existe no pacote (busca por id em todo o ramo); só os testes nomeados no bloco entram, com as cercas.

## 2026-11-17 ~16:55Z — linha 1, estado

- CD-79 e CD-80 lidas. Ramo `sf-w2/mzo-ca/telemetria-gtm-e-11` contém a main `dfaf6e6c`. Revisão independente: PASS (sem reparo exigido).
- Suíte unitária cheia local: 446 falhas/erros; 441 iguais na main limpa (clone raso, sem `helm`, root). 5 em `tests/unit/portal/test_admin_read_api.py` só apareceram na rodada cheia do ramo; passam sozinhas e com `tests/unit/portal` inteiro; a combinação com cada pasta (ci, deploy, dev, platform, runtime, gateway) não reproduz. Rodando a suíte cheia sobre a main limpa para decidir entre ACHADO (dependência de ordem preexistente) e defeito da linha. PR só depois.

## 2026-11-17 ~17:05Z — linha 1, PR aberto

- maezo-operadora #1299 aberto (ramo `sf-w2/mzo-ca/telemetria-gtm-e-11`, contém a main `dfaf6e6c`). CI rodando; pronto depois do verde.
- ACHADO: 5 testes de `tests/unit/portal/test_admin_read_api.py` falham só na ordem da suíte unitária cheia, IGUAL na main limpa (rodada cheia local sobre a main); passam sozinhos e com `tests/unit/portal` inteiro; bisseção por pasta não reproduz. Não corrigidos nem pulados; nenhuma falha local exclusiva do ramo.
- `saida_clinica` (rótulo derivado de sinal de saúde no vocabulário do agente comercial): id para a coordenação do programa, ponto do encarregado (CD-79 item 2).

## 2026-11-17 ~18:10Z — linha 2 (ADD01-02) pronta em ramo, sem PR

- Ramo `sf-w2/mzo-ca/add01-02`: módulo novo `src/maezo/portal/api/specialist_console.py` (não montado; só `work.*` e `journey.get`; visão da jornada recusa com `PHI_IN_COMMERCIAL_INPUT`; compromisso N só com 2 donos e ato, registro `NAO_RATIFICADO`, número nunca exibido; papéis condução × cadastro). Revisão independente: FAIL (tipos opacos/profundidade) → reparo literal → PASS. PR só depois do merge do #1299.
- Console web (TS) e horário de atendimento: não construídos (GAP: não há configuração de horário; Q-SC-1 pendente).
- ACHADO (detector de saúde compartilhado `gateway/communications/whatsapp_templates.contem_dado_de_saude`): termos clínicos fora da lista passam (ex.: gestação, neoplasia) e "J 45" com espaço passa; refs curtas como "j10" casam com o padrão de CID (falso positivo, fecha a favor da segurança). Não alterado nesta linha (afeta o despachante); pede linha própria.

## 2026-11-17 ~18:35Z — linha 1, CI

- #1299 no head `4205a632…`: 42 checks verdes; integração lida do junit (5 shards: 958 + 138 + 130 + 129 + 56 = 1411 testes, 0 falha, 0 erro; inclui os testes PG do runtime administrativo do Lucas).
- A main andou para `a5fedc8e` (#1297), arquivos disjuntos dos meus. Incorporei a main (head `818f4db0bd148c7d7b125fd14fa70fe639e4a48a`) e reabri o CI; pronto depois dele.

## 2026-11-17 ~19:20Z — PRONTO maezo-operadora #1299 head 818f4db0bd148c7d7b125fd14fa70fe639e4a48a checks verdes (integração lida do junit)

- Integração: 5 shards, 958 + 138 + 130 + 129 + 56 = 1411 testes, 0 falha, 0 erro. `flip-path-review-gate` verde.
- Arquivos tocados (9): `src/maezo/agents/comercial/graph.py`; `src/maezo/agents/lucas/administrative/{graph,identity,runtime,state}.py`; `src/maezo/runtime/turn_telemetry.py`; `tests/unit/agents/test_gtm_e11_administrative_identity.py`; `tests/unit/agents/test_terminal_nodes_emit_desfecho.py`; `tests/unit/runtime/test_turn_telemetry_comercial_fechada.py`.
- Simulação de merge sobre a main `dd52a051` (clone isolado, abortada): limpa, só os 9 arquivos. Ramo contém `a5fedc8e`; um merge atrás (#1300, 2 arquivos de documentação, disjuntos — CD-80).
- Pronto enviado à coordenação do programa por `send_message`.

## 2026-11-17 ~19:55Z — CAMINHO (linha nova de CD-87/88, classes fechadas)

- Arquivo: `tests/unit/platform/lembretes/test_optout_config_envio.py`, uma linha em `test_send_template_recusa_autorizacao_alterada_e_faixa_sintetica`: a autorização sintética usa valores placeholder `("a","b","c")`, que o gate agora recusa por classe (`VARIABLE_VALUE_INVALID`). Troca proposta: valores no formato do produtor de lembretes (`("outubro de 2026","320,25","16/10")`). Nenhuma asserção muda. Fora da posse da W2-MZO-CA (pasta de lembretes). Espero a CD antes do PR; o PR só abre depois do merge de ADD01-02.
- Estado: linha 2 (ADD01-02) em verificação local para o PR; linha nova implementada em ramo `sf-w2/mzo-ca/classes-fechadas` (sem PR); linha 3 (GTM-E-63) implementada em ramo `sf-w2/mzo-ca/gtm-e-63` (sem PR). Ordem: ADD01-02 → classes fechadas → GTM-E-63 → E-SF-23 → E-SF-15 → E-SF-22r → E-SF-16/16r.

## 2026-11-17 ~21:05Z — merge do #1299 e PR da linha 2

- maezo-operadora #1299 (telemetria do agente comercial fechada + GTM-E-11) mesclado; main `972130a0`.
- CD-91 e CD-92 lidas. Linha 2: maezo-operadora #1303 (ADD01-02) aberto, head `e01db68e9018638853593200e7a4b11407695175`, contém a main `972130a0`. Revisão independente FAIL → reparo literal → PASS. Local: `tests/unit/portal` + `tests/unit/ci` sem falha exclusiva do ramo.
- Linha 3 (GTM-E-63): revisão independente FAIL (citação de artigo) → reparo literal → PASS; em ramo.
- Linha de classes fechadas: implementada em ramo; aplico o CAMINHO aceito (CD-91 item 2) e o teste de valores reais (CD-91 item 3) antes da revisão.
- Disco do container encheu com temporários do pytest (28 GB em `/tmp/pytest-of-root`); limpo.

## 2026-11-17 ~22:00Z — linha de classes fechadas pronta em ramo

- Ramo `sf-w2/mzo-ca/classes-fechadas`: revisão independente FAIL (CID com separador não-ASCII deixava de casar; `protocolo_ref` e `link_opaco_jornada` aceitavam palavra livre; teste comparando o arquivo consigo mesmo) → reparo literal → PASS na reconferência (sondas refeitas sem patch; 300 mil textos aleatórios sem regressão do padrão de CID da main).
- CAMINHO de CD-91 aplicado (uma linha em `tests/unit/platform/lembretes/test_optout_config_envio.py`); teste de valores reais do lembrete (CD-91 item 3) incluído.
- Local: `tests/unit/gateway` + `tests/unit/ci` + `tests/unit/platform/lembretes` + `tests/unit/tools`: 15636 passed; as 170 falhas/erros locais estão todas na lista da main limpa.
- Resíduo registrado: `primeiro_nome` aceita uma palavra latina qualquer (validador de nome não distingue nome de remédio); o detector continua como defesa em profundidade.
- PR abre depois do merge do #1303.

## 2026-11-17 ~21:45Z — PRONTO maezo-operadora #1303 head e01db68e9018638853593200e7a4b11407695175 checks verdes (integração lida do junit)

- Integração: 5 shards, 958 + 138 + 130 + 129 + 56 = 1411 testes, 0 falha, 0 erro. `flip-path-review-gate` verde.
- Arquivos tocados (2, novos): `src/maezo/portal/api/specialist_console.py`; `tests/unit/portal/test_add01_02_specialist_console.py`.
- Simulação de merge sobre a main `4d44a8ec` (clone isolado, abortada): limpa, só os 2 arquivos. Ramo contém `972130a0`; um merge atrás (#1302, disjunto — CD-80).
- Pronto enviado à coordenação do programa por `send_message`.

## 2026-11-17 ~21:55Z — merge do #1303 e PR das classes fechadas

- maezo-operadora #1303 (ADD01-02) mesclado; main `093357a2`.
- maezo-operadora #1304 (variáveis em classe fechada; CD-87, CD-88, CD-91) aberto, head `aa79b405436e763f34071099c02c9cc4173ae06b`, contém `093357a2`. Edição autorizada em `tests/unit/platform/lembretes/test_optout_config_envio.py` listada no corpo.
