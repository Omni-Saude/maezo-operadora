import { useRef } from "react";

import type { PortalAudience } from "./caseExperienceModels";

export type StaffArea =
  | "overview"
  | "my-work"
  | "team-queues"
  | "cases"
  | "documents"
  | "operations"
  | "administration";

export type ExternalArea = "requests" | "documents" | "communications" | "receipts";

const staffAreas: readonly Readonly<{ id: StaffArea; label: string; hint: string }>[] = [
  { id: "overview", label: "Visão geral", hint: "Resumo do trabalho e prazos" },
  { id: "my-work", label: "Meu trabalho", hint: "Tarefas sob sua responsabilidade" },
  { id: "team-queues", label: "Filas da equipe", hint: "Tarefas elegíveis do seu grupo" },
  { id: "cases", label: "Casos", hint: "Fila do seu grupo e detalhe" },
  { id: "documents", label: "Documentos", hint: "Pedidos, verificação e acesso" },
  { id: "operations", label: "Operações", hint: "Comandos, recibos e dependências" },
  { id: "administration", label: "Administração", hint: "Vínculos e permissões governados" },
];

const externalAreas: readonly Readonly<{ id: ExternalArea; label: string }>[] = [
  { id: "requests", label: "Solicitações" },
  { id: "documents", label: "Documentos" },
  { id: "communications", label: "Mensagens" },
  { id: "receipts", label: "Recibos" },
];

function nextIndex(current: number, key: string, length: number) {
  if (key === "ArrowRight" || key === "ArrowDown") return (current + 1) % length;
  if (key === "ArrowLeft" || key === "ArrowUp") return (current - 1 + length) % length;
  if (key === "Home") return 0;
  if (key === "End") return length - 1;
  return null;
}

export function StaffNavigation({
  active,
  onChange,
}: {
  active: StaffArea;
  onChange: (area: StaffArea) => void;
}) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const activeIndex = staffAreas.findIndex((area) => area.id === active);

  return (
    <nav className="portal-navigation staff-navigation" aria-label="Áreas do portal">
      <p className="navigation-label">Área de colaboradores</p>
      <div
        className="navigation-items"
        role="tablist"
        aria-label="Áreas de trabalho"
        onKeyDown={(event) => {
          const targetIndex = nextIndex(activeIndex, event.key, staffAreas.length);
          if (targetIndex === null) return;
          event.preventDefault();
          const area = staffAreas[targetIndex];
          onChange(area.id);
          buttons.current[targetIndex]?.focus();
        }}
      >
        {staffAreas.map((area, index) => (
          <button
            key={area.id}
            ref={(node) => {
              buttons.current[index] = node;
            }}
            type="button"
            role="tab"
            id={`navigation-${area.id}`}
            aria-controls={`area-${area.id}`}
            aria-selected={active === area.id}
            tabIndex={active === area.id ? 0 : -1}
            onClick={() => onChange(area.id)}
          >
            <span>{area.label}</span>
            <small>{area.hint}</small>
          </button>
        ))}
      </div>
    </nav>
  );
}

export function StaffAreaPanel({
  area,
  active,
  children,
}: {
  area: StaffArea;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      className="portal-area-panel"
      id={`area-${area}`}
      role="tabpanel"
      aria-labelledby={`navigation-${area}`}
      hidden={!active}
      tabIndex={0}
    >
      {children}
    </section>
  );
}

export function ExternalNavigation({
  audience,
  active,
  onChange,
}: {
  audience: Exclude<PortalAudience, "staff">;
  active: ExternalArea;
  onChange: (area: ExternalArea) => void;
}) {
  const buttons = useRef<Array<HTMLButtonElement | null>>([]);
  const activeIndex = externalAreas.findIndex((area) => area.id === active);
  const audienceLabel = audience === "beneficiary" ? "beneficiário" : "prestador";

  return (
    <nav className="portal-navigation external-navigation" aria-label={`Área do ${audienceLabel}`}>
      <div
        className="navigation-items"
        role="tablist"
        aria-label={`Navegação do ${audienceLabel}`}
        onKeyDown={(event) => {
          const targetIndex = nextIndex(activeIndex, event.key, externalAreas.length);
          if (targetIndex === null) return;
          event.preventDefault();
          const area = externalAreas[targetIndex];
          onChange(area.id);
          buttons.current[targetIndex]?.focus();
        }}
      >
        {externalAreas.map((area, index) => (
          <button
            key={area.id}
            ref={(node) => {
              buttons.current[index] = node;
            }}
            type="button"
            role="tab"
            id={`external-navigation-${area.id}`}
            aria-controls={`external-area-${area.id}`}
            aria-selected={active === area.id}
            tabIndex={active === area.id ? 0 : -1}
            onClick={() => onChange(area.id)}
          >
            {area.label}
          </button>
        ))}
      </div>
    </nav>
  );
}

export function ExternalAreaPanel({
  area,
  active,
  children,
}: {
  area: ExternalArea;
  active: boolean;
  children: React.ReactNode;
}) {
  return (
    <section
      className="portal-area-panel"
      id={`external-area-${area}`}
      role="tabpanel"
      aria-labelledby={`external-navigation-${area}`}
      hidden={!active}
      tabIndex={0}
    >
      {children}
    </section>
  );
}
