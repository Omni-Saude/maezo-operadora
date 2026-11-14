import { afterEach, beforeEach, expect, it, vi } from "vitest";

import {
  createStaffDocumentsClient,
  validateStaffDocumentPage,
  validateStaffDocumentRequestPage,
} from "./staffDocumentsClient";

const caseRef = "case_staff_abcdefghijklmnop";

function documentPage() {
  return {
    case_ref: caseRef,
    documents: [
      {
        document_ref: "document_ref_0000000001",
        document_type_ref: "documento_tipo_ref_0001",
        disposition: "verified",
      },
    ],
  } as const;
}

function requestPage() {
  return {
    case_ref: caseRef,
    requests: [
      {
        request_ref: "request_ref_0000000001",
        revision: "3",
        disposition: "open",
        policy_ref: "policy_ref_0000000001",
        requested_document_type_refs: ["documento_tipo_ref_0001"],
      },
    ],
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

it("consulta somente leitura os documentos do caso na rota GET montada", async () => {
  vi.mocked(fetch).mockResolvedValue(response(documentPage()));
  const result = await createStaffDocumentsClient().listDocuments(caseRef, new AbortController().signal);
  expect(result).toEqual({ kind: "success", value: documentPage() });
  expect(fetch).toHaveBeenCalledWith(
    `/api/v1/portal/cases/${caseRef}/documents`,
    expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      // O 307 para o plano PHI protegido nunca é seguido daqui: bytes ficam fora.
      redirect: "error",
      headers: { Accept: "application/json" },
    }),
  );
  expect(JSON.stringify(vi.mocked(fetch).mock.calls[0])).not.toMatch(/"body"|csrf|upload|content/i);
});

it("consulta somente leitura os pedidos de documento do caso", async () => {
  vi.mocked(fetch).mockResolvedValue(response(requestPage()));
  const result = await createStaffDocumentsClient().listDocumentRequests(
    caseRef,
    new AbortController().signal,
  );
  expect(result).toEqual({ kind: "success", value: requestPage() });
  expect(fetch).toHaveBeenCalledWith(
    `/api/v1/portal/cases/${caseRef}/document-requests`,
    expect.objectContaining({ method: "GET", redirect: "error" }),
  );
  expect(JSON.stringify(vi.mocked(fetch).mock.calls[0])).not.toMatch(/"body"|csrf|responses/i);
});

it("expõe somente os dois métodos de leitura — nenhum envio, resposta ou conteúdo", () => {
  expect(Object.keys(createStaffDocumentsClient()).sort()).toEqual([
    "listDocumentRequests",
    "listDocuments",
  ]);
});

it.each([
  (value: ReturnType<typeof documentPage>) => ({ ...value, case_ref: "case_staff_zyxwvutsrqponml" }),
  (value: ReturnType<typeof documentPage>) => ({ ...value, extra: true }),
  (value: ReturnType<typeof documentPage>) => ({
    ...value,
    documents: [{ ...value.documents[0], disposition: "quarantined" }],
  }),
  (value: ReturnType<typeof documentPage>) => ({
    ...value,
    documents: [value.documents[0], value.documents[0]],
  }),
  (value: ReturnType<typeof documentPage>) => ({
    ...value,
    documents: [{ ...value.documents[0], document_ref: "curto" }],
  }),
])("recusa página de documentos fora do contrato %#", (mutate) => {
  expect(validateStaffDocumentPage(mutate(documentPage()), caseRef)).toBeNull();
});

it.each([
  (value: ReturnType<typeof requestPage>) => ({ ...value, case_ref: "case_staff_zyxwvutsrqponml" }),
  (value: ReturnType<typeof requestPage>) => ({
    ...value,
    requests: [{ ...value.requests[0], disposition: "correlated" }],
  }),
  (value: ReturnType<typeof requestPage>) => ({
    ...value,
    requests: [{ ...value.requests[0], revision: "-1" }],
  }),
  (value: ReturnType<typeof requestPage>) => ({
    ...value,
    requests: [{ ...value.requests[0], revision: "01" }],
  }),
])("recusa página de pedidos fora do contrato %#", (mutate) => {
  expect(validateStaffDocumentRequestPage(mutate(requestPage()), caseRef)).toBeNull();
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
  await expect(
    createStaffDocumentsClient().listDocuments(caseRef, new AbortController().signal),
  ).resolves.toEqual({ kind: "failure", failure: code });
});

it("recusa resposta que não é o contrato e não exibe nada", async () => {
  vi.mocked(fetch).mockResolvedValue(response({ unexpected: true }));
  await expect(
    createStaffDocumentsClient().listDocuments(caseRef, new AbortController().signal),
  ).resolves.toEqual({ kind: "failure", failure: "invalid-response" });
});

it("recusa referência local inválida sem iniciar uma consulta", async () => {
  await expect(
    createStaffDocumentsClient().listDocuments("case", new AbortController().signal),
  ).resolves.toEqual({ kind: "failure", failure: "invalid_request" });
  await expect(
    createStaffDocumentsClient().listDocumentRequests(
      "case_staff_zyxwvutsrqponml/../x",
      new AbortController().signal,
    ),
  ).resolves.toEqual({ kind: "failure", failure: "invalid_request" });
  expect(fetch).not.toHaveBeenCalled();
});
