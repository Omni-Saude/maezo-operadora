import { afterEach, expect, it, vi } from "vitest";
import { makeSubmission, readDecisionContext, readDecisionReceipt, submitDecision, validateContext, validateReceipt } from "./decisionClient";
import { formSchema, fieldSchema, validWire, wireSchema, type Schema } from "./decisionSchema";
import { taskReadBindings } from "./taskReadBindings";
import { admissionFixture, contextFixture, digest, huge, jsonResponse, receiptFixture } from "./test/decisionFixtures";

afterEach(() => vi.unstubAllGlobals());
function sample(raw: Schema): unknown {
  const s = fieldSchema(raw);
  if (s.const !== undefined) return s.const;
  if (s.enum) return s.enum[0];
  if (s.type === "boolean") return false;
  if (s.type === "array") return [];
  if (s.type === "object") return Object.fromEntries(Object.entries(s.properties ?? {}).map(([key, value]) => [key, sample(value)]));
  return s.pattern ? "1" : "Texto sintético de teste";
}
it.each(taskReadBindings)("deriva campos fechados do contrato existente: $process/$task", (binding) => {
  const c = contextFixture(binding.form);
  c.snapshot.task_definition_key = binding.task;
  expect(validateContext(c, "task-1")).toEqual(c);
  const inputs = sample(formSchema(binding.form)!);
  const body = makeSubmission(c, inputs, "command-1");
  expect(body).not.toBeNull();
  expect(body!.decision.process_definition_version).toBe(huge);
  expect(makeSubmission(c, { ...inputs as object, actor_id: "forged" }, "command-1")).toBeNull();
  expect(makeSubmission(c, { kind: binding.form }, "command-1")).toBeNull();
});
it.each(["tenant", "actor_id", "variables", "approval_tier", "dossier"])("recusa campo inesperado %s", (key) => {
  const c = contextFixture();
  expect(validateContext({ ...c, [key]: "protected" }, "task-1")).toBeNull();
  expect(validateContext({ ...c, snapshot: { ...c.snapshot, [key]: "protected" } }, "task-1")).toBeNull();
});
it("recusa contexto sem permissão, vencido, tarefa cruzada ou campos divergentes", () => {
  const c = contextFixture();
  expect(validateContext(c, "other")).toBeNull();
  expect(validateContext({ ...c, valid_until: new Date(Date.now() - 1).toISOString() }, "task-1")).toBeNull();
  expect(validateContext({ ...c, snapshot: { ...c.snapshot, allowed_actions: [] } }, "task-1")).toBeNull();
  expect(validateContext({ ...c, snapshot: { ...c.snapshot, allowed_inputs: ["actor_id"] } }, "task-1")).toBeNull();
  expect(validateContext({ ...c, snapshot: { ...c.snapshot, form_key: "escalation" } }, "task-1")).toBeNull();
});
it.each(["pending", "committed", "conflict"])("aceita somente prova coerente %s", (status) => {
  const r = receiptFixture("command-1", status);
  expect(validateReceipt(r, "task-1", "command-1")).toEqual(r);
  expect(validateReceipt(r, "other", "command-1")).toBeNull();
  expect(validateReceipt({ ...r, command_id: "other" }, "task-1", "command-1")).toBeNull();
  expect(validateReceipt({ ...r, variables: {} }, "task-1", "command-1")).toBeNull();
});
it("recusa falso recibo concluído, prova parcial e pending com prova de execução", () => {
  for (const key of ["engine_receipt_ref", "engine_recorded_at", "consumed_task_revision", "audit_result_ref"]) {
    expect(validateReceipt({ ...receiptFixture("c"), [key]: null }, "task-1", "c")).toBeNull();
  }
  expect(validateReceipt({ ...receiptFixture("c", "pending"), audit_result_ref: digest }, "task-1", "c")).toBeNull();
});
it("envia somente o DTO permitido e exige 202 pendente correlacionado", async () => {
  const fetcher = vi.fn().mockResolvedValue(jsonResponse(admissionFixture("c"), 202)); vi.stubGlobal("fetch", fetcher);
  const body = makeSubmission(contextFixture("pagto_aprovacao"), { kind: "pagto_aprovacao", decisao_pagamento: "APROVAR", valor_aprovado_cents: huge, justificativa_aprovacao: "teste" }, "c")!;
  expect((await submitDecision(body, "csrf", new AbortController().signal)).kind).toBe("success");
  const [path, options] = fetcher.mock.calls[0];
  expect(path).toBe("/api/v1/portal/tasks/task-1/decisions");
  expect(options).toMatchObject({ credentials: "same-origin", cache: "no-store", redirect: "error", method: "POST", headers: { "X-CSRF-Token": "csrf" } });
  expect(JSON.parse(options.body)).toEqual(body);
  expect(options.body).not.toMatch(/tenant|actor_id|approval_tier|variables/);
  fetcher.mockResolvedValue(jsonResponse(admissionFixture("wrong"), 202));
  expect((await submitDecision(body, "csrf", new AbortController().signal)).kind).toBe("outcome-unknown");
  fetcher.mockResolvedValue(jsonResponse(admissionFixture("c"), 200));
  expect((await submitDecision(body, "csrf", new AbortController().signal)).kind).toBe("outcome-unknown");
});
it("usa códigos fechados e nunca propaga detalhes de erro", async () => {
  const fetcher = vi.fn().mockResolvedValue(jsonResponse({ schema_version: "portal-decision-error.v1", code: "production_capabilities_unavailable" }, 503)); vi.stubGlobal("fetch", fetcher);
  expect(await readDecisionContext("task-1", new AbortController().signal)).toEqual({ kind: "production_capabilities_unavailable" });
  fetcher.mockResolvedValue(jsonResponse({ schema_version: "portal-decision-error.v1", code: "dependency_unavailable", detail: "protected" }, 503));
  expect(await readDecisionContext("task-1", new AbortController().signal)).toEqual({ kind: "invalid-response" });
});
it("consulta comando e recibo somente com referências opacas", async () => {
  const fetcher = vi.fn().mockImplementation(() => Promise.resolve(jsonResponse(receiptFixture("c")))); vi.stubGlobal("fetch", fetcher);
  expect((await readDecisionReceipt("task-1", "c", new AbortController().signal)).kind).toBe("success");
  expect((await readDecisionReceipt("task-1", "c", new AbortController().signal, true)).kind).toBe("success");
  expect(fetcher.mock.calls.map(([url]) => url)).toEqual(["/api/v1/portal/commands/c?task_id=task-1", "/api/v1/portal/commands/c/receipt?task_id=task-1"]);
});
it("valida sem coerção números, moedas, datas, unicode e formas desconhecidas", () => {
  expect(validWire(huge, wireSchema("Centavos"))).toBe(true);
  for (const v of [1.2, "1.2", "01", "1e3", "1\n"]) expect(validWire(v, wireSchema("Centavos"))).toBe(false);
  expect(validWire("2026-02-30T00:00:00Z", { type: "string", format: "date-time" })).toBe(false);
  expect(validWire({}, {})).toBe(false);
});
it("recusa referências inválidas antes de colocar conteúdo em URL", async () => {
  const fetcher = vi.fn(); vi.stubGlobal("fetch", fetcher);
  for (const value of ["texto livre", "task/path", "task?query", "task\u0085private"]) {
    expect(await readDecisionContext(value, new AbortController().signal)).toEqual({ kind: "invalid_request" });
    expect(await readDecisionReceipt("task-1", value, new AbortController().signal)).toEqual({ kind: "invalid_request" });
  }
  expect(fetcher).not.toHaveBeenCalled();
});
it("mantém texto humano exato e rejeita ampliação futura desconhecida do schema", () => {
  const text = "  Texto sintético\ncom tab\te acento e\u0301  ";
  const body = makeSubmission(contextFixture("escalation"), { kind: "escalation", resultado: "resolvido_humano", notas_resolucao: text }, "c")!;
  expect(JSON.parse(JSON.stringify(body)).decision.inputs.notas_resolucao).toBe(text);
  expect(validWire("value", { type: "string", not: { const: "value" } } as Schema)).toBe(false);
});
