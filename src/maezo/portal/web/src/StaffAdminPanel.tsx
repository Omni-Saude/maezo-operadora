import { useCallback, useEffect, useId, useRef, useState } from "react";

import {
  sessionLevelFailures,
  type StaffAdminClient,
  type StaffAdminFailure,
  type StaffAdminResult,
  type StaffAdminRoster,
  type StaffAdminVinculo,
} from "./staffAdminClient";

// D-40: the staff `administration` area, READ-ONLY. The panel consults the one GET route the
// BFF mounts for it (the reviewed staff-vínculo roster) and renders status only: no grant,
// revoke, extend or re-review exists on this surface — mutations stay on the independently
// granted administrative connection, and the browser is given no affordance for them.

type ConsultState =
  | Readonly<{ kind: "idle" }>
  | Readonly<{ kind: "loading" }>
  | Readonly<{ kind: "ready"; roster: StaffAdminRoster }>
  | Readonly<{ kind: "error"; failure: StaffAdminFailure }>;

const errorMessage: Readonly<Record<StaffAdminFailure, string>> = {
  invalid_request: "O pedido não corresponde ao contrato atual da consulta.",
  authentication_unavailable: "Sua sessão não está mais disponível.",
  operation_forbidden: "Esta consulta não está habilitada para a sua sessão neste ambiente.",
  admin_read_unavailable: "A leitura dos vínculos não está disponível neste ambiente. Nada foi exibido.",
  "invalid-response": "O portal recusou uma resposta inesperada do serviço de administração. Nada foi exibido.",
};

const stateLabels: Readonly<Record<StaffAdminVinculo["state"], string>> = {
  active: "Ativo",
  expired: "Revisão vencida",
  revoked: "Revogado",
};

function formatTimestamp(value: string) {
  return new Intl.DateTimeFormat("pt-BR", {
    dateStyle: "short",
    timeStyle: "medium",
  }).format(new Date(value));
}

function VinculoRow({
  vinculo,
  position,
  total,
}: {
  vinculo: StaffAdminVinculo;
  position: number;
  total: number;
}) {
  const detailId = useId();
  const roles = vinculo.memberships.flatMap((membership) => membership.roles);
  const groups = vinculo.memberships.flatMap((membership) => membership.groups);
  return (
    <section className="admin-vinculo" aria-labelledby={`${detailId}-heading`}>
      {/* F-158: a legible heading; the opaque reference stays available, labelled, below. */}
      <h3 id={`${detailId}-heading`}>
        Vínculo {position} de {total}: {stateLabels[vinculo.state]}
      </h3>
      <dl className="dependency-detail">
        <div>
          <dt>Referência do colaborador</dt>
          <dd className="exact-value">{vinculo.principal_ref}</dd>
        </div>
        <div>
          <dt>Situação da revisão</dt>
          <dd>{stateLabels[vinculo.state]}</dd>
        </div>
        <div>
          <dt>Papéis governados</dt>
          <dd>
            {roles.length === 0
              ? "—"
              : <ul className="document-type-list">{roles.map((role) => <li key={role} className="exact-value">{role}</li>)}</ul>}
          </dd>
        </div>
        <div>
          <dt>Grupos</dt>
          <dd>
            {groups.length === 0
              ? "—"
              : <ul className="document-type-list">{groups.map((group) => <li key={group} className="exact-value">{group}</li>)}</ul>}
          </dd>
        </div>
        <div>
          <dt>Vínculos por recurso</dt>
          <dd>
            {vinculo.subject_bindings.length === 0
              ? "—"
              : (
                  <ul className="document-type-list">
                    {vinculo.subject_bindings.map((binding) => (
                      <li key={`${binding.kind}-${binding.resource_ref}`} className="exact-value">
                        {binding.kind}: {binding.resource_ref}
                      </li>
                    ))}
                  </ul>
                )}
          </dd>
        </div>
        <div>
          <dt>Revisão do registro</dt>
          <dd className="exact-value">{vinculo.membership_revision}</dd>
        </div>
        <div>
          <dt>Revisão vale até</dt>
          <dd>{formatTimestamp(vinculo.reviewed_until)}</dd>
        </div>
      </dl>
    </section>
  );
}

export function StaffAdminPanel({
  client,
  onSessionUnavailable,
}: {
  client: StaffAdminClient;
  onSessionUnavailable: () => void;
}) {
  const [consult, setConsult] = useState<ConsultState>({ kind: "idle" });
  const request = useRef<AbortController | null>(null);
  const epoch = useRef(0);
  const resultHeading = useRef<HTMLHeadingElement | null>(null);

  const consultRoster = useCallback(async () => {
    const current = ++epoch.current;
    request.current?.abort();
    const controller = new AbortController();
    request.current = controller;
    setConsult({ kind: "loading" });
    try {
      const result: StaffAdminResult<StaffAdminRoster> = await client.readVinculos(controller.signal);
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      if (result.kind === "failure") {
        setConsult({ kind: "error", failure: result.failure });
        if (sessionLevelFailures.includes(result.failure)) onSessionUnavailable();
        return;
      }
      setConsult({ kind: "ready", roster: result.value });
    } catch (error) {
      if (controller.signal.aborted || current !== epoch.current) return;
      request.current = null;
      setConsult({ kind: "error", failure: "admin_read_unavailable" });
    }
  }, [client, onSessionUnavailable]);

  useEffect(() => () => {
    epoch.current += 1;
    request.current?.abort();
    request.current = null;
  }, []);

  useEffect(() => {
    if (consult.kind === "error") resultHeading.current?.focus();
  }, [consult]);

  return (
    <section className="staff-admin" aria-labelledby="staff-admin-heading">
      <div className="case-queue-heading">
        <div>
          <h2 id="staff-admin-heading">Vínculos e permissões governados</h2>
          <p>
            Consulta somente leitura dos vínculos administrativos revisados: quem detém quais
            papéis e grupos, até quando cada registro vale e se está revogado.
          </p>
        </div>
      </div>
      <form
        className="case-lookup"
        onSubmit={(event) => {
          event.preventDefault();
          void consultRoster();
        }}
      >
        <button className="primary-action" type="submit" disabled={consult.kind === "loading"}>
          {consult.kind === "loading" ? "Consultando…" : "Consultar vínculos"}
        </button>
      </form>
      <div aria-live="polite" aria-atomic="false">
        {consult.kind === "loading" && <p className="queue-message">Consultando os vínculos revisados.</p>}
        {consult.kind === "error" && (
          <section className="queue-message queue-error" aria-labelledby="staff-admin-error-heading">
            <h3 id="staff-admin-error-heading" ref={resultHeading} tabIndex={-1}>
              Não foi possível consultar os vínculos
            </h3>
            <p role="alert">{errorMessage[consult.failure]}</p>
            {!sessionLevelFailures.includes(consult.failure) && (
              <button className="secondary-action" type="button" onClick={() => void consultRoster()}>
                Consultar novamente
              </button>
            )}
          </section>
        )}
      </div>
      {consult.kind === "ready" && (
        <>
          <p className="freshness-detail">
            {consult.roster.count === 0
              ? "Nenhum vínculo administrativo revisado neste ambiente."
              : `${consult.roster.count} ${consult.roster.count === 1 ? "vínculo revisado" : "vínculos revisados"}.`}
          </p>
          {consult.roster.vinculos.map((vinculo, index) => (
            <VinculoRow
              key={vinculo.principal_ref}
              vinculo={vinculo}
              position={index + 1}
              total={consult.roster.vinculos.length}
            />
          ))}
          <p className="freshness-detail">
            Consulta realizada em {formatTimestamp(consult.roster.generated_at)}.
          </p>
        </>
      )}
      <p className="freshness-detail">
        Somente leitura. Nenhuma alteração de vínculo é enviada por esta tela; a administração
        de vínculos continua na conexão administrativa própria, fora do portal.
      </p>
    </section>
  );
}
