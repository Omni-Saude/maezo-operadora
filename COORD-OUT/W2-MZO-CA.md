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
