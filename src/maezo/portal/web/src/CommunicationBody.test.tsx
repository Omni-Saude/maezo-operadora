import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { CommunicationBody } from "./CommunicationBody";
import { CaseCommunicationsPanel } from "./CaseCommunicationsPanel";
import { createPhiCommunicationClient } from "./phiCommunicationClient";
import type { CaseCommunicationsClient } from "./caseCommunicationsClient";

const caseRef = "case_abcdefghijklmnop";
const communicationRef = "comm_abcdefghijklmnop";
const observed_at = "2026-09-10T12:00:00.000000Z";
const valid_until = "2026-09-10T12:00:10.000000Z";
const body = "<script>PRIVATE_CANARY</script>\nplain text";
const content = { communication_ref: communicationRef, body, observed_at, valid_until };
const response = (value: unknown, status = 200) => new Response(JSON.stringify(value), { status, headers: { "Content-Type": "application/json" } });
function clock() { vi.useFakeTimers(); vi.setSystemTime(new Date(observed_at)); }
async function click(name: string) { await act(async () => { fireEvent.click(screen.getByRole("button", { name })); }); }
afterEach(() => { vi.useRealTimers(); vi.unstubAllGlobals(); });

it("reads through actual PHI client, renders text only and closes without writing", async () => {
  clock();
  const request = vi.fn<typeof fetch>().mockResolvedValue(response(content));
  const client = createPhiCommunicationClient(request);
  const { container } = render(<CommunicationBody caseRef={caseRef} communicationRef={communicationRef} pageValidUntil={valid_until} client={client} onFailure={vi.fn()} />);
  await click("Abrir conteúdo");
  expect(container.textContent).toContain(body);
  expect(container.querySelector("script")).toBeNull();
  expect(request).toHaveBeenCalledWith(`/api/v1/phi/cases/${caseRef}/communications/${communicationRef}/content`, expect.objectContaining({ method: "GET", cache: "no-store", credentials: "same-origin", redirect: "error" }));
  await click("Fechar conteúdo");
  expect(container.textContent).not.toContain("PRIVATE_CANARY");
  expect(request).toHaveBeenCalledTimes(1);
});

it.each(["close", "context", "expiry"])("fences delayed response after %s and aborts the owned request", async (action) => {
  clock();
  let resolve!: (value: Response) => void;
  const request = vi.fn<typeof fetch>(() => new Promise((done) => { resolve = done; }));
  const client = createPhiCommunicationClient(request);
  const props = { caseRef, communicationRef, pageValidUntil: valid_until, client, onFailure: vi.fn() };
  const view = render(<CommunicationBody {...props} />);
  await click("Abrir conteúdo");
  if (action === "close") await click("Fechar conteúdo");
  if (action === "context") view.rerender(<CommunicationBody {...props} communicationRef="other_abcdefghijklmnop" />);
  if (action === "expiry") await act(async () => { vi.advanceTimersByTime(10_000); });
  expect(request.mock.calls[0][1]?.signal?.aborted).toBe(true);
  await act(async () => { resolve(response(content)); });
  expect(view.container.textContent).not.toContain("PRIVATE_CANARY");
});

it("retains the earlier inbox ceiling after a later PHI deadline", async () => {
  clock();
  const client = createPhiCommunicationClient(vi.fn<typeof fetch>().mockResolvedValue(response({ ...content, valid_until: "2026-09-10T12:01:00.000000Z" })));
  const view = render(<CommunicationBody caseRef={caseRef} communicationRef={communicationRef} pageValidUntil={valid_until} client={client} onFailure={vi.fn()} />);
  await click("Abrir conteúdo");
  expect(view.container.textContent).toContain("PRIVATE_CANARY");
  await act(async () => { vi.advanceTimersByTime(10_000); });
  expect(view.container.textContent).not.toContain("PRIVATE_CANARY");
});

it.each([
  { ...content, extra: "forbidden" },
  { ...content, communication_ref: "other_abcdefghijklmnop" },
  { ...content, valid_until: observed_at },
  { ...content, observed_at: "2026-09-10T12:00:00Z" },
])("refuses invalid generated wire or resource/currentness binding", async (value) => {
  clock();
  const client = createPhiCommunicationClient(vi.fn<typeof fetch>().mockResolvedValue(response(value)));
  expect(await client.read(caseRef, communicationRef, new AbortController().signal)).toEqual({ kind: "failure", failure: "invalid-response" });
});

it("actual inbox action reads the PHI route and retires a rejected session", async () => {
  clock();
  const general: CaseCommunicationsClient = {
    listCommunications: vi.fn().mockResolvedValue({ kind: "success", value: {
      schema_version: "portal-communications.v1", case_ref: caseRef, observed_at, valid_until,
      next_cursor: null, items: [{ communication_ref: communicationRef, body_ref: "body_abcdefghijklmnop", sender_kind: "system", authored_at: observed_at, inbox_available_at: observed_at, delivery_state: "inbox_available" }],
    } }),
    listHistory: vi.fn().mockResolvedValue({ kind: "success", value: {
      schema_version: "portal-history.v1", history_scope: "portal_events", case_ref: caseRef,
      observed_at, valid_until, next_cursor: null, items: [],
    } }),
    publishCommunication: vi.fn(),
  };
  const request = vi.fn<typeof fetch>().mockResolvedValue(response({ code: "authentication_unavailable" }, 401));
  vi.stubGlobal("fetch", request);
  const retired = vi.fn();
  await act(async () => { render(<CaseCommunicationsPanel caseRef={caseRef} client={general} onSessionUnavailable={retired} />); });
  await click("Abrir conteúdo");
  expect(request).toHaveBeenCalledTimes(1);
  expect(retired).toHaveBeenCalledTimes(1);
  expect(screen.queryByRole("button", { name: "Abrir conteúdo" })).toBeNull();
});

it("clears a ready body at its shorter content deadline", async () => {
  clock();
  const client = createPhiCommunicationClient(vi.fn<typeof fetch>().mockResolvedValue(response({ ...content, valid_until: "2026-09-10T12:00:02.000000Z" })));
  const view = render(<CommunicationBody caseRef={caseRef} communicationRef={communicationRef} pageValidUntil={valid_until} client={client} onFailure={vi.fn()} />);
  await click("Abrir conteúdo");
  await act(async () => { vi.advanceTimersByTime(2_000); });
  expect(view.container.textContent).not.toContain("PRIVATE_CANARY");
});

it("aborts a previous session client and refuses its late body", async () => {
  clock();
  let resolve!: (value: Response) => void;
  const request = vi.fn<typeof fetch>(() => new Promise((done) => { resolve = done; }));
  const client = createPhiCommunicationClient(request);
  const props = { caseRef, communicationRef, pageValidUntil: valid_until, onFailure: vi.fn() };
  const view = render(<CommunicationBody {...props} client={client} />);
  await click("Abrir conteúdo");
  view.rerender(<CommunicationBody {...props} client={createPhiCommunicationClient(vi.fn())} />);
  expect(request.mock.calls[0][1]?.signal?.aborted).toBe(true);
  await act(async () => { resolve(response(content)); });
  expect(view.container.textContent).not.toContain("PRIVATE_CANARY");
});
