# Funil de Vendas — BPMN 2026-11 (descritivo, nível 1)

14 diagramas BPMN 2.0 **descritivos** (isExecutable=false, colaboração multi-pool) do funil de vendas da operadora: etapas E01–E09, frentes F1–F4 e um ponta-a-ponta (subprocessos recolhidos). Rótulos em pt-BR; só papéis, nunca pessoas.

## Como verificar (cwd tools/; python com lxml)
- `/opt/homebrew/bin/python3 di_verifier.py ../bpmn/FUNIL-<ID>.bpmn` → XSD OMG válido, cobertura DI bidirecional dif a=0 b=0, geometria, domínio=0
- `/opt/homebrew/bin/python3 eye_check_mecanico.py ../bpmn/FUNIL-<ID>.bpmn` → 0 achados (rótulo×rótulo/nó/aresta, transbordo)
- `npx --no-install bpmnlint --config bpmnlintrc-di.json ../bpmn/FUNIL-<ID>.bpmn` → exit 0
- `node render_bpmn.mjs ../bpmn/FUNIL-<ID>.bpmn --out /tmp/r` → import bpmn-js sem warnings

## sha256(16) por artefato (gates verdes em todos)
| Artefato | sha |
|---|---|
| E01-entrada-com-preco | `93fd477961f4370f` |
| E02-qualificacao | `efe8fd5b014b1aa4` |
| E03-cotacao-e-comparador | `c17788b7e86bb519` |
| E04-proposta-e-termos | `59750d6e0933c094` |
| E05-aceite | `378a4b269ba0bb1c` |
| E06-contrato-e-pagamento | `bd39e7d125bd86a8` |
| E07-declaracao-de-saude | `eb19a831a844cf69` |
| E08-admissao-e-acompanhamento | `03cb8d88ccf1f169` |
| E09-pos-venda | `7c2cd44f069cbcca` |
| E2E-ponta-a-ponta | `d2d6ae739b4ca562` |
| F01-mediacao | `a6610bcf20bdbcbb` |
| F02-recuperacao-e-mensagens | `a81b77132cc7f0f0` |
| F03-canal-de-vendas | `8fb3d807f8cbf9af` |
| F04-retencao | `796f948ca4c4cc65` |

## Contratos de link (frentes ⇄ etapas)
Pareamento completo em `extras/PAREAMENTO-LINKS.md` — 22 FULL (1 throw ↔ 1 catch, grafia idêntica), 8 HALF by-design (portões fechados: F1-EMITE-ES1..7 e F3-CORRETOR-ENTRADA), 0 duplicados. Regra do dono: o throw vive onde o evento acontece (diagrama de detalhe); o E2E não lança em nome das etapas.

## Extras
`extras/PORTOES.md` (56 portões fechados com dono/fallback/fonte) · `extras/MATRIZ-RASTREIO.csv` (484 elementos) · `extras/LISTA-SP-OP-FALTANTE.md` (SP-OP existentes × 9 faltantes) · `extras/README-DIRETORIO.md`

## Notas
- Diagramas descritivos para pessoas — não são executáveis em engine; portões fechados aparecem como XOR + anotação com dono e default conservador.
- Citações normativas apontam o pacote de decisões do programa (RATIFY-LATER): pendência de higiene registrada no canal de coordenação (Q-MZO-01).
