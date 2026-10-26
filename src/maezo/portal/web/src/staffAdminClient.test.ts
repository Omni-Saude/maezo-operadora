import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { createStaffAdminClient, validateStaffAdminRoster } from "./staffAdminClient";

// D-40: the administration client exposes exactly ONE read method, GET-only, no body, no
// CSRF — and refuses every answer that is not the closed reviewed-fact wire.

const generatedAt = "2026-10-25T12:00:00Z";

function membership() {
  return { membership_ref: "review-1", roles: ["staff"], groups: ["medico-auditor"] } as const;
}

function vinculo() {
  return {
    principal_ref: "human-internal-1",
    membership_revision: 3,
    memberships: [membership()],
    subject_bindings: [],
    reviewed_until: "2026-10-26T12:00:00Z",
    revoked: false,
    state: "active",
  } as const;
}

function roster() {
  return { schema_version: 1, generated_at: generatedAt, count: 1, vinculos: [vinculo()] } as const;
}

function response(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

beforeEach(() => vi.stubGlobal("fetch", vi.fn()));
afterEach(() => vi.unstubAllGlobals());

it("consulta somente leitura o roster na única rota GET montada", async () => {
  const value = roster();
  vi.mocked(fetch).mockResolvedValue(response(value));
  const result = await createStaffAdminClient().readVinculos(new AbortController().signal);
  expect(result).toEqual({ kind: "success", value });
  expect(fetch).toHaveBeenCalledWith(
    "/api/v1/portal/administration/memberships",
    expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json" },
    }),
  );
  // Prova de leitura pura: o init carrega só o par Accept; nenhum corpo, nenhum CSRF.
  const init: RequestInit | undefined = vi.mocked(fetch).mock.calls[0]?.[1];
  expect(init).toBeDefined();
  expect("body" in (init as RequestInit)).toBe(false);
  expect(JSON.stringify(init)).not.toMatch(/csrf|grant|revoke|extend|review/i);
});

it("expõe um único método de leitura — nenhuma mutação existe no client", () => {
  expect(Object.keys(createStaffAdminClient()).sort()).toEqual(["readVinculos"]);
});

it("mapeia os erros fechados da rota", async () => {
  const client = createStaffAdminClient();
  const signal = new AbortController().signal;
  vi.mocked(fetch)
    .mockResolvedValueOnce(response({ code: "operation_forbidden" }, 403))
    .mockResolvedValueOnce(response({ code: "admin_read_unavailable" }, 503))
    .mockResolvedValueOnce(response({ code: "invalid_request" }, 400))
    .mockResolvedValueOnce(response({ code: "authentication_unavailable" }, 401));
  expect(await client.readVinculos(signal)).toEqual({ kind: "failure", failure: "operation_forbidden" });
  expect(await client.readVinculos(signal)).toEqual({ kind: "failure", failure: "admin_read_unavailable" });
  expect(await client.readVinculos(signal)).toEqual({ kind: "failure", failure: "invalid_request" });
  expect(await client.readVinculos(signal)).toEqual({
    kind: "failure",
    failure: "authentication_unavailable",
  });
});

it("recusa código fora da linha de status, corpo estranho e resposta sem JSON", async () => {
  const client = createStaffAdminClient();
  const signal = new AbortController().signal;
  vi.mocked(fetch)
    // Um 403 cujo corpo nomeia um código de OUTRO plano é recusado, nunca traduzido.
    .mockResolvedValueOnce(response({ code: "dependency_unavailable" }, 403))
    .mockResolvedValueOnce(response({ surpresa: true }, 503))
    .mockResolvedValueOnce(new Response("not json", { status: 200 }));
  expect(await client.readVinculos(signal)).toEqual({ kind: "failure", failure: "invalid-response" });
  expect(await client.readVinculos(signal)).toEqual({ kind: "failure", failure: "invalid-response" });
  expect(await client.readVinculos(signal)).toEqual({ kind: "failure", failure: "invalid-response" });
});

it("valida o wire fechado: vínculo com os sete fatos revisados, nada mais", () => {
  const value = roster();
  expect(validateStaffAdminRoster(value)).toEqual(value);
  // Chave fora do contrato, contagem mentirosa, estado estranho, revisão negativa,
  // roster fora da ordem canônica e vínculo duplicado: tudo recusado.
  expect(validateStaffAdminRoster({ ...value, schema_version: 2 })).toBeNull();
  expect(validateStaffAdminRoster({ ...value, count: 2 })).toBeNull();
  expect(
    validateStaffAdminRoster({
      schema_version: 1,
      generated_at: generatedAt,
      count: 1,
      vinculos: [{ ...vinculo(), state: "pending" }],
    }),
  ).toBeNull();
  expect(
    validateStaffAdminRoster({
      schema_version: 1,
      generated_at: generatedAt,
      count: 1,
      vinculos: [{ ...vinculo(), membership_revision: -1 }],
    }),
  ).toBeNull();
  const other = {
    ...vinculo(),
    principal_ref: "human-internal-2",
    subject_bindings: [{ kind: "vendor", resource_ref: "resource-vendor-1" }],
  };
  expect(
    validateStaffAdminRoster({
      schema_version: 1,
      generated_at: generatedAt,
      count: 2,
      vinculos: [other, vinculo()],
    }),
  ).toBeNull();
  expect(
    validateStaffAdminRoster({
      schema_version: 1,
      generated_at: generatedAt,
      count: 2,
      vinculos: [vinculo(), vinculo()],
    }),
  ).toBeNull();
  // A coordenada de identidade nunca é parte do wire: um `issuer` no payload é recusa.
  expect(
    validateStaffAdminRoster({ ...value, issuer: "https://cognito-idp.example/pool" }),
  ).toBeNull();
});
