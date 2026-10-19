# `GET /api/v1/portal/tasks/{task_id}/context` — o contexto do caso (INTERINO, DL-0050)

O atendente que abre uma tarefa de `SP-OP-ESCALATION-001` precisa saber **por que** o caso foi
escalado, **com que gravidade**, **para qual prioridade e grupo**, **até quando** e **o que a
Helena resumiu**. Esta rota devolve exatamente isso. É um atalho interino, registrado em
`docs/decisions-log.md` (DL-0050), do mesmo tipo e atrás do mesmo portão que a conclusão direta
(DL-0049): o destino é o modelo de leitura assinado, não este endpoint.

## O portão

Liga com `MAEZO_PORTAL_DIRECT_COMPLETION=true` — **a mesma variável** da conclusão. Desligada (o
padrão), a rota responde `501 context_unavailable` **antes** de ler o cookie e de tocar em qualquer
dependência. Só existe em `dev-sa-east-1` (`scripts/ci/check_portal_direct_completion.py`). Não há
variável nova, nem entrada nova no Terraform: o que já está ligado em dev liga a rota.

## Quem pode ler

A autorização é a de `GET /tasks/{task_id}`: sessão `staff` e um vínculo cujo grupo casa com a
tarefa. Só depois disso o motor é consultado, e só se a tarefa for `UT_TratarEscalonamento` ou
`UT_SupervisorAssume` de `SP-OP-ESCALATION-001` do tenant. Outro grupo recebe `404`, como na leitura
da tarefa.

## Resposta `200` — `portal-task-context.v1`

| Campo | Tipo | Origem |
|---|---|---|
| `task_id` | texto | o da rota |
| `etapa` | `atendimento` \| `supervisao` | tarefa aberta (`UT_TratarEscalonamento` / `UT_SupervisorAssume`) |
| `motivo_categoria` | código \| `null` | variável do processo |
| `severidade` | código \| `null` | variável do processo |
| `prioridade` | `P<n>` \| `null` | saída de `escalation_routing` |
| `grupo_atendimento` | código \| `null` | saída de `escalation_routing` |
| `aberto_em` | data-hora \| `null` | início do processo |
| `ack_vence_em`, `resolucao_vence_em` | data-hora \| `null` | criação da tarefa + duração ISO-8601 da DMN; **só na etapa `atendimento`** |
| `resumo_contexto` | texto (até 1000) \| `null` | variável do processo, limpa de novo na saída |
| `observed_at` | data-hora | quando o portal leu |

Um fato ausente é `null`; **nunca** um valor padrão inventado. Os códigos são símbolos abertos: o
rótulo em português mora no cliente web, e um valor novo da DMN aparece como ele mesmo.

**Não há** nome, telefone, conversa nem referência ao beneficiário: nada disso existe no processo,
por desenho (ADR-0006, ADR-0061).

## Erros

`portal-context-error.v1`, sempre com `Cache-Control: no-store`:

| HTTP | `code` | Quando |
|---|---|---|
| 400 | `invalid_request` | referência malformada, query string ou corpo |
| 401 | `session_unavailable` | sem sessão, ou sessão vencida |
| 403 | `employee_access_required` | a sessão não é de colaborador |
| 404 | `resource_unavailable` | tarefa fora do alcance do grupo, ou não é de escalonamento |
| 409 | `refresh_required` | a leitura venceu enquanto era feita |
| 501 | `context_unavailable` | o portão interino está desligado |
| 503 | `read_dependency_unavailable` | o motor não respondeu ou respondeu algo fora do esperado |

Uma falha do motor **nunca** vira "sem contexto": é `503`, e a tela mostra a falha e oferece tentar
de novo.

## O que a rota lê no motor

Só `GET`s, por caminhos fixos no servidor: `/task/{id}`, `/history/process-instance/{id}`,
`/history/decision-instance` (saídas de `escalation_routing`) e **três** variáveis por nome —
`motivo_categoria`, `severidade`, `resumo_contexto` —, uma requisição cada. Nunca se pede "todas as
variáveis da instância". Nada do que o navegador envia escolhe URL, variável ou decisão.

## O que este atalho não é

Nada do que a rota devolve é atestado por assinatura ou digest; o `engine-rest` desta distribuição
não tem autenticação, então a rede é a única fronteira.

## Registro de quem leu (DL-0149)

Cada leitura **bem-sucedida** grava um elo `portal_read.task_context` na mesma cadeia de auditoria
do tenant em que a conclusão grava `portal_direct_completion.*`: quem leu (referências pseudônimas
`principal_ref`/`session_ref` e a revisão da membership), o id da tarefa, o código da leitura e a
hora. Nada do que foi lido entra no registro. Leitura recusada não grava nada.

É **best-effort** (decisão do dono, 19/10/2026): se o registro não estiver instalado, se a gravação
falhar ou se passar de 2 s (o lock da cadeia por tenant), a rota responde **igual** (`200` com o
contexto) e o servidor loga um único evento `portal_read_audit_skipped` com `reason`
(`sink_absent` ou `write_failed`) e `read_kind` — sem o texto do erro e sem dado pessoal.
Ler o log: o evento significa leitura entregue **sem** registro confirmado.

## Como conferir em dev

Com uma sessão de colaborador do grupo da tarefa, no navegador:

    GET /api/v1/portal/tasks/{task_id}/context

Esperado: `200` com o esquema acima. `501` significa portão desligado; `404` numa tarefa que o
próprio grupo enxerga na fila indica que o motor devolveu um `tenantId` diferente do do portal — o
que a rota trata como recusa, e não como contexto.
