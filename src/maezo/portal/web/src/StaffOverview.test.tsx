import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { expect, it, vi } from "vitest";

const queueRender = vi.hoisted(() => vi.fn());

vi.mock("./EmployeeQueues", () => ({
  EmployeeQueues: (props: {
    csrfToken: string;
    sessionBinding: string;
    onSessionUnavailable: () => void;
  }) => {
    queueRender(props);
    return (
      <section aria-label="Fila autorizada">
        <p role="status">Atualizada em 10/09/2026 09:00:00</p>
        <p>Prazo informado pelo engine</p>
        <button type="button" onClick={props.onSessionUnavailable}>Simular sessão indisponível</button>
      </section>
    );
  },
}));

import { StaffOverview } from "./StaffOverview";

it("reutiliza uma única fila autorizada e descreve o limite da página sem fabricar total", async () => {
  const onSessionUnavailable = vi.fn();

  render(
    <StaffOverview
      csrfToken="csrf-protegido"
      sessionBinding="sessão-atual"
      onSessionUnavailable={onSessionUnavailable}
    />,
  );

  expect(screen.getByRole("heading", { name: "Visão geral do trabalho" })).toBeInTheDocument();
  expect(screen.getByText(/página atual de Meu trabalho ou das Filas da equipe/)).toBeInTheDocument();
  expect(screen.getByText(/não é uma contagem total/)).toBeInTheDocument();
  expect(screen.getByText(/prazo informado pelo engine e a validade da projeção/)).toBeInTheDocument();
  expect(screen.getByRole("region", { name: "Fila autorizada" })).toBeInTheDocument();
  expect(queueRender).toHaveBeenCalledOnce();
  expect(queueRender.mock.calls[0][0]).toMatchObject({
    csrfToken: "csrf-protegido",
    sessionBinding: "sessão-atual",
    onSessionUnavailable,
  });
  expect(screen.queryByText("csrf-protegido")).not.toBeInTheDocument();
  expect(screen.queryByText("sessão-atual")).not.toBeInTheDocument();

  await userEvent.click(screen.getByRole("button", { name: "Simular sessão indisponível" }));
  expect(onSessionUnavailable).toHaveBeenCalledOnce();
});
