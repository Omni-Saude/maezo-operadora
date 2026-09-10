import { useId, useMemo, useState, type Ref } from "react";

import { AudienceAuthorizationExperience } from "./AudienceAuthorizationExperience";
import { EmployeeQueues } from "./EmployeeQueues";
import {
  StaffAreaPanel,
  StaffNavigation,
  type StaffArea,
} from "./PortalNavigation";
import type { PortalAudience } from "./caseExperienceModels";
import {
  createCaseExperienceService,
  type AuthorizationIntakeFormProvider,
} from "./caseExperienceServiceFactory";
import { createCaseCommunicationsClient } from "./caseCommunicationsClient";

function formatExpiry(expiresAt: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "long",
    timeStyle: "short",
  }).format(new Date(expiresAt));
}

function SessionHeading({
  audience,
  expiresAt,
  headingRef,
}: {
  audience: PortalAudience;
  expiresAt: string;
  headingRef: Ref<HTMLHeadingElement>;
}) {
  const heading = audience === "staff"
    ? "Área de colaboradores"
    : audience === "beneficiary"
      ? "Área do beneficiário"
      : "Área do prestador";
  return (
    <section className="portal-session-card" aria-labelledby="audience-heading">
      <p className="eyebrow">Sessão ativa</p>
      <p className="visually-hidden" role="status" aria-live="polite" aria-atomic="true">
        Sessão confirmada. {heading}.
      </p>
      <h1 id="audience-heading" ref={headingRef} tabIndex={-1}>{heading}</h1>
      <p>Cada recurso é consultado com a autorização atual desta sessão.</p>
      <dl className="session-detail">
        <div>
          <dt>Validade desta sessão</dt>
          <dd>{formatExpiry(expiresAt)}</dd>
        </div>
      </dl>
    </section>
  );
}

function UnavailableArea({ title, children }: { title: string; children: React.ReactNode }) {
  const headingId = useId();
  return (
    <section className="portal-unavailable" aria-labelledby={headingId}>
      <p className="eyebrow">Disponibilidade atual</p>
      <h2 id={headingId}>{title}</h2>
      <p>{children}</p>
    </section>
  );
}

export function StaffPortalExperience({
  expiresAt,
  csrfToken,
  sessionBinding,
  headingRef,
  onSessionUnavailable,
}: {
  expiresAt: string;
  csrfToken: string;
  sessionBinding: string;
  headingRef: Ref<HTMLHeadingElement>;
  onSessionUnavailable: () => void;
}) {
  const [activeArea, setActiveArea] = useState<StaffArea>("overview");

  let content: React.ReactNode;
  if (activeArea === "overview") {
    content = (
      <section className="portal-guidance" aria-labelledby="staff-guidance-heading">
        <h2 id="staff-guidance-heading">Trabalho autorizado</h2>
        <p>
          Consulte tarefas individuais em Meu trabalho ou Filas da equipe. A responsabilidade e
          cada decisão são confirmadas separadamente pelo servidor.
        </p>
      </section>
    );
  } else if (activeArea === "my-work" || activeArea === "team-queues") {
    content = (
      <EmployeeQueues
        key={activeArea}
        csrfToken={csrfToken}
        sessionBinding={sessionBinding}
        onSessionUnavailable={onSessionUnavailable}
        initialQueue={activeArea === "my-work" ? "mine" : "team"}
        showQueueNavigation={false}
      />
    );
  } else if (activeArea === "cases") {
    content = <UnavailableArea title="Casos">A consulta de casos ainda não está disponível nesta versão do portal.</UnavailableArea>;
  } else if (activeArea === "documents") {
    content = <UnavailableArea title="Documentos">A consulta e o envio de documentos ainda não estão disponíveis nesta versão do portal.</UnavailableArea>;
  } else if (activeArea === "operations") {
    content = (
      <section className="portal-guidance" aria-labelledby="operations-heading">
        <p className="eyebrow">Operações disponíveis</p>
        <h2 id="operations-heading">Comandos por tarefa</h2>
        <p>
          Abra uma tarefa em Meu trabalho ou Filas da equipe para consultar as operações permitidas
          e acompanhar o protocolo até o recibo.
        </p>
      </section>
    );
  } else {
    content = <UnavailableArea title="Administração">Nenhuma função administrativa está disponível para esta sessão.</UnavailableArea>;
  }

  return (
    <main className="portal-session-main">
      <SessionHeading audience="staff" expiresAt={expiresAt} headingRef={headingRef} />
      <div className="staff-portal-workspace">
        <StaffNavigation active={activeArea} onChange={setActiveArea} />
        <div className="staff-panel-stack">
          <StaffAreaPanel area={activeArea} active>
            {content}
          </StaffAreaPanel>
        </div>
      </div>
      <SecurityNote />
    </main>
  );
}

export function ExternalPortalExperience({
  audience,
  expiresAt,
  csrfToken,
  sessionBinding,
  headingRef,
  onSessionUnavailable,
  intakeFormProvider,
}: {
  audience: Exclude<PortalAudience, "staff">;
  expiresAt: string;
  csrfToken: string;
  sessionBinding: string;
  headingRef: Ref<HTMLHeadingElement>;
  onSessionUnavailable: () => void;
  intakeFormProvider?: AuthorizationIntakeFormProvider;
}) {
  const service = useMemo(() => createCaseExperienceService({
    audience,
    csrfToken,
    intakeFormProvider,
  }), [audience, csrfToken, intakeFormProvider, sessionBinding]);
  const communicationsClient = useMemo(
    () => createCaseCommunicationsClient({ csrfToken }),
    [csrfToken, sessionBinding],
  );

  return (
    <main className="portal-session-main external-session-main">
      <SessionHeading audience={audience} expiresAt={expiresAt} headingRef={headingRef} />
      <AudienceAuthorizationExperience
        audience={audience}
        service={service}
        communicationsClient={communicationsClient}
        onSessionUnavailable={onSessionUnavailable}
      />
      <SecurityNote />
    </main>
  );
}

function SecurityNote() {
  return (
    <aside className="security-note portal-security-note" aria-labelledby="security-heading">
      <h2 id="security-heading">Acesso protegido</h2>
      <p>
        Permissões são verificadas pelo servidor. Encerre a sessão ao usar um dispositivo compartilhado.
      </p>
    </aside>
  );
}
