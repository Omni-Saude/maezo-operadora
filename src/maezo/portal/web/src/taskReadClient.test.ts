import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  listTaskQueue,
  readTask,
  validateTaskQueuePage,
  validateTaskReadResponse,
} from "./taskReadClient";

import { taskReadBindings, taskReadInputs } from "./taskReadBindings";

const digest = "a".repeat(64);
const hugeRevision = "99999999999999999999999999999999999999999999999999";

function freshness() {
  return {
    state: "current",
    observed_at: "2026-09-09T15:00:01Z",
    source_observed_at: "2026-09-09T15:00:00Z",
    valid_until: "2026-09-09T15:00:11Z",
    refresh_after_seconds: 10,
  } as const;
}

function queuePage(queue: "mine" | "team" = "mine") {
  return {
    schema: "portal-task-queue.v1",
    queue,
    items: [
      {
        task_id: "task-1",
        process_definition_key: "SP-OP-AUTH-001",
        task_definition_key: "UT_AnaliseMedicoAuditor",
        task_revision: hugeRevision,
        ownership: "self",
        engine_due_at: "2026-09-10T12:00:00Z",
        snapshot_at: "2026-09-09T15:00:00Z",
      },
    ],
    next_cursor: "opaque_cursor_1",
    freshness: freshness(),
  } as const;
}

function taskResponse() {
  return {
    schema: "portal-task-read.v1",
    task: {
      schema_version: 1,
      snapshot_at: "2026-09-09T15:00:00Z",
      task_id: "task-1",
      process_definition_key: "SP-OP-AUTH-001",
      process_definition_version: hugeRevision,
      process_definition_id: "AUTH:version:opaque",
      process_definition_digest: digest,
      task_definition_key: "UT_AnaliseMedicoAuditor",
      form_key: "auth_decisao",
      form_version: hugeRevision,
      form_digest: digest,
      form_source_status: "BPMN_FORMDATA",
      task_revision: hugeRevision,
      assignee_ref: "principal-opaque",
      eligible_candidate_groups: ["medico-auditor"],
      evidence_revision: hugeRevision,
      evidence_digest: digest,
      engine_due_at: null,
      allowed_actions: [],
      allowed_inputs: [...taskReadInputs.auth_decisao],
      read_only_evidence: null,
    },
    freshness: freshness(),
  } as const;
}

const addedBindings = [
  ["SP-OP-ADEQUACAO-001", "UT_CoordenacaoRede", "adequacao_coordenacao"],
  ["SP-OP-ADEQUACAO-001", "UT_DecisaoFallback", "adequacao_decisao"],
  ["SP-OP-CRED-001", "UT_AnaliseCredenciamento", "cred_cred"],
  ["SP-OP-CRED-001", "UT_AnaliseDescredenciamento", "cred_descred"],
  ["SP-OP-CRED-001", "UT_CoordenacaoRedeCred", "cred_cred"],
  ["SP-OP-CRED-001", "UT_CoordenacaoRedeDescred", "cred_descred"],
  ["SP-OP-LGPD-DSR-001", "UT_RevisaoDpo", "lgpd_decisao"],
  ["SP-OP-NIP-001", "UT_CoordenacaoNip", "nip_decisao"],
  ["SP-OP-NIP-001", "UT_ElaborarRespostaNip", "nip_minuta"],
  ["SP-OP-NIP-001", "UT_RevisaoJuridicaNip", "nip_decisao"],
] as const;

function addedTaskResponse(
  process: (typeof addedBindings)[number][0],
  task: (typeof addedBindings)[number][1],
  form: (typeof addedBindings)[number][2],
  taskId = `${process}-${task}`,
) {
  const value = taskResponse();
  return {
    ...value,
    task: {
      ...value.task,
      task_id: taskId,
      process_definition_key: process,
      task_definition_key: task,
      form_key: form,
      form_source_status: "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY" as const,
      allowed_inputs: [...taskReadInputs[form]],
    },
  };
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));

afterEach(() => vi.unstubAllGlobals());

it("consulta a fila por same-origin/no-store e preserva revisão decimal exata", async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse(queuePage()));
  const result = await listTaskQueue("mine", null, new AbortController().signal);
  expect(result).toEqual({ kind: "success", value: queuePage() });
  if (result.kind === "success") {
    expect(result.value.items[0].task_revision).toBe(hugeRevision);
    expect(typeof result.value.items[0].task_revision).toBe("string");
  }
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/portal/tasks?queue=mine&limit=25",
    expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      headers: { Accept: "application/json" },
    }),
  );
});

it("mantém o cursor opaco na paginação e a referência opaca no detalhe", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(queuePage("team")))
    .mockResolvedValueOnce(jsonResponse({ ...taskResponse(), task: { ...taskResponse().task, task_id: "task%opaque" } }));
  await listTaskQueue("team", "opaque_cursor_1", new AbortController().signal);
  const detail = await readTask("task%opaque", new AbortController().signal);
  expect(detail.kind).toBe("success");
  expect(fetch).toHaveBeenNthCalledWith(
    1,
    "/api/v1/portal/tasks?queue=team&limit=25&cursor=opaque_cursor_1",
    expect.any(Object),
  );
  expect(fetch).toHaveBeenNthCalledWith(
    2,
    "/api/v1/portal/tasks/task%25opaque",
    expect.any(Object),
  );
});

it.each([
  [400, "invalid_request"],
  [401, "session_unavailable"],
  [403, "employee_access_required"],
  [404, "resource_unavailable"],
  [409, "refresh_required"],
  [503, "read_dependency_unavailable"],
] as const)("mapeia HTTP %s somente com o erro fechado correspondente", async (status, code) => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse({ schema: "portal-read-error.v1", code }, status),
  );
  await expect(listTaskQueue("mine", null, new AbortController().signal)).resolves.toEqual({ kind: code });
});

it("recusa corpo de erro divergente e não renderiza detalhe do servidor", async () => {
  vi.mocked(fetch).mockResolvedValue(
    jsonResponse(
      {
        schema: "portal-read-error.v1",
        code: "read_dependency_unavailable",
        detail: "private server material",
      },
      503,
    ),
  );
  await expect(listTaskQueue("mine", null, new AbortController().signal)).resolves.toEqual({
    kind: "invalid-response",
  });
});

it("trata falha de transporte como dependência indisponível", async () => {
  vi.mocked(fetch).mockRejectedValue(new TypeError("network detail"));
  await expect(listTaskQueue("mine", null, new AbortController().signal)).resolves.toEqual({
    kind: "read_dependency_unavailable",
  });
});

it("propaga cancelamento sem convertê-lo em estado de erro", async () => {
  vi.mocked(fetch).mockRejectedValue(new DOMException("cancelled", "AbortError"));
  await expect(listTaskQueue("mine", null, new AbortController().signal)).rejects.toMatchObject({
    name: "AbortError",
  });
});

describe("validação fechada da fila", () => {
  it.each([
    (value: ReturnType<typeof queuePage>) => ({ ...value, private_field: "secret" }),
    (value: ReturnType<typeof queuePage>) => ({ ...value, queue: "team" }),
    (value: ReturnType<typeof queuePage>) => ({ ...value, next_cursor: "bad.cursor" }),
    (value: ReturnType<typeof queuePage>) => ({ ...value, freshness: { ...value.freshness, refresh_after_seconds: 9 } }),
    (value: ReturnType<typeof queuePage>) => ({ ...value, freshness: { ...value.freshness, valid_until: value.freshness.observed_at } }),
    (value: ReturnType<typeof queuePage>) => ({ ...value, items: [{ ...value.items[0], task_revision: 9 }] }),
  ])("recusa resposta malformada %#", (mutate) => {
    expect(validateTaskQueuePage(mutate(queuePage()), "mine")).toBeNull();
  });

  it("recusa página vazia que alega continuação", () => {
    expect(validateTaskQueuePage({ ...queuePage(), items: [] }, "mine")).toBeNull();
  });
});

describe("validação fechada do snapshot público", () => {
  it("preserva versões e revisões arbitrariamente grandes como strings", () => {
    const result = validateTaskReadResponse(taskResponse());
    expect(result?.task.process_definition_version).toBe(hugeRevision);
    expect(result?.task.task_revision).toBe(hugeRevision);
    expect(result?.task.evidence_revision).toBe(hugeRevision);
  });

  it.each([
    (value: ReturnType<typeof taskResponse>) => ({ ...value, unknown: true }),
    (value: ReturnType<typeof taskResponse>) => ({ ...value, task: { ...value.task, allowed_actions: ["claim"] } }),
    (value: ReturnType<typeof taskResponse>) => ({ ...value, task: { ...value.task, form_version: 1 } }),
    (value: ReturnType<typeof taskResponse>) => ({ ...value, task: { ...value.task, evidence_digest: "not-a-digest" } }),
    (value: ReturnType<typeof taskResponse>) => ({ ...value, task: { ...value.task, read_only_evidence: { private: true } } }),
  ])("recusa snapshot malformado %#", (mutate) => {
    expect(validateTaskReadResponse(mutate(taskResponse()))).toBeNull();
  });

  it("aceita evidência PAGTO e mantém centavos fora do alcance de Number", () => {
    const value = taskResponse();
    const cents = "99999999999999999999999999999999999999999999999999";
    const pagto = {
      ...value,
      task: {
        ...value.task,
        process_definition_key: "SP-OP-PAGTO-001",
        task_definition_key: "UT_AnaliseAdmissibilidade",
        form_key: "pagto_admissibilidade",
        form_source_status: "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
        allowed_inputs: ["decisao_admissibilidade", "justificativa_recusa"],
        read_only_evidence: {
          kind: "pagto_admissibilidade",
          valor_pagamento_cents: cents,
          dados_pagamento_validos: true,
          lastro_confirmado: false,
          duplicidade_suspeita: true,
          lastro_origem: null,
          lastro_decisor_id: null,
        },
      },
    };
    const result = validateTaskReadResponse(pagto);
    expect(result?.task.read_only_evidence?.valor_pagamento_cents).toBe(cents);
  });

  it("deriva exatamente os 32 bindings e 22 mapas de inputs do registro Q1 atual", () => {
    expect(taskReadBindings).toHaveLength(32);
    expect(Object.keys(taskReadInputs)).toHaveLength(22);
    expect(new Set(taskReadBindings.map(({ process, task }) => `${process}\u0000${task}`)).size).toBe(32);
    for (const binding of taskReadBindings) {
      expect(taskReadInputs[binding.form]).toBeDefined();
    }
    for (const [process, task, form] of addedBindings) {
      expect(taskReadBindings).toContainEqual({
        process,
        task,
        form,
        source: "BPMN_TASK_DOCUMENTATION_DRAFT_VERIFY",
      });
    }
  });

  it.each(addedBindings)(
    "aceita o binding atual %s/%s e seus inputs gerados",
    async (process, task, form) => {
      const taskId = `${process}-${task}`;
      const response = addedTaskResponse(process, task, form, taskId);
      vi.mocked(fetch).mockResolvedValue(jsonResponse(response));

      await expect(readTask(taskId, new AbortController().signal)).resolves.toEqual({
        kind: "success",
        value: response,
      });
    },
  );

  it.each(addedBindings)(
    "recusa input forjado no binding atual %s/%s",
    (process, task, form) => {
      const value = addedTaskResponse(process, task, form);
      expect(
        validateTaskReadResponse({
          ...value,
          task: { ...value.task, allowed_inputs: [...value.task.allowed_inputs, "actor_id"] },
        }),
      ).toBeNull();
    },
  );

  it.each(addedBindings)(
    "recusa formulário de outra tarefa para %s/%s",
    (process, task, form) => {
      const value = addedTaskResponse(process, task, form);
      expect(
        validateTaskReadResponse({
          ...value,
          task: {
            ...value.task,
            form_key: "pagto_admissibilidade",
            allowed_inputs: [...taskReadInputs.pagto_admissibilidade],
          },
        }),
      ).toBeNull();
    },
  );

  it.each(addedBindings)(
    "recusa correlação de task_id divergente no GET de %s/%s",
    async (process, task, form) => {
      const requestedTaskId = `${process}-${task}`;
      vi.mocked(fetch).mockResolvedValue(
        jsonResponse(addedTaskResponse(process, task, form, `${requestedTaskId}-other`)),
      );

      await expect(readTask(requestedTaskId, new AbortController().signal)).resolves.toEqual({
        kind: "invalid-response",
      });
    },
  );
});
