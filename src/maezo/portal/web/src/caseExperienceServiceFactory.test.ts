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

const recoveryDraft: AuthorizationIntakeDraft = {
  beneficiaryRef: ref("beneficiary"), providerRef: ref("provider"), guideRef: ref("guide"),
  procedureCode: "10101012", procedureCategory: "consulta", careCharacter: "urgencia",
  estimatedValueCents: "12500", protectedDocumentRefs: [ref("document")],
};

function recoveryService(fetcher: ProductApiFetch) {
  let next = 0;
  return createCaseExperienceService({
    audience: "provider", csrfToken: "synthetic-current", fetcher,
    commandId: () => ref(`command${++next}`),
  });
}

function admitted(command: string) {
  return json({ schema_version: 1, intake_ref: ref("intake"), command_id: command,
    revision: "0", disposition: "admitted", case_ref: null, start_receipt_ref: null }, 202);
}

it.each([
  [400, "invalid_request"], [401, "authentication_unavailable"], [403, "operation_forbidden"],
  [404, "resource_unavailable"], [409, "conflict"], [503, "dependency_unavailable"],
] as const)("retém identidade e payload desconhecidos após recusa %s", async (status, code) => {
  const bodies: string[] = [];
  const api = recoveryService(vi.fn(async (_input, init) => {
    bodies.push(String(init?.body));
    if (bodies.length === 1) throw new TypeError("synthetic lost acknowledgement");
    return bodies.length === 2 ? json({ code }, status) : admitted(JSON.parse(bodies[0]).command_id);
  }));
  expect(await api.submitAuthorization(recoveryDraft, signal())).toEqual({ kind: "failure", failure: "outcome-unknown" });
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("failure");
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(bodies).toEqual([bodies[0], bodies[0], bodies[0]]);
});

it("retém command_id após 503 estruturado sem inventar ausência de commit", async () => {
  const bodies: string[] = [];
  const api = recoveryService(vi.fn(async (_input, init) => {
    bodies.push(String(init?.body));
    return bodies.length === 1 ? json({ code: "dependency_unavailable" }, 503)
      : admitted(JSON.parse(bodies[0]).command_id);
  }));
  expect(await api.submitAuthorization(recoveryDraft, signal())).toEqual({ kind: "failure", failure: "outcome-unknown" });
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(bodies).toEqual([bodies[0], bodies[0]]);
});

it("impede draft concorrente até o recibo resolver o comando desconhecido", async () => {
  const bodies: string[] = [];
  const api = recoveryService(vi.fn(async (_input, init) => {
    bodies.push(String(init?.body));
    if (bodies.length === 1) throw new TypeError("synthetic lost acknowledgement");
    return admitted(JSON.parse(String(init?.body)).command_id);
  }));
  await api.submitAuthorization(recoveryDraft, signal());
  const changed = { ...recoveryDraft, guideRef: ref("other_guide") };
  expect(await api.submitAuthorization(changed, signal())).toEqual({ kind: "failure", failure: "outcome-unknown" });
  expect(bodies).toHaveLength(1);
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect((await api.submitAuthorization(changed, signal())).kind).toBe("success");
  expect(JSON.parse(bodies[1]).command_id).toBe(JSON.parse(bodies[0]).command_id);
  expect(JSON.parse(bodies[2]).command_id).not.toBe(JSON.parse(bodies[0]).command_id);
});

it("libera tentativa nova após primeira recusa sem resultado desconhecido", async () => {
  const bodies: string[] = [];
  const api = recoveryService(vi.fn(async (_input, init) => {
    bodies.push(String(init?.body));
    return bodies.length === 1 ? json({ code: "invalid_request" }, 400)
      : admitted(JSON.parse(String(init?.body)).command_id);
  }));
  await api.submitAuthorization(recoveryDraft, signal());
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(JSON.parse(bodies[1]).command_id).not.toBe(JSON.parse(bodies[0]).command_id);
});

it("preserva tentativa abortada e bloqueia segundo envio enquanto o primeiro está pendente", async () => {
  let reject!: (reason: unknown) => void;
  const bodies: string[] = [];
  const fetcher = vi.fn(async (_input: RequestInfo | URL, init?: RequestInit) => {
    bodies.push(String(init?.body));
    return bodies.length === 1 ? new Promise<Response>((_resolve, fail) => { reject = fail; })
      : admitted(JSON.parse(bodies[0]).command_id);
  });
  const api = recoveryService(fetcher);
  const pending = api.submitAuthorization(recoveryDraft, signal());
  expect(await api.submitAuthorization(recoveryDraft, signal())).toEqual({ kind: "failure", failure: "outcome-unknown" });
  expect(bodies).toHaveLength(1);
  reject(new DOMException("synthetic abort", "AbortError"));
  await expect(pending).rejects.toMatchObject({ name: "AbortError" });
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(bodies).toEqual([bodies[0], bodies[0]]);
});

it.each(["documents", "document-requests"])("recusa validade expirada aguardando %s", async (slow) => {
  const start = Date.parse("2026-09-10T12:00:00.000Z");
  const clock = vi.spyOn(Date, "now").mockReturnValue(start);
  let release!: (value: Response) => void;
  const delayed = new Promise<Response>((resolve) => { release = resolve; });
  const page = (kind: string) => json(kind === "documents"
    ? { case_ref: summary.case_ref, documents: [] } : { case_ref: summary.case_ref, requests: [] });
  const api = service(vi.fn(async (input) => {
    const url = String(input);
    if (url.endsWith(`/${slow}`)) return delayed;
    if (url.endsWith("/documents")) return page("documents");
    if (url.endsWith("/document-requests")) return page("document-requests");
    return json({ schema: "portal-external-case-detail.v1", case: summary, allowed_actions: [],
      freshness: { observed_at: "2026-09-10T11:59:00.000000Z", valid_until: "2026-09-10T12:00:01.000000Z" } });
  }));
  const pending = api.readCase(summary.case_ref, signal());
  for (let i = 0; i < 30; i++) await Promise.resolve();
  expect(clock).toHaveBeenCalled();
  clock.mockReturnValue(start + 1000); // exclusive exact original expiry boundary
  release(page(slow));
  expect(await pending).toEqual({ kind: "failure", failure: "refresh-required" });
});

it("revalida a página após a conclusão assíncrona do cliente", async () => {
  const start = Date.parse("2026-09-10T12:00:00.000Z");
  vi.spyOn(Date, "now").mockReturnValueOnce(start).mockReturnValue(start + 1000);
  const api = service(vi.fn(async () => json({
    schema: "portal-external-case-page.v1", audience: "provider", items: [summary], next_cursor: null,
    freshness: { observed_at: "2026-09-10T11:59:00.000000Z", valid_until: "2026-09-10T12:00:01.000000Z" },
  })));
  expect(await api.listCases(signal())).toEqual({ kind: "failure", failure: "refresh-required" });
});

it.each([
  [401, "authentication_unavailable", "session-unavailable"],
  [403, "operation_forbidden", "access-revoked"],
  [409, "conflict", "refresh-required"],
  [503, "dependency_unavailable", "outcome-unknown"],
] as const)("preserva o primeiro comando após possível commit seguido de %s", async (status, code, failure) => {
  const bodies: string[] = [];
  const api = recoveryService(vi.fn(async (_input, init) => {
    bodies.push(String(init?.body));
    return bodies.length === 1 ? json({ code }, status) : admitted(JSON.parse(bodies[0]).command_id);
  }));
  expect(await api.submitAuthorization(recoveryDraft, signal())).toEqual({ kind: "failure", failure });
  const competing = { ...recoveryDraft, guideRef: ref("competing_guide") };
  expect(await api.submitAuthorization(competing, signal())).toEqual({ kind: "failure", failure: "outcome-unknown" });
  expect(bodies).toHaveLength(1);
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(bodies).toEqual([bodies[0], bodies[0]]);
  expect((await api.submitAuthorization(competing, signal())).kind).toBe("failure");
  // Its response uses the OLD command and is rejected, not accepted as resolution.
  expect(JSON.parse(bodies[2]).command_id).not.toBe(JSON.parse(bodies[0]).command_id);
});

it("não perde a identidade após recusa inicial seguida de 400", async () => {
  const bodies: string[] = [];
  const api = recoveryService(vi.fn(async (_input, init) => {
    bodies.push(String(init?.body));
    if (bodies.length === 1) return json({ code: "operation_forbidden" }, 403);
    return bodies.length === 2 ? json({ code: "invalid_request" }, 400)
      : admitted(JSON.parse(bodies[0]).command_id);
  }));
  await api.submitAuthorization(recoveryDraft, signal());
  await api.submitAuthorization(recoveryDraft, signal());
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(bodies).toEqual([bodies[0], bodies[0], bodies[0]]);
});

it("não interpreta 404 inicial como prova de que nenhum intake foi admitido", async () => {
  const bodies: string[] = [];
  const api = recoveryService(vi.fn(async (_input, init) => {
    bodies.push(String(init?.body));
    return bodies.length === 1 ? json({ code: "resource_unavailable" }, 404)
      : admitted(JSON.parse(bodies[0]).command_id);
  }));
  await api.submitAuthorization(recoveryDraft, signal());
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(bodies).toEqual([bodies[0], bodies[0]]);
});

it("descobre ponteiros e reautoriza cada recibo antes de apresentar o acompanhamento", async () => {
  const firstCommand = ref("command_1");
  const secondCommand = ref("command_2");
  const firstIntake = ref("intake_1");
  const secondIntake = ref("intake_2");
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input);
    if (path === "/api/v1/portal/intake-recovery") return json({
      schema_version: 1,
      scope: "actor_admissions",
      items: [
        { command_id: firstCommand, intake_ref: firstIntake },
        { command_id: secondCommand, intake_ref: secondIntake },
      ],
      next_cursor: ref("cursor"),
    });
    if (path.endsWith(firstIntake)) return json({
      schema_version: 1, intake_ref: firstIntake, command_id: firstCommand, revision: "4",
      disposition: "started", case_ref: ref("case"), start_receipt_ref: ref("receipt"),
    });
    if (path.endsWith(secondIntake)) return json({
      schema_version: 1, intake_ref: secondIntake, command_id: secondCommand, revision: "5",
      disposition: "rejected", case_ref: null, start_receipt_ref: null,
    });
    throw new Error(`unexpected path ${path}`);
  });
  const result = await recoveryService(fetcher).discoverAuthorizationIntakes(signal());
  expect(result).toMatchObject({
    kind: "success",
    value: {
      nextCursor: ref("cursor"),
      items: [
        { commandId: firstCommand, progress: { state: "started", intakeRef: firstIntake } },
        { commandId: secondCommand, progress: { state: "rejected", intakeRef: secondIntake } },
      ],
    },
  });
  expect(fetcher).toHaveBeenCalledTimes(3);
});

it("falha fechado quando recibo não corresponde ao comando descoberto", async () => {
  const commandId = ref("command");
  const intakeRef = ref("intake");
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json({
      schema_version: 1, scope: "actor_admissions",
      items: [{ command_id: commandId, intake_ref: intakeRef }], next_cursor: null,
    }))
    .mockResolvedValueOnce(json({
      schema_version: 1, intake_ref: intakeRef, command_id: ref("other_command"), revision: "1",
      disposition: "admitted", case_ref: null, start_receipt_ref: null,
    }));
  expect(await recoveryService(fetcher).discoverAuthorizationIntakes(signal())).toEqual({
    kind: "failure", failure: "invalid-response",
  });
});

it("mantém comando e payload exatos após not_observed e só então permite retry explícito", async () => {
  const bodies: string[] = [];
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/intake-recovery/commands/")) {
      const commandId = path.split("/").at(-1)!;
      return json({ schema_version: 1, command_id: commandId,
        observation: "not_observed", intake_ref: null });
    }
    bodies.push(String(init?.body));
    return bodies.length === 1
      ? json({ code: "dependency_unavailable" }, 503)
      : admitted(JSON.parse(bodies[0]).command_id);
  });
  const api = recoveryService(fetcher);
  expect(await api.submitAuthorization(recoveryDraft, signal())).toEqual({
    kind: "failure", failure: "outcome-unknown",
  });
  expect(await api.observePendingAuthorization(signal())).toEqual({
    kind: "not-observed", commandId: JSON.parse(bodies[0]).command_id,
  });
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(bodies).toEqual([bodies[0], bodies[0]]);
});

it("serializa lookup do comando vivo contra retry concorrente", async () => {
  const bodies: string[] = [];
  let release!: (response: Response) => void;
  const observation = new Promise<Response>((resolve) => { release = resolve; });
  let commandId = "";
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/intake-recovery/commands/")) return observation;
    bodies.push(String(init?.body));
    commandId = JSON.parse(bodies[0]).command_id;
    return bodies.length === 1
      ? json({ code: "dependency_unavailable" }, 503)
      : admitted(commandId);
  });
  const api = recoveryService(fetcher);
  await api.submitAuthorization(recoveryDraft, signal());
  const lookup = api.observePendingAuthorization(signal());
  expect(await api.submitAuthorization(recoveryDraft, signal())).toEqual({
    kind: "failure", failure: "outcome-unknown",
  });
  expect(bodies).toHaveLength(1);
  release(json({ schema_version: 1, command_id: commandId,
    observation: "not_observed", intake_ref: null }));
  expect((await lookup).kind).toBe("not-observed");
  expect((await api.submitAuthorization(recoveryDraft, signal())).kind).toBe("success");
  expect(bodies).toEqual([bodies[0], bodies[0]]);
});

it("usa lookup do comando vivo, correlaciona o recibo e libera novo comando depois da prova", async () => {
  const bodies: string[] = [];
  let originalCommand = "";
  const intakeRef = ref("intake");
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input);
    if (path.includes("/intake-recovery/commands/")) return json({
      schema_version: 1, command_id: originalCommand,
      observation: "observed", intake_ref: intakeRef,
    });
    if (path === `/api/v1/portal/intakes/${intakeRef}`) return json({
      schema_version: 1, intake_ref: intakeRef, command_id: originalCommand, revision: "1",
      disposition: "reconciling", case_ref: null, start_receipt_ref: null,
    });
    bodies.push(String(init?.body));
    const commandId = JSON.parse(bodies.at(-1)!).command_id;
    if (bodies.length === 1) {
      originalCommand = commandId;
      return json({ code: "dependency_unavailable" }, 503);
    }
    return admitted(commandId);
  });
  const api = recoveryService(fetcher);
  await api.submitAuthorization(recoveryDraft, signal());
  expect(await api.observePendingAuthorization(signal())).toMatchObject({
    kind: "success", progress: { state: "reconciling", intakeRef },
  });
  const changed = { ...recoveryDraft, guideRef: ref("new_guide") };
  expect((await api.submitAuthorization(changed, signal())).kind).toBe("success");
  expect(JSON.parse(bodies[1]).command_id).not.toBe(originalCommand);
});
