import { act, fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

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
    .mockResolvedValueOnce(jsonResponse(session("beneficiary")));
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

it("revalida ao expirar e remove a autoridade da tela antes da resposta", async () => {
  vi.useFakeTimers();
  const expiresAt = new Date(Date.now() + 1_000).toISOString();
  vi.mocked(fetch)
    .mockResolvedValueOnce(jsonResponse(session("provider", expiresAt)))
    .mockResolvedValueOnce(new Response(null, { status: 401 }));
  render(<App />);
  await act(async () => Promise.resolve());
  expect(screen.getByRole("heading", { name: "Área do prestador" })).toBeInTheDocument();
  await act(async () => vi.advanceTimersByTimeAsync(1_001));
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
    .mockResolvedValueOnce(new Response(null, { status: 503 }))
    .mockResolvedValueOnce(new Response(null, { status: 204 }));
  render(<App />);
  await userEvent.click(await screen.findByRole("button", { name: "Sair com segurança" }));
  expect(await screen.findByRole("heading", { name: "Não foi possível confirmar a saída" })).toBeInTheDocument();
  expect(screen.queryByRole("heading", { name: "Área do beneficiário" })).not.toBeInTheDocument();
  expect(screen.queryByText("Sessão encerrada")).not.toBeInTheDocument();
  Object.defineProperty(document, "visibilityState", { configurable: true, value: "visible" });
  fireEvent(document, new Event("visibilitychange"));
  expect(fetch).toHaveBeenCalledTimes(2);
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
