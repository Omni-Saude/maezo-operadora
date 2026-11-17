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
