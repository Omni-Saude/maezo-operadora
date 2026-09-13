import { expect, it, vi } from "vitest";

import { createIntakeRecoveryClient, type IntakeRecoveryFetch } from "./intakeRecoveryClient";

const ref = (name: string) => `${name}_abcdefghijklmnop`;
const signal = () => new AbortController().signal;

function json(value: unknown, status = 200) {
  return new Response(JSON.stringify(value), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function client(fetcher: IntakeRecoveryFetch) {
  return createIntakeRecoveryClient({ fetcher });
}

it("faz descoberta fresca com credenciais da sessão e cursor opaco", async () => {
  const fetcher = vi.fn().mockResolvedValue(json({
    schema_version: 1,
    scope: "actor_admissions",
    items: [{ command_id: ref("command"), intake_ref: ref("intake") }],
    next_cursor: ref("cursor"),
  }));
  const result = await client(fetcher).discover(signal(), ref("cursor"));
  expect(result.kind).toBe("success");
  expect(fetcher).toHaveBeenCalledWith(
    `/api/v1/portal/intake-recovery?cursor=${ref("cursor")}`,
    expect.objectContaining({
      method: "GET",
      credentials: "same-origin",
      cache: "no-store",
      redirect: "error",
      headers: { Accept: "application/json" },
    }),
  );
  expect(JSON.stringify(fetcher.mock.calls)).not.toMatch(/csrf|audience|principal|tenant/i);
});

it.each([
  ["observed", ref("intake")],
  ["not_observed", null],
] as const)("observa o comando original como %s", async (observation, intakeRef) => {
  const commandId = ref("command");
  const fetcher = vi.fn().mockResolvedValue(json({
    schema_version: 1,
    command_id: commandId,
    observation,
    intake_ref: intakeRef,
  }));
  expect((await client(fetcher).observeCommand(commandId, signal())).kind).toBe("success");
  expect(fetcher.mock.calls[0][0]).toBe(
    `/api/v1/portal/intake-recovery/commands/${commandId}`,
  );
});

it("recusa observação contraditória ou de outro comando", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json({
      schema_version: 1,
      command_id: ref("command"),
      observation: "not_observed",
      intake_ref: ref("intake"),
    }))
    .mockResolvedValueOnce(json({
      schema_version: 1,
      command_id: ref("other_command"),
      observation: "observed",
      intake_ref: ref("intake"),
    }));
  const api = client(fetcher);
  expect(await api.observeCommand(ref("command"), signal())).toEqual({
    kind: "failure", failure: "invalid-response",
  });
  expect(await api.observeCommand(ref("command"), signal())).toEqual({
    kind: "failure", failure: "invalid-response",
  });
});

it("recusa ponteiros duplicados na página", async () => {
  const item = { command_id: ref("command"), intake_ref: ref("intake") };
  const fetcher = vi.fn().mockResolvedValue(json({
    schema_version: 1,
    scope: "actor_admissions",
    items: [item, item],
    next_cursor: null,
  }));
  expect(await client(fetcher).discover(signal())).toEqual({
    kind: "failure", failure: "invalid-response",
  });
});

it("mapeia somente erro fechado compatível com o status", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json({ code: "operation_forbidden" }, 403))
    .mockResolvedValueOnce(json({ code: "dependency_unavailable" }, 403));
  const api = client(fetcher);
  expect(await api.discover(signal())).toEqual({
    kind: "failure", failure: "operation_forbidden",
  });
  expect(await api.discover(signal())).toEqual({
    kind: "failure", failure: "invalid-response",
  });
});

it("recusa comando e cursor inválidos antes da rede", async () => {
  const fetcher = vi.fn();
  const api = client(fetcher);
  expect(await api.observeCommand("short", signal())).toEqual({
    kind: "failure", failure: "invalid_request",
  });
  expect(await api.discover(signal(), "short")).toEqual({
    kind: "failure", failure: "invalid_request",
  });
  expect(fetcher).not.toHaveBeenCalled();
});

it("propaga aborto sem converter a sessão em indisponível", async () => {
  const controller = new AbortController();
  controller.abort();
  const error = new DOMException("aborted", "AbortError");
  const api = client(vi.fn().mockRejectedValue(error));
  await expect(api.discover(controller.signal)).rejects.toBe(error);
});
