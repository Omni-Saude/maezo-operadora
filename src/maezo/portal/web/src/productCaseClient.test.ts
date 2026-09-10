import { expect, it, vi } from "vitest";

import {
  createPortalProductClient,
  type AuthIntakeSubmission,
  type ProductApiFetch,
} from "./productCaseClient";

const future = "2099-09-10T13:00:00.000000Z";
const observed = "2099-09-10T12:00:00.000000Z";
const ref = (name: string) => `${name}_abcdefghijklmnop`;
const signal = () => new AbortController().signal;

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function client(fetcher: ProductApiFetch, audience: "beneficiary" | "provider" = "provider") {
  return createPortalProductClient({ audience, csrfToken: "csrf-current", fetcher });
}

const intake: AuthIntakeSubmission = {
  schema_version: 1,
  command_id: ref("command"),
  beneficiary_ref: ref("beneficiary"),
  provider_ref: ref("provider"),
  guide_ref: ref("guide"),
  codigo_procedimento_tuss: "10101012",
  categoria_procedimento: "consulta",
  carater_atendimento: "eletivo",
  valor_estimado_centavos: "90071992547409931234567890",
  document_refs: [ref("document")],
};

it("consulta casos sem enviar audiência e recusa projeção de outro vínculo", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json({
      schema: "portal-external-case-page.v1",
      audience: "provider",
      items: [{
        case_ref: ref("case"), kind: "authorization", state: "active",
        record_revision: "2", state_observed_at: observed,
      }],
      next_cursor: "cursor-next",
      freshness: { observed_at: observed, valid_until: future },
    }))
    .mockResolvedValueOnce(json({
      schema: "portal-external-case-page.v1",
      audience: "beneficiary",
      items: [], next_cursor: null,
      freshness: { observed_at: observed, valid_until: future },
    }));
  const api = client(fetcher);

  expect((await api.listCases(signal())).kind).toBe("success");
  expect(fetcher).toHaveBeenNthCalledWith(1, "/api/v1/portal/cases", expect.objectContaining({
    method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: { Accept: "application/json" },
  }));
  const mismatch = await api.listCases(signal());
  expect(mismatch).toEqual({ kind: "failure", failure: "invalid-response" });
  expect(JSON.stringify(fetcher.mock.calls)).not.toMatch(/audience|provider_ref|tenant|principal/i);
});

it("envia AUTH com CSRF no cabeçalho e preserva centavos e recibo 202", async () => {
  const fetcher = vi.fn().mockResolvedValue(json({
    schema_version: 1,
    intake_ref: ref("intake"),
    command_id: intake.command_id,
    revision: "0",
    disposition: "admitted",
    case_ref: null,
    start_receipt_ref: null,
  }, 202));
  const api = client(fetcher);

  const result = await api.submitAuthorization(intake, signal());
  expect(result).toMatchObject({ kind: "success", value: { disposition: "admitted" } });
  const [, options] = fetcher.mock.calls[0];
  expect(options).toMatchObject({
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    redirect: "error",
    headers: {
      Accept: "application/json",
      "Content-Type": "application/json",
      "X-CSRF-Token": "csrf-current",
    },
  });
  expect(JSON.parse(String(options.body))).toEqual(intake);
  expect(String(options.body)).toContain("90071992547409931234567890");
});

it("trata resposta POST malformada como resultado desconhecido e valida início comprovado", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(new Response("not-json", { status: 202 }))
    .mockResolvedValueOnce(json({
      schema_version: 1, intake_ref: ref("intake"), command_id: intake.command_id,
      revision: "1", disposition: "started", case_ref: null, start_receipt_ref: null,
    }, 202));
  const api = client(fetcher);
  expect(await api.submitAuthorization(intake, signal())).toEqual({
    kind: "failure", failure: "outcome-unknown",
  });
  expect(await api.submitAuthorization(intake, signal())).toEqual({
    kind: "failure", failure: "outcome-unknown",
  });
});

it("executa as quatro operações de metadados documentais nas rotas aprovadas", async () => {
  const uploadRef = ref("upload");
  const caseRef = ref("case");
  const intakeRef = ref("intake");
  const requestRef = ref("request");
  const commandId = ref("command");
  const upload = { command_id: commandId, policy_ref: ref("policy"), document_type_ref: ref("type") };
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json({ upload_ref: uploadRef, disposition: "awaiting_content", document_ref: null }, 202))
    .mockResolvedValueOnce(json({ upload_ref: uploadRef, disposition: "screening", document_ref: null }, 202))
    .mockResolvedValueOnce(json({ upload_ref: uploadRef, disposition: "verified", document_ref: ref("document") }, 202))
    .mockResolvedValueOnce(json({
      command_id: commandId, request_ref: requestRef, revision: "4",
      disposition: "correlated", correlation_receipt_ref: ref("receipt"),
    }, 202));
  const api = client(fetcher);

  expect((await api.initiateCaseUpload(caseRef, upload, signal())).kind).toBe("success");
  expect((await api.initiateIntakeUpload(intakeRef, upload, signal())).kind).toBe("success");
  expect((await api.completeUpload(uploadRef, { command_id: commandId }, signal())).kind).toBe("success");
  expect((await api.respondToDocumentRequest(caseRef, requestRef, {
    command_id: commandId, expected_revision: "3", document_refs: [ref("document")],
  }, signal())).kind).toBe("success");

  expect(fetcher.mock.calls.map(([path]) => path)).toEqual([
    `/api/v1/portal/cases/${caseRef}/document-uploads`,
    `/api/v1/portal/intakes/${intakeRef}/document-uploads`,
    `/api/v1/portal/document-uploads/${uploadRef}/complete`,
    `/api/v1/portal/cases/${caseRef}/document-requests/${requestRef}/responses`,
  ]);
});

it("não envia resposta documental vazia ou repetida", async () => {
  const fetcher = vi.fn();
  const api = client(fetcher);
  const base = { command_id: ref("command"), expected_revision: "2" };
  expect(await api.respondToDocumentRequest(ref("case"), ref("request"), {
    ...base, document_refs: [],
  }, signal())).toEqual({ kind: "failure", failure: "invalid_request" });
  expect(await api.respondToDocumentRequest(ref("case"), ref("request"), {
    ...base, document_refs: [ref("document"), ref("document")],
  }, signal())).toEqual({ kind: "failure", failure: "invalid_request" });
  expect(fetcher).not.toHaveBeenCalled();
});

it("aceita bytes apenas após redirecionamento para a rota PHI de mesma origem", async () => {
  const documentRef = ref("document");
  const response = new Response("protected bytes", {
    status: 200,
    headers: { "Content-Type": "application/pdf" },
  });
  Object.defineProperties(response, {
    redirected: { value: true },
    url: { value: `http://localhost:3000/api/v1/phi/documents/${documentRef}/content` },
  });
  const fetcher = vi.fn().mockResolvedValue(response);
  const result = await client(fetcher).downloadDocument(documentRef, signal());
  expect(result.kind).toBe("success");
  if (result.kind === "success") expect(result.value).toMatchObject({ size: 15, type: "application/pdf" });
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/portal/documents/${documentRef}/content`,
    expect.objectContaining({ redirect: "follow", credentials: "same-origin", cache: "no-store" }),
  );
});

it("recusa redirecionamento de documento para outra origem", async () => {
  const response = new Response("private", { status: 200 });
  Object.defineProperties(response, {
    redirected: { value: true },
    url: { value: `https://files.example/api/v1/phi/documents/${ref("document")}/content` },
  });
  const result = await client(vi.fn().mockResolvedValue(response)).downloadDocument(ref("document"), signal());
  expect(result).toEqual({ kind: "failure", failure: "invalid-response" });
});

it("propaga aborto sem convertê-lo em indisponibilidade", async () => {
  const controller = new AbortController();
  controller.abort();
  const error = new DOMException("aborted", "AbortError");
  const api = client(vi.fn().mockRejectedValue(error));
  await expect(api.listCases(controller.signal)).rejects.toBe(error);
});
