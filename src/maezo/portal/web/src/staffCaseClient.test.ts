import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  createStaffCaseClient,
  validateStaffDetail,
  validateStaffPage,
} from "./staffCaseClient";

const caseRef = "case_staff_abcdefghijklmnop";

function detail() {
  return {
    schema: "portal-staff-case-detail.v1",
    case: {
      case_ref: caseRef,
      kind: "authorization",
      state: "active",
      record_revision: "9007199254740993",
      state_observed_at: "2099-09-10T12:00:00.000000Z",
    },
    identity: {
      upstream_resource_key: "guide-opaque",
      case_ref: caseRef,
      process_instance_ref: "instance-opaque",
      process_definition_id: "definition-opaque",
      process_definition_key: "SP-OP-AUTH-001",
      process_definition_version: "3",
      process_definition_digest: "a".repeat(64),
      kind: "authorization",
    },
    active_tasks: [{
      task_id: "task-opaque-1",
      task_definition_key: "UT_AnaliseMedicoAuditor",
      task_revision: "9007199254740994",
      created_at: "2099-09-10T11:00:00.000000Z",
      due_at: null,
      assignee_ref: null,
    }],
    next_task_cursor: null,
    tasks_complete: true,
    freshness: {
      observed_at: "2099-09-10T12:00:00.000000Z",
      source_observed_at: "2099-09-10T11:59:59.000000Z",
      valid_until: "2099-09-10T12:00:10.000000Z",
      refresh_after_seconds: 10,
    },
  } as const;
}

function page() {
  return {
    schema: "portal-staff-case-page.v1",
    items: [
      {
        case_ref: "case_staff_abcdefghijklmnop",
        kind: "authorization",
        state: "active",
        record_revision: "7",
        state_observed_at: "2099-09-10T11:59:58.000000Z",
      },
      {
        case_ref: "case_staff_bcdefghijklmnopq",
        kind: "authorization",
        state: "ended",
        record_revision: "8",
        state_observed_at: "2099-09-10T11:59:59.000000Z",
      },
    ],
    next_cursor: "cursor.staff-page-2",
    freshness: {
      observed_at: "2099-09-10T12:00:00.000000Z",
      source_observed_at: "2099-09-10T11:59:59.000000Z",
      valid_until: "2099-09-10T12:00:10.000000Z",
      refresh_after_seconds: 10,
    },
  } as const;
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => vi.unstubAllGlobals());

it("consulta somente a referência exata com a sessão atual", async () => {
  vi.mocked(fetch).mockResolvedValue(response(detail()));
  const result = await createStaffCaseClient().readCase(caseRef, new AbortController().signal);
  expect(result).toEqual({ kind: "success", value: detail() });
  expect(fetch).toHaveBeenCalledWith(
    `/api/v1/portal/cases/${caseRef}`,
    expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json" },
    }),
  );
  expect(JSON.stringify(vi.mocked(fetch).mock.calls[0])).not.toMatch(/audience|csrf|principal/i);
});

it("consulta uma página staff sem enviar autoridade pelo navegador", async () => {
  vi.mocked(fetch).mockResolvedValue(response(page()));
  const result = await createStaffCaseClient().listCases(
    "cursor.staff-page-1",
    new AbortController().signal,
  );
  expect(result).toEqual({ kind: "success", value: page() });
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/portal/cases?kind=authorization&limit=25&cursor=cursor.staff-page-1",
    expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json" },
    }),
  );
  expect(JSON.stringify(vi.mocked(fetch).mock.calls[0])).not.toMatch(/audience|csrf|principal/i);
});

it.each([
  (value: ReturnType<typeof page>) => ({ ...value, total: 2 }),
  (value: ReturnType<typeof page>) => ({ ...value, schema: "portal-external-case-page.v1" }),
  (value: ReturnType<typeof page>) => ({ ...value, next_cursor: "" }),
  (value: ReturnType<typeof page>) => ({ ...value, items: [...value.items].reverse() }),
  (value: ReturnType<typeof page>) => ({ ...value, items: [value.items[0], value.items[0]] }),
  (value: ReturnType<typeof page>) => ({
    ...value,
    freshness: { ...value.freshness, refresh_after_seconds: "10" },
  }),
])("recusa página que não satisfaz o contrato staff %#", (mutate) => {
  expect(validateStaffPage(mutate(page()))).toBeNull();
});

it.each([
  (value: ReturnType<typeof detail>) => ({ ...value, private_note: "PRIVATE_CANARY" }),
  (value: ReturnType<typeof detail>) => ({ ...value, schema: "portal-external-case-detail.v1" }),
  (value: ReturnType<typeof detail>) => ({ ...value, case: { ...value.case, case_ref: "other_case_abcdefghijklmnop" } }),
  (value: ReturnType<typeof detail>) => ({ ...value, freshness: { ...value.freshness, refresh_after_seconds: "10" } }),
  (value: ReturnType<typeof detail>) => ({ ...value, active_tasks: [
    { ...value.active_tasks[0], task_id: "task-z" },
    { ...value.active_tasks[0], task_id: "task-a" },
  ] }),
  (value: ReturnType<typeof detail>) => ({ ...value, tasks_complete: false }),
])("recusa projeção que não satisfaz o contrato staff %#", (mutate) => {
  expect(validateStaffDetail(mutate(detail()), caseRef)).toBeNull();
});

it.each([
  [400, "invalid_request"],
  [401, "authentication_unavailable"],
  [403, "operation_forbidden"],
  [404, "resource_unavailable"],
  [409, "conflict"],
  [503, "dependency_unavailable"],
] as const)("mapeia HTTP %s somente com o erro fechado correspondente", async (status, code) => {
  vi.mocked(fetch).mockResolvedValue(response({ code }, status));
  await expect(createStaffCaseClient().readCase(caseRef, new AbortController().signal)).resolves.toEqual({
    kind: "failure",
    failure: code,
  });
});

it("recusa referência local inválida sem iniciar uma consulta", async () => {
  await expect(createStaffCaseClient().readCase("case", new AbortController().signal)).resolves.toEqual({
    kind: "failure",
    failure: "invalid_request",
  });
  expect(fetch).not.toHaveBeenCalled();
});

it("recusa cursor local inválido sem iniciar uma consulta", async () => {
  await expect(createStaffCaseClient().listCases(" cursor ", new AbortController().signal)).resolves.toEqual({
    kind: "failure",
    failure: "invalid_request",
  });
  expect(fetch).not.toHaveBeenCalled();
});
