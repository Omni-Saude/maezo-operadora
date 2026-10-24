# ADR-0066: Helena — roteador de habilidades com travas de segurança fixas

**Status:** Accepted pelo dono para dev (21/10/2026, "Pode executar"), implementação em fases atrás de chave; NÃO vale
para produção
**Data:** 2026-10-21
**Área:** Agentes (Helena) | Orquestração | Segurança
**Decisor:** dono do repositório (filipecarmo81 — CTO e DPO).
**Plano aprovado:** `Plano_Nova_Arquitetura_Helena.docx` (21/10/2026). **Registro curto:** DL-0134
(`docs/decisions-log.md`). **Contrato do roteador:** `spec/agents/helena/roteador.yaml`.
**Complementa (não altera):** ADR-0062 (número único) e o nó `classify` da Helena. **Mantém:** ADR-0006, ADR-0012,
ADR-0062, ADR-0064, ADR-0065.

## Contexto

A decisão do assunto de cada mensagem da Helena virou uma pilha de ~88 regras de palavra-chave em `graph.py`
(~10.700 linhas; eram ~920 e 1 regra em julho). As regras disputam a mensagem antes e depois do classificador, ganha
a primeira que "pega", e o modelo vê só a mensagem atual. Uma pergunta pendente de uma habilidade (ex.: "para quem
é?" da cobertura) sequestra a mensagem seguinte, mesmo quando a pessoa mudou de assunto.

Medição independente de 21/10/2026 (imagem 993134cb): o entendimento do procedimento pedido está em 91–95%, as
travas de segurança deram zero respostas perigosas fora de um padrão (o sinal de alarme dito no turno seguinte à
batida na cabeça), e só **53% das mensagens em conversas completas** foram tratadas certo (~14% com assunto perdido
ou preso em pergunta anterior). A causa é o desenho do roteamento, não o modelo: cada correção pontual acrescenta
outra regra à pilha.

Alternativas consideradas: (a) continuar corrigindo regra a regra — rejeitada, é o que produziu o problema;
(b) entregar a conversa inteira ao modelo, sem camada fixa — rejeitada, faria a segurança depender do modelo
(contra a ADR-0012); (c) **roteador pelo modelo entre camadas fixas** — escolhida.

## Decisão

A Helena passa a decidir cada turno em **quatro camadas**, nesta ordem:

1. **Travas de entrada (regras fixas, sempre, nesta ordem, sem modelo):** (1) consentimento e identificação;
   (2) alarme pela tabela DMN (ADR-0012: 192; risco psicossocial com CVV 188); (3) risco psicossocial pelo texto;
   (4) proteção de dados pessoais (PII). Uma trava que decide encerra o turno; o roteador não é chamado.
2. **Roteador (GLM-5, zona PHI regional sa-east-1, ADR-0006/0017):** lê as últimas mensagens da conversa e a
   **pergunta pendente** (que pertence a uma habilidade e é informada ao roteador como contexto, não sequestra a
   mensagem) e escolhe **uma** habilidade, com confiança. Na dúvida, pergunta "Você quer saber sobre X ou Y?".
   Falas curtas ("ok", "valeu", "1", "sim") são lidas no contexto da pendência. Falha ou prazo do modelo → a
   resposta segura padrão (nunca silêncio) e as travas continuam valendo.
3. **Habilidades (módulos fechados):** cada uma decide só o seu assunto, guarda o seu estado (a sua pendência) e usa
   os seus textos aprovados: `cobertura`, `plano`, `saude`, `sintoma`, `equipe`, `pedidos`, `cobranca` e
   `conversa`. O contrato (`spec/agents/helena/roteador.yaml`) lista, para cada uma, quando usar, quando não usar,
   exemplos, as pendências que possui e o código atual reaproveitado. Habilidade nova só entra pelo contrato, com
   módulo, textos, testes, chave e o portão de conversas completas.
4. **Travas de saída (regras fixas):** nunca prometer nem negar cobertura; sem dose nem diagnóstico; sem link
   externo; caso aberto só com "sim" a uma oferta ou pedido explícito de pessoa; nome de dependente só do próprio
   titular verificado (DL-0127). As cercas atuais (`cerca_de_saida.py`, `saude_gerativa.verificar`) são
   reaproveitadas.

**Chave e escopo.** `MAEZO_HELENA_ROTEADOR` com três valores: `desligado` (default literal; a Helena atual byte a
byte), `sombra` (o roteador e as travas rodam em paralelo e só registram a decisão, sem responder) e `ativo` (o
roteador decide, só para as conversas da allowlist de teste). Desfazer em qualquer fase: voltar a chave para
`desligado` + apply direcionado (minutos).

**Fases.** F0 esta ADR + contrato + ficha; F1 roteador e travas em sombra, comparados com a decisão atual no
simulador e nas conversas de teste; F2 habilidades atuais migradas para módulos atrás do roteador (chave + allowlist);
F3 medição pelo avaliador independente (30 conversas ocultas + 82 pedidos de cobertura); F4 só com o OK do dono,
liga para a lista de teste; F5 remove as regras antigas substituídas.

**Aceite (medição independente, F3):** ≥ 90% das mensagens tratadas certo em conversas completas; procedimento
certo direto ≥ 90%; 0 casos abertos sem pedido ou sem "sim"; 0 falhas de segurança (inclusive o sinal de alarme no
turno seguinte); assunto perdido ≤ 3%; mediana ≤ 12 s com um procedimento.

**O que não muda.** ADR-0012 (alarme pela tabela, nunca pelo modelo; nunca diagnosticar nem orientar conduta);
ADR-0006/0017 (zonas de dados: o roteador usa o GLM-5 regional; o Haiku global continua só na redação de saúde da
allowlist do DPO, ADR-0065); ADR-0062 (número único, Helena na entrada, cobrança e cancelamento passados ao Lucas);
os 17 processos e os contratos de dados com a AMH; as decisões já registradas (textos, consentimento, nome dos
dependentes, sem links, caso só com "sim", recusa encerra P2/P3).

## Consequências

**Positivas:** a pendência deixa de sequestrar a mensagem; o assunto é decidido com a conversa à vista; cada
habilidade pode ser testada e trocada sem quebrar as outras; a segurança fica numa camada fixa, auditável e
independente do modelo; o código de roteamento encolhe na F5.

**Negativas (aceitas):** uma chamada a mais ao GLM-5 por turno (latência medida por turno; meta ≤ 12 s com um
procedimento); o roteador pode errar a habilidade (tratado por confiança + pergunta de desambiguação e medido por
conjunto oculto antes de qualquer teste do dono); durante a reconstrução coexistem os dois caminhos (a Helena atual
continua no ar para o time, com aviso de que a cobertura em conversa longa ainda falha); habilidades novas
(reembolso, inadimplência, programa, LGPD) ficam pausadas até a F4.

**Produção:** continua dependendo do médico responsável, do jurídico e da decisão do DPO sobre a residência.

## Supersedes

— (complementa a forma de implementar a ADR-0062 e o nó `classify` da Helena; nenhuma ADR antiga é alterada).
