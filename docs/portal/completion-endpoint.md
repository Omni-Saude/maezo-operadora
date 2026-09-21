# `POST /api/v1/portal/tasks/{task_id}/completion` — contrato para a página de teste

**Estado: INTERINO e declarado.** Registrado em `docs/decisions-log.md` **DL-0049**. Esta rota
contorna os comandos atômicos da ADR-0049 (D5/D6/D7) e nasce **desligada**. Leia DL-0049 antes de
construir algo em cima dela: o destino é o relé durável (#427), não este endpoint.

Este documento existe para a **Frente 3** do mandato de 21/09/2026: outro agente constrói a página
`src/maezo/platform/testchannel/paginas/escalonamento.html` em paralelo, a partir daqui.

---

## 1. Pré-condições de ambiente (nada disto é código da página)

A rota só responde quando **as duas** variáveis abaixo estiverem ligadas no serviço do portal, e
`scripts/ci/check_portal_direct_completion.py` reprova ligá-las fora de `dev-sa-east-1`.

| Variável | Default | Efeito |
|---|---|---|
| `MAEZO_PORTAL_DIRECT_COMPLETION` | *(desligada)* | Abre a rota. Desligada, ela responde **501** `completion_unavailable`. |
| `MAEZO_PORTAL_DIRECT_COMPLETION_ENGINE_ORIGIN` | *(ausente)* | Base REST do motor (`.../engine-rest`). Obrigatória junto com a flag; sem ela o portal **não sobe**. |
| `MAEZO_PORTAL_DIRECT_COMPLETION_TIMEOUT_SECONDS` | *(ausente)* | Intervalo operacional explícito, 1–120 s. Obrigatória junto com a flag. |
| `MAEZO_PORTAL_CORS_ORIGINS` | *(vazia)* | **É a variável que a Frente 3 precisa.** Lista de origens HTTPS exatas, separadas por vírgula, que podem chamar o BFF com credenciais. |

Para a página de teste, o valor é exatamente:

```
MAEZO_PORTAL_CORS_ORIGINS=https://maezo-teste-dev.austa.com.br
```

Regras que o portal aplica na subida (falha no boot se violadas): HTTPS, DNS puro, sem path,
porta, query, fragmento ou credencial; no máximo 4 entradas; sem duplicatas; **nunca** `*`; e
nenhuma entrada igual ao `public_origin` do portal (essa já é permitida) nem ao `cognito_origin`.

Com a allowlist vazia — o estado padrão — **nenhum** cabeçalho `Access-Control-Allow-*` é emitido
por nenhum caminho de código, e a rota aceita apenas o próprio `public_origin` do portal.

---

## 2. O que a página tem de fazer antes de chamar

1. **Login primeiro.** A rota usa o cookie de sessão `__Host-maezo-session`, que o navegador só
   envia com `credentials: "include"`. A página não emite, não lê e não fabrica esse cookie.
2. **Pegar o CSRF token** em `GET /api/v1/portal/session` (mesma sessão, `credentials: "include"`).
   A resposta é o `SessionDTO`: `{ "schema_version": 1, "principal_ref": ..., "audience": "staff",
   "roles": [...], "expires_at": ..., "csrf_token": "..." }`.
3. **Mandar o token de volta** no cabeçalho `X-CSRF-Token`. O servidor compara com
   `secrets.compare_digest` contra o token da sessão resolvida — não é um token derivável.

### Requisição

```js
const r = await fetch(`${PORTAL}/api/v1/portal/tasks/${taskId}/completion`, {
  method: "POST",
  credentials: "include",                 // obrigatório: é o cookie de sessão
  headers: {
    "Content-Type": "application/json",   // exatamente este, uma vez
    "X-CSRF-Token": csrfToken,            // exatamente uma vez
  },
  body: JSON.stringify({
    resultado: "resolvido_humano",        // um dos três; NÃO há default
    notas_resolucao: "texto livre",       // 1..2000 caracteres, não pode ser só espaço
  }),
});
```

Restrições de enquadramento, todas verificadas **antes** de o corpo ser parseado (400
`invalid_request` se violadas): sem query string na URL; corpo ≤ 65536 bytes; exatamente um
`Content-Type: application/json`; exatamente um `Origin` e um `X-CSRF-Token` (se faltar ou
duplicar um destes dois, a resposta é 401 `session_unavailable`).

### Corpo

| Campo | Tipo | Regra |
|---|---|---|
| `resultado` | string | **Obrigatório, sem default.** Um de `resolvido_humano`, `devolvido_agente`, `emergencia_acionada`. Qualquer outro valor → **422**. |
| `notas_resolucao` | string | Obrigatório, 1–2000 caracteres, não em branco. |

Campo extra no corpo → **422**. O enum vem do próprio BPMN
(`spec/processes/bpmn/SP-OP-ESCALATION-001_Escalonamento_Humano_Universal.bpmn`) e é derivado em
código de `EscalationDecisionInputs`, não redigitado.

**`notas_resolucao` é pseudonimizada no servidor antes de ir ao motor**, com o mesmo
`phi_vars.redact_free_text` que a Helena roda na entrada: e-mail, CPF/CNPJ, telefone BR e
sequências de 11+ dígitos viram tokens de classe, e o texto é limitado a 500 caracteres. A página
**não** precisa (e não deve) tentar reproduzir isso — mas saiba que o que chega ao motor pode ser
diferente do que foi digitado, e que é isso que o painel "O QUE O PORTAL VÊ" deveria mostrar.

---

## 3. Respostas

### 200 — concluída

```json
{
  "schema": "portal-task-completion.v1",
  "task_id": "…",
  "process_definition_key": "SP-OP-ESCALATION-001",
  "process_definition_version": "1",
  "task_definition_key": "UT_TratarEscalonamento",
  "form_key": "escalation",
  "state": "completed",
  "resultado": "resolvido_humano",
  "consumed_task_revision": "2",
  "authority_revision": "7",
  "evidence_revision": "3",
  "evidence_digest": "<64 hex>",
  "completed_at": "2026-09-21T18:04:05.123456Z",
  "audit_intent_ref": "<64 hex>",
  "audit_result_ref": "<64 hex>"
}
```

Toda revisão/versão é **string decimal canônica** (a convenção D5 do repo), não número.

`audit_intent_ref` e `audit_result_ref` são hashes de elos na **mesma cadeia de auditoria** em que
`human_command.intent`/`.result` escrevem. São eles que tornam o critério 6 do mandato
("conclusão pelo portal e pelo motor produzem rastro idêntico") verificável na tela: a conclusão
pelo motor não os devolve, então a comparação é feita na cadeia/Cockpit, não entre dois JSONs.

Nenhuma nota, narrativa ou referência de beneficiário volta no corpo.

### Erros

Todos com o mesmo formato:

```json
{ "schema": "portal-completion-error.v1", "code": "<code>" }
```

| HTTP | `code` | Quando | O que a página deve fazer |
|---|---|---|---|
| 400 | `invalid_request` | `task_id` malformado, query string, corpo grande demais, `Content-Type` errado, Host errado | Bug da página. Não retentar igual. |
| 401 | `session_unavailable` | Sem cookie (ou cookie ambíguo), CSRF ausente/duplicado/errado, `Origin` fora da allowlist | Mandar o colaborador fazer login de novo / conferir a allowlist. |
| 403 | `employee_access_required` | Público não é `staff`; **ou o grupo do colaborador não bate com os candidate groups da tarefa**; ou papel e grupo não coincidem na mesma membership; ou a tarefa é de outro colaborador | **É o caso de isolamento.** Mostrar como resultado esperado ao trocar de colaborador, não como falha. |
| 404 | `resource_unavailable` | A tarefa não existe, não está ativa, ou o snapshot não é corrente | Recarregar a fila. |
| 409 | `revision_conflict` | Tarefa já concluída, revisão mudou, membership mudou entre a leitura e o efeito, ou outra conclusão da mesma revisão já foi reivindicada | Recarregar a tarefa e decidir de novo. **Nunca** retentar automaticamente. |
| 422 | `invalid_completion` | `resultado` ausente/fora dos três valores, `notas_resolucao` vazia/longa demais, campo extra | Bug da página ou do formulário. |
| **501** | `completion_unavailable` | **A flag `MAEZO_PORTAL_DIRECT_COMPLETION` está desligada** | Mostrar o botão "Concluir pelo portal" desabilitado, com o motivo: a conclusão direta não está habilitada neste ambiente. É o estado padrão, não um erro. |
| 503 | `completion_dependency_unavailable` | Autoridade/motor/auditoria indisponível, ou resposta do motor que não prova nem conclusão nem conflito | **Efeito INCERTO.** Não afirmar "falhou": recarregar a tarefa e olhar o estado real antes de qualquer coisa. |

Um 503 nunca é evidência de rollback. Se o motor não respondeu de forma conclusiva, a conclusão
pode ter commitado: o elo de auditoria `intent` já está na cadeia e é por ele que se reconcilia.

Toda resposta (incluindo os erros) carrega `Cache-Control: no-store`, `Pragma: no-cache`,
`Referrer-Policy: no-referrer`, `X-Content-Type-Options: nosniff` e uma CSP `default-src 'none'`.
Nenhuma delas contém texto do motor, stack trace, PHI ou o segredo de sessão.

---

## 4. Autorização, em uma frase

A rota não tem lógica de autorização própria: ela resolve o **mesmo** `HumanGateway` que serve
`GET /api/v1/portal/tasks` e chama `HumanGateway.complete_task`, que exige público `staff`, papel
e grupo coincidindo na **mesma** membership, o grupo do colaborador dentro dos candidate groups da
tarefa, e reconsulta a sessão depois das leituras remotas e antes do efeito. Nada — grupo, papel,
revisão, tenant — é lido da requisição.

A única divergência deliberada em relação aos GETs: aqui um grupo que não bate responde **403**, e
não 404. Os GETs dobram `operation_forbidden` em 404 para não virarem oráculo de existência; o
mandato pediu 403 explicitamente nesta rota, e ela só é alcançável com um `task_id` que o chamador
já tem em mãos.

---

## 5. O que este endpoint **não** faz

- Não aceita `resultado` por default, nem completa uma tarefa que não seja `form_key=escalation`.
- Não manda uma terceira variável ao motor: só `resultado` e `notas_resolucao`.
- Não assina envelope, não gera receipt, não grava outbox e não tem cerca otimista no motor.
- Não retenta. Uma reentrega do mesmo clique é recusada com 409 pelo claim de auditoria.
- Não substitui o relé durável. Quando #427 existir, esta rota sai.
