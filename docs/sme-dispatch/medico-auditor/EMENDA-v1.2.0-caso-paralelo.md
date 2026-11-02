# RASCUNHO — Emenda v1.2.0 do SP-OP-ESCALATION-001: caso clínico paralelo

**Status deste documento:** RASCUNHO para o médico-auditor e o regulatório confirmarem ou corrigirem.
**Não altera o contrato.** `docs/processes/contracts/SP-OP-ESCALATION-001.md` continua FINAL (v1.1.0)
até que um PR revisado aplique a emenda e as duas pessoas assinem a v1.2.0 (modelo de signoff em
`SIGNOFF-MODELO-SP-OP-ESCALATION-001.yaml`, nesta pasta). Preparado em 20/10/2026 (com apoio de IA);
nenhuma linha aqui é parecer médico ou jurídico.

Na planilha `Revisao_Clinica_Helena_Escalonamento_2026-10-20.xlsx`, aba **"Caso paralelo"**, há uma linha
para cada diferença da tabela abaixo, com a coluna de veredito vazia.

## 1. Por que emendar

O contrato v1.1.0 (`docs/processes/contracts/SP-OP-ESCALATION-001.md:99-112`) descreve o caso clínico
paralelo como a DL-0072 o criou em 04/10/2026: só alerta clínico **grave**. Em 14/10/2026 o dono decidiu
ampliar (DL-0097, item B) e o código passou a abrir o caso paralelo para **qualquer** motivo clínico. O
contrato ficou para trás, e ele é mais permissivo que o código: quem lê só o contrato acha que uma queixa
clínica moderada pode ficar atrás de um caso de cobrança, e o sistema não faz isso.

**Sugestão:** emendar o contrato para o comportamento do código, que é o mais conservador. A alternativa
(voltar o código ao contrato) faria uma queixa clínica moderada receber "seu atendimento já está aberto"
atrás de um caso de cobrança — o defeito que a DL-0097 fechou no teste real do dono.

## 2. Contrato × código

| # | Ponto | Contrato v1.1.0 diz | Código faz | Sugestão |
|---|---|---|---|---|
| 1 | Severidade | Só alerta clínico GRAVE abre o caso `-clin` (`SP-OP-ESCALATION-001.md:99-101`) | Qualquer severidade clínica (`src/maezo/agents/helena/graph.py:827-833`, `_MOTIVOS_CLINICOS_COM_CHAVE_PROPRIA`; uso em `graph.py:9000-9025`) | Emendar o contrato para o código |
| 2 | Motivos | `motivo_categoria` ∈ {`red_flag_clinico`, `risco_psicossocial`} (`:100`) | Também `intencao_clinica` (pergunta clínica, P2/enfermagem pela `escalation_routing` r4) (`graph.py:831-833`) | Emendar o contrato para o código |
| 3 | Caso clínico já aberto | "Com um caso CLINICO ja aberto na conversa (qualquer das duas chaves) nao se abre outro" (`:109`) | Um caso de **alerta** cobre qualquer motivo clínico; um caso de **pergunta clínica** (`intencao_clinica`) só cobre outra pergunta clínica — um alerta novo abre o próprio `-clin` (`graph.py:836-841`, `_caso_clinico_cobre_o_motivo`) | Emendar o contrato para o código |

Os números de linha de `graph.py` valem para `origin/main` de 20/10/2026; a planilha os recalcula a cada
geração (`scripts/sme/revisao_clinica.py`, `coletar_caso_paralelo`).

**Limite conhecido, que nem o contrato nem o código cobrem hoje** (não é uma diferença, é um buraco a
decidir): a conversa tem no máximo **duas** chaves. Se a chave da conversa estiver ocupada por outro
assunto (ex.: cobrança) e a chave `-clin` já tiver uma **pergunta clínica** aberta, um **alerta** novo
cai na instância `-clin` existente (o início idempotente devolve a instância viva e o grafo só registra
`helena_escalonamento_nao_aberto`, `graph.py:9051-9066`): o alerta fica dentro de um caso P2. A decisão
de como tratar isso (ex.: o alerta sobe a prioridade da instância viva, ou abre uma terceira chave) é de
produto e clínica, e mudaria o BPMN; esta emenda **não** a propõe, só a registra para não ficar escondida.

## 3. Texto proposto para substituir as linhas 99-112 do contrato

> **Excecao ratificada (DL-0072, 04/10/2026; ampliada pela DL-0097 B, 14/10/2026) — caso clinico
> paralelo.** Um motivo CLINICO — `motivo_categoria` = `red_flag_clinico`, `risco_psicossocial` ou
> `intencao_clinica`, de QUALQUER `severidade` — nunca e suprimido por um caso aberto de OUTRA classe na
> mesma conversa (por exemplo, cobranca do Lucas ou pedido de pessoa). Nesse caso a Helena abre UMA
> segunda instancia, sob a chave
>
> ```
> ESC-{tenant_id}-{conversation_id}-clin
> ```
>
> com o mesmo contrato de variaveis (o `resumo_contexto` anota que ha outro caso aberto). O caso
> original nao e cancelado nem alterado. A prioridade e o grupo da segunda instancia sao os da
> `escalation_routing` para o motivo e a severidade dela (alerta grave e risco psicossocial: P1;
> alerta nao grave e pergunta clinica: P2).
>
> **Quando ja ha caso clinico aberto.** Um caso de ALERTA clinico aberto (`red_flag_clinico` ou
> `risco_psicossocial`, em qualquer das duas chaves) cobre qualquer motivo clinico novo: nao se abre
> outro. Um caso de PERGUNTA clinica (`intencao_clinica`) aberto cobre so outra pergunta clinica: um
> ALERTA novo, com a chave da conversa ocupada, abre o proprio caso `-clin`. No maximo duas instancias
> ativas por conversa (a chave da conversa e a `-clin`).
>
> O `agent-resume` aceita as duas chaves como ancora da conversa
> (`runtime/caso_clinico.py::chave_e_da_conversa`); o `conversation_id` das duas instancias e o mesmo,
> entao a retomada volta para a mesma conversa. Cobranca nunca abre caso clinico.

Junto com o texto, a linha `**Status:**` do contrato passa a `FINAL (v1.2.0)` com a emenda citada, e a
entrada da v1.2.0 entra no histórico do contrato. Isso exige **signoff da v1.2.0**: um signoff da v1.1.0
não vale para a v1.2.0 (`src/maezo/platform/validation/signoff.py`, `_check_final_signoff`).
**Ordem sugerida:** assinar primeiro a v1.1.0 (retroverificação que já está pendente), e só depois
aplicar esta emenda e assinar a v1.2.0 — ou aplicar a emenda antes e assinar direto a v1.2.0. As duas
ordens são válidas; a segunda poupa uma rodada.

## 4. Pergunta curta ao regulatório

> Com caso aberto de outro assunto na mesma conversa (por exemplo, uma cobrança), a Helena abre um
> **segundo caso, clínico**, para qualquer queixa ou pergunta clínica, com prazo próprio (alerta: ciência
> em 5 min; queixa não grave e pergunta clínica: 30 min). (a) Isso atende ao art. 35-C da Lei 9.656/1998
> ("urgência/emergência = atendimento imediato") — em particular, o prazo de 30 min da pergunta clínica
> é aceitável quando o relato não tem sinal de alarme? (b) Para a RN 623/2024, cada um dos dois casos é
> uma demanda com protocolo e prazo de resposta próprios, ou o segundo pode seguir sob o protocolo do
> primeiro? Resposta esperada: "sim/não" em cada item, com o ajuste se for "não".
