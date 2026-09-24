import { useCallback, useEffect, useId, useMemo, useState, type Ref } from "react";

import { AudienceAuthorizationExperience } from "./AudienceAuthorizationExperience";
import { EmployeeQueues } from "./EmployeeQueues";
import { StaffOverview } from "./StaffOverview";
import { StaffCaseWorkspace, casePath, caseRoutePrefix } from "./StaffCaseWorkspace";
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
import { createStaffCaseClient } from "./staffCaseClient";

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

type StaffRoute = Readonly<{ area: StaffArea; caseRef: string | null }>;

// Only the Cases area has an address: /portal/cases and /portal/cases/:ref.
// Every other area lives at /portal/ as before.
function readStaffRoute(): StaffRoute {
  const path = window.location.pathname.replace(/\/+$/, "");
  if (path === caseRoutePrefix) return { area: "cases", caseRef: null };
  if (path.startsWith(`${caseRoutePrefix}/`)) {
    const tail = path.slice(caseRoutePrefix.length + 1);
    if (!tail.includes("/")) {
      try {
        return { area: "cases", caseRef: decodeURIComponent(tail) };
      } catch {
        return { area: "cases", caseRef: null };
      }
    }
  }
  return { area: "overview", caseRef: null };
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
  const [route, setRoute] = useState(readStaffRoute);
  const activeArea = route.area;
  useEffect(() => {
    const onPop = () => setRoute(readStaffRoute());
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);
  const navigate = useCallback((next: StaffRoute) => {
    const path = next.area === "cases" ? casePath(next.caseRef) : "/portal/";
    if (window.location.pathname !== path) window.history.pushState(null, "", path);
    setRoute(next);
  }, []);
  const setActiveArea = useCallback(
    (area: StaffArea) => navigate({ area, caseRef: null }),
    [navigate],
  );
  const selectCase = useCallback(
    (caseRef: string | null) => navigate({ area: "cases", caseRef }),
    [navigate],
  );
  const staffCaseClient = useMemo(() => createStaffCaseClient(), [sessionBinding]);

  let content: React.ReactNode;
  if (activeArea === "overview") {
    content = (
      <StaffOverview
        csrfToken={csrfToken}
        sessionBinding={sessionBinding}
        onSessionUnavailable={onSessionUnavailable}
      />
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
    content = (
      <StaffCaseWorkspace
        service={staffCaseClient}
        onSessionUnavailable={onSessionUnavailable}
        selectedCaseRef={route.caseRef}
        onSelectCase={selectCase}
      />
    );
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
