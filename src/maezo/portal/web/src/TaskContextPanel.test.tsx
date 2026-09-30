import { render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, expect, it, vi } from "vitest";

import { TaskContextPanel } from "./TaskContextPanel";

function context(overrides: Record<string, unknown> = {}) {
  return {
    schema: "portal-task-context.v1",
    task_id: "task-1",
    etapa: "atendimento",
    motivo_categoria: "red_flag_clinico",
    severidade: "grave",
    prioridade: "P1",
    grupo_atendimento: "plantao-clinico",
    aberto_em: "2026-09-28T21:31:32Z",
    ack_vence_em: "2026-09-28T21:36:33Z",
    resolucao_vence_em: "2026-09-28T22:01:33Z",
    resumo_contexto: "Idoso com dor no peito e falta de ar.",
    observed_at: "2026-09-28T21:40:00Z",
    ...overrides,
  };
}

function json(body: unknown, status = 200) {
  return new Response(JSON.stringify(body), { status, headers: { "Content-Type": "application/json" } });
}

function refusal(code: string) {
  return { schema: "portal-context-error.v1", code };
}

function panel(props: Partial<Parameters<typeof TaskContextPanel>[0]> = {}) {
  return <TaskContextPanel taskId="task-1" sessionBinding="session-a" onSessionUnavailable={vi.fn()} {...props} />;
}

beforeEach(() => {
  vi.setSystemTime(new Date("2026-09-28T21:40:00Z"));
  vi.stubGlobal("fetch", vi.fn());
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

it("mostra motivo, gravidade, prioridade, grupo, prazos e o resumo da Helena", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(json(context()));
  render(panel());

  expect(screen.getByRole("status")).toHaveTextContent("Carregando o contexto do caso");
  expect(await screen.findByRole("heading", { name: "Contexto do caso" })).toBeInTheDocument();
  expect(screen.getByText("P1")).toHaveClass("priority", "priority-p1");
  expect(screen.getByText("Plantão clínico")).toBeInTheDocument();
  expect(screen.getByText("Sinal de alerta clínico")).toBeInTheDocument();
  expect(screen.getByText("Grave")).toBeInTheDocument();
  expect(screen.getByRole("group", { name: /Resumo escrito pela Helena/ })).toHaveTextContent(
    "Idoso com dor no peito e falta de ar.",
  );
});

it("diz quanto falta ou quanto passou de cada prazo, e marca o vencido", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(json(context()));
  render(panel());
  await screen.findByText("Prazo de ciência");

  // 21:36:33 já passou às 21:40:00; 22:01:33 ainda não.
  const ack = screen.getByText("Prazo de ciência").parentElement as HTMLElement;
  expect(within(ack).getByText(/venceu há 3 minutos/)).toBeInTheDocument();
  expect(ack.querySelector("dd")).toHaveClass("due-late");
  const resolution = screen.getByText("Prazo de resolução").parentElement as HTMLElement;
  expect(within(resolution).getByText(/vence em 22 minutos/)).toBeInTheDocument();
  expect(resolution.querySelector("dd")).not.toHaveClass("due-late");
  expect(screen.getByText("Aberto").parentElement).toHaveTextContent(/há 8 minutos/);
});

it("na supervisão não há prazo do grupo e a tela explica por quê", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    json(context({ etapa: "supervisao", ack_vence_em: null, resolucao_vence_em: null })),
  );
  render(panel());

  expect(await screen.findByText("Supervisão — última linha")).toBeInTheDocument();
  expect(screen.getByText(/passou do prazo de resolução do grupo/)).toBeInTheDocument();
  expect(screen.queryByText("Prazo de ciência")).not.toBeInTheDocument();
  expect(screen.queryByText("Prazo de resolução")).not.toBeInTheDocument();
});

it("um fato ausente aparece como ausente, nunca como um valor inventado", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    json(
      context({
        motivo_categoria: null,
        severidade: null,
        prioridade: null,
        grupo_atendimento: null,
        aberto_em: null,
        ack_vence_em: null,
        resolucao_vence_em: null,
        resumo_contexto: null,
      }),
    ),
  );
  render(panel());

  // Motivo e "aberto" chegaram nulos: dois campos dizem que não sabem, nenhum inventa.
  expect(await screen.findAllByText("Não informado", { selector: "dd" })).toHaveLength(2);
  expect(screen.getByText("Não informada")).toBeInTheDocument();
  expect(screen.getByText("A Helena não gerou um resumo para este caso.")).toBeInTheDocument();
  expect(screen.queryByText(/^P\d/)).not.toBeInTheDocument();
});

it("um código sem rótulo aparece como ele mesmo, e não some", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(
    json(context({ motivo_categoria: "motivo_novo", severidade: "critica", grupo_atendimento: "grupo-novo" })),
  );
  render(panel());

  expect(await screen.findByText("motivo_novo")).toBeInTheDocument();
  expect(screen.getByText("critica")).toBeInTheDocument();
  expect(screen.getByText("grupo-novo")).toBeInTheDocument();
});

it("o resumo é texto: marcação HTML digitada nele nunca vira elemento", async () => {
  const hostile = '<img src=x onerror="window.pwned=1"><script>window.pwned=2</script> dor forte';
  vi.mocked(fetch).mockResolvedValueOnce(json(context({ resumo_contexto: hostile })));
  const { container } = render(panel());

  const group = await screen.findByRole("group", { name: /Resumo escrito pela Helena/ });
  expect(group).toHaveTextContent(hostile);
  expect(container.querySelector("img")).toBeNull();
  expect(container.querySelector("script")).toBeNull();
  expect((window as unknown as { pwned?: number }).pwned).toBeUndefined();
});

it("avisa, sem rodeios, que nome, telefone e conversa não estão aqui", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(json(context()));
  render(panel());
  expect(await screen.findByText(/Nome, telefone e a conversa não aparecem aqui/)).toBeInTheDocument();
});

it.each([
  ["invalid_request", 400, /solicitação foi recusada/],
  ["employee_access_required", 403, /não permite ver o contexto/],
  ["resource_unavailable", 404, /não está mais disponível/],
  ["refresh_required", 409, /tarefa mudou/],
  ["context_unavailable", 501, /desligado neste ambiente/],
  ["read_dependency_unavailable", 503, /Não foi possível carregar o contexto/],
])("explica a recusa %s", async (code, status, message) => {
  vi.mocked(fetch).mockResolvedValueOnce(json(refusal(code), status));
  render(panel());
  expect(await screen.findByRole("alert")).toHaveTextContent(message);
});

it("só oferece tentar de novo onde tentar de novo pode ajudar", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(json(refusal("employee_access_required"), 403));
  const { unmount } = render(panel());
  await screen.findByRole("alert");
  expect(screen.queryByRole("button", { name: "Tentar novamente" })).not.toBeInTheDocument();
  unmount();

  vi.mocked(fetch).mockResolvedValueOnce(json(refusal("read_dependency_unavailable"), 503));
  render(panel());
  await screen.findByRole("alert");
  expect(screen.getByRole("button", { name: "Tentar novamente" })).toBeInTheDocument();
});

it("tentar de novo busca outra vez e mostra o contexto quando o motor volta", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(json(refusal("read_dependency_unavailable"), 503))
    .mockResolvedValueOnce(json(context()));
  render(panel());

  await userEvent.click(await screen.findByRole("button", { name: "Tentar novamente" }));

  expect(await screen.findByText("Sinal de alerta clínico")).toBeInTheDocument();
  expect(screen.queryByRole("alert")).not.toBeInTheDocument();
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("uma falha de rede vira mensagem e nunca fica presa em carregando", async () => {
  vi.mocked(fetch).mockRejectedValueOnce(new TypeError("Failed to fetch"));
  render(panel());
  expect(await screen.findByRole("alert")).toHaveTextContent(/Não foi possível carregar o contexto/);
  expect(screen.queryByRole("status")).not.toBeInTheDocument();
});

it("avisa o dono da sessão quando a sessão se perde", async () => {
  const onSessionUnavailable = vi.fn();
  vi.mocked(fetch).mockResolvedValueOnce(json(refusal("session_unavailable"), 401));
  render(panel({ onSessionUnavailable }));

  expect(await screen.findByRole("alert")).toHaveTextContent(/sessão não está mais disponível/);
  expect(onSessionUnavailable).toHaveBeenCalledOnce();
});

it("não busca de novo só porque o pai recriou o callback", async () => {
  vi.mocked(fetch).mockResolvedValue(json(context()));
  const { rerender } = render(panel({ onSessionUnavailable: () => undefined }));
  await screen.findByText("Sinal de alerta clínico");

  rerender(panel({ onSessionUnavailable: () => undefined }));
  rerender(panel({ onSessionUnavailable: () => undefined }));

  expect(fetch).toHaveBeenCalledTimes(1);
});

it("busca de novo ao trocar de tarefa e descarta o contexto da anterior", async () => {
  vi.mocked(fetch)
    .mockResolvedValueOnce(json(context()))
    .mockResolvedValueOnce(json(context({ task_id: "task-2", resumo_contexto: "Outra pessoa, outro caso." })));
  const { rerender } = render(panel());
  expect(await screen.findByText(/Idoso com dor no peito/)).toBeInTheDocument();

  rerender(panel({ taskId: "task-2" }));

  expect(await screen.findByText("Outra pessoa, outro caso.")).toBeInTheDocument();
  expect(screen.queryByText(/Idoso com dor no peito/)).not.toBeInTheDocument();
  expect((vi.mocked(fetch).mock.calls[1] as [string])[0]).toBe("/api/v1/portal/tasks/task-2/context");
});

it("cancela a requisição em andamento ao sair da tela", async () => {
  let signal: AbortSignal | undefined;
  vi.mocked(fetch).mockImplementationOnce((_url, init) => {
    signal = (init as RequestInit).signal ?? undefined;
    return new Promise<Response>(() => undefined);
  });
  const { unmount } = render(panel());
  await waitFor(() => expect(signal).toBeDefined());

  unmount();

  expect(signal?.aborted).toBe(true);
});

it("não deixa nada do caso no armazenamento do navegador", async () => {
  vi.mocked(fetch).mockResolvedValueOnce(json(context()));
  render(panel());
  await screen.findByText(/Idoso com dor no peito/);

  expect(localStorage.length).toBe(0);
  expect(sessionStorage.length).toBe(0);
  expect(document.cookie).toBe("");
});
