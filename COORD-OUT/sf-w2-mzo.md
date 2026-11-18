# COORD-OUT — sf-w2/mzo (BPMN funil 2026-11)

## Q-MZO-01 — citações a documentos do pacote privado nos BPMN (higiene de repo público)
Contexto: os 14 BPMN citam verbatim (arquivo:linha) o pacote de decisões (RT-J3.md:22, ADDENDUM-01.md:42, RATIFY-LATER-REGISTER.md:28, baseline L###, RLR:###, TSV L###, EXECUTION-LOG §7) — exigência da missão (§4 citação verbatim rastreável). O maezo-operadora é público hoje e §6.3 veda nome de documento do pacote privado em PR público; já existem 38 citações publicadas com PR de higiene em curso.
Opções:
  A) Entregar como está; incluir estas citações na mesma fila de higiene centralizada (mapeamento para docs/compliance/legal/textos-base/). [RECOMENDADA — preserva rastreabilidade RATIFY-LATER e os vereditos cegos; alterar as citações agora invalidaria os PASS]
  B) Sanitizar antes do PR (substituir refs por números de norma públicos). [Custo: retrabalho de 14 arquivos + nova rodada de revisão cega]
PROVISÓRIO-aguarda-coordenador: seguindo com A.

## PRONTO (preenchido no push)
PRONTO — funil de vendas BPMN 2026-11 (janela sf-w2/mzo)
pronto: maezo-operadora #1307, head 784c55be05f3c7ba7f518e3a76230a8f8e86b87e, checks verdes (ver abaixo)

- 14 diagramas BPMN descritivos (E01-E09, F1-F4, E2E) + verificador (di_verifier/eye_check/render/bpmnlint cfg) + extras (PORTÕES, MATRIZ-RASTREIO, LISTA-SP-OP, PAREAMENTO-LINKS, README-DIRETORIO) + README — adição pura em docs/processes/sales-funnel/BPMN-2026-11/ (24 arquivos, +8,6k linhas, 0 deleções; rebasado no origin/main; make validate-artifacts OK)
- Gates por artefato (14/14): XSD OMG, cobertura DI bidirecional 0/0, domínio 0, bpmnlint 0, eye-check mecânico 0, render bpmn-js 0 warnings
- Revisão: cega PASS 14/14 + micro-rechecks pós-onda-de-links 10/10 (evidências em REVISAO-CEGA/*-VEREDITO.md no workspace da janela) + olho humano final: 10/10 zonas do E09 RESOLVIDAS, 0 defeitos persistentes (OLHO-HUMANO-10.md)
- Pareamento de links fleet-scale: 22 FULL · 8 HALF by-design (portões fechados, documentados) · 0 DUP
- Pedido à fila: PR docs-only, sem toque em caminhos compartilhados; mescla por squash pela Coordenação estratégica

DONE 784c55be05f3c7ba7f518e3a76230a8f8e86b87e FUNIL-E01-entrada-com-preco.bpmn=93fd477961f4370f FUNIL-E02-qualificacao.bpmn=efe8fd5b014b1aa4 FUNIL-E03-cotacao-e-comparador.bpmn=c17788b7e86bb519 FUNIL-E04-proposta-e-termos.bpmn=59750d6e0933c094 FUNIL-E05-aceite.bpmn=378a4b269ba0bb1c FUNIL-E06-contrato-e-pagamento.bpmn=bd39e7d125bd86a8 FUNIL-E07-declaracao-de-saude.bpmn=eb19a831a844cf69 FUNIL-E08-admissao-e-acompanhamento.bpmn=03cb8d88ccf1f169 
- Branch: sf-w2/mzo/bpmn-funil-2026-11 (rebaseado no origin/main; adição pura: 24 arquivos, +8621/-0)
- Conteúdo: docs/processes/sales-funnel/BPMN-2026-11/ — 14 BPMN descritivos + verificador (di_verifier/eye_check/render/bpmnlintrc) + extras + README
- Gates por artefato: XSD, DI 0/0, domínio 0, bpmnlint 0, eye 0, render 0 warnings; make validate-artifacts OK
- Pareamento de links: 22 FULL · 8 HALF by-design · 0 DUP (extras/PAREAMENTO-LINKS.md)
- Revisão: cega PASS em 14/14 + micro-rechecks pós-onda-de-links (evidências em REVISAO-CEGA/)
DONE <commit-sha> <sha256s> — preenchido no push final
