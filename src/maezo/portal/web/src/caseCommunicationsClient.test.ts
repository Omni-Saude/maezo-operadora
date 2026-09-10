import { expect, it, vi } from "vitest";

import {
  createCaseCommunicationsClient,
  type CommunicationPage,
  type CommunicationSubmission,
  type CommunicationsFetch,
  type HistoryPage,
} from "./caseCommunicationsClient";

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

function communicationPage(overrides: Partial<CommunicationPage> = {}): CommunicationPage {
  return {
    schema_version: "portal-communications.v1",
    case_ref: ref("case"),
    items: [{
      communication_ref: ref("communication"),
      sender_kind: "provider",
      authored_at: observed,
      inbox_available_at: observed,
      delivery_state: "inbox_available",
      body_ref: ref("body"),
    }],
    next_cursor: null,
    observed_at: observed,
    valid_until: future,
    ...overrides,
  };
}

function historyPage(overrides: Partial<HistoryPage> = {}): HistoryPage {
  return {
    schema_version: "portal-history.v1",
    history_scope: "portal_events",
    case_ref: ref("case"),
    items: [{
      event_ref: ref("event"),
      sequence: "1",
      occurred_at: observed,
      kind: "communication_available",
      communication_ref: ref("communication"),
      command_ref: null,
      receipt_ref: null,
    }],
    next_cursor: null,
    observed_at: observed,
    valid_until: future,
    ...overrides,
  };
}

function client(fetcher: CommunicationsFetch) {
  return createCaseCommunicationsClient({ csrfToken: "csrf-current", fetcher });
}

it("consulta caixa e histórico com credenciais atuais e cursor opaco", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json(communicationPage({ next_cursor: ref("cursor") })))
    .mockResolvedValueOnce(json(historyPage()));
  const api = client(fetcher);
  expect((await api.listCommunications(ref("case"), signal(), ref("cursor"), 10)).kind).toBe("success");
  expect((await api.listHistory(ref("case"), signal())).kind).toBe("success");
  expect(fetcher).toHaveBeenNthCalledWith(
    1,
    `/api/v1/portal/cases/${ref("case")}/communications?cursor=${ref("cursor")}&limit=10`,
    expect.objectContaining({
      method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
      headers: { Accept: "application/json" },
    }),
  );
  expect(JSON.stringify(fetcher.mock.calls[0][1])).not.toContain("csrf-current");
});

it("recusa página expirada, caso divergente e campos estruturais ausentes", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json(communicationPage({ valid_until: "2020-01-01T00:00:00.000000Z" })))
    .mockResolvedValueOnce(json(historyPage({ case_ref: ref("other_case") })))
    .mockResolvedValueOnce(json(communicationPage({
      items: [{ communication_ref: ref("communication"), sender_kind: null }],
    })));
  const api = client(fetcher);
  for (const operation of [
    api.listCommunications(ref("case"), signal()),
    api.listHistory(ref("case"), signal()),
    api.listCommunications(ref("case"), signal()),
  ]) expect(await operation).toEqual({ kind: "failure", failure: "invalid-response" });
});

it("valida ordem e referência condicional do histórico limitado", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json(historyPage({
      items: [
        { event_ref: ref("event_2"), sequence: "2", occurred_at: observed, kind: "communication_available", communication_ref: ref("communication"), command_ref: null, receipt_ref: null },
        { event_ref: ref("event_1"), sequence: "1", occurred_at: observed, kind: "command_receipt_indexed", communication_ref: null, command_ref: ref("command"), receipt_ref: ref("receipt") },
      ],
    })))
    .mockResolvedValueOnce(json(historyPage({
      items: [{ event_ref: ref("event"), sequence: "1", occurred_at: observed, kind: "command_receipt_indexed", communication_ref: ref("communication"), command_ref: ref("command"), receipt_ref: ref("receipt") }],
    })));
  const api = client(fetcher);
  expect(await api.listHistory(ref("case"), signal())).toEqual({ kind: "failure", failure: "invalid-response" });
  expect(await api.listHistory(ref("case"), signal())).toEqual({ kind: "failure", failure: "invalid-response" });
});

it("publica somente referências preservadas e envia CSRF apenas no cabeçalho", async () => {
  const submission: CommunicationSubmission = {
    command_id: ref("command"), body_ref: ref("body"), recipient_set_ref: ref("recipients"),
  };
  const fetcher = vi.fn().mockResolvedValue(json({
    schema_version: "portal-communication-receipt.v1",
    communication_ref: ref("communication"),
    command_id: submission.command_id,
    disposition: "inbox_available",
  }));
  const result = await client(fetcher).publishCommunication(ref("case"), submission, signal());
  expect(result).toMatchObject({ kind: "success", value: { disposition: "inbox_available" } });
  const [, options] = fetcher.mock.calls[0];
  expect(options).toMatchObject({
    method: "POST", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: { Accept: "application/json", "Content-Type": "application/json", "X-CSRF-Token": "csrf-current" },
  });
  expect(JSON.parse(String(options.body))).toEqual(submission);
  expect(String(options.body)).not.toMatch(/body\s*:|message|subject|tenant|principal/i);
});

it("trata sucesso POST malformado como resultado desconhecido", async () => {
  const fetcher = vi.fn().mockResolvedValue(json({
    schema_version: "portal-communication-receipt.v1",
    communication_ref: ref("communication"),
    command_id: ref("other_command"),
    disposition: "inbox_available",
  }));
  expect(await client(fetcher).publishCommunication(ref("case"), {
    command_id: ref("command"), body_ref: ref("body"), recipient_set_ref: ref("recipients"),
  }, signal())).toEqual({ kind: "failure", failure: "outcome-unknown" });
});

it("mapeia somente o erro fechado compatível com o status", async () => {
  const fetcher = vi.fn()
    .mockResolvedValueOnce(json({ code: "operation_forbidden" }, 403))
    .mockResolvedValueOnce(json({ code: "dependency_unavailable" }, 403));
  const api = client(fetcher);
  expect(await api.listHistory(ref("case"), signal())).toEqual({ kind: "failure", failure: "operation_forbidden" });
  expect(await api.listHistory(ref("case"), signal())).toEqual({ kind: "failure", failure: "invalid-response" });
});

it("rejeita cursor e limite antes de criar URL", async () => {
  const fetcher = vi.fn();
  const api = client(fetcher);
  expect(await api.listCommunications(ref("case"), signal(), "short", 25)).toEqual({ kind: "failure", failure: "invalid_request" });
  expect(await api.listHistory(ref("case"), signal(), undefined, 101)).toEqual({ kind: "failure", failure: "invalid_request" });
  expect(fetcher).not.toHaveBeenCalled();
});

it("propaga aborto sem convertê-lo em indisponibilidade", async () => {
  const controller = new AbortController();
  controller.abort();
  const error = new DOMException("aborted", "AbortError");
  const api = client(vi.fn().mockRejectedValue(error));
  await expect(api.listCommunications(ref("case"), controller.signal)).rejects.toBe(error);
});
