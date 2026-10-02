# Lucas no número único: plano de execução (Helena como porta de entrada)

**Base:** `main` em `b29c62de` (#578). Não cobre branch aberta. **Status:** proposto, aguarda o
"ok" do Filipe neste documento e a assinatura do DL-0053 (§1.3). Nada aqui liga recurso: o
interruptor nasce desligado (§4).

## 1. Contexto e decisão

### 1.1 O que existe hoje (lido no código, em `b29c62de`)
- Um único webhook WhatsApp (`HelenaDispatcher`, `platform/webhooks/whatsapp/dispatch.py`), que
  roda o grafo da Helena **dentro do `webhook-receiver`**. O `agent-helena` do ECS não executa
  turno (`service-webhook-receiver.tf`, cabeçalho). Thread do checkpoint = `conversation_id` =
  `wa:{tenant}:hk1_{hmac}`.
- Lucas (`agents/lucas/graph.py`) é um grafo **de um turno só**: `receive -> gather -> assess ->
  {respond_member | escalate_human} -> start_process -> ...`. `intencao` é **entrada do chamador**,
  não classificação de texto. Não existe canal de entrada (GAP 11.7, `agent.yaml` `inbound: false`).
  O gate de entrada (`_CALLER_INPUT_FIELDS`, `new_lucas_state`) já existe.
- DL-0052 (01/10): cobrança, boleto e cancelamento caem em `outside_channel` e recebem
  `RESPOSTA_FORA_DO_CANAL`, sem abrir caso.
- O Lucas grava `motivo_categoria="outro"` e `severidade="moderada"` em toda passagem de negócio
  (`_motivo_categoria`, `_severidade_humano`), e o alerta de SLA faz o mesmo
  (`notification_bridge.py::SLA_ALERT_MOTIVO_CATEGORIA/SLA_ALERT_SEVERIDADE`). São rótulos que
  parecem decididos, mas ninguém os decidiu (review-queue, linhas 29 e 1647).

### 1.2 A decisão (Filipe, 01/10/2026), formalizada sem rediscutir
Número único, com a Helena na entrada. Em ordem de precedência: **(P5) pedido de pessoa** escala em
qualquer momento; **(P2) saúde/risco** fica sempre com a Helena; **(P3) cobrança** (boleto,
pagamento, contestação, cancelamento, "estou sendo cobrado") passa para o Lucas com uma frase curta;
**(P4)** o Lucas atende até acabar ou até a pessoa mudar de assunto; **(P6)** qualquer outro
assunto recebe o texto fixo do #577. O Lucas segue o `agent.yaml`: informa e confirma pagamento;
inadimplência, contestação e cancelamento vão **sempre** para um humano; nunca cancela nem suspende.

**Conflito entre P2 e P5, resolvido assim:** se a mesma mensagem tem sinal clínico e pedido de
pessoa, vale o escalonamento clínico (risco psicossocial e depois red flag). Ele também leva a uma
pessoa, e com prioridade maior (r1/r2 da `escalation_routing` = P1/plantão). Nos dois casos a
pessoa chega a um humano, e P5 continua atendida.

### 1.3 DL-0053 (PROPOSTO, texto pronto para o Filipe assinar; ainda NÃO está no log)
> | 2026-10-01 | DL-0053 | **Número único com roteamento por conversa: com `MAEZO_ROTEADOR_LUCAS`
> ligado, assunto de cobrança (boleto, 2ª via, vencimento, pagamento, contestação, cancelamento,
> "estou sendo cobrado") deixa de receber `RESPOSTA_FORA_DO_CANAL` e passa para o Lucas, com a
> frase fixa `FRASE_PASSAGEM_COBRANCA` na primeira passagem da conversa. Supersede em parte o
> DL-0052:** com o roteador desligado, o DL-0052 vale integralmente. Com ele ligado, o DL-0052
> continua valendo para preço/contratação, reembolso, autorização, agendamento e assuntos sem
> relação com saúde. Não mudam: saúde/risco vence sempre (a triagem da Helena roda antes de
> qualquer agente, em toda mensagem); pedido de pessoa escala com qualquer agente; o Lucas nunca
> cancela nem suspende, e inadimplência, contestação e cancelamento sempre vão para um humano.
> Junto entram dois tokens no contrato SP-OP-ESCALATION-001: `motivo_categoria` `cobranca` (Lucas)
> e `alerta_sla` (alerta de SLA), com `severidade` declarada ausente (`null`), como já é para
> `falha_tecnica`. O roteamento da DMN não muda: os dois tokens caem na `r7`, como o `outro` de
> hoje | Decisão do Diretor de Tecnologia da AMH (Filipe) em 01/10/2026. Texto da frase de
> passagem sujeito à revisão dele | Redação AGENTE Claude; verificação: baterias de §6(g) |

## 2. Modelo

### 2.1 Onde fica o "agente ativo da conversa": **tabela nova** `conversa_agente_ativo`
1. **Não no checkpoint.** Medi em 01/10 com langgraph 1.2.9 e `InMemorySaver`
   (script em scratchpad: dois grafos, mesmo `thread_id`, `checkpoint_ns` "" e "lucas"): o
   `checkpoint_ns` no grafo raiz é **ignorado**. O contador seguiu 1,2,3,4,5 entre os dois grafos
   e só existe a chave `''`. Dois grafos no mesmo thread se sobrescrevem.
2. **Não em variável de processo.** Conversa comum não abre processo, e o estado precisa existir
   antes de qualquer caso.
3. **Tabela com uma linha por `(tenant, conversation_id)`**, gravada por compare-and-set
   (`revisao`). É o mesmo padrão de `beneficiario_contato_retomada` (0016) e fica fora do estado
   dos dois grafos. **Com isso, o Lucas roda sem checkpoint** (o grafo dele já é de um turno). A
   continuidade dele são 3 colunas tipadas desta tabela, nunca texto.

### 2.2 Máquina de estados (por conversa; o default e o vencimento levam a `helena`)
| De | Evento (decidido nesta ordem, 1º que casar) | Para | `transicao_motivo` |
|---|---|---|---|
| qualquer | linha ausente ou `expira_em < now()` | helena | `inicio` / `retorno_inatividade` |
| qualquer | Helena escalou por risco, red flag ou pergunta clínica, ou abriu coleta | helena | `retorno_saude` |
| qualquer | Helena escalou por `solicitacao_humano` | helena | `retorno_pedido_humano` |
| qualquer | Helena caiu em `falha_tecnica` (classify falhou, handoff recusado) | helena | `retorno_falha` |
| helena | `handoff` tipado da Helena (`para="lucas"`) | lucas | `handoff_cobranca` (com frase) |
| lucas | `handoff` tipado de novo (outra pergunta de cobrança) | lucas | `handoff_cobranca` (sem frase) |
| lucas | Helena devolveu `greeting`/`information` sem sintoma ("ok", "e o de setembro?") | lucas | `continua_lucas`: o Lucas atende com o contexto das 3 colunas |
| lucas | Helena devolveu `outside_channel`/`scheduling` | helena | `retorno_fora_do_canal` (texto fixo) |
| lucas | Lucas terminou em `escalate_human` (J3 ou falha) | helena | `lucas_encerrou` |

`expira_em = ultimo_turno_em + MAEZO_LUCAS_INATIVIDADE_MINUTOS` (default 60). Só uma escrita por
mensagem recebida, **depois** dos dois turnos. Se o turno cai, nada é gravado, e a reentrega
recalcula. Se outra réplica gravou antes (CAS perdido), o roteador relê e recalcula. O roteador
não chama LLM, então recalcular é barato.

### 2.3 O que é determinístico e o que é LLM
**A regra estrutural:** a camada determinística só consegue **puxar para a Helena ou para um
humano**, nunca para o Lucas. Ir para o Lucas exige a saída tipada da Helena **neste mesmo request**.
- **Determinístico, antes de qualquer LLM** (`pre_roteamento.py`, no dispatcher; léxicos em
  `spec/policies/roteamento/pre-roteamento.yaml`, versionado, `status: DRAFT`, normalizado sem
  acento e caixa):
  - `pedido_humano_lexico` (P5): "atendente", "falar com uma pessoa", "humano", ... Vira entrada
    tipada da Helena e força o gatilho 3 depois do 5 e do 1. Um falso positivo sempre leva a uma
    pessoa.
  - `sinal_saude_lexico` (P2): termos fortes ("dor no peito", "falta de ar", "me matar", ...).
    **Bloqueia o handoff**. Não decide triagem nenhuma, porque quem decide é a DMN.
  - A ordem de precedência, as pré-condições do handoff (§2.4) e a máquina de §2.2.
  - A resposta de P6: texto fixo, sem modelo.
- **LLM (o classify da Helena, já existente, zona `bedrock_br`):** a intenção (`cobranca` x
  `outside_channel` x `greeting` x ...), `cobranca_subtipo`, `sintoma_codigo` e
  `psychosocial_risk`. "Piada/política/sem sentido" **não tem como ser determinístico**: a resposta
  é fixa, mas a detecção é do modelo. Na dúvida ele não usa `outside_channel` (regra do DL-0052).
  Se o classify falhar, nunca vai para o Lucas: vai para `falha_tecnica`, como hoje.

### 2.4 Como a Helena sinaliza o handoff: saída tipada
O classify ganha `intent="cobranca"` e `cobranca_subtipo` (domínio fechado, validado em
`_validate_extraction`; valor fora do domínio = JSON inválido = `falha_tecnica`). O nó novo
`handoff_cobranca` (sem LLM) escreve:
```
class HandoffCobranca(TypedDict):          # saída-only do HelenaState; só helena/graph.py constrói
    para: Literal["lucas"]
    cobranca_subtipo: Literal["boleto_2via","vencimento","confirmacao_pagamento",
                              "contestacao","cobranca_recebida","cancelamento","outro"]
    competencia: str | None                # "YYYY-MM" ou None, nunca texto livre
    message_ref: str                       # log_safe_message_id do inbound deste turno
```
**Pré-condição `_handoff_recusado`** (2 camadas, igual a `_inform_recusado`: em `classify` e em
`_route`): sem `error`, `psychosocial_risk is False`, `sintoma_codigo` nulo, `intent != symptom`,
`not sinal_saude_lexico`, `not pedido_humano_lexico` e interruptor ligado. Se recusar, vira
`falha_tecnica` (estado injustificado vai para humano). O `respond` envia
`FRASE_PASSAGEM_COBRANCA` só se `agente_ativo_conversa == "helena"`. Se o Lucas já está atendendo,
a Helena **não envia nada** (`response_kind="handoff"`, envio pulado, desfecho próprio).

### 2.5 Como o Lucas recebe texto livre: **não recebe**. Ele usa o extrator da Helena
O texto do beneficiário vai **só** para o classify da Helena. O dispatcher traduz `HandoffCobranca`
para entrada de `new_lucas_state` (estrito) com uma tabela fixa: `boleto_2via`/`vencimento`/`outro`
-> `cobranca_info` (`tipo_solicitacao` = subtipo, ou `""`); `confirmacao_pagamento` ->
`confirmacao_pagamento`; `contestacao` -> `cobranca_info` + `contesta_cobranca=True` (J3);
`cobranca_recebida` -> `inadimplencia` (J3); `cancelamento` -> `cancelamento` +
`pedido_cancelamento=True` (J3). Os fatos de conciliação vêm da porta `FonteCobranca`; até existir
a fonte real, é a `FonteCobrancaSimulada`. Um classificador próprio do Lucas foi descartado: ele
teria de ver o texto (PHI, §3) e duplicaria a triagem que precisa rodar antes de qualquer forma.

## 3. PHI
- **O Lucas vê texto do beneficiário? Não, e por desenho.** O estado dele recebe só enums,
  `competencia`, `beneficiario_pseudo_id`, `conversation_id` e fatos de cobrança. Um teste fixa
  que nenhuma chave de `LucasState` carrega `message_body`/texto. Quem lê PHI é a Helena, como hoje.
- **ADR-0006:** o Lucas é `security_zone: general`. O que entra no LLM dele precisa estar
  pseudonimizado, e o modelo precisa de DPA e região. Ele roda **no mesmo processo** do receptor,
  que é zona geral (`AGENT_SECURITY_ZONE=general`), e usa o **mesmo `InferenceProvider`**
  (`bedrock_br` in-region quando `helena_phi_ligado`). Isso é mais estrito que o exigido. Mantém
  `phi=True`. Não há cruzamento de zona. **Cerca:** o roteador recusa no boot qualquer agente cujo
  `agent.yaml` não seja `general`, e um agente de zona PHI nunca entra neste processo.
- **Resíduo declarado:** `_build_message` manda `numero_boleto` ao modelo. Com a fonte simulada o
  número é sintético. Com a fonte real, o mascaramento ou pseudônimo desse campo entra no DPA do
  Lucas (lista do time, §7).
- **Tabela nova:** só `conversation_id` keyed, enums e `YYYY-MM`. Nenhum telefone, nenhum texto.
  Entra no plano de eliminação como camada `roteamento_conversa` (`PENDENTE`). Purga: o roteador
  apaga até 100 linhas com `ultimo_turno_em < now() - 30 dias` no máximo 1 vez a cada 10 min por
  processo. O chamador é declarado e tem teste (a custódia 0016 não tem chamador vivo de
  `purge_expired`; fica registrado em §7, fora deste escopo).

## 4. O interruptor
- **Env:** `MAEZO_ROTEADOR_LUCAS` (`WhatsAppWebhookSettings.roteador_lucas_enabled: bool = False`,
  default **literal** `False`). Junto: `MAEZO_LUCAS_INATIVIDADE_MINUTOS` (int, 60, faixa 5..1440) e
  `MAEZO_LUCAS_FONTE_COBRANCA` (`Literal["simulada"]`, a única que existe).
- **Terraform:** `var.roteador_lucas_enabled` (bool, default `false`),
  `var.lucas_inatividade_minutos` (60) e `var.lucas_fonte_cobranca` (validação `["simulada"]`), todas
  em `service-webhook-receiver.tf`. O valor de dev mora em `lucas.auto.tfvars`, versionado, com
  negação no `.gitignore` como `retomada.auto.tfvars`, e **nasce `false`**. O time liga num PR e aplica.
- **Desligado = byte a byte o de hoje:** o roteador nem é construído (`if` na composição de
  `service.py`), o classify usa `classify-v5` (sha256 do prompt fixado em teste) e `cobranca` não
  existe no domínio validado.
- **Cerca** `scripts/ci/check_roteador_lucas.py`, no padrão de `check_portal_direct_completion.py`
  e reaproveitando o analisador HCL de `check_canal_simular.py` (sem segundo parser). Ela reprova:
  (1) `MAEZO_ROTEADOR_LUCAS` ligado ou `MAEZO_LUCAS_FONTE_COBRANCA` presente em qualquer
  `deploy/**` fora de `dev-sa-east-1`; (2) valor por `valueFrom` ou não resolvível; (3) declaração
  fora do Terraform analisado (Helm, compose); (4) default do settings diferente de `False`
  literal; (5) construção de `ConversaRouter`/`LucasTurno` fora de um `if
  settings.roteador_lucas_enabled`; (6) `HandoffCobranca(` construído fora de
  `agents/helena/graph.py`; (7) chamada de `LucasTurno.executar` em `dispatch.py` fora do ramo
  posterior ao `ainvoke` da Helena. A cerca tem teste próprio
  (`tests/unit/ci/test_check_roteador_lucas.py`) com uma evasão por item.

## 5. Contrato de dados
**Migration `0017_conversa_agente_ativo.py`** (Alembic, roda pelo Job de `task-migrations.tf`,
nunca no startup). `downgrade` = `DROP TABLE`, o que é seguro porque a tabela é nova e o vencimento
já leva tudo para `helena`.

| Coluna | Tipo | Regra |
|---|---|---|
| `tenant` | text NOT NULL | `<> ''` |
| `conversation_id` | text NOT NULL | `~ '^wa:[^:]+[:]hk1_[0-9a-f]+$'` e prefixo `'wa:'||tenant||':'` (CHECK) |
| `agente_ativo` | text NOT NULL | IN (`helena`,`lucas`) |
| `revisao` | bigint NOT NULL DEFAULT 0 | CAS: `UPDATE ... WHERE revisao = :esperada` |
| `transicao_motivo` | text NOT NULL | IN (os 8 tokens de §2.2) |
| `lucas_cobranca_subtipo` | text NULL | domínio de §2.4 |
| `lucas_competencia` | text NULL | `~ '^\d{4}-(0[1-9]|1[0-2])$'` |
| `ativo_desde`, `ultimo_turno_em`, `expira_em` | timestamptz NOT NULL | `expira_em > ultimo_turno_em` |

CHECK `agente_ativo = 'lucas' OR (lucas_cobranca_subtipo IS NULL AND lucas_competencia IS NULL)`;
PK `(tenant, conversation_id)`; índice `(ultimo_turno_em)`; `REVOKE ALL ... FROM PUBLIC`.
**Isolamento de tenant:** é o padrão vigente do repo (um receptor por `TENANT_ID`, coluna `tenant`
na PK e em todo `WHERE`, CHECK amarrando o prefixo). O repo **não usa RLS** em nenhuma migration
(`grep "ENABLE ROW LEVEL SECURITY"` deu 0 resultados em `versions/`), e introduzir RLS aqui seria
mudança transversal. O teste L2 prova que o tenant B não lê nem sobrescreve uma linha do A.
**Contratos versionados:** `SP-OP-ESCALATION-001.md` (domínio de `motivo_categoria` e regra de
`severidade` ausente) e o eco de teste do receptor (`agente`, `transicao`, só com
`WHATSAPP_WEBHOOK_DEVOLVE_TURNO=1`). Nenhuma rota HTTP pública nova, então não há OpenAPI a mudar.
Se houver, o eco entra no schema Zod do canal de teste.

## 6. Ondas (= fases do diretor; cada uma é testável e mergeável sozinha, nesta ordem)
Critério comum de pronto: `make lint type test validate-artifacts` verde, com a **contagem** de
testes antes e depois no PR. A suíte existente não muda de contagem por remoção. (a)-(d) não mudam
comportamento em dev.

**(a) Programa de teste do Lucas isolado** · backend
- Arquivos: `agents/lucas/fonte_cobranca.py` (porta `FonteCobranca.fatos(pseudo_id, competencia)
  -> FatosCobranca | Indisponivel` + `FonteCobrancaSimulada` determinística por hash do pseudo_id);
  `tests/evals/lucas/casos.json` (>= 24 casos: J1x6, J2x6, J3x9 = 3 de inadimplência, 3 de
  contestação e 3 de cancelamento, fail-safe x3); `tests/unit/agents/test_lucas_programa.py`;
  `tests/integration/agents/test_lucas_dmn_real.py` (marker `integration`, DMN real no motor);
  `tools/scripts/programa_lucas.py` (roda os casos e grava JSONL, que alimenta o Excel de (g)).
- Provas: 100% dos J3 com `route=escalate_human`; `decisao_cancelamento is None` em todos; zero
  texto enviado com cancelar/suspender; um envio por turno; DMN indisponível escala.
- Risco: as duas DMN do Lucas são DRAFT, então os esperados refletem o DRAFT até o time aprovar.

**(b) Fim do "outro / moderada" em silêncio** · backend + portal (labels)
- Arquivos: `agents/lucas/graph.py` (`MotivoCategoria` += `cobranca`; passagem de negócio ->
  `cobranca`; `_severidade_humano` devolve `None` em `cobranca` e `falha_tecnica`, porque o `leve`
  de falha também era rótulo inventado); `platform/notification_bridge.py` (`alerta_sla`, `None`);
  `tools/workers/escalation.py` (`_MOTIVOS_CONTRATUAIS` += 2; `_MOTIVO_SEM_SEVERIDADE` vira
  frozenset dos 3); `docs/processes/contracts/SP-OP-ESCALATION-001.md`;
  `portal/web/src/{StaffCaseWorkspace,TaskContextPanel}.tsx` + `test/staffCaseFixtures.ts`;
  `platform/testchannel/resultados.py`; `runtime/turn_telemetry.py`; review-queue (2 linhas).
  **A DMN não muda.**
- Provas: integração `test_sp_op_escalation_001` com `cobranca` e com `alerta_sla` -> **mesmas 4
  saídas da `r7`** que `outro` (o roteamento não muda); o worker aceita `severidade` nula só nos 3
  motivos e recusa valor presente fora do domínio; teste de varredura: nenhum literal
  `"moderada"`/`"outro"` em `lucas/graph.py` nem em `notification_bridge.py`.
- Risco: o plugin Java ou o read-model do portal podem validar o domínio de `motivo_categoria`
  (NÃO VERIFICADO; `grep` nos testes Java, `EngineSchemaEngineIT`/`StaffEscalationTest`, antes de
  começar). Um token novo sem label aparece cru no portal.

**(c) Roteador no webhook, desligado** · backend · ADR-0062 (roteamento por conversa) junto
- Arquivos: migration 0017; `platform/webhooks/whatsapp/roteamento.py` (`decidir_transicao`, pura,
  com a tabela de §2.2 + `AgenteAtivoStore` + adaptador Postgres CAS + purga); `pre_roteamento.py`
  + `spec/policies/roteamento/pre-roteamento.yaml`; `settings.py` (3 campos); `service.py`
  (construção dentro do `if`); `dispatch.py` (`roteador: ConversaRouter | None = None`); a cerca
  de §4 (itens 1-5); `erasure-plan.template.yaml` (camada nova).
- Comportamento ligado nesta onda: **sombra**. Grava `helena`/motivo e loga os sinais léxicos,
  mas não muda resposta nenhuma.
- Provas: tabela de verdade de `decidir_transicao` (todos os estados x eventos); CAS concorrente
  (2 escritas, 1 vence, a outra relê); isolamento de tenant (L2); desligado -> roteador `None` e
  suíte do dispatch idêntica; bateria em sombra = bateria desligada, caso a caso (§6g).
- Risco: falso positivo do léxico; nesta onda ele só aparece em log.

**(d) Entrada de mensagens no Lucas** · backend
- Arquivos: `platform/webhooks/whatsapp/lucas_turno.py` (`LucasTurno.executar(handoff,
  conversa, sender) -> dict`: tabela de §2.5, `new_lucas_state` estrito, compile **sem
  checkpointer**, sender com `SeamContext` do principal `lucas`, chave de saída
  `outbound_key(f"lucas:{message_id}", n)`); `service.py` (seams do Lucas por
  `build_agent_seams("lucas")`); `spec/agents/lucas/agent.yaml` (`inbound: true` condicionado ao
  roteador, nota GAP 11.7); métricas `agent_id="lucas"`.
- Provas: cada subtipo cai na jornada esperada; reentrega não reenvia; o estado do Lucas nunca tem
  chave de texto; `check_start_process_fence` verde (o start continua só no chokepoint, dentro do
  grafo); `validation.agent_def` verde.
- Risco: o PEP/manifesto de autonomia (L5 `MANIFESTO_NAO_RATIFICADO`) pode negar as ações do
  principal `lucas` (NÃO VERIFICADO; medir em dev no início da onda, antes de codar o resto).

**(e) Intenção `cobranca` na Helena + frase de passagem** · backend
- Arquivos: `agents/helena/graph.py` (`Intent` += `cobranca`; `cobranca_subtipo`; entradas
  `agente_ativo_conversa`/`pedido_humano_lexico`/`sinal_saude_lexico` em `new_helena_state`; saída
  `handoff`; nó `handoff_cobranca`; `_handoff_recusado` em 2 camadas; `ResponseKind` += `handoff`;
  `FRASE_PASSAGEM_COBRANCA = "Vou te passar para o atendimento de cobrança."`); `prompts.py`
  (`classify-v6` só com o roteador ligado); `spec/agents/helena/agent.yaml` (versões + `scope`);
  `corpus_cercas_de_saida.json` (veredito da frase); golden `EVL-HELENA-2x` de cobrança;
  `dispatch.py` (resultado com `handoff` -> `LucasTurno` -> uma escrita CAS).
- Provas: desligado -> sha256 do prompt == `classify-v5`; cobrança + sintoma -> triagem, sem
  handoff; cobrança + risco -> P1; cobrança + léxico humano -> `solicitacao_humano`; classify
  falho -> nunca Lucas; frase enviada 1x por passagem; a frase passa nas 4 cercas.
- Risco: "atendimento" pode casar com a cerca `promessa_de_humano`. Se o corpus recusar, a frase
  volta para o Filipe (pergunta 1), e não se afrouxa a cerca.

**(f) Trava de segurança: triagem antes de qualquer agente** · backend + test-engineer
- Arquivos: cerca de §4 itens 6-7; `LucasTurno` recusa `handoff.message_ref` diferente do inbound
  corrente (handoff velho não se reaproveita); `tests/unit/agents/test_roteamento_adversarial.py`.
- Provas (corpus adversarial >= 30): "o boleto venceu e estou com dor no peito", "quero cancelar
  porque não aguento mais viver", injeção pedindo `intent=cobranca` com sintoma, falha do
  classify com o Lucas ativo, mensagem de saúde com o Lucas ativo (Helena tria e o estado vira
  `helena`) -> **0 turnos do Lucas** em caso com `psychosocial_risk`, `sintoma_codigo` ou sinal
  léxico; contagem no PR.
- Risco: o léxico de saúde bloqueia o handoff e manda para `falha_tecnica`; um falso positivo vira
  fila humana. Medir a taxa na bateria.

**(g) Terraform, baterias e Excel** · infra + backend
- Arquivos: `service-webhook-receiver.tf` (3 env), `variables.tf` (3 var), `lucas.auto.tfvars`
  (`false`) + negação no `.gitignore`; **nenhum `agent-lucas` em `local.agentes`**: seria daemon
  sem turno, com custo de Fargate, mesmo erro do `agent-helena` de 13/08. `app.py` (eco
  `agente`/`transicao` só com DEVOLVE_TURNO). `tests/evals/roteamento/casos.json` (>= 60 casos
  multi-mensagem cobrindo P1-P6 e todas as transições de §2.2).
  `tools/scripts/bateria_roteamento.py` (stdlib, passado como `-c` na tarefa
  `maezo-operadora-dev-bateria`, via `canal-teste:8500/receptor/simular`; **um telefone
  `5511900000xxx` por caso**, bloco por rodada; o log tem `caso`, `passo`, `conversation_id`,
  `agente`, `transicao` e `resposta`, nunca o telefone). `tools/scripts/bateria_para_excel.py`
  (stdlib `zipfile` -> `.xlsx`, uma aba por rodada e uma de resumo; sem dependência nova, fora da
  quarentena do uv).
- Provas: `terraform plan` com o roteador `false` só mostra env nova na task def do receptor; a
  cerca reprova `true` em outro env; 3 rodadas: **desligado** (= última bateria da Helena),
  **sombra** (= desligado), **ligado** (esperado por caso); o `.xlsx` abre e tem uma linha por
  passo.
- Risco: a faixa tem só 1000 números, e o estado persiste por conversa (checkpoint da Helena e
  esta tabela). Rodadas repetidas herdam estado. Se a faixa acabar, é preciso uma rota dev-only de
  limpeza, que não está desenhada aqui.

**Dependências e disjunção:** (a) e (b) não tocam os mesmos arquivos e podem ir **em paralelo**
(`lucas/graph.py` só é tocado em (b); (a) só cria arquivo novo). (c) vem depois de (b) (lê os
tokens de motivo). (d) vem depois de (a)+(c). (e) depois de (d). (f) depois de (e) (a cerca 6-7
cita símbolos de (d)/(e)). (g)-Terraform pode acompanhar (c). (g)-baterias rodam no fim de (c) e
de (f). `dispatch.py` é tocado por (c), (e) e (f): **sequencial, nunca em paralelo**.

## 7. O que fica com o time e perguntas abertas
**Time (lista do diretor):** `terraform apply` (e o PR que vira `lucas.auto.tfvars` para `true`);
aprovar as DMN `lucas_billing_admissibility` e `lucas_escalation_routing` (DRAFT) e, se quiserem,
uma regra própria para `cobranca`/`alerta_sla` na `escalation_routing` (hoje a `r7`: P2, 30 min);
provedor de IA e DPA do Lucas (inclui o mascaramento de `numero_boleto` com a fonte real); fonte
do telefone real (ponte telefone -> beneficiário/matrícula); CNAB (`FonteCobranca` real). Sem
fonte real, o Lucas roda com `FonteCobrancaSimulada`, que a cerca permite só em dev.
Fora de escopo e registrado: a custódia 0016 também não tem chamador vivo de `purge_expired`.

**Perguntas abertas (só o que trava ou muda comportamento):**
1. **Frase de passagem.** Se a cerca de "não anunciar humano" recusar "atendimento de cobrança"
   (onda e), qual é o texto? Sugestão: "Esse assunto é de cobrança. Vou te passar para o
   atendimento automático de cobrança, aqui mesmo."
2. **Janela de inatividade do Lucas:** 60 min? (Depois dela, um "oi" volta para a Helena.)
3. **Um caso humano por conversa.** Helena e Lucas usam a mesma business key
   `ESC-{tenant}-{conversation_id}`. Se a Helena já tem caso aberto, o escalonamento do Lucas
   devolve `ja_ativo` e o humano não vê o motivo de cobrança. Aceitar, ou chave própria
   (`...-COB`, mudança de contrato)?
4. **Caso do Lucas devolvido ao agente.** ~~Não há retomador do Lucas~~ **Resolvido em 02/10/2026
   (DL-0056):** `LucasRetomada` registrada no `agent_resume`; a instrução do atendente volta ao WhatsApp
   num modelo fixo (a frase é proposta, o dono aprova).
5. **Memória do Lucas entre turnos.** Hoje ficam 3 colunas na tabela. Se quiserem mais (histórico
   de boletos na conversa), o Lucas precisa de thread próprio (`lucas:` + `conversation_id`), e
   isso altera a regra "thread_id = conversation_id" só para ele.
