import { expect, it, vi } from "vitest";

import { createCaseExperienceService } from "./caseExperienceServiceFactory";
import type { AuthorizationIntakeDraft } from "./caseExperienceModels";
import type { ProductApiFetch } from "./productCaseClient";

const ref = (name: string) => `${name}_abcdefghijklmnop`;
const observed = "2099-09-10T12:00:00.000000Z";
const future = "2099-09-10T13:00:00.000000Z";
const signal = () => new AbortController().signal;

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const summary = {
  case_ref: ref("case"), kind: "authorization" as const, state: "active" as const,
  record_revision: "7", state_observed_at: observed,
};

function detailResponses() {
  return [
    json({
      schema: "portal-external-case-detail.v1", case: summary, allowed_actions: [],
      freshness: { observed_at: observed, valid_until: future },
    }),
    json({
      case_ref: summary.case_ref,
      documents: [{ document_ref: ref("document"), document_type_ref: ref("type"), disposition: "verified" }],
    }),
    json({
      case_ref: summary.case_ref,
      requests: [{
        request_ref: ref("request"), revision: "3", disposition: "replaced",
        policy_ref: ref("policy"), requested_document_type_refs: [ref("type")],
      }],
    }),
  ];
}

function service(fetcher: ProductApiFetch, audience: "beneficiary" | "provider" = "provider") {
  return createCaseExperienceService({
    audience,
    csrfToken: "csrf-current",
    commandId: () => ref("command"),
    fetcher,
  });
}

it("compõe o caso com documentos e pedidos usando apenas fatos retornados", async () => {
  const fetcher = vi.fn();
  for (const response of detailResponses()) fetcher.mockResolvedValueOnce(response);
  const api = service(fetcher);
  const result = await api.readCase(summary.case_ref, signal());
  expect(result.kind).toBe("success");
  if (result.kind !== "success") return;

  expect(result.value.summary).toEqual({
    caseRef: summary.case_ref,
    kind: "authorization",
    state: "active",
    recordRevision: "7",
    stateObservedAt: observed,
  });
  expect(result.value.documents.items[0]).toEqual({
    documentRef: ref("document"),
    name: "Documento verificado",
    kindLabel: `Tipo protegido: ${ref("type")}`,
    statusLabel: "Custódia verificada",
    canDownload: true,
  });
  expect(result.value.documents.items[0]).not.toHaveProperty("recordedAt");
  expect(result.value.documentRequests.items[0]).toMatchObject({
    requestRef: ref("request"), status: "replaced", statusLabel: "Pedido substituído",
    expectedRevision: "3", canRespond: false, dueAt: null,
  });
  expect(result.value.dossier.state.kind).toBe("unavailable");
  expect(result.value.chronology.state.kind).toBe("unavailable");
  expect(result.value.communications.state.kind).toBe("unavailable");
  expect(result.value.commands.state.kind).toBe("unavailable");
  expect(JSON.stringify(result.value)).not.toMatch(/undefined|Date\.now|concedida/i);
});

it("falha fechado no caso inteiro quando subrecurso revoga acesso", async () => {
  const [detail, , requests] = detailResponses();
  const fetcher = vi.fn()
    .mockResolvedValueOnce(detail)
    .mockResolvedValueOnce(json({ code: "operation_forbidden" }, 403))
    .mockResolvedValueOnce(requests);
  expect(await service(fetcher).readCase(summary.case_ref, signal())).toEqual({
    kind: "failure", failure: "access-revoked",
  });
});

it("mantém formulário AUTH indisponível sem provedor autorizado", async () => {
  const fetcher = vi.fn();
  const api = service(fetcher);
  expect(await api.readAuthorizationIntakeForm(signal())).toEqual({
    kind: "failure", failure: "resource-unavailable",
  });
  expect(api.contentUpload).toEqual({
    kind: "unavailable",
    message: "A transferência do conteúdo do arquivo ainda não está disponível neste serviço.",
  });
  expect(fetcher).not.toHaveBeenCalled();
});

it("não oferece submissão AUTH à audiência beneficiário", async () => {
  const fetcher = vi.fn();
  const result = await service(fetcher, "beneficiary").submitAuthorization({} as AuthorizationIntakeDraft, signal());
  expect(result).toEqual({ kind: "failure", failure: "access-revoked" });
  expect(fetcher).not.toHaveBeenCalled();
});

it("mapeia admissão 202 sem tratá-la como caso iniciado", async () => {
  const fetcher = vi.fn().mockResolvedValue(json({
    schema_version: 1, intake_ref: ref("intake"), command_id: ref("command"),
    revision: "0", disposition: "admitted", case_ref: null, start_receipt_ref: null,
  }, 202));
  const draft: AuthorizationIntakeDraft = {
    beneficiaryRef: ref("beneficiary"), providerRef: ref("provider"), guideRef: ref("guide"),
    procedureCode: "10101012", procedureCategory: "consulta", careCharacter: "eletivo",
    estimatedValueCents: "900719925474099312345", protectedDocumentRefs: [],
  };
  const result = await service(fetcher).submitAuthorization(draft, signal());
  expect(result).toEqual({
    kind: "success",
    progress: {
      intakeRef: ref("intake"), revision: "0", state: "admitted",
      stateLabel: "Solicitação recebida",
      description: "O comando foi admitido e ainda não confirma o início do caso.",
    },
  });
  expect(JSON.stringify(result)).not.toMatch(/caseRef|startReceiptRef|concedida/i);
});

it("reutiliza o command_id após resultado de envio desconhecido", async () => {
  const fetcher = vi.fn()
    .mockRejectedValueOnce(new TypeError("network"))
    .mockResolvedValueOnce(json({
      schema_version: 1, intake_ref: ref("intake"), command_id: ref("command"),
      revision: "1", disposition: "started", case_ref: ref("case"),
      start_receipt_ref: ref("receipt"),
    }, 202));
  const draft: AuthorizationIntakeDraft = {
    beneficiaryRef: ref("beneficiary"), providerRef: ref("provider"), guideRef: ref("guide"),
    procedureCode: "10101012", procedureCategory: "consulta", careCharacter: "urgencia",
    estimatedValueCents: "12500", protectedDocumentRefs: [ref("document")],
  };
  const api = service(fetcher);
  expect(await api.submitAuthorization(draft, signal())).toEqual({
    kind: "failure", failure: "outcome-unknown",
  });
  const recovered = await api.submitAuthorization(draft, signal());
  expect(recovered).toMatchObject({
    kind: "success",
    progress: {
      state: "started", revision: "1", caseRef: ref("case"), startReceiptRef: ref("receipt"),
    },
  });
  const commands = fetcher.mock.calls.map(([, options]) => JSON.parse(String(options?.body)).command_id);
  expect(commands).toEqual([ref("command"), ref("command")]);
});

it("acompanha intake por referência e apresenta a revisão recebida", async () => {
  const fetcher = vi.fn().mockResolvedValue(json({
    schema_version: 1, intake_ref: ref("intake"), command_id: ref("command"), revision: "9",
    disposition: "reconciling", case_ref: null, start_receipt_ref: null,
  }));
  const result = await service(fetcher).readAuthorizationProgress(ref("intake"), signal());
  expect(result).toMatchObject({
    kind: "success", progress: { state: "reconciling", revision: "9" },
  });
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/portal/intakes/${ref("intake")}`,
    expect.objectContaining({ method: "GET", redirect: "error" }),
  );
});
