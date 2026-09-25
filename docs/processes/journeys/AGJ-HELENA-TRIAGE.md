# AGJ-HELENA-TRIAGE — Jornada de triagem da Helena (Phase 0)

Jornada conduzida por agente (AGJ — sem BPMN, ADR-0001: nao ha gatilho regulatorio na
conversa em si; o gatilho regulatorio mora no escalonamento, que E processo:
SP-OP-ESCALATION-001). Runtime: LangGraph (`src/maezo/agents/helena/graph.py`),
autonomia L3 `triage_and_routing`/`scheduling`/`informational_response` (ADR-0008).

## Estados

| # | Estado | O que acontece | Tools |
|---|---|---|---|
| 1 | `saudacao_identificacao` | Mensagem WhatsApp chega; identifica beneficiario (pseudonimo via gateway) e recupera contexto | `mcp-whatsapp.send_message`, `mcp-memory.read_write` |
| 2 | `coleta_sintomas` | Conversa para entender a demanda; classifica intencao (sintoma / agendamento / informacao / pedido de humano); normaliza sintomas em `sintoma_codigo` + `intensidade` + campo populacional | `mcp-whatsapp.send_message` |
| 3 | `avaliacao_red_flag` | **TODA conversa com sintoma passa aqui, sempre.** Avalia a tabela populacional: `triage_redflag_adult` \| `triage_redflag_pediatric` (<12 anos) \| `triage_redflag_gestante` \| `triage_redflag_mental_health`. O LLM nunca decide red flag — a DMN decide (ADR-0012) | `mcp-dmn.evaluate` |
| 4 | `roteamento` | Sem red flag: orienta proximo passo administrativo (rede, especialidade, elegibilidade informativa) | `mcp-dmn.evaluate` |
| 5 | `agendamento` | Agenda/encaminha conforme L3 `scheduling` | (Phase 0: orientacao; tool de agenda em fase posterior) |
| 6 | `encerramento` | Resume, registra memoria episodica, NPS hook | `mcp-memory.read_write`, `mcp-whatsapp.send_message` |
| E | `escalado` | Conversa suspensa aguardando humano; Helena so retoma com `agents.events.process_completed` (`resultado=devolvido_agente`) | `mcp-cibseven.start_process` |
| R | `retomada` | (GAP-XHITL-4) O humano devolveu o caso: o consumidor `platform/integrations/agent_resume.py` le `notas_resolucao` do HISTORICO do motor e invoca a porta `resume` do grafo sob o MESMO thread de checkpoint da conversa. A instrucao passa pela cerca de saida (`motivo_de_recusa_da_retomada`) e sai pelo WhatsApp; desfecho proprio (`retomada_enviada` / `retomada_recusada` / `retomada_falha_envio` / `retomada_sem_instrucoes`) | `mcp-whatsapp.send_message` |

> **Helena NAO le FHIR.** Os estados 2 e 4 listavam `mcp-fhir.read_patient_summary`; a coluna
> foi corrigida pelo WP FHIR-TOOL-SURFACE-PARITY (NEW-09/GAP-TRIAGE-5). Nenhum no de
> `src/maezo/agents/helena/graph.py` tem campo `_fhir`, helena esta ausente de
> `gateway/tool_registry.py::_FHIR_ADAPTER_BY_AGENT` (unico lugar que preenche `deps["fhir"]`) e
> `spec/agents/helena/agent.yaml` deixou de declarar `mcp-fhir.read_patient_summary` e
> `mcp-fhir.search_coverage`. O unico `mcp-fhir.*` que resta no yaml dela e
> `mcp-fhir.read_coverage`, PIN DE CATALOGO sem call site e sem seam (OWNER-GATED — ver a nota do
> proprio yaml). Reintroduzir qualquer id aqui exige antes o no que o exerca.

## Gatilhos de escalonamento -> SP-OP-ESCALATION-001

Qualquer gatilho abaixo move a jornada para `escalado` IMEDIATAMENTE (business key
`ESC-{tenant_id}-{conversation_id}`, start idempotente):

| Gatilho | `motivo_categoria` | `severidade` | Origem |
|---|---|---|---|
| DMN `triage_redflag_*` retorna `red_flag=true` | `red_flag_clinico` (ou `risco_psicossocial` se tabela mental_health) | P1 -> `grave`; P2 -> `moderada` | estado 3 |
| Intencao classificada como pergunta clinica (diagnostico/conduta/medicacao) | `intencao_clinica` | `moderada` | qualquer estado (L0 hard `clinical_decision` — Helena NUNCA responde clinica) |
| Beneficiario pede humano (ou frustracao detectada 2x) | `solicitacao_humano` | `leve` | qualquer estado |
| Falha do classificador (excecao, JSON/schema invalido, roteamento PHI recusado) | `falha_tecnica` | `null` (ausencia declarada, nunca `leve` fabricado) | contrato SP-OP-ESCALATION-001, secao HEL-04 |
| DMN indisponivel com sintoma classificado | `falha_tecnica` | derivada da intensidade validada, conforme a secao HEL-04 do contrato SP-OP-ESCALATION-001 | estado 3 |
| Mencao a suicidio/autolesao/violencia em QUALQUER texto | `risco_psicossocial` | `grave` | classificador sempre-ativo, mesmo fora do estado 3 |

Payload completo do start: contrato em `docs/processes/contracts/SP-OP-ESCALATION-001.md`
(inclui `resumo_contexto` escrito pela Helena e `dmn_decision_ref`).

## Regras duras da jornada

1. Red flag e pergunta clinica nao tem "segunda opiniao do LLM": DMN/classificador
   disparou => escala. Ajuste fino e mudanca de DMN/policy, nao de prompt.
2. Enquanto `escalado`, Helena nao responde conteudo — apenas confirma que um humano
   assumira ("ja estou chamando alguem da equipe") e mantem o canal aberto.
3. Toda transicao de estado grava checkpoint (ADR-0002); toda tool call passa pelo PEP.
3a. **Saida de `escalado` (GAP-XHITL-4, conferido no codigo).** O grafo NAO guarda um marcador
    de "conversa suspensa": `escalado` e' a instancia viva de SP-OP-ESCALATION-001 no motor, e
    todo turno do beneficiario ja' zera as saidas do escalonamento em `receive`
    (`escalation_*`, `start_desfecho`). O que SOBREVIVIA ao escalonamento e precisava ser limpo
    e' a memoria de COLETA (`coleta_pendente`/`coleta_rodadas`/`coleta_contexto`): uma pergunta
    deixada em aberto antes do humano seria lida como respondida pela proxima mensagem. A porta
    `resume` zera essa memoria e preserva so' o que continua verdadeiro depois do humano
    (`apresentacao_ja_feita`, `memoria_clinica`). A origem do turno (`origem_do_turno`) e'
    reescrita por todo construtor de entrada, entao uma retomada que falhe no meio nao desvia o
    turno seguinte do beneficiario.
4. KPIs (agent.yaml): `first_response_p95 < 15s`, `nps > 75`, `escalation_rate` rastreada.
