import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { readTaskContext } from "./contextClient";

function context(overrides: Record<string, unknown> = {}) {
  return {
    schema: "portal-task-context.v1",
    task_id: "task-1",
    etapa: "atendimento",
    motivo_categoria: "red_flag_clinico",
    severidade: "grave",
    prioridade: "P1",
    grupo_atendimento: "plantao-clinico",
    aberto_em: "2026-09-28T21:31:32Z",
    ack_vence_em: "2026-09-28T21:36:33Z",
    resolucao_vence_em: "2026-09-28T22:01:33Z",
    resumo_contexto: "Idoso com dor no peito.",
    observed_at: "2026-09-28T21:40:00Z",
    ...overrides,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function refusal(code: string) {
  return { schema: "portal-context-error.v1", code };
}

const signal = () => new AbortController().signal;

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("readTaskContext", () => {
  it("lê o contexto com a requisição fechada: GET, mesma origem, sem cache e sem redirecionamento", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(context()));
    const result = await readTaskContext("task-1", signal());

    expect(result.kind).toBe("success");
    const [url, init] = vi.mocked(fetch).mock.calls[0] as [string, RequestInit];
    expect(url).toBe("/api/v1/portal/tasks/task-1/context");
    expect(init.method).toBe("GET");
    expect(init.credentials).toBe("same-origin");
    expect(init.cache).toBe("no-store");
    expect(init.redirect).toBe("error");
    expect(init.body).toBeUndefined();
    // Nothing the page knows about the session travels here beyond the cookie.
    expect(Object.keys(init.headers as Record<string, string>)).toEqual(["Accept"]);
  });

  it("aceita fatos ausentes como nulos, sem inventar valores", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(
      json(
        context({
          etapa: "supervisao",
          motivo_categoria: null,
          severidade: null,
          prioridade: null,
          grupo_atendimento: null,
          aberto_em: null,
          ack_vence_em: null,
          resolucao_vence_em: null,
          resumo_contexto: null,
        }),
      ),
    );
    const result = await readTaskContext("task-1", signal());
    expect(result.kind).toBe("success");
    if (result.kind === "success") expect(result.value.resumo_contexto).toBeNull();
  });

  it("codifica no caminho um identificador permitido que precisa de codificação", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(context({ task_id: "a%b:1" })));
    await readTaskContext("a%b:1", signal());
    expect((vi.mocked(fetch).mock.calls[0] as [string])[0]).toBe("/api/v1/portal/tasks/a%25b%3A1/context");
  });

  it.each(["", " ", "a/b", "a?b", "a#b", "a\nb", "x".repeat(513)])(
    "recusa o identificador %j sem sair do navegador",
    async (taskId) => {
      expect(await readTaskContext(taskId, signal())).toEqual({ kind: "invalid_request" });
      expect(fetch).not.toHaveBeenCalled();
    },
  );

  it("recusa uma resposta sobre outra tarefa", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(context({ task_id: "task-2" })));
    expect(await readTaskContext("task-1", signal())).toEqual({ kind: "invalid-response" });
  });

  it.each([
    ["campo extra", { telefone: "11912345678" }],
    ["esquema errado", { schema: "portal-task-context.v2" }],
    ["etapa fora do contrato", { etapa: "outra" }],
    ["prioridade fora do formato", { prioridade: "urgente" }],
    ["grupo com barra", { grupo_atendimento: "a/b" }],
    ["data que não é data", { aberto_em: "ontem" }],
    ["resumo que não é texto", { resumo_contexto: 42 }],
    ["resumo acima do limite", { resumo_contexto: "x".repeat(1001) }],
    ["resumo vazio", { resumo_contexto: "" }],
  ])("recusa uma resposta com %s", async (_name, changes) => {
    vi.mocked(fetch).mockResolvedValueOnce(json(context(changes)));
    expect(await readTaskContext("task-1", signal())).toEqual({ kind: "invalid-response" });
  });

  it("recusa uma resposta a que falta um campo obrigatório", async () => {
    const { resumo_contexto: _omitted, ...missing } = context();
    vi.mocked(fetch).mockResolvedValueOnce(json(missing));
    expect(await readTaskContext("task-1", signal())).toEqual({ kind: "invalid-response" });
  });

  it.each([
    [400, "invalid_request"],
    [401, "session_unavailable"],
    [403, "employee_access_required"],
    [404, "resource_unavailable"],
    [409, "refresh_required"],
    [501, "context_unavailable"],
    [503, "read_dependency_unavailable"],
  ])("traduz o HTTP %i em %s", async (status, code) => {
    vi.mocked(fetch).mockResolvedValueOnce(json(refusal(code), status));
    expect(await readTaskContext("task-1", signal())).toEqual({ kind: code });
  });

  it("não aceita um código de erro que não corresponde ao status HTTP", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(json(refusal("employee_access_required"), 404));
    expect(await readTaskContext("task-1", signal())).toEqual({ kind: "invalid-response" });
  });

  it.each([418, 500, 502])("trata o HTTP %i inesperado como resposta inválida", async (status) => {
    vi.mocked(fetch).mockResolvedValueOnce(json(refusal("resource_unavailable"), status));
    expect(await readTaskContext("task-1", signal())).toEqual({ kind: "invalid-response" });
  });

  it("trata um corpo ilegível como resposta inválida", async () => {
    vi.mocked(fetch).mockResolvedValueOnce(new Response("<html>x</html>", { status: 200 }));
    expect(await readTaskContext("task-1", signal())).toEqual({ kind: "invalid-response" });
  });

  it("trata uma falha de rede como dependência indisponível", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"));
    expect(await readTaskContext("task-1", signal())).toEqual({ kind: "read_dependency_unavailable" });
  });

  it("propaga o cancelamento em vez de o disfarçar de falha", async () => {
    vi.mocked(fetch).mockRejectedValueOnce(new DOMException("Aborted", "AbortError"));
    await expect(readTaskContext("task-1", signal())).rejects.toMatchObject({ name: "AbortError" });
  });
});
