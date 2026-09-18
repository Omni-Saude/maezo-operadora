# ADR-0051: A memória clínica da conversa NÃO é leitura de prontuário — o `ClinicalContextPort` continua sendo a única costura clínica (XRD-06 do ADR-0037)

**Status:** Proposed — **NÃO RATIFICADO.** · **Data:** 2026-09-18 · **Área:** Fronteira de plataforma / Privacidade / Governança de ADR

> **Enquadramento.** Este ADR **não emenda o texto do ADR-0037**: a regra da casa é explícita
> (`docs/adr/README.md:8` — *"ADR aceito só muda por novo ADR"*) e **DL-0043** registrou e proibiu
> nominalmente que um agente emende um ADR aceito por conta própria. O que ele faz é **registrar uma
> medição** e propor que ela seja anexada ao XRD-06 como nota interpretativa. A ratificação é ato do
> dono do repositório.
>
> **Escopo estritamente docs-only.** Não edita `src/`, `spec/`, `tests/`, `deploy/`, nenhuma
> allowlist e nenhum port.

---

## Contexto

A Frente 2.1 (PR #404, mergeada) deu à Helena uma **memória clínica** de conversa: população
(adulto/pediátrico/gestante), idade em anos, idade em meses e idade gestacional em semanas
sobrevivem de um turno para o seguinte, dentro de uma janela de tempo.

Ela existe porque a ausência desses quatro campos **muda a tabela de triagem consultada**. O
defeito que a motivou foi medido: a Helena triou um bebê pela tabela de adulto porque a idade tinha
sido dita num turno e esquecida no seguinte.

**E o nome dela é um problema de governança.** "Memória clínica" soa como leitura de prontuário, e
o XRD-06 do ADR-0037 é taxativo sobre isso:

> *"Só a AMH escreve no HAPI. O Maezo lê contexto clínico **exclusivamente** via um
> `ClinicalContextPort` read-only sobre a API subject-context; na célula AMH não recebe credencial
> de escrita HAPI."*

Um leitor que encontre `memoria_clinica` no estado da Helena tem duas leituras possíveis, e as duas
são ruins: que a exclusividade do XRD-06 foi furada, ou que ela nunca valeu de verdade.

## O que foi medido (18/09/2026)

**A memória clínica não lê prontuário nenhum.** Cada um dos quatro campos vem do que **o
beneficiário disse na própria conversa**:

- o único escritor é `_memoria_a_gravar(extraction, ...)`, e `extraction` é a extração da mensagem
  recebida (`src/maezo/agents/helena/graph.py`);
- o único leitor é `_fundir_memoria_clinica(extraction, memoria)`, que funde o que a mensagem trouxe
  com o que a conversa lembrava;
- o transporte entre turnos é o **checkpoint do grafo**, não um datastore clínico;
- nenhum adapter, nenhum port e nenhuma credencial participam do caminho.

**E o `ClinicalContextPort` não tem chamador vivo.** Hoje ele é referenciado pelo próprio Protocol,
pelo adapter `adapters/amh/subject_context.py`, pelo registro de classes de efeito
(`gateway/effect_classes.py`) e pelo `gateway/amh.py`. **Nenhum agente o chama** — a Helena
inclusive. O MZO-050+ que ligaria isso continua gated.

**Conclusão da medição: a exclusividade do XRD-06 está intacta, e por uma razão mais forte do que
"ninguém furou" — não há, hoje, nenhuma leitura de contexto clínico acontecendo no payer core.**

## Decisão proposta

1. **Anexar ao XRD-06 a distinção de fonte**, como nota interpretativa e sem alterar a cláusula:

   > *Contexto clínico **relatado pelo próprio sujeito dentro de um turno de conversa** e carregado
   > no estado do agente não é leitura de contexto clínico para efeito desta cláusula, e não passa
   > pelo `ClinicalContextPort`. A exclusividade do XRD-06 governa a leitura do **registro
   > clínico**; ela não governa o que uma pessoa acabou de dizer sobre si.*

   O critério que separa os dois casos é **de onde o dado veio**, não do que ele fala: dado que
   nasce na mensagem do beneficiário é auto-relato; dado que nasce num sistema de registro é
   leitura, e só o port pode fazê-la.

2. **Registrar que a distinção tem uma fronteira, e onde ela está.** No dia em que alguém quiser
   preencher `memoria_clinica` a partir de uma condição, um encontro ou uma cobertura do registro,
   isso **é** XRD-06 e passa pelo port, com `purpose_of_use` e `consent_decision_ref` — que são
   argumentos obrigatórios na costura, sem default, e o mypy recusa a chamada que os omita. Este
   ADR não abre esse caminho; ele nomeia onde ele começa.

3. **Não propor nenhuma mudança de código.** A medição encontrou o sistema já conforme. Um ADR que
   ratifica o estado medido vale mais do que um refactor que o perturbaria.

## Consequências

**O que fica melhor.** A próxima pessoa que ler `memoria_clinica` tem a resposta escrita, em vez de
precisar refazer a medição ou — pior — supor. E a fronteira fica nomeada antes de alguém chegar
nela com um PR pronto.

**O que continua verdadeiro e incômodo.** Auto-relato clínico **ainda é dado de saúde** para efeito
de LGPD, e a memória clínica o retém por uma janela. O que o protege hoje é o desenho estreito da
Frente 2.1 (quatro campos, vocabulário fechado, janela de tempo, falha para `None` em qualquer
dúvida) — não uma base legal registrada. A **Frente 1.3** é quem define o que se pode guardar e por
quanto tempo, o `consent_log` tem **zero linhas**, e esse buraco é anterior a este ADR e não é
fechado por ele. A recomendação do `docs/design/o-que-falta-decidir-frentes-2-9-10.md` de **aposentar**
a memória episódica genérica (ADR-0043) se apoia exatamente nesse ponto, e continua de pé.

**O que este ADR não faz.** Não ratifica nada do MZO-040/050. Não descarrega nenhuma aprovação
Médica/ANS/Security. Não altera o `ClinicalContextPort`, que permanece read-only por construção,
sem método de escrita e sem escape hatch.

## Relação com ADRs existentes

- **ADR-0037 (XRD-06)** — este ADR **anexa** uma nota interpretativa; não supersede, não emenda o
  arquivo, não muda a cláusula.
- **ADR-0006** — a memória clínica vive no checkpoint, Zona Geral, e o `conversation_id` continua
  sendo HMAC irreversível. Nada aqui altera essa fronteira.
- **ADR-0043 (DRAFT)** — memória episódica/semântica: recomendação de aposentar, registrada em
  `docs/design/o-que-falta-decidir-frentes-2-9-10.md`. Este ADR é sobre a memória **estreita** da
  Frente 2.1, que é outra coisa e sobrevive à aposentadoria daquela.

## Ratificação

Ato do **dono do repositório**. Enquanto o status for `Proposed`, **nenhuma cláusula tem efeito** —
inclusive a nota interpretativa do item 1, que só passa a valer sobre o XRD-06 quando ratificada.

## Supersedes

Nenhum.
