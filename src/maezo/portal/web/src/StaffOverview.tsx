import { EmployeeQueues } from "./EmployeeQueues";

export function StaffOverview({
  csrfToken,
  sessionBinding,
  onSessionUnavailable,
  queuesNotice,
}: {
  csrfToken: string;
  sessionBinding: string;
  onSessionUnavailable: () => void;
  /** Rendered instead of the queues when this environment has not enabled them. */
  queuesNotice?: React.ReactNode;
}) {
  return (
    <section aria-labelledby="staff-overview-heading">
      <div className="portal-guidance">
        <p className="eyebrow">Trabalho autorizado</p>
        <h2 id="staff-overview-heading">Visão geral do trabalho</h2>
        <p>
          Consulte a página atual de Meu trabalho ou das Filas da equipe. Esta consulta não é uma
          contagem total do trabalho da operadora.
        </p>
        <p>
          Cada tarefa mostra a responsabilidade, o prazo informado pelo engine e a validade da
          projeção. Abra a tarefa para consultar os próximos passos permitidos na sessão atual.
        </p>
      </div>
      {queuesNotice ?? (
        <EmployeeQueues
          csrfToken={csrfToken}
          sessionBinding={sessionBinding}
          onSessionUnavailable={onSessionUnavailable}
        />
      )}
    </section>
  );
}
