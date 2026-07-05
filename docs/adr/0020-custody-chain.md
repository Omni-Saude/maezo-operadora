# ADR-0020: Chain-of-custody tamper-evidente como PROJECAO sobre a cadeia de auditoria ADR-0007 (nao fork da cadeia)

**Status:** Accepted (2026-07-05)
**Data:** 2026-06-13
**Area:** Auditoria / Seguranca / Compliance
**Regulado:** parcial — o periodo de retencao regulatoria (5+ anos), a interacao retencao-vs-erasure
LGPD e o desenho de legal-hold sao **DRAFT — requires human review (jurídico/regulatório/DPO) before
any deploy**.

## Contexto

A Phase 3 introduz SP-OP-FRAUDE-001 (deteccao de fraude/abuso). Por ADR-0018, **nenhum caminho
automatizado acusa fraude**: o indicio so roteia para uma User Task humana (`UT_DecisaoInvestigador`,
espelhando `indicio_fraude_sinalizado` → `auditoria-contas` em SP-OP-CONTAS-001); a acusacao e um
efeito adverso L0-hard que nasce no humano, guardado por worker `ERR_*_NOT_HUMAN`. Mas um processo de
fraude tem uma exigencia adicional que os demais negativa-like nao tem com a mesma forca: a **prova**
que sustenta a decisao humana precisa ser **tamper-evidente** e **reproduzivel** — um investigador (e,
depois, ANS/justica) precisa demonstrar que o conjunto de evidencias que embasou a decisao nao foi
alterado depois, e que a referencia analitica (o sinal populacional/atuarial de ADR-0019) pode ser
reproduzida anos depois.

A ADR-0007 ja entrega o instrumento certo: uma **cadeia de auditoria append-only assinada** (Postgres
append-only + topico `agents.audit`), onde todo efeito no mundo registra
`(agent_id, agent_version, tenant, tool, input_hash, decision_basis, dmn_versions, model_id,
prompt_version, timestamp)`, com `decision_basis` estruturado e o aprovador humano registrado nas
acoes que exigem humano. A cadeia ja e a fonte de nao-repudio da plataforma.

A tentacao seria construir um **segundo** mecanismo de evidencia — uma cadeia de custodia paralela,
com seu proprio armazenamento imutavel e sua propria sequencia hash. Isso seria um **fork da cadeia**:
duas fontes de verdade de integridade a reconciliar, dois pontos de falha, duas superficies de
adulteracao. O mesmo anti-padrao que a ADR-0013/ADR-0019 evitam nos dados (duplicar registro), agora
na auditoria.

Tres opcoes foram consideradas:

1. **Cadeia de custodia paralela e independente** (rejeitada). Forka a integridade: a custodia teria
   sua propria sequencia hash desconectada da `agents.audit`, exigindo reconciliacao e abrindo a
   possibilidade de as duas divergirem (qual e a verdade?).

2. **Guardar a evidencia bruta (PHI clinico) dentro da cadeia de auditoria** (rejeitada). Tamper-
   evidencia trivial, mas viola ADR-0006 (PHI fora de zona) e ADR-0002/ADR-0019 (erasure): a cadeia
   append-only seria contaminada com PHI bruto impossivel de apagar.

3. **CustodyBundle como PROJECAO sobre a cadeia ADR-0007, selada de volta na propria cadeia, com a
   evidencia bruta apenas como `input_hash` + Object-Lock no S3 PHI-zone do amh-data-platform**
   (escolhida). Uma fonte de integridade (a cadeia ADR-0007); a custodia e uma vista derivada+selada,
   nao um segundo livro-razao.

## Decisao

1. **A chain-of-custody e uma PROJECAO sobre a cadeia de auditoria ADR-0007 — nao um fork.** Nao
   existe segundo livro-razao de integridade. A custodia deriva, ordena e sela referencias que ja
   vivem na cadeia ADR-0007; a unica fonte de verdade de integridade continua sendo a
   `agents.audit` append-only assinada.

2. **`CustodyBundle` + `bundle_root` (Merkle sobre `record_hashes` ordenados).** Para uma
   investigacao, monta-se um `CustodyBundle`: o conjunto **ordenado** das referencias de evidencia
   (cada uma um `record_hash` ja presente na cadeia ADR-0007), e calcula-se um `bundle_root` como
   **raiz Merkle** sobre esses `record_hashes` ordenados. O `bundle_root` resume, num unico hash, o
   conjunto exato de evidencia e sua ordem — qualquer adulteracao posterior (adicao, remocao,
   reordenacao, edicao) muda a raiz.

3. **Selagem de volta na cadeia ANTES da decisao humana.** O `bundle_root` e **selado de volta na
   propria cadeia ADR-0007** (um registro de auditoria cujo conteudo inclui o `bundle_root`) **antes**
   da User Task `UT_DecisaoInvestigador`. Consequencia: a decisao humana de fraude so e tomada sobre
   um bundle ja selado; o ato de decidir fica ancorado a um conjunto de evidencia cuja integridade foi
   fixada **antes** da decisao, nao depois. Espelha o padrao ADR-0018 (o terminal adverso so apos a
   User Task humana) e o reforca: aqui, a User Task so apos o selo do bundle.

4. **Evidencia bruta como `input_hash` apenas + Object-Lock no S3 PHI-zone do amh-data-platform.** A
   cadeia ADR-0007 **nunca** carrega evidencia clinica bruta (respeita ADR-0006/ADR-0002): carrega o
   `input_hash` (ja parte do registro ADR-0007). A evidencia bruta em si reside no **S3 PHI-zone do
   amh-data-platform sob Object-Lock** (WORM — write-once-read-many), consumindo a imutabilidade que o
   amh-data-platform ja entrega (consume-not-duplicate, ADR-0013/ADR-0019): nao construimos um store
   imutavel proprio. O `input_hash` na cadeia + o objeto Object-Locked no lake juntos provam que a
   evidencia referenciada e exatamente a evidencia selada.

5. **Reprodutibilidade: freeze do snapshot de feature-store referenciado.** Quando a evidencia inclui
   sinal analitico/atuarial (o agregado de ADR-0019), o `CustodyBundle` **congela a versao do snapshot
   de feature-store** que o `PopulationFeatureClient` pinou (ADR-0019 item 2). Assim a referencia
   analitica e **reproduzivel** anos depois — a decisao pode ser re-derivada sobre o mesmo snapshot,
   nao sobre o estado atual (mutante) do lake.

6. **Retencao 5+ anos vs erasure LGPD — interacao explicita (DRAFT).** O snapshot/bundle precisa
   sobreviver pela retencao regulatoria (**5+ anos — DRAFT/verify**, periodo pendente de sign-off
   jurídico/regulatório), o que **tensiona** o direito ao esquecimento (ADR-0002/ADR-0019). O desenho:
   - a cadeia carrega **hashes**, nao PHI — hashes nao sao, isoladamente, dado pessoal apagavel;
   - a evidencia bruta sob Object-Lock fica sob **legal-hold** enquanto durar a retencao/investigacao:
     um pedido de erasure LGPD que colida com um legal-hold ativo e **resolvido a favor do hold**
     durante o periodo regulatorio (a base legal de obrigacao legal/exercicio de direito prevalece
     sobre o apagamento), e o erasure e **diferido/reconciliado** ao fim do hold;
   - o surrogate de ligacao `mpi_id<->fhir_patient_id` (ADR-0019) so e dropado quando nao houver
     legal-hold ativo que dele dependa.
   Toda essa interacao retencao-vs-erasure-vs-legal-hold e **DRAFT — requires human review
   (jurídico/regulatório/DPO) before any deploy**; o codigo nao decide unilateralmente apagar nem
   reter sob colisao — roteia a decisao a humano (consistente com ADR-0018).

7. **Base para SP-OP-FRAUDE-001.** Este ADR e a base de evidencia/custodia do SP-OP-FRAUDE-001: o
   processo de fraude monta o `CustodyBundle`, sela o `bundle_root` antes de `UT_DecisaoInvestigador`,
   e so entao o investigador humano decide (acusacao = efeito adverso L0-hard, ADR-0018, guardado por
   worker `ERR_*_NOT_HUMAN`). Nenhum branch automatizado acusa fraude nem sela um bundle como
   "fraude confirmada".

## Consequencias

**Positivas:**
- Uma so fonte de integridade: a custodia e vista+selo sobre a cadeia ADR-0007, nao um segundo
  livro-razao a reconciliar; nao ha "qual cadeia e a verdade?".
- Tamper-evidencia por construcao: o `bundle_root` Merkle selado de volta na cadeia detecta qualquer
  adicao/remocao/reordenacao/edicao da evidencia apos o selo.
- O selo **antes** da decisao humana ancora a decisao a um conjunto de evidencia fixado a priori —
  forte para defesa ANS/justica (a evidencia nao foi montada depois para justificar a decisao).
- Respeita ADR-0006/ADR-0002/ADR-0019: a cadeia carrega so hashes; o PHI bruto fica no S3 PHI-zone
  Object-Locked do amh-data-platform (imutabilidade consumida, nao reconstruida).
- Reproduzivel a longo prazo: o freeze do snapshot de feature-store permite re-derivar a decisao sobre
  o mesmo estado analitico anos depois.

**Negativas (aceitas):**
- Custo de retencao: snapshots de feature-store congelados por 5+ anos tem custo de armazenamento
  (Object-Lock + snapshot); aceitavel como custo regulatorio.
- Tensao retencao-vs-erasure nao tem resolucao puramente tecnica: a colisao legal-hold × direito ao
  esquecimento e **DRAFT** e roteia a sign-off jurídico/DPO — o codigo nao a automatiza nos dois
  sentidos.
- Dependencia do Object-Lock do amh-data-platform (consume-not-duplicate): se o lake nao expuser
  WORM/legal-hold para a PHI-zone, o controle de imutabilidade da evidencia bruta fica gated por esse
  desbloqueio (analogo ao AWS-blocked da ADR-0019).
- A custodia herda a latencia humana de ADR-0018: a decisao de fraude so apos o selo do bundle e a
  User Task — por desenho, nao acelera o adverso.

## Supersedes

— (e uma PROJECAO sobre a ADR-0007 — nao a substitui nem a forka; consome a imutabilidade do
amh-data-platform da ADR-0013/ADR-0019 (Object-Lock S3 PHI-zone + snapshot de feature-store),
respeita a ADR-0006 (PHI fora da cadeia) e a ADR-0002/ADR-0019 (erasure × retencao/legal-hold), e
materializa a evidencia do efeito adverso L0-hard regido pela ADR-0018. Base de SP-OP-FRAUDE-001.)
