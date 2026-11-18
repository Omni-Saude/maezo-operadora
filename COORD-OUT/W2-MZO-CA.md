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
