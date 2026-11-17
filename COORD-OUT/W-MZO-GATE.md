# COORD-OUT — W-MZO-GATE (maezo-operadora)

Canal janela → coordenador (CD-20). Só acréscimo.

## Registro anterior à CD-20

- 2026-11-16: antes de ver a CD-20, mandei duas mensagens entre sessões: uma ao coordenador, com a pergunta que virou Q-W-MZO-GATE-01, e um aviso de pronto direto à mescladora para o #1268. A partir daqui, uso só este arquivo.
- PRONTO maezo-operadora #1268 head 2dec30002db2b7e755a350038f7b8fa378c47e36 checks verdes (integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skips). MESCLADO em 2026-11-16 (merge 10c6f86).

## Q-W-MZO-GATE-01 — E-SF-03 / EP-CORE-11b: ADR 0078 × contagens canônicas

Adicionar `docs/adr/0078-*.md` deixa vermelhos os checks de contagem. `check_plans_counts` lê `PLANS.md:26` (77 ADRs / 79 arquivos) e `PLANS.md:180` (77; 62 Accepted, 15 Proposed). `contagens_canonicas` lê `AGENTS.md:22,57` e `README.md:139,202`. A CD-11 item 5 só me libera a região gerada de `PLANS.md`.

- (A) **Recomendada.** O PR do E-SF-03 leva o ADR 0078 (Proposto), a linha do índice `docs/adr/README.md`, as contagens em AGENTS.md e README.md e só estas trocas em `PLANS.md`: :26 77→78 e 79→80; :180 77→78 e 15→16 Proposed. Tudo declarado no corpo do PR.
- (B) O PR do E-SF-03 sai sem o ADR, só com o teste de guarda do CORE-11b. A coordenação do programa aplica o ADR e as contagens num commit próprio.
- (C) Outra forma, a critério da coordenação.

Sigo por (A): PROVISÓRIO-aguarda-coordenador.

Ponto relacionado, já corrigido: na minha mensagem anterior à CD-20, eu disse que a disclosure de `plan_choices` sem A1 ficaria fora do E-SF-03. Errei: o DEC-CORE (EP-CORE-00a, §(4)(b)) exige fechá-la. O E-SF-03 fecha esse caminho com um gate de disclosure.

## 2026-11-16 ~20:20Z — E-SF-03

- PR #1270 aberto (head 5d88ee6). Q-W-MZO-GATE-01 seguiu pela opção (A), PROVISÓRIO. A mescladora confirmou o ADR 0078 por mensagem (20:12Z).
- Bloqueio de revisão: `flip-path-review-gate` está vermelho porque `docs/adr/` tem um único code owner, o autor do PR. É preciso a aprovação de um membro ativo da equipe sobre o head 5d88ee6; um push não resolve. Comentário no PR registrado.
- PRONTO do #1270 só depois do restante do CI verde e da integração lida no junit.

## 2026-11-16 ~21:10Z — E-SF-04 pronto localmente

- O ramo `sf-w1/gate/E-SF-04` (0a03c3e) foi publicado sem PR, só para preservar o trabalho; ele está empilhado sobre o E-SF-03. O PR fica para depois do merge do #1270 (um PR aberto por janela). A revisão independente deu FAIL na 1ª rodada (3 major) e PASS na 2ª.
- GAPs do E-SF-04, cada um com a busca feita:
  - GAP-04-1. Lista de finalidades pré-DS: não publicada. Busca: grep `purpose_ref` em docs/ e spec/; contracts.json:3285 diz "no new literal purpose enum". Até ser publicada, o codec recusa o descriptor e os values de intake por padrão.
  - GAP-04-2. Marcador de `requirement_ref` de DS e vocabulário fechado de labels da jornada: inexistentes. Busca em src/, spec/ e docs/; o `health-commercial.ts` citado no DEC-REG não existe no checkout do omni. Até lá, labels são recusados por padrão.
  - GAP-04-3. O C6-4 pinado tem 6 propriedades `cpt` (PublicCatalog*) fora dos gates :485/:499 citados por EP-REG-02a, então a aceitação (1) não se cumpre literalmente. Decisão do custodiante AMH/DPO.
  - GAP-04-4. Taxonomia de classe de campo do DPO: RATIFY-LATER. Classe desconhecida é tratada como PHI.
  - Residual: quando alguém configurar uma lista de finalidades não vazia em produção, será preciso ligar `allowed_field_refs` ao descriptor admitido.
- Ainda no E-SF-04: a recusa tipada PHI_IN_COMMERCIAL_INPUT não existe no enum de reason_code do C6-4. Levá-la ao fio depende de um schema sucessor.

## Q-W-MZO-GATE-02 — E-SF-06 / TB-E-16-b precisa de caminho de outra janela

TB-E-16-b pede o guarda `records_access_notice_present` em `tests/unit/reembolso/test_textos.py`, mais o aviso do art. 19 no catálogo de textos de reembolso. Os dois caminhos não são da GATE.
- (A) **Recomendada.** A janela dona do reembolso (PROC) entrega o filho TB-E-16-b como linha dela.
- (B) A coordenação libera os dois caminhos para a GATE neste épico.
- (C) TB-E-16-b fica para a onda 2.

Sigo por (A): o PR do E-SF-06 sai sem TB-E-16-b e declara isso. PROVISÓRIO-aguarda-coordenador.

## 2026-11-16 ~21:50Z

PRONTO maezo-operadora #1270 head 5d88ee6a0acb7657bf632a158fe05e0185765750 checks verdes (integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skips). Exceção: `flip-path-review-gate` fica vermelho até haver aprovação humana de um membro da equipe que não seja o autor, porque o único code owner de `docs/adr/` é o autor do PR. Com essa aprovação, o check fica verde sem novo push.

E-SF-06 pronto localmente. O ramo `sf-w1/gate/E-SF-06` (25c2e02) foi publicado sem PR, empilhado sobre o E-SF-04. A revisão independente deu FAIL, FAIL e PASS na 3ª rodada. GAPs do épico, com o que foi buscado (C6-4, source-readiness.json, src/):
- GAP-06-1. EP-REG-01a: `contact.request` do C6-4 não tem slot de protocolo. O nome do plano `test_prospect_contact_returns_protocol_first` virou `test_prospect_contact_protocol_first_is_gap_at_c64_pin`, sem xfail.
- GAP-06-2. CR-F5: vocabulário de casos não publicado. O pedido foi registrado em source-readiness.json como `requested_not_published`; a publicação é ato de dono A5.
- GAP-06-3. EP-REG-29a: `reason_ref` humano em `review_required` não existe no pin C6-4 (depende de sucessor).
- GAP-06-4. EP-REG-29b: recusa motivada só depois da ratificação RL-REG-29; o estado fechado está provado por teste.
- GAP-06-5. TB-E-08-a: alerta de compliance sem emissor no plano. TB-E-08-c: prazo de fila ≤ dia 25 depende do relógio de TB-E-09-a/E-SF-07 e da ratificação TB-08.
- GAP-06-6. EP-REG-02b: formato de `review_route` indefinido.
- Limite declarado: `sla_claim_marker` é heurística de marcador; a garantia é o estado fechado até Q1/DEC-18.
- TB-E-16-b fora do épico: ver Q-W-MZO-GATE-02.

## 2026-11-16 ~23:40Z

- #1270 (E-SF-03) mesclado. CD-23 recebida e aplicada; ACHADO do SUPP-001 anotado para quando o E-SF-08 estiver na main.
- PR #1273 (E-SF-04) aberto. A base é a main 0e6c515, trazida por merge porque o ramo já tinha sido publicado e force-push é recusado; o diff só tem os 5 arquivos do épico. A revisão independente deu PASS. PRONTO só depois do CI e da integração lida.
- E-SF-10 pronto localmente. O ramo `sf-w1/gate/E-SF-10` (00cf46f) foi publicado sem PR, empilhado sobre o E-SF-06. A revisão independente deu PASS com 3 minor; os 2 reparos de texto (F1/F2) entram como commit adicional quando for a vez do PR. GAPs:
  - GAP-10-1. EP-REG-04c: literal TTL 1825 em SUPP-001/LGPD-DSR-001, inventariado como dívida, sem valor ratificado do qual derivar.
  - GAP-10-2. C6-4 sem campo `retention_class`: o intake real segue recusado mesmo com matriz válida (sucessor).
  - GAP-10-3. RN 623 art. 10 §6º ausente de NORMATIVE-BASELINE-v2.
- Nota do E-SF-10: `prospect.save`, `contact.request` e `journey.save` passam a recusar `unavailable/lifecycle_unqualified` em toda composição enquanto não houver matriz ratificada. O texto do TB-25 (i) exige isso. A vitrine pública e o preço por catálogo seguem servidos.

## 2026-11-17 ~00:20Z

- #1273 (E-SF-04): o CI pegou duas falhas reais que o meu recorte local não cobria. (1) Pureza do adaptador: o `maezo.adapters.amh` não pode importar `maezo.gateway`, e o módulo de cercas foi movido para `maezo.adapters.amh.commercial_fences`. (2) Dois `# noqa: PLC0415` inertes. Os dois reparos já foram publicados; o head atual é 714cfdb e o CI está rodando. Daqui em diante rodo `tests/unit/ci`, `tests/unit/adapters` e `tests/unit/ports` antes de cada push.
- **E-SF-36 — caminho fixado (o plano não nomeia arquivos):** `src/maezo/gateway/commercial_disclosure_checklist.py` + `tests/unit/gateway/test_esf36_disclosure_checklist.py`. Motivo: o REG-34 põe o checklist na admissão de `offer.terms`, no gateway, ao lado dos `commercial_*.py` da GATE. O arquivo novo não está na tabela do §4.
  - CAMINHO src/maezo/gateway/commercial_disclosure_checklist.py — registrar como da GATE.
  - O ramo `sf-w1/gate/E-SF-36` (1c3e69f) foi publicado sem PR. A revisão independente deu PASS. O módulo ainda não está ligado ao gateway: não existe botão de decisão no maezo, e o C6-4 não tem campo por inciso.
  - GAPs:
    - GAP-36-1. Incisos V e VI do art. 4º da RN 413 elididos ("[...]") na NORMATIVE-BASELINE-v2; ficam com verbatim nulo.
    - GAP-36-2. O Guia do art. 18 continua sem fonte (GAP-10); o slot fica impreenchível.
    - GAP-36-3. O C6-4 não tem campo por inciso; os slots são candidatos para o sucessor.
    - GAP-36-4. Falta superfície de botão de decisão; o teste usa a pré-condição de `acceptance.record` com decision=accept.

## 2026-11-17 ~00:55Z

pronto: maezo-operadora #1273, head 019b5046f444f74d080aae83a466e52805ee5780, checks verdes (41 success e 2 skipped; integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skips). Enviado direto à mescladora (CD-42). A descrição do PR foi atualizada para o caminho `adapters/amh/commercial_fences.py`, sem push.

## Q-W-MZO-GATE-03 — E-SF-20 / EP-REG-08a: catálogo público misto (PF + coletivo)

A trava do coletivo fica na leitura do catálogo público, porque o C6-4 só traz o tipo de contrato ali. A revisão independente pediu que eu tirasse da resposta só os produtos coletivos `usable` e servisse o PF. Isso não é possível sem violar a custódia, por dois motivos:
- a rota só divulga depois que `PublicCatalogProof.result_digest` bate com o sha256 do corpo exato da fonte;
- o C6-4 manda "Maezo validates admitted source projection and returns the same source wire to Sales".

Opções:
- (A) **Recomendada para a onda 1, junto com (C).** O catálogo que traz coletivo `usable` é recusado inteiro, PF incluído (fail-closed). Hoje o efeito é latente: sem registro A1, o catálogo público já está fechado em toda composição.
- (B) O catálogo passa inteiro e a retenção do coletivo fica a jusante (omni/AMH com `offerState` rebaixado).
- (C) Demanda de sucessor C6-4 ao AMH: catálogo público só-PF, ou campo de retenção que permita avisar o comprador.
- (D) O maezo divulga uma projeção re-autorizada (exige sucessor C6-4).

Sigo por (A) e registro (C) como demanda: PROVISÓRIO-aguarda-coordenador. Também declarado: GAP-ESF20-TERMS. A admissão não impede `offer.quote`/`offer.terms` com `product_ref` coletivo; o critério depende do sucessor C6-4 (E-04) e do AMH honrar `offerState`.

## 2026-11-17 ~01:35Z

- #1273 (E-SF-04) mesclado (main 71f19004).
- PR #1278 (E-SF-06) aberto, head c4be57001609c4aeb63c5e73d2de97e38a62fa58: main por merge e imports apontados para `maezo.adapters.amh.commercial_fences`. Antes do push rodaram ci, adapters, ports, gateway, portal, docs e comercial; só houve falhas ambientais. PRONTO depois do CI.
- E-SF-20 pronto localmente. O ramo `sf-w1/gate/E-SF-20` (5f92e33) foi publicado sem PR, empilhado sobre o E-SF-36. A revisão independente deu FAIL e depois PASS; a decisão sobre o catálogo misto está em Q-W-MZO-GATE-03.
  - Flag `collective_legitimacy_screen` OFF, garantida por teste.
  - Tela staff dormente: 403/503 mostra "não habilitada", sem POST.
  - Art. 29 §1º aparece só como texto.
  - Vitest 786/786.
  - GAPs:
    - GAP-ESF20-TERMS.
    - O C6-4 só traz o tipo de contrato no catálogo.
    - A recusa C6-4 não tem campo de texto; a constante vai para o omni (E-SF-ADD01-06).
    - Não estão na tela: a entrada de referência de evidência e a data de reconfirmação.
- Situação da janela: os 7 épicos estão implementados e revisados. Mesclados: 02, 03 e 04. Aberto: 06 (#1278). Ramos publicados aguardando a vez: 10, 36 e 20.

## 2026-11-17 ~02:10Z

pronto: maezo-operadora #1278, head c4be57001609c4aeb63c5e73d2de97e38a62fa58, checks verdes (40 success e 2 skipped; integração lida do junit: 5 shards, 1269 testes, 0 falhas, 0 erros, 7 skips). Enviado direto à mescladora (CD-42).

## 2026-11-17 ~03:05Z

#1278: a mescladora fez Update branch (head 6ca9325f6e2f3f59fa4096ced89cf2a263cd21d9); o CI desse head está verde (40 success e 2 skipped). A main avançou para 2b421e5 com o #1277, então o head está 3 commits atrás. Simulei o merge localmente: a cerca do #1277 e as suítes esf06, a5 e a7 deram 256 passed. Pela regra, o PRONTO só sai depois do próximo Update branch. Avisei a mescladora.

## 2026-11-17 ~04:00Z

#1278: o CI está verde no head dafb55af84a83bc7b0132cbb80d071a433394c23 (40 success e 2 skipped), mas o #1279 entrou depois, e o head ficou 4 commits atrás da main 9cf06684. Simulei o merge localmente e rodei gateway, agents, a5, a7, agent_def, adapters e noqa: 15326 passed e 1 failed, que é o test_audit_anchor (root, ambiental). A mescladora foi consultada; o PRONTO depende da decisão dela.
