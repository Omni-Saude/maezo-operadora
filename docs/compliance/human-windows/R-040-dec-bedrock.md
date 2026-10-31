# R-040 — Embeddings Bedrock (boto3 `bedrock-runtime`) fora de `runtime.inference`: registro da decisão do dono

**Natureza:** ARTEFATO DE RATIFICAÇÃO. Registra a opção que o dono escolheu e o que foi construído em seguida. Segue a regra de ouro desta árvore: o agente informa, não decide (ADR-0007). O bloco de ratificação (§4) fica em branco: nenhum agente preenche nome, data de assinatura ou assinatura.

**Origem:**
- Item R-040 do roadmap da auditoria forense FORENSIC-COMPLETENESS-2026-10 @f57f0821 (bundle fora da árvore versionada): decisão humana; dono: owner; flag `decision_conflict`; refs F-051 e PEND-168.
- Achado F-051 (alias F-ADR-07): `src/maezo/conhecimento/busca_semantica.py:102-106` (em `main` @331f7c4f, antes deste pacote) criava `boto3.client("bedrock-runtime")` fora de `runtime.inference`.
- Regra violada: AGENTS.md, regra dura 6 ("Não importar SDK de LLM fora do pacote `runtime.inference` nem credencial fora do gateway"); ADR-0009 §1.
- DEC-20 na issue #1084: <https://github.com/Omni-Saude/maezo-operadora/issues/1084#issuecomment-6545662766> (registrado em 2026-10-31T15:52:05Z): "DEC-20 embeddings boto3: (a) mover para runtime.inference."

## 1. Opções apresentadas ao dono

| Opção | Conteúdo |
|---|---|
| **(a)** | Mover a chamada de embeddings para `runtime.inference`. |
| (b) | Exceção registrada por ADR: embeddings não são LLM generativo; a credencial continua só no gateway. |
| (c) | Manter como está. |

Recomendação do roadmap: (a). A regra é dura e o custo é baixo.

## 2. Decisão registrada

- **Opção escolhida pelo dono:** **(a)**, mover a chamada para `runtime.inference`.
- **Data da decisão:** 2026-10-31.
- **Fonte:** issue #1084, comentário acima (DEC-20).

## 3. O que foi construído (PR do pacote R-040)

- `src/maezo/runtime/inference/embeddings.py` passa a ser o único lugar do repositório que constrói o cliente `bedrock-runtime` para embeddings. O código foi movido sem redefinição: mesmos modelos regionais admitidos (`cohere.embed-multilingual-v3`, `amazon.titan-embed-text-v2:0`), mesma região (`sa-east-1`, a região padrão do transporte da zona PHI), mesmo corpo do pedido, mesmas tentativas e mesma configuração do cliente.
- `src/maezo/conhecimento/busca_semantica.py` não importa mais `boto3` nem `botocore`. Os nomes antigos continuam importáveis dali e apontam para o mesmo objeto.
- `src/maezo/platform/webhooks/service.py` (raiz de composição) importa o transporte de `runtime.inference`.
- Cerca estrutural nova: `tests/unit/runtime/test_llm_sdk_only_in_runtime_inference.py`. Faz varredura AST de `src/maezo` fora de `runtime/inference` e recusa import de SDK de modelo e o nome de serviço AWS de invocação de modelo (`bedrock-runtime` e afins).
- Equivalência byte a byte, provada pelo teste `tests/unit/runtime/test_inference_embeddings.py` e por uma comparação A/B contra `origin/main` @331f7c4f (sha256 idêntico dos pedidos gravados).
- Os falsos de `TransporteDeEmbeddings` em `tests/` passam a ser conferidos contra o Protocol real (`tests/unit/runtime/test_inference_fakes_match_protocol.py`).
- Comportamento em execução: inalterado. A chave `MAEZO_HELENA_BUSCA_SEMANTICA` continua desligada por padrão. Nenhuma flag nova.

## 4. Bloco de ratificação (preenchimento exclusivo do dono; agentes não preenchem)

- **Opção ratificada:** [  ]
- **Decisor (papel owner):** [nome]   **Data:** [  ]   **Assinatura:** [  ]
- **Status:** [  ] (o dono escreve aqui a palavra de status que o `dod` do item R-040 procura no roadmap da auditoria)

**Fora do escopo desta decisão:** credenciais. A credencial AWS continua sendo a cadeia padrão do `boto3` no processo (papel da tarefa), como antes. Esta decisão não muda onde a credencial vive.
