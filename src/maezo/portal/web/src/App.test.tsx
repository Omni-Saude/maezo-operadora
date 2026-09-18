import { act, fireEvent, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./EmployeeQueues", () => ({
  EmployeeQueues: ({ initialQueue = "mine" }: { initialQueue?: "mine" | "team" }) => (
    <section aria-label="Filas de colaboradores">{initialQueue}</section>
  ),
}));

import { App } from "./App";

type Audience = "staff" | "beneficiary" | "provider";

function session(audience: Audience = "staff", expiresAt?: string) {
  return {
    schema_version: 1,
    principal_ref: "opaque-principal",
    audience,
    roles: ["authorized-role"],
    expires_at: expiresAt ?? new Date(Date.now() + 60_000).toISOString(),
    csrf_token: "csrf-secret",
  } as const;
}

function jsonResponse(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

function emptyCasePage(audience: Exclude<Audience, "staff">) {
  return {
    schema: "portal-external-case-page.v1",
    audience,
    items: [],
    next_cursor: null,
    freshness: {
      observed_at: "2099-09-10T12:00:00.000000Z",
      valid_until: "2099-09-10T13:00:00.000000Z",
    },
  } as const;
}

const selectedCaseRef = "case_selected_abcdefghijklmnop";
const observed = "2099-09-10T12:00:00.000000Z";
const future = "2099-09-10T13:00:00.000000Z";

function selectedCasePage() {
  return {
    ...emptyCasePage("beneficiary"),
    items: [{
      case_ref: selectedCaseRef,
      kind: "authorization",
      state: "active",
      record_revision: "1",
      state_observed_at: observed,
    }],
  } as const;
}

function caseDetail() {
  return {
    schema: "portal-external-case-detail.v1",
    case: selectedCasePage().items[0],
    allowed_actions: [],
    freshness: { observed_at: observed, valid_until: future },
  } as const;
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("mostra carregamento enquanto a validação real está pendente", async () => {
  const pending = deferred<Response>();
  vi.mocked(fetch).mockReturnValue(pending.promise);
  render(<App />);
  expect(screen.getByRole("heading", { name: "Validando sua sessão" })).toBeInTheDocument();
  pending.resolve(jsonResponse(session()));
  expect(await screen.findByRole("heading", { name: "Área de colaboradores" })).toBeInTheDocument();
});

describe.each([
  ["staff", "Área de colaboradores"],
  ["beneficiary", "Área do beneficiário"],
  ["provider", "Área do prestador"],
] as const)("sessão %s", (audience, heading) => {
  it("usa somente o público determinado pelo servidor", async () => {
    vi.mocked(fetch).mockResolvedValue(jsonResponse(session(audience)));
    render(<App />);
    expect(await screen.findByRole("heading", { name: heading })).toBeInTheDocument();
    expect(screen.queryByText("opaque-principal")).not.toBeInTheDocument();
    expect(screen.queryByText("csrf-secret")).not.toBeInTheDocument();
    expect(screen.queryByText("authorized-role")).not.toBeInTheDocument();
  });
});

it("oferece sete áreas e abre a fila autorizada na visão geral e nas áreas de trabalho", async () => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse(session("staff")));
  const view = render(<App />);
  expect(await screen.findByRole("heading", { name: "Área de colaboradores" })).toBeInTheDocument();
  expect(screen.getAllByRole("tab")).toHaveLength(7);
  expect(screen.getByRole("heading", { name: "Visão geral do trabalho" })).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Filas de colaboradores" })).toHaveTextContent("mine");
  await userEvent.click(screen.getByRole("tab", { name: /Meu trabalho/ }));
  expect(screen.getByRole("region", { name: "Filas de colaboradores" })).toHaveTextContent("mine");
  await userEvent.click(screen.getByRole("tab", { name: /Filas da equipe/ }));
  expect(screen.getByRole("region", { name: "Filas de colaboradores" })).toHaveTextContent("team");
  await userEvent.click(screen.getByRole("tab", { name: /^Casos/ }));
  // StaffCaseWorkspace mounts fresh on tab activation and auto-loads its case
  // list (see StaffCaseWorkspace.test.tsx for full coverage of that flow);
  // this smoke assertion only confirms the right area renders with its real
  // accessible heading and search-by-reference affordance.
  expect(screen.getByRole("heading", { name: "Casos de autorização" })).toBeInTheDocument();
  expect(screen.getByLabelText("Abrir pela referência exata")).toHaveValue("");
  expect(fetch).toHaveBeenCalledTimes(2);
  view.unmount();

  vi.mocked(fetch).mockResolvedValue(jsonResponse(session("provider")));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Área do prestador" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Filas de colaboradores" })).not.toBeInTheDocument();
});

it("compõe a consulta externa real sem enviar público ou CSRF na leitura", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(session("beneficiary")))
    .mockResolvedValueOnce(jsonResponse(emptyCasePage("beneficiary")));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Área do beneficiário" })).toBeInTheDocument();
  expect(await screen.findByRole("heading", { name: "Autorizações", level: 2 })).toBeInTheDocument();
  expect(screen.getAllByRole("tab")).toHaveLength(4);
  expect(await within(screen.getByRole("tabpanel")).findByText(
    "Nenhuma solicitação autorizada foi encontrada.",
  )).toBeInTheDocument();
  await userEvent.click(screen.getByRole("tab", { name: "Documentos" }));
  expect(within(screen.getByRole("tabpanel")).getByText(
    "Abra uma solicitação para consultar esta área.",
  )).toBeInTheDocument();
  const [path, options] = vi.mocked(fetch).mock.calls[1];
  expect(path).toBe("/api/v1/portal/cases");
  expect(options).toMatchObject({
    method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: { Accept: "application/json" },
  });
  expect(JSON.stringify([path, options])).not.toMatch(/beneficiary|csrf-secret|opaque-principal/i);
});

it("mantém o formulário do prestador indisponível sem provedor de opções autorizado", async () => {
  vi.mocked(fetch).mockImplementation(async (path) => {
    if (path === "/api/v1/portal/session") return jsonResponse(session("provider"));
    if (path === "/api/v1/portal/cases") return jsonResponse(emptyCasePage("provider"));
    if (path === "/api/v1/portal/intake-recovery") {
      return jsonResponse({ schema_version: 1, scope: "actor_admissions", items: [], next_cursor: null });
    }
    throw new Error("Unexpected request in provider form fixture");
  });
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Área do prestador" })).toBeInTheDocument();
  const unavailable = await screen.findByText("Este recurso não está mais disponível.");
  expect(unavailable.closest('[role="alert"]')).toBeInTheDocument();
  expect(vi.mocked(fetch).mock.calls.map(([path]) => path).sort()).toEqual([
    "/api/v1/portal/cases", "/api/v1/portal/intake-recovery", "/api/v1/portal/session",
  ]);
});

it("trata 401 como sessão ausente e oferece a entrada canônica", async () => {
  vi.mocked(fetch).mockResolvedValue(new Response(null, { status: 401 }));
  render(<App />);
  const link = await screen.findByRole("link", { name: "Entrar no portal" });
  expect(link).toHaveAttribute("href", "/api/v1/portal/auth/login?return_to=/portal/");
  await userEvent.tab();
  expect(link).toHaveFocus();
});

it("distingue 503, nega acesso e permite repetir a validação", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockResolvedValueOnce(jsonResponse(session("beneficiary")))
    .mockResolvedValueOnce(jsonResponse(emptyCasePage("beneficiary")));
  render(<App />);
  expect(await screen.findByText("Uma dependência do portal não respondeu. Nenhum acesso foi concedido.")).toBeInTheDocument();
  await userEvent.click(screen.getByRole("button", { name: "Tentar novamente" }));
  expect(await screen.findByRole("heading", { name: "Área do beneficiário" })).toBeInTheDocument();
});

it.each([
  { ...session(), schema_version: 2 },
  { ...session(), audience: "admin" },
  { ...session(), campo_desconhecido: "não aceito" },
])("falha fechada para schema ou público inesperado %#", async (payload) => {
  vi.mocked(fetch).mockResolvedValue(jsonResponse(payload));
  render(<App />);
  expect(await screen.findByText("O portal recusou uma resposta inesperada. Nenhum acesso foi concedido.")).toBeInTheDocument();
  expect(screen.queryByText("Sessão ativa")).not.toBeInTheDocument();
});

it("invalida a sessão quando a revalidação visível recebe 401", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(session()))
    .mockResolvedValueOnce(new Response(null, { status: 401 }));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Área de colaboradores" })).toBeInTheDocument();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  fireEvent(document, new Event("visibilitychange"));
  expect(await screen.findByRole("heading", { name: "Sua sessão não está ativa" })).toBeInTheDocument();
});

it("remove o workspace antes de aplicar uma mudança de audiência", async () => {
  const changedAudience = deferred<Response>();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(session("staff")))
    .mockReturnValueOnce(changedAudience.promise);
  render(<App />);
  await screen.findByRole("heading", { name: "Área de colaboradores" });
  await userEvent.click(screen.getByRole("tab", { name: /Meu trabalho/ }));
  expect(screen.getByRole("region", { name: "Filas de colaboradores" })).toBeInTheDocument();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  fireEvent(document, new Event("visibilitychange"));
  expect(screen.queryByRole("region", { name: "Filas de colaboradores" })).not.toBeInTheDocument();
  changedAudience.resolve(jsonResponse(session("provider")));
  expect(await screen.findByRole("heading", { name: "Área do prestador" })).toBeInTheDocument();
  expect(screen.queryByRole("region", { name: "Filas de colaboradores" })).not.toBeInTheDocument();
});

it("substitui o serviço externo e descarta a consulta antiga após revalidar a sessão", async () => {
  const oldCases = deferred<Response>();
  let sessionReads = 0;
  let caseReads = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const path = String(input);
    if (path === "/api/v1/portal/session") {
      sessionReads += 1;
      return Promise.resolve(jsonResponse(session(sessionReads === 1 ? "beneficiary" : "provider")));
    }
    if (path === "/api/v1/portal/cases") {
      caseReads += 1;
      return caseReads === 1
        ? oldCases.promise
        : Promise.resolve(jsonResponse(emptyCasePage("provider")));
    }
    throw new Error(`rota inesperada: ${path}`);
  });
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Área do beneficiário" })).toBeInTheDocument();
  await waitFor(() => expect(caseReads).toBe(1));
  const oldCaseCall = vi.mocked(fetch).mock.calls.find(([path]) => path === "/api/v1/portal/cases");
  const oldSignal = oldCaseCall?.[1]?.signal;
  expect(oldSignal).toBeInstanceOf(AbortSignal);

  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  fireEvent(document, new Event("visibilitychange"));
  expect(await screen.findByRole("heading", { name: "Área do prestador" })).toBeInTheDocument();
  expect(oldSignal?.aborted).toBe(true);

  oldCases.resolve(jsonResponse({
    ...emptyCasePage("beneficiary"),
    items: [{
      case_ref: "case_old_abcdefghijklmnop",
      kind: "authorization",
      state: "active",
      record_revision: "1",
      state_observed_at: "2099-09-10T12:00:00.000000Z",
    }],
  }));
  await act(async () => Promise.resolve());
  expect(screen.queryByText(/case_old_/)).not.toBeInTheDocument();
  expect(await within(screen.getByRole("tabpanel")).findByText(
    "Nenhuma solicitação autorizada foi encontrada.",
  )).toBeInTheDocument();
});

it("alcança caixa e histórico do caso e aborta ambas as leituras na revalidação", async () => {
  const pendingCommunications = deferred<Response>();
  const pendingHistory = deferred<Response>();
  let sessionReads = 0;
  let casePageReads = 0;
  vi.mocked(fetch).mockImplementation((input) => {
    const path = String(input);
    if (path === "/api/v1/portal/session") {
      sessionReads += 1;
      return Promise.resolve(jsonResponse(session(sessionReads === 1 ? "beneficiary" : "provider")));
    }
    if (path === "/api/v1/portal/cases") {
      casePageReads += 1;
      return Promise.resolve(jsonResponse(casePageReads === 1
        ? selectedCasePage()
        : emptyCasePage("provider")));
    }
    if (path === `/api/v1/portal/cases/${selectedCaseRef}`) {
      return Promise.resolve(jsonResponse(caseDetail()));
    }
    if (path === `/api/v1/portal/cases/${selectedCaseRef}/documents`) {
      return Promise.resolve(jsonResponse({ case_ref: selectedCaseRef, documents: [] }));
    }
    if (path === `/api/v1/portal/cases/${selectedCaseRef}/document-requests`) {
      return Promise.resolve(jsonResponse({ case_ref: selectedCaseRef, requests: [] }));
    }
    if (path === `/api/v1/portal/cases/${selectedCaseRef}/communications`) {
      return pendingCommunications.promise;
    }
    if (path === `/api/v1/portal/cases/${selectedCaseRef}/history`) {
      return pendingHistory.promise;
    }
    throw new Error(`rota inesperada: ${path}`);
  });

  render(<App />);
  await userEvent.click(await screen.findByRole("tab", { name: "Mensagens" }));
  await userEvent.click(screen.getByRole("button", { name: "Abrir solicitação" }));
  await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) =>
    path === `/api/v1/portal/cases/${selectedCaseRef}/history`)).toBe(true));
  const communicationsCall = vi.mocked(fetch).mock.calls.find(([path]) =>
    path === `/api/v1/portal/cases/${selectedCaseRef}/communications`);
  const historyCall = vi.mocked(fetch).mock.calls.find(([path]) =>
    path === `/api/v1/portal/cases/${selectedCaseRef}/history`);
  expect(communicationsCall?.[1]).toMatchObject({
    method: "GET", credentials: "same-origin", cache: "no-store", redirect: "error",
    headers: { Accept: "application/json" },
  });
  expect(JSON.stringify([communicationsCall, historyCall])).not.toMatch(
    /csrf-secret|opaque-principal|beneficiary/,
  );

  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  fireEvent(document, new Event("visibilitychange"));
  expect(await screen.findByRole("heading", { name: "Área do prestador" })).toBeInTheDocument();
  expect(communicationsCall?.[1]?.signal?.aborted).toBe(true);
  expect(historyCall?.[1]?.signal?.aborted).toBe(true);

  pendingCommunications.resolve(jsonResponse({
    schema_version: "portal-communications.v1",
    case_ref: selectedCaseRef,
    items: [{
      communication_ref: "late_communication_abcdefghijklmnop",
      sender_kind: "system",
      authored_at: observed,
      inbox_available_at: observed,
      delivery_state: "inbox_available",
      body_ref: null,
    }],
    next_cursor: null,
    observed_at: observed,
    valid_until: future,
  }));
  pendingHistory.resolve(jsonResponse({
    schema_version: "portal-history.v1",
    history_scope: "portal_events",
    case_ref: selectedCaseRef,
    items: [],
    next_cursor: null,
    observed_at: observed,
    valid_until: future,
  }));
  await act(async () => Promise.all([pendingCommunications.promise, pendingHistory.promise]));
  expect(screen.queryByText(/late_communication/)).not.toBeInTheDocument();
});

it("revalida ao expirar e remove a autoridade da tela antes da resposta", async () => {
  vi.useFakeTimers();
  const expiresAt = new Date(Date.now() + 1_000).toISOString();
  const pendingSession = deferred<Response>();
  let sessionReads = 0;
  vi.mocked(fetch).mockImplementation(async (path) => {
    if (path === "/api/v1/portal/session") {
      sessionReads += 1;
      return sessionReads === 1
        ? jsonResponse(session("provider", expiresAt))
        : pendingSession.promise;
    }
    if (path === "/api/v1/portal/cases") return jsonResponse(emptyCasePage("provider"));
    if (path === "/api/v1/portal/intake-recovery") {
      return jsonResponse({ schema_version: 1, scope: "actor_admissions", items: [], next_cursor: null });
    }
    throw new Error("Unexpected request in session expiry fixture");
  });
  render(<App />);
  await act(async () => Promise.resolve());
  expect(screen.getByRole("heading", { name: "Área do prestador" })).toBeInTheDocument();
  await act(async () => vi.advanceTimersByTimeAsync(1_001));
  expect(sessionReads).toBe(2);
  expect(screen.getByRole("heading", { name: "Revalidando sua sessão" })).toBeInTheDocument();
  expect(screen.queryByText("Sessão ativa")).not.toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Área do prestador" })).not.toBeInTheDocument();
  await act(async () => {
    pendingSession.resolve(new Response(null, { status: 401 }));
    await pendingSession.promise;
  });
  expect(screen.getByRole("heading", { name: "Sua sessão não está ativa" })).toBeInTheDocument();
  expect(screen.queryByText("Sessão ativa")).not.toBeInTheDocument();
});

it("encerra por POST com credenciais same-origin, no-store e CSRF apenas no header", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(session()))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  render(<App />);
  const logout = await screen.findByRole("button", { name: "Sair com segurança" });
  const user = userEvent.setup();
  await user.tab();
  await user.tab();
  expect(logout).toHaveFocus();
  await user.click(logout);
  expect(await screen.findByRole("heading", { name: "Sessão encerrada" })).toBeInTheDocument();
  const [, init] = vi.mocked(fetch).mock.calls[1];
  expect(init).toMatchObject({
    method: "POST",
    credentials: "same-origin",
    cache: "no-store",
    headers: { Accept: "application/json", "X-CSRF-Token": "csrf-secret" },
  });
  expect(JSON.stringify(init)).not.toContain("opaque-principal");
});

it("não anuncia logout no erro, limpa a sessão visível e oferece retry seguro", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(session("beneficiary")))
    .mockResolvedValueOnce(jsonResponse(emptyCasePage("beneficiary")))
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "Sair com segurança" }));
  expect(await screen.findByRole("heading", { name: "Não foi possível confirmar a saída" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Área do beneficiário" })).not.toBeInTheDocument();
  expect(screen.queryByText("Sessão encerrada")).not.toBeInTheDocument();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  fireEvent(document, new Event("visibilitychange"));
  expect(fetch).toHaveBeenCalledTimes(3);
  await userEvent.click(screen.getByRole("button", { name: "Tentar sair novamente" }));
  expect(await screen.findByRole("heading", { name: "Sessão encerrada" })).toBeInTheDocument();
});

it("ignora resposta GET antiga que chega depois de uma invalidação 401", async () => {
  const staleGet = deferred<Response>();
  vi.mocked(fetch).mockImplementation(() => {
    const call = vi.mocked(fetch).mock.calls.length;
    if (call === 1) return Promise.resolve(jsonResponse(session()));
    if (call === 2) return staleGet.promise;
    return Promise.resolve(new Response(null, { status: 401 }));
  });
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Área de colaboradores" })).toBeInTheDocument();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  act(() => {
    document.dispatchEvent(new Event("visibilitychange"));
    document.dispatchEvent(new Event("visibilitychange"));
  });
  expect(await screen.findByRole("heading", { name: "Sua sessão não está ativa" })).toBeInTheDocument();
  staleGet.resolve(jsonResponse(session("staff")));
  await act(async () => Promise.resolve());
  expect(screen.getByRole("heading", { name: "Sua sessão não está ativa" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Área de colaboradores" })).not.toBeInTheDocument();
});

it("não usa storage persistente do navegador", async () => {
  const local = vi.spyOn(Storage.prototype, "setItem");
  const remove = vi.spyOn(Storage.prototype, "removeItem");
  vi.mocked(fetch).mockResolvedValue(jsonResponse(session()));
  render(<App />);
  expect(await screen.findByRole("heading", { name: "Área de colaboradores" })).toBeInTheDocument();
  expect(local).not.toHaveBeenCalled();
  expect(remove).not.toHaveBeenCalled();
});

it("leva o foco pelo carregamento até o destino após retry pelo teclado", async () => {
  const retried = deferred<Response>();
  vi.mocked(fetch)
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockReturnValueOnce(retried.promise);
  render(<App />);
  const retry = await screen.findByRole("button", { name: "Tentar novamente" });
  retry.focus();
  await userEvent.keyboard("{Enter}");
  const loading = screen.getByRole("heading", { name: "Revalidando sua sessão" });
  await waitFor(() => expect(loading).toHaveFocus());
  retried.resolve(jsonResponse(session()));
  const destination = await screen.findByRole("heading", { name: "Área de colaboradores" });
  await waitFor(() => expect(destination).toHaveFocus());
  expect(screen.getByRole("status")).toHaveTextContent(
    "Sessão confirmada. Área de colaboradores.",
  );
});

it("leva o foco ao erro útil quando o retry pelo teclado falha", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockResolvedValueOnce(new Response(null, { status: 503 }));
  render(<App />);
  const retry = await screen.findByRole("button", { name: "Tentar novamente" });
  retry.focus();
  await userEvent.keyboard("{Enter}");
  const destination = await screen.findByRole("heading", {
    name: "Não foi possível validar sua sessão",
  });
  await waitFor(() => expect(destination).toHaveFocus());
  expect(
    screen.getByText("Uma dependência do portal não respondeu. Nenhum acesso foi concedido."),
  ).toBeInTheDocument();
});

it("orienta o foco no logout confirmado e no retry após falha", async () => {
  const firstLogout = deferred<Response>();
  const retryLogout = deferred<Response>();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(session()))
    .mockReturnValueOnce(firstLogout.promise)
    .mockReturnValueOnce(retryLogout.promise);
  render(<App />);
  const logout = await screen.findByRole("button", { name: "Sair com segurança" });
  logout.focus();
  await userEvent.keyboard("{Enter}");
  const loggingOut = screen.getByRole("heading", { name: "Encerrando a sessão" });
  await waitFor(() => expect(loggingOut).toHaveFocus());

  firstLogout.resolve(new Response(null, { status: 503 }));
  const failure = await screen.findByRole("heading", {
    name: "Não foi possível confirmar a saída",
  });
  await waitFor(() => expect(failure).toHaveFocus());
  expect(screen.queryByText("Sessão ativa")).not.toBeInTheDocument();

  const retry = screen.getByRole("button", { name: "Tentar sair novamente" });
  retry.focus();
  await userEvent.keyboard("{Enter}");
  await waitFor(() =>
    expect(screen.getByRole("heading", { name: "Encerrando a sessão" })).toHaveFocus(),
  );
  retryLogout.resolve(new Response(null, { status: 204 }));
  const confirmed = await screen.findByRole("heading", { name: "Sessão encerrada" });
  await waitFor(() => expect(confirmed).toHaveFocus());
});

it("revalidação automática anuncia o estado sem focar o destino", async () => {
  const revalidated = deferred<Response>();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(session()))
    .mockReturnValueOnce(revalidated.promise);
  render(<App />);
  const logout = await screen.findByRole("button", { name: "Sair com segurança" });
  logout.focus();
  Object.defineProperty(document, "visibilityState", {
    configurable: true,
    value: "visible",
  });
  fireEvent(document, new Event("visibilitychange"));
  const loading = screen.getByRole("heading", { name: "Revalidando sua sessão" });
  expect(loading).not.toHaveFocus();
  expect(screen.queryByText("Sessão ativa")).not.toBeInTheDocument();

  revalidated.resolve(jsonResponse(session()));
  const destination = await screen.findByRole("heading", { name: "Área de colaboradores" });
  expect(destination).not.toHaveFocus();
  expect(screen.getByRole("status")).toHaveTextContent(
    "Sessão confirmada. Área de colaboradores.",
  );
});
