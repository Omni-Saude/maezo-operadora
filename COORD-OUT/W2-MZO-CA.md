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

## 2026-11-17 ~22:00Z — resposta à observação do #1303 (projeção da jornada × detector por lista)

Conferido no código mesclado (`src/maezo/portal/api/specialist_console.py`): a lista de campos permitidos (`CAMPOS_DA_VISAO`) garante por construção que nenhum CAMPO clínico sai (só `journey_ref`, `revision`, `observed_at`, `prospect_ref`, `account_ref`, `party_ref`). Mas o VALOR desses campos não é validado por classe: `_ref` aceita qualquer `str` não vazia, `observed_at` qualquer `str`, e o tipo `Ref` do contrato é `string` sem padrão. Hoje, um valor de referência com texto clínico só é barrado pelo detector por lista (a varredura do snapshot inteiro). Portanto: campo = por construção; valor = depende da lista.
- ACHADO / pedido de linha: validar os seis valores por classe fechada (referência opaca estrita, revisão inteira, instante ISO-8601 com fuso), no mesmo molde do #1304 (`valor_valido_para_classe`), recusando o que não passa. Arquivo: `src/maezo/portal/api/specialist_console.py` e o teste dele (minha posse). Proponho entrar como PR pequeno depois do #1304, antes de GTM-E-63; aguardo CD (sigo a fila publicada se não houver resposta).

## 2026-11-17 ~22:40Z — estado da fila (siga a fila)

- #1304 (classes fechadas): CI em andamento.
- Prontas em ramo, com revisão independente PASS (todas com FAIL → reparo literal → PASS): GTM-E-63 (`sf-w2/mzo-ca/gtm-e-63`), E-SF-23 variante fechada (`sf-w2/mzo-ca/esf23`, parte do ramo das classes fechadas), E-SF-15 (`sf-w2/mzo-ca/esf15`, idem).
- E-SF-15: valores de critério só em conjuntos fechados (UF; tipo de plano já usado no repositório; etapa não de saúde; produto vazio — sem catálogo publicado, GAP); campanha só registrada (registro vazio) e com finalidade igual; idade e saúde não entram nem como chave nem como valor.
- E-SF-23: canal recusa `protocol_email_channel_unqualified`; transporte escuro; destinatário só referência opaca; aceite ≠ entrega; evento não assinado só pista; prazo de guarda "proposto, sem base".
- Observação de ROOT sobre a visão da jornada do console: registrada acima como pedido de linha; não entra na fila sem CD.

## 2026-11-17 ~23:05Z — E-SF-22r pronta em ramo; E-SF-16 aguardando E-SF-07

- E-SF-22r (`sf-w2/mzo-ca/esf22r`, parte do ramo das classes fechadas): gatilho só por cobertura ativa da fonte (tipado; caixa/espaço não passam); uma intenção por beneficiário e contrato, selada (forjada é recusada no convite, no registro e no pedido de envio); documentos "entregue" só de "disponível" com recibo; convite com texto único, sem pergunta nem saúde, público = todos; aceite sem consentimento específico não processa nada; envio só pelo despachante (registro publicado recusa `PURPOSE_NOT_RATIFIED`). Revisão independente FAIL (4 achados) → reparo literal → PASS.
- Ponte para o processo de programas de cuidado (SP-OP-PROGRAMA-001, BPMN e contrato): caminho da W2-MZO-GP, não tocado; GAP. `canal_ref` do pedido de boas-vindas fixo como "não resolvido": quem ligar o envio resolve o canal real.
- E-SF-16/16r: E-SF-07 ainda não mesclado (nenhum PR dele na main); linha não iniciada, motivo: dependência.

## 2026-11-17 ~22:30Z — PRONTO maezo-operadora #1304 head aa79b405436e763f34071099c02c9cc4173ae06b checks verdes (integração lida do junit)

- Integração: 5 shards, 958 + 138 + 130 + 129 + 56 = 1411 testes, 0 falha, 0 erro. `flip-path-review-gate` verde. Ramo contém a main `093357a2`.
- Arquivos tocados (4): `src/maezo/gateway/communications/outbound_gate.py`; `src/maezo/gateway/communications/whatsapp_templates.py`; `tests/unit/gateway/test_cd87_classes_fechadas.py` (novo); `tests/unit/platform/lembretes/test_optout_config_envio.py` (uma linha, CD-91).
- Simulação de merge sobre `093357a2` (clone isolado, abortada): limpa, só os 4 arquivos.
- Pronto enviado à coordenação do programa por `send_message`.

## 2026-11-17 ~22:45Z — #1304 mesclado; CD-94/CD-95 lidas; ordem

- maezo-operadora #1304 mesclado; main `4826d014`.
- Ordem: a coordenação do programa (mensagem de 22:26Z) pediu o conserto do `primeiro_nome` primeiro e depois a linha da visão do console; a CD-94 punha a visão antes. Sigo a coordenação do programa (posterior; é quem mescla). Depois: GTM-E-63 → E-SF-23 → E-SF-15 → E-SF-22r.
- CD-95 item 2 (o corpo do #1304 já tinha sido mesclado; a lista vai aqui e no PR do conserto): classes validadas só por formato e o que aceitariam indevidamente:
  - `primeiro_nome`: qualquer palavra latina (ex.: nome de medicamento). → passa a ser resolvida pela fonte; valor do chamador recusado.
  - `competencia_mes_ano`: qualquer mês por extenso + ano de 4 dígitos (mês/ano que não é o da cobrança).
  - `valor_cobranca_reais`, `valor_centavos_formatado`: qualquer valor no formato (valor que não é o cobrado).
  - `data_vencimento_dia_mes`, `data`, `data_publicacao_fonte`: qualquer data válida (data que não é a do fato).
  - `contagem`: qualquer inteiro.
  - `protocolo_ref`: qualquer sequência de dígitos com hífen (protocolo inexistente).
  - `link_opaco_jornada`: qualquer hexadecimal/UUID ou URL com token hex (link que não é da jornada).
  - `produto_catalogo`: já é conjunto fechado (vazio por falta de catálogo).
- Q-W2-MZO-CA-03: CD-95 manda resolver pela fonte ou fechar em conjunto TODAS as classes da lista. Fonte de primeiro nome não existe no repositório (a custódia do destinatário guarda só telefone). Proposta (A, recomendada): neste PR, `primeiro_nome` passa a "resolvida pela fonte" — o chamador não informa o valor; sem fonte (GAP), todo modelo com essa classe é recusado. As classes de data, valor, contagem, protocolo e link usam só dígitos/separadores/hex (não codificam palavra, logo não carregam termo de saúde); a resolução pela fonte delas exige mudar o produtor de lembretes (caminho da W2-MZO-GP, E-SF-21r) e as fontes de cobrança: linha própria, com a lista acima como pré-condição de D20–D22. (B) resolver todas agora: fora de posse e sem fontes. Sigo (A), `PROVISÓRIO-aguarda-coordenador`.

## 2026-11-17 ~23:40Z — PR do conserto do primeiro_nome

- maezo-operadora #1306 aberto (`sf-w2/mzo-ca/primeiro-nome`), head `d3bd8a1e8191207575c5a12eeb02490458a3ac88`, contém a main `4826d014`. Revisão independente PASS (observações de texto aplicadas). Local sem falha exclusiva do ramo.
- O corpo do PR traz a lista de CD-95 item 2 e declara este PR e o #1304 como pré-condição de D20–D22.
- Falha de processo minha: limpei `/tmp/pytest-of-root` durante a suíte local da GTM-E-63 (resultado inválido, 1541 erros espúrios). Passo a usar `--basetemp` próprio em cada rodada; a suíte da GTM-E-63 roda de novo antes do PR dela.

## 2026-11-18 ~01:30Z — PRONTO #1306 e estado das linhas seguintes

- PRONTO: maezo-operadora #1306, head `847f3543b979490b3bb3a4c232250ebb737949c9`, checks verdes (43: 41 success, 2 skipped; integração 1411 testes, 0 falhas, 0 erros). Contém a main `6520e4a1`; simulação de merge sobre ela limpa e abortada.
- Atendido CD-99 item 2 neste PR: teste do conjunto fechado de doze meses em `competencia_mes_ano` (detector desligado; prova de guarda em cópia isolada: palavra extra no conjunto deixa o teste vermelho).
- Visão do console (CD-94 item 2), ramo `sf-w2/mzo-ca/console-valores`: revisão independente deu FAIL (prefixo livre de letras na referência opaca permitia escrever palavra, ex. termo clínico + hex, e os seis valores ficam fora da varredura). Reparo literal aplicado: prefixo em conjunto fechado (`jrn_`, `pro_`, `acc_`, `pty_`) e caso no teste. Local sem falha exclusiva do ramo. PR depois do merge do #1306.
- GTM-E-63: suíte local refeita com `--basetemp` próprio sobre a main `6520e4a1`; sem falha exclusiva do ramo. PR na vez dela.
- Pedido de linha (CD-99 item 3): veracidade do valor das classes só por formato (data e valor do fato) é propriedade do produtor de lembretes, posse da W2-MZO-GP; não tocado.

## 2026-11-18 ~02:40Z — PRONTO #1309 (visão do console, CD-94 item 2)

- PRONTO: maezo-operadora #1309, head `af729d3fc51e41c005cbb147fcdc04ee6bff6023`, checks verdes (42: 40 success, 2 skipped; integração 1411 testes, 0 falhas, 0 erros). Contém `70c9b97a`; main atual `22790308` um merge à frente (#1301), arquivos disjuntos (CD-80). Simulação de merge limpa e abortada.
- Próximas, com a main `70c9b97a` incorporada e empurradas sem PR: GTM-E-63 (local sem falha exclusiva do ramo), E-SF-23, E-SF-15, E-SF-22r (suítes locais em curso).

## 2026-11-18 ~03:50Z — PRONTO #1311 (GTM-E-63)

- PRONTO: maezo-operadora #1311, head `c8f8ea65cdb8f42e0892d30295604fafaa054ed5`, checks verdes (42: 40 success, 2 skipped; integração 1413 testes, 0 falhas, 0 erros). Contém `a226697d`; main atual `136444a1` um merge à frente (#1310), arquivos disjuntos (CD-80). Simulação de merge limpa e abortada.
- E-SF-23, E-SF-15, E-SF-22r: suítes locais sem falha exclusiva do ramo sobre `70c9b97a`; ramos já com `a226697d` incorporada; cada um refaz a suíte local sobre a main da vez antes do PR.

## 2026-11-18 ~05:05Z — PRONTO #1313 (E-SF-23)

- PRONTO: maezo-operadora #1313, head `3fd6f2f7cd37c72a53cb4794fbc41cd5cc8b0255`, checks verdes (42: 40 success, 2 skipped; integração 1413 testes, 0 falhas, 0 erros; gitleaks verde). Contém `98e4e37b`; main atual `7360216c` um merge à frente (#1312), arquivos disjuntos (CD-80). Simulação de merge limpa e abortada.
- E-SF-15 pronta para PR depois do merge (prova de guarda em cópia isolada: `faixa_etaria` admitida, sem parada, sem conferir finalidade, cada uma vermelha).

## 2026-11-18 ~06:05Z — PRONTO #1314 (E-SF-15)

- PRONTO: maezo-operadora #1314, head `648fafef7b4a0162c46574ea57305714927539a8`, checks verdes (42: 40 success, 2 skipped; integração 1416 testes, 0 falhas, 0 erros). Contém a main atual `2a986442`; simulação de merge limpa e abortada.
- Falha de processo minha: um diretório temporário de suíte interrompida (9,6 GB) esgotou a cota de disco da sessão e truncou três logs de suíte local da E-SF-22r; resultados descartados, diretório apagado, suíte refeita. Nenhum resultado truncado foi usado como verificação.

## 2026-11-18 ~06:35Z — #1313 e #1314 mesclados; ACHADO e Q-04 no #1315 (E-SF-22r)

- Registro pedido em CD-119/CD-121: #1313 (E-SF-23) mesclado na main `2a986442`; #1314 (E-SF-15) mesclado na main `204999bf`.
- #1315 (E-SF-22r) aberto, head `819ad7d7`, sobre `204999bf`. CI vermelho, causa deste PR: `tests/unit/processes/test_e_sf_rt_01_retention_signals.py::test_nenhum_emissor_de_evento_ret_na_onda_1`. A cerca da E-SF-RT-01 proíbe qualquer nome `ret.*` em `src/`, `spec/`, `deploy/` e `config/` fora da allowlist e do registro de modelos ("o dia em que um emissor nascer, este teste cai e a mudança é consciente"). O módulo de boas-vindas declara `TEMPLATE_BOAS_VINDAS = "ret.boas_vindas.v1"` em `src/maezo/gateway/capabilities/journeys/boas_vindas.py`. Reproduzido local.
- Falha de processo minha: a suíte local da E-SF-22r cobriu `tests/unit/gateway` e `tests/unit/ci`, não `tests/unit/processes`. Daqui em diante a suíte local de toda linha inclui `tests/unit/processes`.
- Não mexo na cerca, não dou push e não mando pronto até a decisão.
- Q-W2-MZO-CA-04: como a boas-vindas referencia o modelo `ret.boas_vindas.v1`?
  - (A, recomendo) Mudança consciente da cerca neste PR: exceção estreita, nomeada para `boas_vindas.py` e só para o nome do modelo, condicionada a um teste que prova o envio fechado (como já se faz para o registro de modelos). Toca o teste da RT-01, por isso pede CD.
  - (B) O módulo resolve o id do modelo no registro por finalidade, sem literal em `src/`. Não recomendo: tira a dependência da vista da cerca sem decisão.
  - (C) Segurar a E-SF-22r até a E-SF-35r (o emissor consciente) e fechar o #1315.

## 2026-11-18 ~07:55Z — #1315 (E-SF-22r): CD-123 aplicada, segundo push, CI em curso

- CD-123 aplicada no head `395f141bd7c441a05cbd686b58e5b62fed6d860d` (push às ~07:08Z): o identificador do modelo de boas-vindas é parâmetro injetado (`template_id`, obrigatório, sem padrão), validado só na forma; sem ele, recusa `modelo_nao_injetado` antes do portão. Zero literal `ret.*` em `src/`. A cerca da RT-01 e o seu arquivo de teste não foram tocados.
- Testes novos: sem modelo injetado recusa sem chamar o portão; modelo injetado não publicado nem ratificado; fonte do módulo sem nome de sinal e sem emissor. Os testes de CD-122 item 2 seguem cobertos (não despacha, sem evento, não ratificado); nada montado.
- Mutação em cópia isolada: literal de volta em `src/` → a cerca original e a varredura do módulo vermelhas; módulo despachando sem o portão → teste de envio fechado vermelho.
- Suíte local sobre `204999bf`: `tests/unit/gateway` 6320 passed, `tests/unit/processes` 114 passed (a cerca incluída), `tests/unit/sec` 162 passed, `tests/unit/ci` 3403 passed; falhas e erros só os da lista da main limpa. Revisão independente: PASS, sem reparo. Corpo do PR atualizado (CD-123 item 6).
- CI do head `395f141b`: 38 success, 2 skipped, 2 em curso (integração), nenhuma falha até agora.
- Main atual `8c509a09` está um merge à frente do ramo (#1316, rco), arquivos disjuntos dos meus e sem nome `ret.*` novo (CD-80). Simulação de merge sobre ela vai no pronto.

## 2026-11-18 ~08:05Z — PRONTO #1315 (E-SF-22r) e RELATÓRIO PARCIAL (CD-121; sem DONE)

- PRONTO: maezo-operadora #1315, head `395f141bd7c441a05cbd686b58e5b62fed6d860d`, checks verdes (43: 41 success, 2 skipped; integração 1416 testes, 0 falhas, 0 erros). Cerca de `ret.*` da RT-01 NÃO alterada; identificador do modelo injetado (CD-123). Contém `204999bf`; main `8c509a09` um merge à frente (#1316), disjunto e sem `ret.*` novo. Simulação de merge limpa; nela a cerca e os testes do módulo passam (83).

### Relatório parcial
1. Telemetria comercial + GTM-E-11 — #1299 mesclado; revisão PASS; portão de medição fechado (`comercial` sem medição admitida).
2. ADD01-02 console do especialista — #1303 mesclado; PASS; console não montado, compromisso N não ratificado e nunca exibido.
3. Classes fechadas (CD-87/88/91) — #1304 mesclado; PASS depois de reparo.
4. `primeiro_nome` pela fonte + doze meses (CD-94/95/99) — #1306 mesclado; PASS; todo modelo com `primeiro_nome` recusado (sem fonte).
5. Visão do console por classe fechada (CD-94 item 2) — #1309 mesclado; FAIL (prefixo livre) → reparo → PASS.
6. GTM-E-63 contestação de reajuste — #1311 mesclado; PASS; prazo do art. 11 sem linha no catálogo (GAP), protocolo só da custódia.
7. E-SF-23 e-mail de protocolo — #1313 mesclado; PASS; canal recusado na admissão.
8. E-SF-15 portão de campanha — #1314 mesclado; PASS; registro vazio, campanha nasce parada.
9. E-SF-22r boas-vindas — #1315 com PRONTO; PASS; envio fechado, identificador injetado.
10. E-SF-16 e 16r — NÃO iniciadas: dependem de E-SF-07 (W2-MZO-GP), ausente da main em `8c509a09`.
- Não verificado (todas): helm e testes que exigem histórico completo ou usuário não root, só no CI; integração lida do junit do CI.
- Portões fechados (todas): nenhuma mensagem proativa, nenhum evento de medição, zero dado de saúde, idade fora de critério, nenhum prazo ou norma ao cliente, política não ratificada.
- Pedidos de linha: (a) veracidade do valor das classes só por formato no produtor de lembretes (W2-MZO-GP), pré-condição de D20–D22; (b) aprovação humana de campanha (pré-condição de ligar E-SF-15); (c) fonte de protocolo de suporte (GTM-E-63) e linha do prazo do art. 11 no catálogo; (d) provedor de e-mail e verificador de eventos (E-SF-23); (e) formato fixo das referências no contrato comercial (console); (f) ponte para programas de cuidado, documentos dos coletivos e canal do destinatário (E-SF-22r); (g) fonte de primeiro nome na custódia.
- ACHADOS: testes de `tests/unit/portal/test_admin_read_api.py` dependentes de ordem (também na main limpa); limites do detector por lista (corrigidos por classe, nunca por exceção); corrida da tela de legitimidade (não minha).
- Falhas de processo: id de sessão errado no primeiro commit do canal; limpeza de temporários no meio de suíte; diretório temporário de 15–20 GB por rodada de `tests/unit/ci` esgotando a cota (agora apagado a cada rodada); `tests/unit/processes` fora da suíte local da E-SF-22r (corrigido).
- Ramos para apagar depois dos merges (não apago): `sf-w2/mzo-ca/telemetria-gtm-e-11`, `add01-02`, `classes-fechadas`, `primeiro-nome`, `console-valores`, `gtm-e-63`, `esf23`, `esf15`; `esf22r` depois do merge do #1315.

## 2026-11-18 ~08:35Z — CAMINHO E-SF-RT-04 ("Meu plano") na variante RT-04r (CD-128; sem push de código)

Base: main `ee1ac446`. Ramo previsto: `sf-w2/mzo-ca/rt04`. Módulo puro, na mesma forma do console do especialista: nada montado, nenhum router, nenhuma importação em `app.py`.

Arquivos (só estes, todos novos):
- `src/maezo/portal/api/meu_plano.py`
- `tests/unit/portal/test_rt04_meu_plano.py`
Nenhuma migração, nada em `spec/policies/autonomy/`, sem CODEOWNERS, sem arquivo de contagem, sem `portal/web/` (as telas ficam como projeção tipada; o molde web é de outro PR). Não toca `specialist_console.py`, `legitimacy_screen.py` nem nenhum arquivo do console da trilha do programa.

O que o módulo faz, e o que fica fechado (com o teste de cada um):
1. Chave da superfície desligada (`MEU_PLANO_LIGADO = False`). O leitor recusa toda projeção com `meu_plano_desligado`. Teste: `test_superficie_desligada_recusa`.
2. Visão do plano só com fatos da fonte, nunca calculados: data de aniversário, valor antes e depois do reajuste e data do fato vêm da porta de fonte (sem implementação: GAP), cada um com validador de classe fechada.
   - Nenhum percentual, total ou diferença calculados aqui (J8/J12). Teste: `test_meu_plano_sem_total_local`.
   - Varredura de AST: sem aritmética sobre valores e sem campo de percentual.
3. Renovação com zero ação (RT-04): nenhuma ação "renovar" ou "confirmar renovação" na visão; o recibo D0 só existe com fato de fonte. Teste: `test_sem_botao_renovar`.
4. Cancelar sempre visível em toda visão de "Meu plano", no primeiro nível. Pedido em 2 toques até o protocolo: o protocolo vem só da custódia (porta sem implementação, recusa sem ela), e nenhum prazo é exibido. Testes: `test_cancelar_sempre_visivel`, `test_cancelamento_dois_toques`.
5. Opções a pedido (RT-03, variante informativa): só existem depois do pedido; catálogo fechado do portfólio, sem preço calculado.
   - Troca de plano só assistida (J54 b = B): vira pedido para pessoa, com protocolo da custódia.
   - Sem DS e sem critério de idade, uso ou saúde. Testes: `test_opcoes_so_a_pedido`, `test_troca_so_assistida`.
6. Linha de alternativa no "quero cancelar" (J54 a = A): estrutura construída com exibição DESLIGADA (`LINHA_ALTERNATIVA_EXIBIDA = False`), só no portal, nunca no WhatsApp.
   - Texto `None`: sem redação aprovada.
   - Teste do estado fechado: a visão do cancelamento nunca traz a linha nem passo extra. Teste: `test_linha_alternativa_desligada`.
7. Sem oferta no bloco do aviso de reajuste: `test_aviso_sem_oferta_no_bloco`.
8. Zero dado de saúde: esquema fechado na entrada e detector como defesa em profundidade; valor fora da classe recusa sem eco. Testes por classe com o detector desligado.
9. Nenhum envio e nenhum evento de medição. Sem nome `ret.*` em `src/` (a cerca da RT-01 continua valendo; se precisar de identificador de modelo, será injetado, como em CD-123).

Subperguntas sem item na cédula, com o padrão conservador proposto:
- S1. J54 (a): a resposta do dono a Q-OS08-1 diz "sem linha"; a decisão verificada diz "construir com exibição desligada". Proponho construir desligada e sem texto, com teste do estado fechado. Ligar fica para ato do dono com a redação do jurídico.
- S2. Recibo D0 e aviso no coletivo (J5 pendente): texto neutro, sem a frase do art. 13; nenhum dispositivo exibido ao cliente.
- S3. Retirada de dependente em individual/familiar (GAP-RT-04): fora do catálogo de opções.
- S4. Exibição do teto do prazo do cancelamento (J72 B): não exibido.
- S5. Casa da superfície (GAP-RT-06): maezo provisório, como na cédula; nada no omni.
- S6. Fonte dos fatos (aniversário, valores do reajuste, recibo): porta sem implementação, e a visão recusa sem fonte, sem valor padrão. Fato de reajuste é da AMH (E-01/E-02), não construído aqui.
- S7. Decreto 11.034 (J74 C): conferido só no que cabe ao módulo (cancelar no primeiro nível e protocolo); o resto do checklist é da linha de cancelamento (E-SF-09).

Suíte local antes do push: `tests/unit/portal`, `tests/unit/processes`, `tests/unit/sec`, `tests/unit/ci`, com `--basetemp` próprio apagado a cada rodada. Revisão independente antes do push. Mutação em cópia isolada: chave ligada; linha exibida; total calculado; cancelar escondido; troca em autosserviço. Cada uma tem de deixar teste vermelho.

## 2026-11-18 ~09:35Z — E-SF-RT-04: correção do CAMINHO (CD-130) e estado

- Correção de fundamento (CD-130 item 3): meu CAMINHO (S1) citou errado. O dono pediu a alternativa no "quero cancelar" em página ou portal; a pergunta Q-OS08-1 tratava só do WhatsApp e foi respondida "ainda não". A redação e a posição da linha dependem do jurídico e de ato do dono. A variante que segue sem esperar é o fluxo SEM a linha, e é a que está construída.
- CD-130 aplicada: sem chave, texto, campo ou passo de alternativa no módulo. Fica o teste negativo pela forma do tipo da tela de cancelamento, mais a varredura de nomes, funções, classes e textos.
- Revisão independente: primeira passada FAIL (asserção sempre verdadeira no teste antigo, que saiu com CD-130). Segunda passada FAIL (varredura de textos não percorrida; nomes de função e classe fora). Reparo literal aplicado, mais um teste novo (toda função pública começa pela chave). Reverificação por mutação: função `oferecer_alternativa`, oferta no texto e função pública sem chave, cada uma vermelha.
- Mutações do CAMINHO: chave ligada, valor calculado, cancelar no segundo nível e troca em autosserviço, cada uma vermelha.
- Suíte local: `tests/unit/portal` 3222 passed (head atual); `processes` 114 e `sec` 162 passed; `ci` em curso. Push e PR quando o `ci` fechar sem falha nova.

## 2026-11-18 ~10:10Z — PRONTO #1319 (E-SF-RT-04 "Meu plano", variante RT-04r)

- PRONTO: maezo-operadora #1319, head `4e462f5f33fe8556f98c2ea21dfc122cc357238a`, checks verdes (42: 40 success, 2 skipped; integração 1419 testes, 0 falhas, 0 erros). Contém `ee1ac446`; main atual `c06620f0` um merge à frente (#1317), arquivos disjuntos (CD-131). Simulação de merge limpa; nela os testes do módulo e do console passam (76).
- Estado da fila: todas as linhas executáveis com PR mesclado ou pronto. E-SF-16 e 16r seguem à espera da E-SF-07 (W2-MZO-GP); sem `DONE` (CD-121).
- Acréscimo ao relatório parcial: linha 11, E-SF-RT-04, #1319 com PRONTO. Revisão em duas passadas, ambas FAIL e reparadas. Portões fechados: superfície desligada e não montada, linha de alternativa não construída, opções recusadas até haver catálogo publicado. Pedidos de linha: fonte dos fatos do plano, custódia de protocolo de atendimento, catálogo publicado, casa definitiva da superfície. Ramo para apagar depois do merge: `sf-w2/mzo-ca/rt04`.

## 2026-11-18 ~10:45Z — CAMINHO E-SF-07 (relógio de contratação: sombra, alarmes, 26º dia; variante executável; sem push de código)

Base: main `b2540f9b`. Ramo previsto: `sf-w2/mzo-ca/esf07`. Base normativa citada só pelo arquivo `docs/compliance/legal/textos-base/RN-413-2016.md` (art. 5º e §§; art. 6º). Nada da 07r: nem orquestração do aceite à cobrança, nem processo humano, nem marca de contrato efetivado, nem marcador de início legal, nem suspensão que pause a contagem.

Arquivos (todos novos; nenhum existente é editado):
- `src/maezo/gateway/commercial_t0_ledger.py` (EP-REG-32a): registro append-only dos eventos-candidatos a T0 por `prospect_ref` opaco. Grava só o tipo do evento e o instante do servidor, zero conteúdo: primeiro `prospect.save` com valores, primeiro `party.submit`, `acceptance.record`, e pedidos de documentação ou perícia (§5º/§7º) com a causa em classe fechada. Porta de persistência mais implementação em memória para teste. Reescrita recusa com `t0_ledger_immutable`.
- `src/maezo/tools/workers/rn413_contracting_clock.py` (TB-E-09-a, EP-REG-32b, EP-REG-23c e TB-E-14-a num relógio só). É função pura sobre o registro, sem timer próprio, sem BPMN e sem DMN, e não é registrada no runtime de workers (nada montado).
  - Início = o evento mais cedo com dados.
  - Contagem em dias corridos, sem pausa: as suspensões só ficam registradas.
  - Alarmes internos em D+15, D+20 e D+23 viram item de escalonamento para pessoa, com grupo responsável obrigatório.
  - Em D+25: flag `possivel_vigencia_automatica` e fila humana.
  - Nenhum comando adverso, nunca: sem rescisão, negativa, cobrança ou bloqueio.
- `src/maezo/gateway/commercial_pos_dia_25.py` (EP-REG-09a): para caso com a flag, recusa bloqueio de uso por falta de pagamento (`human_review_required`) e alegação de DLP (`dlp_barred_rn413_5_6`). Rescisão autônoma recusada. Não toca a pasta de autonomia; só recusa no próprio módulo.
- Testes:
  - `tests/unit/gateway/test_esf07_t0_ledger.py`
  - `tests/unit/tools/workers/test_rn413_contracting_clock.py`
  - `tests/unit/gateway/test_esf07_pos_dia_25.py`
  - `tests/unit/commercial/test_contracting_overrun.py`

O que fica fechado, e por qual teste:
1. Registro imutável e sem conteúdo: `test_t0_ledger_records_first_prospect_save` (segundo `prospect.save` não sobrescreve o primeiro); `test_t0_ledger_sem_conteudo` (esquema fechado: só tipo, referência opaca e instante; valor fora da classe recusa sem eco).
2. Wire C6-4 inalterado: `test_wire_unchanged` (nenhum arquivo do contrato candidato tocado; nenhum campo de instante no payload).
3. Relógio: `test_shadow_clock_starts_at_first_prospect_save`, `test_clock_not_suspended_by_marketing_reminder`, `test_clock_alerts_before_day_25`, `test_rn413_monitor_ignores_suspension_until_ratified`, `test_rn413_monitor_alerts_on_shadow_clock_without_anchor` (nunca silêncio) e `test_rn413_monitor_never_adverse`.
4. Um relógio só: `overrun_alarm_reads_canonical_clock` (`SECOND_CLOCK_FORBIDDEN`); `overrun_requires_responsible_group` (`RESPONSIBLE_QUEUE_REQUIRED`); `overrun_requires_named_ceiling` (`OVERRUN_WITHOUT_NAMED_CEILING`).
5. Pós-D+25: `test_no_autonomous_termination_after_day25`, `test_no_dlp_allegation_after_day25`, `test_auto_effectivated_contract_never_blocked_for_payment`.
6. Nenhum prazo ao cliente: os três módulos sem importador em `src/` (nada montado); o estado do relógio não tem campo de data-limite exibível; varredura de que nenhuma projeção do comprador (`journey.get`, portal) os importa.
7. Zero dado de saúde, nenhum envio, nenhum evento de medição, sem nome `ret.*`, sem `assert` em `src/`.

Fora do meu limite, pedindo decisão:
- NUMERO / persistência (S1): o registro durável pede migração (a próxima livre do funil é `0038`) e, com dado pessoal pseudonimizado por `prospect_ref`, uma categoria na matriz de retenção. Isso toca arquivo de contagem (`spec/policies/retention/`) e o ato do encarregado (CD-109). Proponho que ESTE PR fique sem migração: porta de persistência mais implementação em memória, nada ligado à admissão viva. A migração `0038`, a categoria de retenção e a ligação aos comandos de admissão vão para linha própria, com `NUMERO` e `CAMINHO`. Se a coordenação preferir a migração já neste PR, peço `NUMERO 0038` e o `CAMINHO` da retenção antes do push.
- Limiares (S2): TB-E-14-a pede o limiar em DMN. DMN nova pede linha em CODEOWNERS, que o funil não edita. Proponho: os alarmes D+15/D+20/D+23 e o corte D+25 ficam como constantes operacionais da decisão já tomada (relógio-sombra, não relógio legal), marcados "operacional, não legal". O DMN do limiar vai para a linha que ligar o relógio, com a linha de CODEOWNERS pela coordenação do programa.

Subperguntas sem item na cédula, com o padrão conservador proposto:
- S3. Início legal: o relógio-sombra começa no evento mais cedo com dados. O início legal não é marcado (07r); quando ratificado, só pode relaxar os tetos, nunca antecipar.
- S4. Suspensões (§5º/§7º): registradas com causa, sem pausar a contagem até a ratificação; lembrete de recuperação nunca suspende.
- S5. Recuperação de abandono depois de D+20: o relógio expõe `recuperacao_bloqueada` a partir de D+20. A E-SF-16 vai lê-lo; aqui não se envia nada.
- S6. Fila e grupo: o item vai para o grupo responsável da fila comercial, só pelo nome de papel; sem grupo, recusa. Não toca `SP-OP-ESCALATION-001`: rotear o item ao processo é linha de quem ligar o relógio.

Suíte local antes do push: `tests/unit/gateway`, `tests/unit/tools`, `tests/unit/commercial`, `tests/unit/processes`, `tests/unit/sec`, `tests/unit/ci`, com `--basetemp` próprio apagado a cada rodada. Revisão independente e mutações antes do push.

## 2026-11-18 ~11:25Z — E-SF-07: CAMINHO para um arquivo a mais (efeito da CD-137); sem push

- Com o relógio em `src/maezo/domain/` (CD-137), a guarda `tests/unit/domain/integration/test_identity.py::test_domain_modules_discovered` reprova localmente. Ela fixa o conjunto EXATO de módulos de `maezo.domain` (`_EXPECTED_MODULES`) e a contagem (`len(modules) == 21`). É arquivo de contagem fora da lista aceita na CD-135.
- CAMINHO pedido, só para esta edição em `tests/unit/domain/integration/test_identity.py`: acrescentar a `_EXPECTED_MODULES` a entrada `"rn413_contracting_clock.py"`, com uma linha de comentário no molde das demais (relógio-sombra da contratação, função pura, stdlib-only, sem importar outra camada), e mudar a contagem de 21 para 22. Nenhuma outra linha.
- As outras cercas do mesmo arquivo passam com o módulo novo, porque ele só importa a biblioteca padrão.
- Estado da E-SF-07: implementação, mudança de pasta (CD-137) e reparos da revisão independente (cinco achados, inclusive selo do estado copiável por `replace`) commitados localmente em `sf-w2/mzo-ca/esf07`. Mutações todas vermelhas.
- Suíte local: `domain` 527 passed e 1 falha NOVA (esta guarda); `gateway` 6346 passed (1 falha conhecida); `tools` 6087, `commercial` 22, `processes` 114 e `sec` 162 passed; `ci` em curso.
- Gates da CD-138 rodados: `check_fronteira_agentes` PASS (769 arquivos, nenhuma dependência nova); `check_qa_isolada` PASS; `check_start_process_fence` PASS (785); `check_plans_counts` PASS (4 contagens); testes que varrem `gateway/commercial_*` e as allowlists de SQL: 135 passed.
- Main nova `7fefa48d` (CD-142): incorporo por merge antes do push.
- Sem push até o CAMINHO.
